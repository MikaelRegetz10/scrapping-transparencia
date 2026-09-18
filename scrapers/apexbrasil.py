import re
from typing import Dict, List, Optional
from urllib.parse import unquote, urljoin
import requests
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper


class ApexBrasilScraper(BaseScraper):
    """Scraper para os dados de Licitações e Contratos da ApexBrasil (Portal AEM)."""

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    def __init__(self, ano: Optional[int] = None):
        base_url = "https://apexbrasil.com.br"
        main_route = (
            f"{base_url}/content/apexbrasil/br/pt/transparencia-e-prestacao-de-contas/"
            "licitacoes-e-contratos.html"
        )

        routes = {
            "Licitações e Contratos": main_route,
        }

        super().__init__(
            name="ApexBrasil",
            base_url=base_url,
            routes=routes,
        )
        self.ano = ano

    def extract_links(self) -> List[Dict[str, str]]:
        """Raspa a página de Licitações da ApexBrasil e retorna todos os links

        de planilhas (.ods, .xlsx, .csv) e documentos (.pdf).
        """
        extracted_data: List[Dict[str, str]] = []

        for section_name, route_url in self.routes.items():
            try:
                res = requests.get(route_url, headers=self.DEFAULT_HEADERS, timeout=25)
                if res.status_code != 200:
                    continue

                soup = BeautifulSoup(res.content, "html.parser")

                # Localiza todos os links <a> na página
                for a_tag in soup.find_all("a", href=True):
                    href = a_tag["href"].strip()
                    if not href:
                        continue

                    # Ignora links de navegação interna e javascript
                    clean_href = href.split("?")[0].split("#")[0]
                    file_ext = (
                        clean_href.split(".")[-1].lower() if "." in clean_href else ""
                    )

                    # Filtra extensões de arquivos relevantes
                    if file_ext in ["ods", "xlsx", "xls", "csv", "pdf", "zip"]:
                        full_url = urljoin(self.base_url, href)

                        # Título visível do link
                        title = a_tag.get_text(strip=True)

                        # Tratamento para rótulos genéricos ("Baixar", "Download")
                        if not title or title.lower() in [
                            "baixar",
                            "download",
                            "clique aqui",
                            "acesse",
                        ]:
                            parent_elem = a_tag.find_parent(["p", "div", "li"])
                            if parent_elem:
                                title = parent_elem.get_text(strip=True)

                        # Extrai o nome do arquivo decodificando caracteres de URL (%C3%A7 -> ç)
                        raw_file_name = clean_href.split("/")[-1]
                        file_name = unquote(raw_file_name)

                        # Identifica subseções do DOM (abas ou accordions AEM se houver)
                        sub_section = section_name
                        accordion_item = a_tag.find_parent(
                            class_=re.compile(r"cmp-accordion|cmp-tabs|cmp-text")
                        )
                        if accordion_item:
                            header = accordion_item.find_previous(
                                ["h2", "h3", "h4", "button"]
                            )
                            if header:
                                sub_section = (
                                    f"{section_name} | {header.get_text(strip=True)}"
                                )

                        # Tenta identificar data no formato DD.MM.AAAA ou DD/MM/AAAA no título
                        date_match = re.search(
                            r"\b(\d{2}[\.\/]\d{2}[\.\/]\d{4})\b", title
                        )
                        published_at = date_match.group(1) if date_match else ""

                        extracted_data.append(
                            {
                                "source": self.name,
                                "section": sub_section,
                                "title": title or file_name,
                                "context": f"ApexBrasil | {sub_section}",
                                "download_url": full_url,
                                "file_name": file_name,
                                "file_type": file_ext,
                                "published_at": published_at,
                                # Metadados adicionais suportados pelo pipeline do projeto
                                "tcu_tema": "Licitações e Contratos",
                                "tcu_tipo_documento": (
                                    "Planilha de Licitações"
                                    if file_ext in ["ods", "xlsx", "csv"]
                                    else "Documento"
                                ),
                                "tcu_ano": self.ano or 2026,
                                "tcu_uf": "DF",  # ApexBrasil Sede / Distrito Federal
                            }
                        )

            except Exception as e:
                # O pipeline pai lidará com os logs e falhas
                continue

        return extracted_data