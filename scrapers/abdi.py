import json
import os
<<<<<<< HEAD
import re
from typing import Dict, List, Set
=======
>>>>>>> main
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

<<<<<<< HEAD
    def __init__(self, ano: int = 2026):
        routes = {
            "Dados Abertos": "https://www.abdi.com.br/transparencia/dados-abertos/",
            "Aquisição de Bens e Serviços": "https://www.abdi.com.br/transparencia/aquisicao-de-bens-e-servicos/",
            "Processo Seletivo": "https://www.abdi.com.br/transparencia/processo-seletivo/",
=======
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
>>>>>>> main
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

<<<<<<< HEAD
        for section_name, url in self.routes.items():
            try:
                if section_name == "Dados Abertos":
                    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=25)
                    response.raise_for_status()
                    soup = BeautifulSoup(response.text, "html.parser")
                    extracted_data.extend(
                        self._extract_dados_abertos(soup, url, section_name)
                    )
                elif section_name == "Aquisição de Bens e Serviços":
                    # Extração com paginação profunda
                    extracted_data.extend(
                        self._extract_aquisicoes_paginadas(url, section_name)
                    )
                else:
                    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=25)
                    response.raise_for_status()
                    soup = BeautifulSoup(response.text, "html.parser")
                    extracted_data.extend(
                        self._extract_pdf_documents(soup, url, section_name)
                    )

            except Exception as e:
                print(f"⚠️ Erro ao acessar a rota [{section_name}] ({url}): {e}")

        return extracted_data

    def _extract_aquisicoes_paginadas(
        self, base_url: str, section: str
    ) -> List[Dict[str, str]]:
        """Varre as páginas de aquisição de bens e serviços por paginação de URL."""
        items: List[Dict[str, str]] = []
        seen_urls: Set[str] = set()

        # 1. Tenta a página inicial
        try:
            res = requests.get(base_url, headers=DEFAULT_HEADERS, timeout=25)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                items.extend(self._extract_pdf_documents(soup, base_url, section, seen_urls))
        except Exception as e:
            print(f"⚠️ Erro na página inicial de aquisições: {e}")

        # 2. Pagina pelas variações de parâmetros suportadas pelo JetEngine / WordPress
        max_paginas = 25
        paginas_vazias_consecutivas = 0

        for page_num in range(2, max_paginas + 1):
            # Formatos de paginação comuns no JetEngine/Elementor
            url_paginada = f"{base_url}?jet_paged={page_num}"

            try:
                res = requests.get(url_paginada, headers=DEFAULT_HEADERS, timeout=20)
                if res.status_code != 200:
                    break

                soup = BeautifulSoup(res.text, "html.parser")
                novos_itens = self._extract_pdf_documents(soup, url_paginada, section, seen_urls)

                if not novos_itens:
                    paginas_vazias_consecutivas += 1
                    if paginas_vazias_consecutivas >= 2:
                        break
                else:
                    paginas_vazias_consecutivas = 0
                    items.extend(novos_itens)

            except Exception:
                break

        # 3. Fallback: Consulta via WP REST API se a paginação tradicional esgotar
        if len(items) <= 15:
            api_items = self._fetch_from_wp_api(section, seen_urls)
            items.extend(api_items)

        return items

    def _fetch_from_wp_api(
        self, section: str, seen_urls: Set[str]
    ) -> List[Dict[str, str]]:
        """Consulta endpoints REST do WordPress da ABDI para recuperar anexos de licitações."""
        items: List[Dict[str, str]] = []
        endpoints = [
            "https://www.abdi.com.br/wp-json/wp/v2/media?per_page=100&mime_type=application/pdf",
        ]

        for ep in endpoints:
            try:
                res = requests.get(ep, headers=DEFAULT_HEADERS, timeout=25)
                if res.status_code == 200:
                    data = res.json()
                    for entry in data:
                        source_url = entry.get("source_url") or entry.get("guid", {}).get("rendered")
                        if not source_url or source_url in seen_urls:
                            continue

                        title = entry.get("title", {}).get("rendered", "Documento PDF")
                        title = BeautifulSoup(title, "html.parser").get_text(strip=True)

                        seen_urls.add(source_url)
                        items.append(
                            {
                                "source": self.name,
                                "section": section,
                                "title": title[:150] if title else "Documento PDF",
                                "download_url": source_url,
                                "file_type": "pdf",
                            }
                        )
            except Exception:
                continue

        return items

    def _extract_dados_abertos(
        self, soup: BeautifulSoup, page_url: str, section: str
    ) -> List[Dict[str, str]]:
        """Extrai planilhas/dados dos botões 'Dados Abertos'."""
        items = []
        for anchor in soup.find_all("a", href=True):
            if anchor.get_text(strip=True).lower() == "dados abertos":
                href = anchor["href"].strip()
                full_url = urljoin(page_url, href)
                title = "Dados Abertos ABDI"

                for parent in anchor.parents:
                    heading = parent.find(
                        class_=[
                            "elementor-heading-title",
                            "elementor-widget-heading",
                        ]
                    )
                    if heading:
                        title = " ".join(heading.get_text().split()).rstrip(".")
                        break

                ext = (
                    os.path.splitext(urlparse(full_url).path)[1]
                    .lower()
                    .replace(".", "")
                )
                items.append(
                    {
                        "source": self.name,
                        "section": section,
                        "title": title,
                        "download_url": full_url,
                        "file_type": ext if ext else "csv",
                    }
                )
        return items

    def _extract_pdf_documents(
        self,
        soup: BeautifulSoup,
        page_url: str,
        section: str,
        seen_urls: Set[str] = None,
    ) -> List[Dict[str, str]]:
        """Extrai links de PDFs e botões 'Visualizar', descartando links de termos e cookies."""
        items = []
        if seen_urls is None:
            seen_urls = set()

        # Blacklist rigorosa de links utilitários que não são documentos de auditoria
        ignorar = [
            "cookie",
            "politica-de-privacidade",
            "termos-de-uso",
            "opt-out",
            "adopt",
            "javascript:",
            "#",
            "whatsapp",
            "facebook",
            "instagram",
            "linkedin",
        ]

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or any(ig in href.lower() for ig in ignorar):
=======
        registros: list[dict[str, str]] = []
        falhas: list[str] = []
        vistos: set[str] = set()

        for habilitada, rotulo, extrator in secoes:
            if not habilitada:
>>>>>>> main
                continue

            try:
                encontrados = extrator()
            except Exception as e:
                falhas.append(f"{rotulo}: {e}")
                print(f"      ⚠️ Falha ao varrer '{rotulo}': {e}")
                continue

<<<<<<< HEAD
            is_pdf = ".pdf" in href_lower or "jet_download=" in href_lower or "/download/" in href_lower
=======
            novos = [r for r in encontrados if r["download_url"] not in vistos]
            vistos.update(r["download_url"] for r in novos)
            for registro in novos:
                registro["section"] = rotulo
            registros.extend(novos)
            print(f"      📑 {rotulo}: {len(novos)} link(s) de download.")
>>>>>>> main

        if not registros and falhas:
            raise RuntimeError(
                "Nenhuma seção da ABDI pôde ser lida. Detalhes: " + " | ".join(falhas)
            )

<<<<<<< HEAD
                title = "Documento PDF"
                container = (
                    anchor.find_parent("div", class_=lambda c: c and ("elementor-toggle-item" in c or "jet-listing" in c or "elementor-widget" in c or "e-con" in c))
                    or anchor.find_parent(["li", "tr", "div", "p"])
=======
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
>>>>>>> main
                )
                if heading:
                    titulo = _normaliza(heading)
                    break

<<<<<<< HEAD
                if container:
                    # Busca o cabeçalho do acordeão / processo
                    heading = container.find(
                        ["h1", "h2", "h3", "h4", "h5", "a", "div"],
                        class_=lambda c: c and ("title" in c or "heading" in c or "tab-title" in c),
                    )
                    if heading and len(heading.get_text(strip=True)) > 5:
                        raw_text = heading.get_text(separator=" ", strip=True)
                    else:
                        raw_text = container.get_text(separator=" ", strip=True)

                    clean_title = (
                        raw_text.replace("Visualizar", "")
                        .replace("Baixar", "")
                        .replace("Em andamento", "")
                        .replace("Concluído", "")
                        .strip()
=======
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
            resposta = self.session.post(
                url_ajax,
                data=payload,
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=30,
            )
            resposta.raise_for_status()

            trecho = resposta.json().get("data", {}).get("html", "")
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
>>>>>>> main
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
