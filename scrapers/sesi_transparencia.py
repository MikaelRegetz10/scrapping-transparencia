# scrapers/sesi_transparencia.py
"""Scraping das páginas de transparência do SESI/DN no Portal da Indústria.

Cobre duas páginas independentes:
  * /transparencia/licitacoes-processos-de-selecao/      -> arquivos publicados
    na página + a lista de processos de contratação (/licitacoes/)
  * /transparencia/informacoes-de-empregados-e-dirigentes/ -> arquivos da página
    + as três abas alimentadas por API (dirigentes, corpo técnico, estrutura
    remuneratória)

Nada aqui clica em botão: o "Carregar Mais" da lista de processos é apenas um
GET paginado em /api/licitacoes/. A rota é lida do próprio JS embutido no HTML
da listagem e a varredura segue o campo `next` que a API devolve, que é
exatamente o que o botão faz no navegador.
"""

import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.validator import DEFAULT_HEADERS
from scrapers.base import BaseScraper

BASE_SITE = "https://www.portaldaindustria.com.br"

PATH_LICITACOES = "/sesi/canais/transparencia/licitacoes-processos-de-selecao/"
PATH_EMPREGADOS = "/sesi/canais/transparencia/informacoes-de-empregados-e-dirigentes/"

# Caminho da API do app Angular embutido via <iframe> na aba "Relação de
# Dirigentes". Só o host sai do HTML (atributo src do iframe); o prefixo abaixo
# é fixo no bundle do app (environment.baseUrl + rota pública).
PATH_API_DDR = "/api-rol-responsaveis/gestao/publico/publicacao"

# Formatos oferecidos pelos botões "Salvar" dentro do iframe de dirigentes.
FORMATOS_DDR = ("XLSX", "ODS")


def _texto(elemento) -> str:
    """Colapsa espaços/quebras de linha do texto de uma tag."""
    if elemento is None:
        return ""
    return " ".join(elemento.get_text(" ").split())


def _preenche_template(template: str, valores: dict[str, Any]) -> str | None:
    """Resolve os `${placeholders}` das arrow functions do JS da página.

    Devolve None quando algum placeholder não tem valor conhecido — melhor
    ignorar o link do que registrar uma URL com `${ano}` literal dentro.
    """
    faltando: list[str] = []

    def substitui(match: re.Match) -> str:
        chave = match.group(1)
        if chave not in valores:
            faltando.append(chave)
            return match.group(0)
        return str(valores[chave])

    resolvido = re.sub(r"\$\{(\w+)\}", substitui, template)
    return None if faltando else resolvido


class SesiTransparenciaScraper(BaseScraper):
    """Varre as duas páginas de transparência do SESI/DN.

    Args:
        licitacoes: varre a página de Licitações / Processos de Seleção.
        empregados: varre a página de Informações de Empregados e Dirigentes.
        apenas_sesi: filtra os processos de contratação pela entidade SESI
            (o código da entidade é lido do checkbox da própria listagem).
        status_licitacoes: filtro de status da API. O site abre a listagem com
            "A" (só os em andamento); None traz todos os processos.
        max_paginas: teto de páginas seguidas no "Carregar Mais".
        incluir_regionais: inclui também os portais de transparência dos
            Departamentos Regionais listados no <select> das páginas.
    """

    def __init__(
        self,
        licitacoes: bool = True,
        empregados: bool = True,
        apenas_sesi: bool = True,
        status_licitacoes: str | None = None,
        max_paginas: int = 100,
        incluir_regionais: bool = False,
    ):
        super().__init__(
            name="SESI_Transparencia",
            base_url=urljoin(BASE_SITE, "/sesi/canais/transparencia/"),
            routes={
                "Licitações / Processos de Seleção": urljoin(BASE_SITE, PATH_LICITACOES),
                "Informações de Empregados e Dirigentes": urljoin(BASE_SITE, PATH_EMPREGADOS),
            },
        )
        self.licitacoes = licitacoes
        self.empregados = empregados
        self.apenas_sesi = apenas_sesi
        self.status_licitacoes = status_licitacoes
        self.max_paginas = max_paginas
        self.incluir_regionais = incluir_regionais

        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

        # O portal derruba conexão de vez em quando; sem repetição uma falha
        # isolada aborta a varredura inteira no meio das 600+ requisições.
        retentativas = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=(429, 502, 503, 504),
            allowed_methods=("GET",),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retentativas))

    # ------------------------------------------------------------------
    # Infraestrutura
    # ------------------------------------------------------------------
    def _get(self, url: str) -> requests.Response:
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response

    def _get_json(self, url: str) -> Any:
        response = self._get(url)
        try:
            return response.json()
        except ValueError as e:
            raise RuntimeError(f"Resposta não-JSON em {url}: {e}") from e

    @staticmethod
    def _tipo_arquivo(url: str) -> str:
        """Deduz o formato pela extensão ou pelo parâmetro de exportação."""
        partes = urlparse(url)
        query = parse_qs(partes.query)

        for chave in ("formatoExportacao", "tipoExportacao", "formato"):
            if chave in query and query[chave][0]:
                return query[chave][0].lower()

        ext = os.path.splitext(partes.path)[1].lower().lstrip(".")
        if ext and len(ext) <= 5:
            return ext

        # Endpoints REST sem extensão devolvem o dataset em JSON.
        if "/api" in partes.path or "api-" in partes.path:
            return "json"

        return "link/página"

    def _registro(self, titulo: str, contexto: str, url: str) -> dict[str, str]:
        return {
            "source": self.name,
            "title": titulo or "Título não identificado",
            "context": contexto,
            "download_url": url,
            "file_type": self._tipo_arquivo(url),
        }

    # ------------------------------------------------------------------
    # Contrato do BaseScraper
    # ------------------------------------------------------------------
    def extract_links(self) -> list[dict[str, str]]:
        secoes = [
            (self.licitacoes, "Licitações / Processos de Seleção", self._extrai_licitacoes),
            (self.empregados, "Informações de Empregados e Dirigentes", self._extrai_empregados),
        ]

        registros: list[dict[str, str]] = []
        falhas: list[str] = []
        vistos: set[str] = set()

        for habilitada, rotulo, extrator in secoes:
            if not habilitada:
                continue

            try:
                encontrados = extrator()
            except Exception as e:
                falhas.append(f"{rotulo}: {e}")
                print(f"      ⚠️ Falha ao varrer '{rotulo}': {e}")
                continue

            novos = [r for r in encontrados if r["download_url"] not in vistos]
            vistos.update(r["download_url"] for r in novos)
            for registro in novos:
                registro["section"] = rotulo
            registros.extend(novos)
            print(f"      📑 {rotulo}: {len(novos)} link(s).")

        if not registros and falhas:
            raise RuntimeError(
                "Nenhuma página do SESI pôde ser lida. Detalhes: " + " | ".join(falhas)
            )

        return registros

    # ------------------------------------------------------------------
    # Página 1: Licitações / Processos de Seleção
    # ------------------------------------------------------------------
    def _extrai_licitacoes(self) -> list[dict[str, str]]:
        url_pagina = urljoin(BASE_SITE, PATH_LICITACOES)
        soup = BeautifulSoup(self._get(url_pagina).text, "html.parser")
        contexto = "Licitações / Processos de Seleção"

        registros = self._downloads_da_pagina(soup, url_pagina, contexto)

        try:
            registros.extend(self._processos_de_contratacao(soup, url_pagina))
        except Exception as e:
            print(f"      ⚠️ Falha na lista de processos de contratação: {e}")

        if self.incluir_regionais:
            registros.extend(self._departamentos_regionais(soup, contexto))

        return registros

    def _processos_de_contratacao(
        self, soup: BeautifulSoup, url_pagina: str
    ) -> list[dict[str, str]]:
        """Segue o botão "Acesse os processos de contratação" e pagina a API.

        O HTML da listagem só traz o esqueleto; as fichas são montadas no
        cliente a partir de /api/licitacoes/. É essa rota que o "Carregar Mais"
        consome, então percorrer o campo `next` cobre todos os resultados.
        """
        url_listagem = self._url_da_listagem(soup, url_pagina)
        html_listagem = self._get(url_listagem).text

        caminho_api = self._rota_da_api(html_listagem)
        endpoint = urljoin(url_listagem, caminho_api)

        params: dict[str, str] = {}
        if self.status_licitacoes:
            params["status"] = self.status_licitacoes
        if self.apenas_sesi:
            codigo = self._codigo_entidade(html_listagem, "SESI")
            if codigo:
                params["empresas"] = codigo
            else:
                print("      ⚠️ Código da entidade SESI não encontrado: trazendo todas.")

        url = f"{endpoint}?{urlencode(params)}" if params else endpoint
        registros: list[dict[str, str]] = []
        processos = 0

        for pagina in range(1, self.max_paginas + 1):
            dados = self._get_json(url)
            resultados = dados.get("results") or []
            if not resultados:
                break

            for processo in resultados:
                processos += 1
                registros.extend(self._documentos_do_processo(processo))

            if pagina == 1:
                print(
                    f"      📄 Processos de contratação: {dados.get('count')} "
                    f"resultado(s) na API {caminho_api}"
                )

            url = dados.get("next")
            if not url:
                break
        else:
            print(f"      ⚠️ Limite de {self.max_paginas} páginas atingido na listagem.")

        print(f"      🧾 {processos} processo(s) percorrido(s) via 'Carregar Mais'.")
        return registros

    @staticmethod
    def _url_da_listagem(soup: BeautifulSoup, url_pagina: str) -> str:
        """Acha na página o link para a listagem de processos (/licitacoes/)."""
        for anchor in soup.find_all("a", href=True):
            destino = urljoin(url_pagina, anchor["href"].strip())
            if urlparse(destino).path.rstrip("/") == "/licitacoes":
                return destino

        raise RuntimeError(
            "Link para a lista de processos de contratação não encontrado na página."
        )

    @staticmethod
    def _rota_da_api(html_listagem: str) -> str:
        """Extrai a rota da API do JS da listagem.

        `var url = "/api/licitacoes/..."` monta a primeira carga; o botão
        `#carregar_mais` (hoje comentado no HTML, substituído pela paginação)
        aponta para a mesma rota via data-url. Aceita os dois formatos.
        """
        padroes = (
            r"""var\s+url\s*=\s*["'](/api/licitacoes/[^"']*)["']""",
            r"""data-url=['"](/api/licitacoes/[^'"]*)['"]""",
        )

        for padrao in padroes:
            match = re.search(padrao, html_listagem)
            if match:
                # Só o caminho interessa: os filtros são remontados aqui.
                return urlparse(match.group(1)).path

        raise RuntimeError("Rota /api/licitacoes/ não localizada no HTML da listagem.")

    @staticmethod
    def _codigo_entidade(html_listagem: str, sigla: str) -> str:
        """Lê o código da entidade no checkbox de filtro da própria listagem."""
        soup = BeautifulSoup(html_listagem, "html.parser")
        checkbox = soup.find("input", id=f"check-{sigla}")
        return checkbox.get("value", "") if checkbox else ""

    def _documentos_do_processo(self, processo: dict) -> list[dict[str, str]]:
        """Cada processo publica N documentos (edital, ata, resultado...)."""
        titulo_processo = (processo.get("titulo") or "").strip()
        entidades = ", ".join(
            e.get("nome", "") for e in processo.get("empresas") or []
        )
        contexto_base = " | ".join(
            parte
            for parte in (
                "Processos de contratação",
                entidades,
                processo.get("modalidade"),
                processo.get("status"),
                f"Abertura: {processo.get('data_abertura')}"
                if processo.get("data_abertura")
                else "",
            )
            if parte
        )

        registros = []
        for documento in processo.get("documentos") or []:
            arquivo = (documento.get("arquivo") or "").strip()
            if not arquivo:
                continue

            descricao = (documento.get("descricao") or "").strip()
            tipo = (documento.get("tipo") or "").strip()

            titulo = (
                f"{titulo_processo} — {descricao}"
                if descricao and descricao != titulo_processo
                else titulo_processo or descricao
            )
            contexto = f"{contexto_base} | {tipo}" if tipo else contexto_base

            registros.append(self._registro(titulo, contexto, arquivo))

        return registros

    # ------------------------------------------------------------------
    # Página 2: Informações de Empregados e Dirigentes
    # ------------------------------------------------------------------
    def _extrai_empregados(self) -> list[dict[str, str]]:
        url_pagina = urljoin(BASE_SITE, PATH_EMPREGADOS)
        html = self._get(url_pagina).text
        soup = BeautifulSoup(html, "html.parser")
        contexto = "Informações de Empregados e Dirigentes"

        registros = self._downloads_da_pagina(soup, url_pagina, contexto)

        componentes = self._componentes_alpine(soup, contexto)
        registros.extend(componentes["registros"])

        try:
            registros.extend(
                self._relacao_de_dirigentes(
                    soup, url_pagina, contexto, componentes["departamento"]
                )
            )
        except Exception as e:
            print(f"      ⚠️ Falha na aba 'Relação de Dirigentes': {e}")

        if self.incluir_regionais:
            registros.extend(self._departamentos_regionais(soup, contexto))

        return registros

    def _componentes_alpine(
        self, soup: BeautifulSoup, contexto: str
    ) -> dict[str, Any]:
        """Lê os componentes Alpine embutidos no <script> da página.

        Cada aba declara um `window.<nome> = function()` com as arrow functions
        que montam as URLs de dados e de exportação, além da entidade, do
        departamento e da lista de anos do <select>. Reconstruir as URLs a
        partir desses templates evita chumbar endpoints no código.
        """
        registros: list[dict[str, str]] = []
        departamento = ""

        for script in soup.find_all("script"):
            js = script.string or ""
            nome_match = re.search(r"window\.(\w+)\s*=\s*function\s*\(\)", js)
            if not nome_match or "const urls" not in js:
                continue

            nome = nome_match.group(1)
            config = dict(
                re.findall(r"^\s*(api|entidade|departamento):\s*'([^']*)'", js, re.M)
            )
            departamento = departamento or config.get("departamento", "")

            templates = dict(
                re.findall(
                    r"(data|download):\s*\(\{[^}]*\}\)\s*=>\s*`([^`]+)`", js
                )
            )
            anos = self._anos_do_componente(js)
            formatos = sorted(set(re.findall(r'this\.download\("(\w+)"\)', js)))
            rotulo = self._rotulo_da_aba(soup, nome) or nome

            for ano in anos:
                valores = {**config, "ano": ano}

                url_dados = _preenche_template(templates.get("data", ""), valores)
                if url_dados:
                    registros.append(
                        self._registro(
                            f"{rotulo} — {ano} (dados)",
                            f"{contexto} | {rotulo} | API de dados",
                            url_dados,
                        )
                    )

                for formato in formatos:
                    url_export = _preenche_template(
                        templates.get("download", ""), {**valores, "formato": formato}
                    )
                    if url_export:
                        registros.append(
                            self._registro(
                                f"{rotulo} — {ano} ({formato})",
                                f"{contexto} | {rotulo} | Botão Salvar ({formato})",
                                url_export,
                            )
                        )

        return {"registros": registros, "departamento": departamento}

    @staticmethod
    def _anos_do_componente(js: str) -> list[int]:
        """Lê a lista de anos que alimenta o <select> da aba."""
        match = re.search(r"JSON\.parse\('(\[[^']*\])'\)", js)
        if match:
            try:
                return [int(ano) for ano in json.loads(match.group(1))]
            except (ValueError, TypeError):
                pass

        # Sem a lista, resta o ano inicial que o componente já traz resolvido.
        match = re.search(r"parseInt\('(\d{4})'\)", js)
        return [int(match.group(1))] if match else []

    @staticmethod
    def _rotulo_da_aba(soup: BeautifulSoup, nome_componente: str) -> str:
        """Nome legível da aba que hospeda o componente (texto do <a> da tab)."""
        elemento = soup.find(
            attrs={"x-data": re.compile(rf"\b{re.escape(nome_componente)}\(\)")}
        )
        if elemento is None:
            return ""

        painel = elemento.find_parent(attrs={"role": "tabpanel"})
        if painel is None or not painel.get("id"):
            return ""

        aba = soup.find("a", href=f"#{painel['id']}")
        return _texto(aba) or painel["id"]

    def _relacao_de_dirigentes(
        self,
        soup: BeautifulSoup,
        url_pagina: str,
        contexto: str,
        departamento: str,
    ) -> list[dict[str, str]]:
        """Monta os links da aba servida pelo <iframe> do sistema DDR.

        A aba é um app Angular externo; entidade/órgão saem da query string do
        próprio iframe e o resto vem da API pública dele (anos publicados e
        grupos de cada publicação).
        """
        iframe = soup.find("iframe", src=re.compile(r"relacao-dirigentes"))
        if iframe is None:
            raise RuntimeError("Iframe de relação de dirigentes não encontrado.")

        src = urljoin(url_pagina, iframe["src"].strip())
        partes = urlparse(src)
        query = parse_qs(partes.query)
        entidade = (query.get("entidade") or [""])[0]
        orgao = (query.get("orgao") or [""])[0]

        if not departamento:
            # O app deriva a sigla do órgão; no DN é sempre <ENTIDADE>-DN.
            if "nacional" not in orgao.lower():
                raise RuntimeError(f"Departamento não identificado para o órgão '{orgao}'.")
            departamento = f"{entidade}-DN"

        api = f"{partes.scheme}://{partes.netloc}{PATH_API_DDR}"
        rotulo = self._rotulo_da_aba_por_iframe(soup, iframe) or "Relação de Dirigentes"

        registros = [
            self._registro(
                f"{rotulo} — página embutida ({orgao})",
                f"{contexto} | {rotulo} | Iframe da aba",
                src,
            )
        ]

        anos = self._get_json(f"{api}/anos")
        for ano in anos:
            base_params = {
                "ano": ano,
                "entidade": entidade,
                "departamento": departamento,
            }
            url_dados = f"{api}/relacao-dirigentes?{urlencode(base_params)}"
            registros.append(
                self._registro(
                    f"{rotulo} — {ano} (dados)",
                    f"{contexto} | {rotulo} | API de dados",
                    url_dados,
                )
            )

            for publicacao in self._get_json(url_dados):
                for grupo in publicacao.get("grupos") or []:
                    for formato in FORMATOS_DDR:
                        url_export = f"{api}/download/iframe?" + urlencode(
                            {
                                **base_params,
                                "formatoExportacao": formato,
                                "idGrupo": grupo.get("id"),
                            }
                        )
                        registros.append(
                            self._registro(
                                f"{rotulo} — {grupo.get('nome')} — {ano} ({formato})",
                                f"{contexto} | {rotulo} | Botão Salvar ({formato})",
                                url_export,
                            )
                        )

        return registros

    @staticmethod
    def _rotulo_da_aba_por_iframe(soup: BeautifulSoup, iframe) -> str:
        painel = iframe.find_parent(attrs={"role": "tabpanel"})
        if painel is None or not painel.get("id"):
            return ""

        aba = soup.find("a", href=f"#{painel['id']}")
        return _texto(aba) or painel["id"]

    # ------------------------------------------------------------------
    # Blocos comuns às duas páginas
    # ------------------------------------------------------------------
    def _downloads_da_pagina(
        self, soup: BeautifulSoup, url_pagina: str, contexto: str
    ) -> list[dict[str, str]]:
        """Arquivos publicados direto na página (cartões "si-download")."""
        registros = []

        for anchor in soup.select("a.si-download-grande, a.si-download-pequeno"):
            href = anchor.get("href", "").strip()
            if not href:
                continue

            # <p> traz a descrição e <h4> o ano; <span> guarda o tamanho.
            partes = [_texto(t) for t in anchor.select("p, h4")]
            titulo = " ".join(p for p in partes if p) or anchor.get("title", "")
            tamanho = _texto(anchor.find("span"))

            detalhe = f"{contexto} | Arquivo publicado na página"
            if tamanho:
                detalhe += f" ({tamanho})"

            registros.append(
                self._registro(titulo, detalhe, urljoin(url_pagina, href))
            )

        return registros

    def _departamentos_regionais(
        self, soup: BeautifulSoup, contexto: str
    ) -> list[dict[str, str]]:
        """Portais de transparência dos DRs listados no <select> da página."""
        seletor = soup.select_one("select.departamento-regional-select")
        if seletor is None:
            return []

        registros = []
        for option in seletor.find_all("option"):
            valor = (option.get("value") or "").strip()
            if not valor.startswith("http"):
                continue

            registros.append(
                self._registro(
                    f"Transparência {_texto(option)}",
                    f"{contexto} | Departamento Regional",
                    valor,
                )
            )

        return registros
