import json
import os
import time
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import requests

from core.validator import DEFAULT_HEADERS
from scrapers.base import BaseScraper

BASE_SITE = "https://www.abdi.com.br"

# Tipos que o core/profiler.py sabe perfilar. Os demais (pdf, zip...) são
# apenas auditados quanto à disponibilidade pelo pipeline.
PATH_DADOS_ABERTOS = "/transparencia/dados-abertos/"
PATH_AQUISICOES = "/transparencia/aquisicao-de-bens-e-servicos/"
PATH_PROCESSO_SELETIVO = "/transparencia/processo-seletivo/"


class CloudflareChallengeError(RuntimeError):
    """Levantada quando a ABDI devolve o desafio da Cloudflare em vez do HTML."""


def _normaliza(elemento) -> str:
    """Colapsa espaços/quebras de linha e remove ponto final decorativo."""
    # get_text(" ") evita colar textos de tags irmãs ("...Projetos(Publicado em...").
    return " ".join(elemento.get_text(" ").split()).rstrip(".")


def _trunca(texto: str, limite: int) -> str:
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


def _rotulo_licitacao(licitacao: str, documento: str) -> str:
    """Junta licitação + documento evitando repetição e títulos quilométricos.

    O nome do documento fica no fim e nunca é truncado: é ele que diferencia
    o edital da ata e dos termos dentro de uma mesma licitação.
    """
    if not licitacao:
        return _trunca(documento or "Documento", 200)

    if not documento or licitacao.startswith(documento) or documento.startswith(licitacao):
        maior = licitacao if len(licitacao) >= len(documento) else documento
        return _trunca(maior, 200)

    return f"{_trunca(licitacao, 120)} — {documento}"


def _achata_para_form(prefixo: str, valor, destino: list) -> None:
    """Converte dict/list aninhado no formato de form PHP (`chave[sub][]=v`).

    É o formato que o admin-ajax do JetEngine espera receber no load more.
    """
    if isinstance(valor, dict):
        for chave, sub in valor.items():
            _achata_para_form(f"{prefixo}[{chave}]", sub, destino)
    elif isinstance(valor, list):
        for sub in valor:
            _achata_para_form(f"{prefixo}[]", sub, destino)
    elif valor is None:
        destino.append((prefixo, ""))
    elif isinstance(valor, bool):
        destino.append((prefixo, "true" if valor else "false"))
    else:
        destino.append((prefixo, str(valor)))


class ABDIScraper(BaseScraper):
    """Scraping estático dos caminhos de transparência da ABDI.

    Cobre três caminhos independentes:
      * /dados-abertos/            -> botões "Dados Abertos" (CSV/XLSX)
      * /aquisicao-de-bens-e-servicos/ -> listing grid JetEngine paginado por AJAX
      * /processo-seletivo/        -> HTML estático com PDFs dos comunicados
    """

    def __init__(
        self,
        dados_abertos: bool = True,
        aquisicoes: bool = True,
        processo_seletivo: bool = True,
        max_paginas: int = 200,
        pausa_paginas: float = 2.0,
    ):
        super().__init__(
            name="ABDI",
            base_url=urljoin(BASE_SITE, "/transparencia/"),
            routes={
                "Dados Abertos": urljoin(BASE_SITE, PATH_DADOS_ABERTOS),
                "Aquisição de Bens e Serviços": urljoin(BASE_SITE, PATH_AQUISICOES),
                "Processo Seletivo": urljoin(BASE_SITE, PATH_PROCESSO_SELETIVO),
            },
        )
        self.dados_abertos = dados_abertos
        self.aquisicoes = aquisicoes
        self.processo_seletivo = processo_seletivo
        self.max_paginas = max_paginas
        # A ABDI derruba a conexão (RemoteDisconnected) quando o load more vai
        # sem respiro: a varredura inteira morre por volta da 10ª página. Com
        # pausa entre as páginas ela vai até o fim das ~34.
        self.pausa_paginas = pausa_paginas

        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    # ------------------------------------------------------------------
    # Infraestrutura HTTP
    # ------------------------------------------------------------------
    def _get(self, url: str) -> requests.Response:
        response = self.session.get(url, timeout=30)

        # O site fica atrás da Cloudflare: quando o desafio dispara o corpo
        # devolvido é a página "Just a moment..." e não o HTML da seção.
        corpo = response.text[:4000]
        if response.status_code == 403 and (
            "Just a moment" in corpo or "cf-chl" in corpo or "challenge-platform" in corpo
        ):
            raise CloudflareChallengeError(
                f"Cloudflare bloqueou o acesso a {url} (HTTP 403 - desafio JS). "
                "Este IP precisa ser liberado ou o scraper migrado para navegador headless."
            )

        response.raise_for_status()
        return response

    @staticmethod
    def _tipo_arquivo(url: str) -> str:
        """Deduz o tipo pelo caminho; links jet_download não têm extensão."""
        if "jet_download=" in url:
            # Verificado via Content-Disposition: o JetEngine da ABDI serve PDF.
            return "pdf"

        ext = os.path.splitext(urlparse(url).path)[1].lower().lstrip(".")
        return ext if ext else "desconhecido"

    def _registro(self, titulo: str, contexto: str, url: str) -> dict:
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
            (self.dados_abertos, "Dados Abertos", self._extrai_dados_abertos),
            (self.aquisicoes, "Aquisição de Bens e Serviços", self._extrai_aquisicoes),
            (self.processo_seletivo, "Processo Seletivo", self._extrai_processo_seletivo),
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
            print(f"      📑 {rotulo}: {len(novos)} link(s) de download.")

        if not registros and falhas:
            raise RuntimeError(
                "Nenhuma seção da ABDI pôde ser lida. Detalhes: " + " | ".join(falhas)
            )

        return registros

    # ------------------------------------------------------------------
    # Seção 1: /transparencia/dados-abertos/
    # ------------------------------------------------------------------
    def _extrai_dados_abertos(self) -> list[dict[str, str]]:
        url_secao = urljoin(BASE_SITE, PATH_DADOS_ABERTOS)
        soup = BeautifulSoup(self._get(url_secao).text, "html.parser")

        registros = []
        for anchor in soup.find_all("a", href=True):
            if anchor.get_text(strip=True).lower() != "dados abertos":
                continue

            full_url = urljoin(url_secao, anchor["href"].strip())

            # Sobe a árvore DOM procurando o título do relatório na mesma linha.
            titulo = ""
            for parent in anchor.parents:
                heading = parent.find(
                    class_=["elementor-heading-title", "elementor-widget-heading"]
                )
                if heading:
                    titulo = _normaliza(heading)
                    break

            registros.append(self._registro(titulo, "Botão Dados Abertos", full_url))

        return registros

    # ------------------------------------------------------------------
    # Seção 2: /transparencia/aquisicao-de-bens-e-servicos/
    # ------------------------------------------------------------------
    def _extrai_aquisicoes(self) -> list[dict[str, str]]:
        url_secao = urljoin(BASE_SITE, PATH_AQUISICOES)
        html_inicial = self._get(url_secao).text
        soup = BeautifulSoup(html_inicial, "html.parser")

        registros = self._parseia_itens_aquisicao(soup)

        # O botão "Carregar Mais" é um POST para a própria página. Todo o payload
        # (query + widget_settings + assinatura) vem embutido no atributo data-nav.
        grid = soup.select_one("div.jet-listing-grid__items[data-nav]")
        if grid is None:
            print("      ⚠️ Grid JetEngine não encontrado: apenas a 1ª página foi lida.")
            return registros

        nav = json.loads(grid["data-nav"])
        campos: list[tuple[str, str]] = [
            ("action", "jet_engine_ajax"),
            ("handler", "listing_load_more"),
        ]
        _achata_para_form("query", nav.get("query", {}), campos)
        _achata_para_form("widget_settings", nav.get("widget_settings", {}), campos)
        campos.append(("page_settings[post_id]", "false"))
        campos.append(("listing_type", "false"))
        campos.append(("isEditMode", "false"))

        url_ajax = f"{url_secao}?nocache=1"
        for pagina in range(2, self.max_paginas + 1):
            payload = campos + [("page_settings[page]", str(pagina))]

            trecho = self._carrega_pagina_aquisicao(url_ajax, payload, pagina)
            if trecho is None:
                # Página perdida de vez: devolvemos o que já foi coletado em vez
                # de derrubar a seção inteira por causa de uma queda de conexão.
                print(
                    f"      ⚠️ Aquisições interrompidas na página {pagina}: "
                    f"{len(registros)} registro(s) preservado(s)."
                )
                return registros

            if not trecho.strip():
                break

            novos = self._parseia_itens_aquisicao(
                BeautifulSoup(trecho, "html.parser")
            )
            if not novos:
                break

            registros.extend(novos)
        else:
            print(
                f"      ⚠️ Limite de {self.max_paginas} páginas atingido em Aquisições."
            )

        return registros

    def _carrega_pagina_aquisicao(
        self, url_ajax: str, payload: list, pagina: int
    ) -> str | None:
        """Busca uma página do load more. Devolve o HTML ou None se desistir.

        Recua e tenta de novo antes de desistir: a queda costuma ser a ABDI
        cortando o ritmo, e não a paginação tendo acabado.
        """
        for tentativa in range(1, 4):
            time.sleep(self.pausa_paginas * tentativa)
            try:
                resposta = self.session.post(
                    url_ajax,
                    data=payload,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=30,
                )
                resposta.raise_for_status()
                return resposta.json().get("data", {}).get("html", "")
            except Exception as e:
                print(
                    f"      ↻ Página {pagina} falhou ({type(e).__name__}), "
                    f"tentativa {tentativa}/3."
                )
        return None

    def _parseia_itens_aquisicao(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        """Cada item é um accordion: título da licitação + N documentos + situação."""
        registros = []

        for item in soup.select(".jet-listing-grid__item"):
            titulo_el = item.select_one(".e-n-accordion-item-title-text")
            licitacao = _normaliza(titulo_el) if titulo_el else ""

            # Percorre o item em ordem de documento pareando cada botão de
            # download com o último cabeçalho visto (ex: "Ata da Sessão - ...").
            # O cabeçalho que sobra sem botão é a situação ("Em andamento").
            do_item = []
            rotulo_corrente = ""
            rotulo_usado = True
            situacao = ""

            for el in item.find_all(True):
                classes = el.get("class") or []

                if "elementor-heading-title" in classes:
                    if not rotulo_usado and rotulo_corrente:
                        situacao = rotulo_corrente
                    rotulo_corrente = _normaliza(el)
                    rotulo_usado = False
                    continue

                if el.name == "a" and "jet-download" in classes and el.get("href"):
                    rotulo_usado = True
                    do_item.append(
                        self._registro(
                            _rotulo_licitacao(licitacao, rotulo_corrente),
                            "Aquisição de Bens e Serviços",
                            urljoin(BASE_SITE, el["href"].strip()),
                        )
                    )

            if not rotulo_usado and rotulo_corrente:
                situacao = rotulo_corrente

            if situacao:
                for registro in do_item:
                    registro["context"] += f" | Situação: {situacao}"

            registros.extend(do_item)

        return registros

    # ------------------------------------------------------------------
    # Seção 3: /transparencia/processo-seletivo/
    # ------------------------------------------------------------------
    def _extrai_processo_seletivo(self) -> list[dict[str, str]]:
        url_secao = urljoin(BASE_SITE, PATH_PROCESSO_SELETIVO)
        soup = BeautifulSoup(self._get(url_secao).text, "html.parser")

        return self._parseia_processo_seletivo(soup)

    def _parseia_processo_seletivo(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        conteudo = soup.select_one(".eael-tabs-content") or soup
        registros = []

        # Mesma lógica de ordem de documento: cabeçalho do comunicado imediatamente
        # antes do botão "Visualizar". Cabeçalho sem botão é o cargo/processo.
        grupo = ""
        rotulo_corrente = ""
        rotulo_usado = True

        for el in conteudo.find_all(True):
            classes = el.get("class") or []

            if "elementor-heading-title" in classes:
                if not rotulo_usado and rotulo_corrente:
                    grupo = rotulo_corrente
                rotulo_corrente = _normaliza(el)
                rotulo_usado = False
                continue

            if el.name != "a" or not el.get("href"):
                continue

            href = urljoin(BASE_SITE, el["href"].strip())
            texto = el.get_text(strip=True).lower()
            if not (texto in ("visualizar", "download", "baixar") or ".pdf" in href.lower()):
                continue

            rotulo_usado = True
            contexto = (
                f"Processo Seletivo | {grupo}" if grupo else "Processo Seletivo"
            )
            registros.append(self._registro(rotulo_corrente, contexto, href))

        return registros
