# scrapers/sesi.py
from datetime import datetime
from typing import Dict, List
from urllib.parse import urlencode

from scrapers.base import BaseScraper


class SesiScraper(BaseScraper):

    def __init__(self, entidade: str = "SESI", regional: str = "DN", ano: int = 2026):
        super().__init__(
            name="SESI",
            base_url="https://sorsdn.sistemaindustria.com.br",
        )
        self.entidade = entidade
        self.regional = regional
        self.ano = ano

    def extract_links(self) -> List[Dict[str, str]]:
        """Mapeia os endpoints REST da API do SESI (Sistema Indústria) conforme a

        documentação Swagger.
        """
        # Endpoints capturados das documentações do Swagger
        endpoints = [
            {
                "title": f"Execução Orçamentária ({self.entidade} - {self.regional} - {self.ano})",
                "path": "/api/Transparencia/Get",
                "params": {
                    "entidade": self.entidade,
                    "regional": self.regional,
                    "ano": self.ano,
                },
            },
            {
                "title": f"Saldo Exercício Anterior ({self.entidade} - {self.regional} - {self.ano})",
                "path": "/api/Transparencia/GetSaldoExercicioAnterior",
                "params": {
                    "entidade": self.entidade,
                    "regional": self.regional,
                    "ano": self.ano,
                },
            },
            {
                "title": f"Previsão Orçamentária ({self.entidade} - {self.ano})",
                "path": "/api/Transparencia/GetPrevisaoOrcamentaria",
                "params": {
                    "entidade": self.entidade,
                    "ano": self.ano,
                },
            },
            {
                "title": f"Rateio de Despesas ({self.entidade} - {self.regional} - {self.ano})",
                "path": "/api/Transparencia/GetRateio",
                "params": {
                    "entidade": self.entidade,
                    "regional": self.regional,
                    "ano": self.ano,
                },
            },
            {
                "title": f"Despesas por Licitação ({self.entidade} - {self.regional} - {self.ano})",
                "path": "/api/Transparencia/GetLicitacao",
                "params": {
                    "entidade": self.entidade,
                    "regional": self.regional,
                    "ano": self.ano,
                },
            },
            {
                "title": f"Bens Imóveis ({self.entidade} - {self.regional} - {self.ano})",
                "path": "/api/Transparencia/GetBensImoveis",
                "params": {
                    "entidade": self.entidade,
                    "regional": self.regional,
                    "ano": self.ano,
                },
            },
        ]

        extracted_data = []

        for ep in endpoints:
            base_endpoint_url = f"{self.base_url}{ep['path']}"
            query_string = urlencode(ep["params"])
            full_url = f"{base_endpoint_url}?{query_string}"

            extracted_data.append(
                {
                    "source": self.name,
                    "title": ep["title"],
                    "context": "API REST Sistema Indústria",
                    "download_url": full_url,
                    "file_type": "json",
                }
            )

        return extracted_data