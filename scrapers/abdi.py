# scrapers/abdi.py
import os
from typing import Dict, List
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests

from core.validator import DEFAULT_HEADERS
from scrapers.base import BaseScraper


class ABDIScraper(BaseScraper):

    def __init__(self):
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
                response = requests.get(
                    url, headers=DEFAULT_HEADERS, timeout=20
                )
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

                if section_name == "Dados Abertos":
                    extracted_data.extend(
                        self._extract_dados_abertos(soup, url, section_name)
                    )
                else:
                    extracted_data.extend(
                        self._extract_pdf_documents(soup, url, section_name)
                    )

            except Exception as e:
                print(f"⚠️ Erro ao acessar a rota [{section_name}] ({url}): {e}")

        return extracted_data

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
        self, soup: BeautifulSoup, page_url: str, section: str
    ) -> List[Dict[str, str]]:
        """Extrai links de PDFs e botões 'Visualizar' (JetDownload / Elementor)

        nas seções de Licitações e Processo Seletivo.
        """
        items = []
        seen_urls = set()

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            full_url = urljoin(page_url, href)
            href_lower = full_url.lower()

            # Identifica se o link é um PDF direto ou uma URL de jet_download da ABDI
            is_pdf = ".pdf" in href_lower or "jet_download=" in href_lower

            if is_pdf and full_url not in seen_urls:
                seen_urls.add(full_url)

                # Busca o contexto do título no container Elementor superior
                title = "Documento PDF"
                container = (
                    anchor.find_parent("div", class_=lambda c: c and ("elementor-widget" in c or "jet-" in c or "e-con" in c))
                    or anchor.find_parent(["li", "tr", "div", "p"])
                )

                if container:
                    raw_text = container.get_text(separator=" ", strip=True)
                    # Limpa palavras do botão visual da string do título
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