# scrapers/sesi_pdf.py
"""Varredura das páginas de transparência do SESI/DN via navegador headless.

Cobre as três rotas alvo:
  * /sesi/canais/transparencia                              -> página índice
  * /sesi/canais/transparencia/licitacoes-processos-de-selecao/
  * /sesi/canais/transparencia/informacoes-de-empregados-e-dirigentes/

A página índice não publica arquivo nenhum: ela é o menu do canal. Por isso a
varredura dela segue as subpáginas de transparência listadas ali (contratos,
demonstrações contábeis, orçamento...), que é onde os PDFs realmente estão.

As outras duas páginas escondem parte do conteúdo atrás de JavaScript — as abas
Alpine da página de empregados e a listagem de processos de contratação, que é
montada no cliente a partir de /api/licitacoes/. Abrindo com o Chromium as abas
podem ser clicadas de verdade, e a API é consultada pela própria sessão do
navegador (`page.request`), herdando cookies e cabeçalhos.
"""

import logging
from typing import Dict, List
from urllib.parse import urljoin, urlparse

from scrapers.base import BaseScraper
from scrapers.navegador import (
    abrir_navegador,
    abrir_pagina,
    ancoras_da_pagina,
    eh_documento,
    nome_arquivo,
    rotulo,
    separa_publicacao,
    tipo_arquivo,
)

logger = logging.getLogger("scrapers.sesi_pdf")

BASE_SITE = "https://www.portaldaindustria.com.br"

PATH_HUB = "/sesi/canais/transparencia"
PATH_LICITACOES = "/sesi/canais/transparencia/licitacoes-processos-de-selecao/"
PATH_EMPREGADOS = "/sesi/canais/transparencia/informacoes-de-empregados-e-dirigentes/"

# Listagem de processos de contratação que o botão da página de licitações abre.
PATH_LISTAGEM = "/licitacoes/"
PATH_API_LICITACOES = "/api/licitacoes/"

# Subpáginas do índice que não têm documento para coletar.
SUBPAGINAS_IGNORADAS = ("acessibilidade", "duvidas-frequentes-faq", "ouvidoria", "sac")

ABAS_EMPREGADOS = "[role=tab], .nav-tabs a, ul.nav a"


class SesiPdfScraper(BaseScraper):
    """Coleta os links de documentos das páginas de transparência do SESI/DN.

    Args:
        hub: varre a página índice e as subpáginas de transparência ligadas a ela.
        licitacoes: varre a página de Licitações / Processos de Seleção.
        empregados: varre a página de Informações de Empregados e Dirigentes.
        apenas_pdf: mantém só PDFs (padrão). Com False registra também csv,
            xlsx, ods, docx e afins publicados nas mesmas páginas.
        apenas_sesi: filtra os processos de contratação pela entidade SESI. O
            código é lido do checkbox da própria listagem; a lista completa
            mistura CNI, SENAI e IEL.
        max_paginas_api: teto de páginas seguidas na API de processos.
        max_subpaginas: teto de subpáginas visitadas a partir do índice.
        headless: False abre o Chromium com janela, útil para depurar.
    """

    def __init__(
        self,
        hub: bool = True,
        licitacoes: bool = True,
        empregados: bool = True,
        apenas_pdf: bool = True,
        apenas_sesi: bool = True,
        max_paginas_api: int = 100,
        max_subpaginas: int = 30,
        headless: bool = True,
    ):
        super().__init__(
            name="SESI_PDF",
            base_url=urljoin(BASE_SITE, PATH_HUB),
            routes={
                "Transparência (índice)": urljoin(BASE_SITE, PATH_HUB),
                "Licitações / Processos de Seleção": urljoin(BASE_SITE, PATH_LICITACOES),
                "Informações de Empregados e Dirigentes": urljoin(BASE_SITE, PATH_EMPREGADOS),
            },
        )
        self.hub = hub
        self.licitacoes = licitacoes
        self.empregados = empregados
        self.apenas_pdf = apenas_pdf
        self.apenas_sesi = apenas_sesi
        self.max_paginas_api = max_paginas_api
        self.max_subpaginas = max_subpaginas
        self.headless = headless

    # ------------------------------------------------------------------
    # Contrato do BaseScraper
    # ------------------------------------------------------------------
    def extract_links(self) -> List[Dict[str, str]]:
        registros: List[Dict[str, str]] = []
        vistos: set[str] = set()
        falhas: List[str] = []

        with abrir_navegador(headless=self.headless) as page:
            rotas = [
                (self.hub, "Transparência (índice)", self._extrai_hub),
                (self.licitacoes, "Licitações / Processos de Seleção", self._extrai_licitacoes),
                (self.empregados, "Informações de Empregados e Dirigentes", self._extrai_empregados),
            ]

            for habilitada, secao, extrator in rotas:
                if not habilitada:
                    continue

                try:
                    encontrados = extrator(page)
                except Exception as e:
                    falhas.append(f"{secao}: {e}")
                    logger.error("Falha ao varrer '%s': %s", secao, e)
                    continue

                # A deduplicação precisa valer também dentro da mesma rota: a
                # mesma página repete o link em mais de um lugar.
                novos = []
                for registro in encontrados:
                    if registro["download_url"] in vistos:
                        continue
                    vistos.add(registro["download_url"])
                    registro.setdefault("section", secao)
                    novos.append(registro)

                registros.extend(novos)
                logger.info("📑 %s: %d documento(s).", secao, len(novos))

        if not registros and falhas:
            raise RuntimeError(
                "Nenhuma página do SESI pôde ser lida. Detalhes: " + " | ".join(falhas)
            )

        return registros

    def _registro(self, titulo: str, contexto: str, url: str) -> Dict[str, str]:
        titulo_limpo, publicado_em = separa_publicacao(titulo)
        return {
            "source": self.name,
            "title": titulo_limpo or "Título não identificado",
            "context": contexto,
            "download_url": url,
            "file_name": nome_arquivo(url),
            "file_type": tipo_arquivo(url),
            "published_at": publicado_em,
        }

    def _documentos_da_pagina(self, page, contexto: str) -> List[Dict[str, str]]:
        """Todo documento linkado na página já renderizada."""
        return [
            self._registro(rotulo(ancora), contexto, ancora["url"])
            for ancora in ancoras_da_pagina(page)
            if eh_documento(ancora["url"], self.apenas_pdf)
        ]

    # ------------------------------------------------------------------
    # Rota 1: página índice + subpáginas de transparência
    # ------------------------------------------------------------------
    def _extrai_hub(self, page) -> List[Dict[str, str]]:
        url = urljoin(BASE_SITE, PATH_HUB)
        if not abrir_pagina(page, url):
            raise RuntimeError("a página índice de transparência não carregou")

        registros = self._documentos_da_pagina(page, "Transparência (índice)")
        subpaginas = self._subpaginas_do_hub(page)
        logger.info(
            "Índice de transparência: %d subpágina(s) a visitar.", len(subpaginas)
        )

        for destino, nome in subpaginas[: self.max_subpaginas]:
            if not abrir_pagina(page, destino, espera_ms=2500, tentativas=2):
                logger.warning("Subpágina não carregou: %s", destino)
                continue

            contexto = f"Transparência (índice) | {nome}"
            encontrados = self._documentos_da_pagina(page, contexto)
            for registro in encontrados:
                registro["section"] = f"Índice — {nome}"
            registros.extend(encontrados)
            logger.debug("  %s: %d documento(s).", nome, len(encontrados))

        return registros

    def _subpaginas_do_hub(self, page) -> List[tuple[str, str]]:
        """Links do índice que apontam para outra página do canal transparência.

        As páginas que já têm extrator próprio ficam de fora: visitá-las aqui
        antes da rota dedicada faria os PDFs delas serem creditados ao índice,
        já que a deduplicação mantém a primeira ocorrência de cada URL.
        """
        raiz = urlparse(urljoin(BASE_SITE, PATH_HUB)).path.rstrip("/")
        encontradas: dict[str, str] = {}

        proprias = set()
        if self.licitacoes:
            proprias.add(PATH_LICITACOES.rstrip("/"))
        if self.empregados:
            proprias.add(PATH_EMPREGADOS.rstrip("/"))

        for ancora in ancoras_da_pagina(page):
            partes = urlparse(ancora["url"])
            caminho = partes.path.rstrip("/")

            if partes.netloc != urlparse(BASE_SITE).netloc:
                continue
            if not caminho.startswith(raiz + "/") or caminho == raiz:
                continue
            if caminho in proprias:
                continue
            # Só um nível abaixo do índice; nada de descer a árvore inteira.
            if caminho.count("/") != raiz.count("/") + 1:
                continue
            if any(ign in caminho for ign in SUBPAGINAS_IGNORADAS):
                continue

            destino = f"{partes.scheme}://{partes.netloc}{partes.path}"
            nome = (ancora.get("texto") or "").strip() or caminho.rsplit("/", 1)[-1]
            encontradas.setdefault(destino, nome)

        return sorted(encontradas.items(), key=lambda par: par[0])

    # ------------------------------------------------------------------
    # Rota 2: Licitações / Processos de Seleção
    # ------------------------------------------------------------------
    def _extrai_licitacoes(self, page) -> List[Dict[str, str]]:
        url = urljoin(BASE_SITE, PATH_LICITACOES)
        contexto = "Licitações / Processos de Seleção"
        if not abrir_pagina(page, url):
            raise RuntimeError("a página de licitações não carregou")

        registros = self._documentos_da_pagina(page, contexto)

        try:
            registros.extend(self._processos_de_contratacao(page))
        except Exception as e:
            logger.error("Falha na lista de processos de contratação: %s", e)

        return registros

    def _processos_de_contratacao(self, page) -> List[Dict[str, str]]:
        """Percorre a API que alimenta a listagem, seguindo o campo `next`.

        É a mesma rota que o "Carregar Mais" da listagem consome. Consultá-la
        pela sessão do navegador (`page.request`) aproveita os cookies já
        negociados ao abrir a página.
        """
        listagem = urljoin(BASE_SITE, PATH_LISTAGEM)
        if not abrir_pagina(page, listagem):
            raise RuntimeError("a listagem de processos não carregou")

        url = urljoin(BASE_SITE, PATH_API_LICITACOES)
        codigo_sesi = self._codigo_entidade(page, "SESI") if self.apenas_sesi else ""
        if self.apenas_sesi:
            if codigo_sesi:
                url = f"{url}?empresas={codigo_sesi}"
            else:
                logger.warning(
                    "Código da entidade SESI não encontrado na listagem: "
                    "trazendo os processos de todas as entidades."
                )

        registros: List[Dict[str, str]] = []
        processos = 0

        for pagina in range(1, self.max_paginas_api + 1):
            resposta = page.request.get(url, timeout=45000)
            if not resposta.ok:
                raise RuntimeError(f"API de licitações devolveu HTTP {resposta.status}")

            dados = resposta.json()
            resultados = dados.get("results") or []
            if not resultados:
                break

            if pagina == 1:
                logger.info(
                    "Processos de contratação: %s resultado(s) na API.",
                    dados.get("count"),
                )

            for processo in resultados:
                processos += 1
                registros.extend(self._documentos_do_processo(processo))

            url = dados.get("next")
            if not url:
                break
        else:
            logger.warning(
                "Limite de %d páginas atingido na API de licitações.",
                self.max_paginas_api,
            )

        logger.info("%d processo(s) percorrido(s) na listagem.", processos)
        return registros

    @staticmethod
    def _codigo_entidade(page, sigla: str) -> str:
        """Lê o código da entidade no checkbox de filtro da própria listagem."""
        caixa = page.locator(f"input#check-{sigla}")
        if caixa.count() == 0:
            return ""
        return caixa.first.get_attribute("value") or ""

    def _documentos_do_processo(self, processo: dict) -> List[Dict[str, str]]:
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
            if not arquivo or not eh_documento(arquivo, self.apenas_pdf):
                continue

            descricao = (documento.get("descricao") or "").strip()
            tipo = (documento.get("tipo") or "").strip()

            titulo = (
                f"{titulo_processo} — {descricao}"
                if descricao and descricao != titulo_processo
                else titulo_processo or descricao
            )
            registro = self._registro(
                titulo,
                f"{contexto_base} | {tipo}" if tipo else contexto_base,
                arquivo,
            )
            registro["section"] = "Processos de contratação"
            registros.append(registro)

        return registros

    # ------------------------------------------------------------------
    # Rota 3: Informações de Empregados e Dirigentes
    # ------------------------------------------------------------------
    def _extrai_empregados(self, page) -> List[Dict[str, str]]:
        """Cada aba monta o próprio conteúdo, então todas precisam ser clicadas."""
        url = urljoin(BASE_SITE, PATH_EMPREGADOS)
        contexto = "Informações de Empregados e Dirigentes"
        if not abrir_pagina(page, url):
            raise RuntimeError("a página de empregados e dirigentes não carregou")

        registros = self._documentos_da_pagina(page, contexto)

        abas = page.locator(ABAS_EMPREGADOS)
        total_abas = abas.count()
        logger.info("Página de empregados com %d aba(s).", total_abas)

        for indice in range(total_abas):
            aba = abas.nth(indice)
            try:
                nome = (aba.inner_text() or "").strip() or f"Aba {indice + 1}"
                aba.scroll_into_view_if_needed(timeout=5000)
                aba.click(timeout=8000)
                page.wait_for_timeout(2500)
            except Exception as e:
                logger.debug("Aba %d não pôde ser aberta: %s", indice + 1, e)
                continue

            for registro in self._documentos_da_pagina(page, f"{contexto} | {nome}"):
                registros.append(registro)

        return registros
