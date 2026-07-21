from abc import ABC, abstractmethod
from typing import Dict, List


class BaseScraper(ABC):
    """Classe base abstrata para todos os scrapers de transparência."""

    def __init__(self, name: str, base_url: str):
        self.name = name
        self.base_url = base_url

    @abstractmethod
    def extract_links(self) -> List[Dict[str, str]]:
        """Extrai os links da página de transparência.

        Deve retornar uma lista de dicionários no formato:
        [
            {
                "source": "Nome do Portal",
                "title": "Título/Descrição do link",
                "context": "Texto contextual ao redor",
                "download_url": "https://...",
                "file_type": "pdf | csv | xlsx | link/página"
            },
            ...
        ]
        """
        pass