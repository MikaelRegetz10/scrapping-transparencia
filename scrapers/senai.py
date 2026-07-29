# scrapers/senai.py
from datetime import datetime
from typing import Dict, List
from urllib.parse import urlencode

from scrapers.base import BaseScraper


class SenaiScraper(BaseScraper):

    def __init__(self, entidade: str = "SENAI", regional: str = "SENAI-DN"):
        super().__init__(
            name="SENAI",
            base_url="https://sorsdn.sistemaindustria.com.br",
        )
        self.entidade = entidade
        self.regional = regional
        self.base_transparencia_web = "https://sistematransparenciaweb.com.br"

    def extract_links(self) -> List[Dict[str, str]]:
        """Mapeia todos os endpoints REST do SENAI (Sistema Indústria +

        Transparência Web).
        """
        ano_atual = datetime.now().year
        extracted_data = []

        # =========================================================
        # 1. ENDPOINTS DO SISTEMA INDÚSTRIA (sorsdn.sistemaindustria.com.br)
        # =========================================================
        sistemaindustria_endpoints = [
            {
                "title": f"Execução Orçamentária ({self.entidade} - {self.regional} - {ano_atual})",
                "path": "/api/Transparencia/Get",
                "params": {
                    "entidade": self.entidade,
                    "regional": self.regional,
                    "ano": ano_atual,
                },
            },
            {
                "title": f"Saldo Exercício Anterior ({self.entidade} - {self.regional} - {ano_atual})",
                "path": "/api/Transparencia/GetSaldoExercicioAnterior",
                "params": {
                    "entidade": self.entidade,
                    "regional": self.regional,
                    "ano": ano_atual,
                },
            },
            {
                "title": f"Previsão Orçamentária ({self.entidade} - {ano_atual})",
                "path": "/api/Transparencia/GetPrevisaoOrcamentaria",
                "params": {
                    "entidade": self.entidade,
                    "ano": ano_atual,
                },
            },
            {
                "title": f"Rateio de Despesas ({self.entidade} - {self.regional} - {ano_atual})",
                "path": "/api/Transparencia/GetRateio",
                "params": {
                    "entidade": self.entidade,
                    "regional": self.regional,
                    "ano": ano_atual,
                },
            },
            {
                "title": f"Despesas por Licitação ({self.entidade} - {self.regional} - {ano_atual})",
                "path": "/api/Transparencia/GetLicitacao",
                "params": {
                    "entidade": self.entidade,
                    "regional": self.regional,
                    "ano": ano_atual,
                },
            },
            {
                "title": f"Bens Imóveis ({self.entidade} - {self.regional} - {ano_atual})",
                "path": "/api/Transparencia/GetBensImoveis",
                "params": {
                    "entidade": self.entidade,
                    "regional": self.regional,
                    "ano": ano_atual,
                },
            },
        ]

        for ep in sistemaindustria_endpoints:
            full_url = f"{self.base_url}{ep['path']}?{urlencode(ep['params'])}"
            extracted_data.append(
                {
                    "source": self.name,
                    "title": ep["title"],
                    "context": "API REST Sistema Indústria",
                    "download_url": full_url,
                    "file_type": "json",
                }
            )

        # =========================================================
        # 2. ENDPOINTS DO TRANSPARÊNCIA WEB (sistematransparenciaweb.com.br)
        # =========================================================

        # A) Relação de Dirigentes e Gestão
        dirigentes_url = (
            f"{self.base_transparencia_web}/api-relacaodirigentes-gestao"
            f"/publico/relacao-dirigente-empregado/entidades/{self.entidade}"
            f"/departamentos/{self.regional}/relacao-dirigente-empregado?ano={ano_atual}"
        )
        extracted_data.append(
            {
                "source": self.name,
                "title": f"Relação de Dirigentes e Gestão ({self.entidade} - {self.regional} - {ano_atual})",
                "context": "API REST Transparência Web",
                "download_url": dirigentes_url,
                "file_type": "json",
            }
        )

        # B) Composição do Conselho
        conselho_url = (
            f"{self.base_transparencia_web}/api-composicao-conselho"
            f"/publico/composicao-conselho/entidades/{self.entidade}"
            f"/departamentos/{self.regional}/composicao-conselho?ano={ano_atual}"
        )
        extracted_data.append(
            {
                "source": self.name,
                "title": f"Composição do Conselho ({self.entidade} - {self.regional} - {ano_atual})",
                "context": "API REST Transparência Web",
                "download_url": conselho_url,
                "file_type": "json",
            }
        )

        # C) Comissão de Contas e Orçamentos
        comissao_params = {
            "ano": ano_atual,
            "entidade": self.entidade,
            "departamento": self.regional,
        }
        comissao_url = (
            f"{self.base_transparencia_web}/api-comissao-contaorcamento"
            f"/api-comissao-contaorcamento?{urlencode(comissao_params)}"
        )
        extracted_data.append(
            {
                "source": self.name,
                "title": f"Comissão de Contas e Orçamentos ({self.entidade} - {self.regional} - {ano_atual})",
                "context": "API REST Transparência Web",
                "download_url": comissao_url,
                "file_type": "json",
            }
        )

        return extracted_data