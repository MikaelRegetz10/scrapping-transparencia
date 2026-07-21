import os
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import requests

from core.validator import DEFAULT_HEADERS
from scrapers.base import BaseScraper


class ABDIScraper(BaseScraper):

    def __init__(self):
        super().__init__(
            name="ABDI",
            base_url="https://www.abdi.com.br/transparencia/dados-abertos/",
        )

    def extract_links(self) -> list[dict[str, str]]:
        response = requests.get(
            self.base_url, headers=DEFAULT_HEADERS, timeout=15
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        extracted_data = []

        for anchor in soup.find_all("a", href=True):
            anchor_text = anchor.get_text(strip=True)

            # 1. Filtra estritamente os botões "Dados Abertos"
            if anchor_text.lower() != "dados abertos":
                continue

            href = anchor["href"].strip()
            full_url = urljoin(self.base_url, href)

            dataset_title = "Título não identificado"

            # 2. Sobe a árvore DOM procurando o container da linha que possui o título (elementor-heading-title)
            for parent in anchor.parents:
                heading_el = parent.find(class_=["elementor-heading-title", "elementor-widget-heading"])

                if heading_el:
                    # Extrai o texto do h2/link do título
                    raw_title = heading_el.get_text(strip=True)

                    # Limpa espaços duplicados e pontos no final
                    dataset_title = " ".join(raw_title.split()).rstrip(".")
                    break  # Encontrou o título da linha atual, interrompe a busca para não subir até o topo da página

            # 3. Identifica extensão do arquivo (.csv, .xlsx, etc)
            path = urlparse(full_url).path
            ext = os.path.splitext(path)[1].lower().replace(".", "")

            extracted_data.append(
                {
                    "source": self.name,
                    "title": dataset_title,
                    "context": "Botão Dados Abertos",
                    "download_url": full_url,
                    "file_type": ext if ext else "desconhecido",
                }
            )

        return extracted_data