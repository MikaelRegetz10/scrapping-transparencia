# scrapers/base.py
from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class BaseScraper(ABC):
    """Classe base abstrata com suporte a múltiplas rotas por scraper."""

    def __init__(
        self,
        name: str,
        base_url: Optional[str] = None,
        routes: Optional[Dict[str, str]] = None,
    ):
        self.name = name
        self.base_url = base_url or ""
        # Dicionário no formato: {"Nome da Seção": "https://url..."}
        self.routes = routes or (
            {"Principal": base_url} if base_url else {}
        )

    @abstractmethod
    def extract_links(self) -> List[Dict[str, str]]:
        """Deve retornar a lista de links extraídos de todas as rotas ativas.

        Retorno padrão de cada item:
        {
            "source": "ABDI",
            "section": "Aquisição de Bens e Serviços",
            "title": "Dispensa de Licitação nº 027/2026",
            "context": "Aquisição de Bens e Serviços | Situação: Concluída",
            "download_url": "https://...",
            "file_name": "dispensa-027-2026.pdf",   # opcional
            "file_type": "pdf | csv | xlsx | json",
            "published_at": "21/08/2024",            # opcional
        }

        `title` é o título do documento como o portal o publica — o texto ao
        lado do botão, não o rótulo do link ("Visualizar", "Download").
        """
        pass