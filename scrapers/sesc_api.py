# scrapers/sesc_api.py
import logging
import time
from typing import Dict, List, Optional
import urllib3
import requests

from core.validator import DEFAULT_HEADERS
from scrapers.base import BaseScraper

logger = logging.getLogger("scrapers.sesc_api")

# Desabilita avisos de certificado SSL para domínios corporativos regionais
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class SescApiScraper(BaseScraper):

    # Mapeamento descritivo dos códigos de API do SESC
    MAPA_CODIGOS_SESC = {
        199: "Despesas por Categoria",
        200: "Plano de Contas / Demonstrativo",
        201: "Execução Orçamentária",
        202: "Receita por Programa e Atividade",
    }

    UFS = [
        "ac", "al", "am", "ap", "ba", "ce", "df", "es", "go",
        "ma", "mg", "ms", "mt", "pa", "pb", "pe", "pi", "pr",
        "rj", "rn", "ro", "rr", "rs", "sc", "se", "sp", "to",
    ]

    def __init__(self, ano: Optional[int] = 2026):
        self.ano_alvo = ano
        super().__init__(
            name="SESC_API",
            base_url="https://transparencia-{uf}.sesc.com.br/transparencia/dados/api/",
        )

    def extract_links(self) -> List[Dict[str, str]]:
        """Testa e varre os endpoints da API REST do SESC para todas as UFs e

        códigos {199, 200, 201, 202}.
        """
        extracted_data = []

        logger.info(
            "Iniciando varredura da API REST do SESC para %d UFs e %d códigos...",
            len(self.UFS),
            len(self.MAPA_CODIGOS_SESC),
        )

        for uf in self.UFS:
            for cod, descricao_cod in self.MAPA_CODIGOS_SESC.items():
                # Monta a URL da API com page_size=1000 para otimizar requisições
                api_url = (
                    f"https://transparencia-{uf}.sesc.com.br/transparencia/dados/api/{cod}"
                    f"?page=1&page_size=1000"
                )

                try:
                    res = requests.get(
                        api_url,
                        headers=DEFAULT_HEADERS,
                        timeout=10,
                        verify=False,
                    )

                    if res.status_code == 200:
                        data = res.json()
                        total_registros = data.get("registro_total", 0)

                        if total_registros > 0:
                            title = f"SESC {uf.upper()} - {descricao_cod} (Cód {cod})"

                            extracted_data.append({
                                "source": self.name,
                                "section": f"API Cód {cod}",
                                "title": title,
                                "download_url": api_url,
                                "file_type": "json",
                                "tcu_entidade": "SESC",
                                "tcu_uf": uf.upper(),
                                "tcu_ano": self.ano_alvo or 2026,
                            })

                            logger.info(
                                "✅ [SESC-%s] Cód %d: Encontrados %d registros.",
                                uf.upper(),
                                cod,
                                total_registros,
                            )
                        else:
                            logger.debug(
                                "ℹ️ [SESC-%s] Cód %d: Retornou 0 registros.",
                                uf.upper(),
                                cod,
                            )

                    elif res.status_code == 404:
                        logger.debug(
                            "⚠️ [SESC-%s] Cód %d: Endpoint não encontrado (404).",
                            uf.upper(),
                            cod,
                        )
                    else:
                        logger.warning(
                            "❌ [SESC-%s] Cód %d: Status HTTP %d",
                            uf.upper(),
                            cod,
                            res.status_code,
                        )

                except requests.exceptions.RequestException as e:
                    logger.debug(
                        "Falha de conexão com a API SESC-%s (Cód %d): %s",
                        uf.upper(),
                        cod,
                        e,
                    )

                time.sleep(0.1)  # Delay leve para evitar bloqueio de taxa de requisições

        logger.info(
            "Varredura da API SESC concluída. Total de endpoints válidos capturados: %d",
            len(extracted_data),
        )
        return extracted_data