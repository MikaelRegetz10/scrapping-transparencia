# scrapers/abdi.py
import os
import re
from typing import Dict, List, Set
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import requests

from core.validator import DEFAULT_HEADERS
from scrapers.base import BaseScraper


class ABDIScraper(BaseScraper):

    def __init__(self, ano: int = 2026):
        routes = {
            "Dados Abertos": "https://www.abdi.com.br/transparencia/dados-abertos/",
            "Aquisição de Bens e Serviços": "https://www.abdi.com.br/transparencia/aquisicao-de-bens-e-servicos/",
            "Processo Seletivo": "https://www.abdi.com.br/transparencia/processo-seletivo/",
        }
        super().__init__(name="ABDI", routes=routes)

    def extract_links(self) -> List[Dict[str, str]]:
        extracted_data = []

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
                continue

            full_url = urljoin(page_url, href)
            href_lower = full_url.lower()

            is_pdf = ".pdf" in href_lower or "jet_download=" in href_lower or "/download/" in href_lower

            if is_pdf and full_url not in seen_urls:
                seen_urls.add(full_url)

                title = "Documento PDF"
                container = (
                    anchor.find_parent("div", class_=lambda c: c and ("elementor-toggle-item" in c or "jet-listing" in c or "elementor-widget" in c or "e-con" in c))
                    or anchor.find_parent(["li", "tr", "div", "p"])
                )

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
                    )
                    clean_title = " ".join(clean_title.split())
                    if len(clean_title) > 5:
                        title = clean_title[:150]

                items.append(
                    {
                        "source": self.name,
                        "section": section,
                        "title": title,
                        "download_url": full_url,
                        "file_type": "pdf",
                    }
                )

        return items