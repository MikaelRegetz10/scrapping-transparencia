import time
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlencode

from scrapers.base import BaseScraper

BASE_SENAC_URL = "https://transparencia.senac.br"
DOWNLOAD_API_URL = f"{BASE_SENAC_URL}/service/api/download"


class SenacScraper(BaseScraper):
    """Scraper para o Portal de Transparência do SENAC.

    Gera os links da API oficial de download para todas as UFs + DN,
    Anos (2020 a 2026) e as 3 tabelas de Execução Orçamentária.
    """

    UFS = [
        "ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma",
        "mg", "ms", "mt", "pa", "pb", "pe", "pi", "pr", "rj", "rn",
        "ro", "rr", "rs", "sc", "se", "sp", "to", "dn",
    ]

    ANOS_PADRAO = [2020, 2021, 2022, 2023, 2024, 2025, 2026]

    LAYOUTS = [
        {
            "id": "execucaoorcamentariareceitacategoria",
            "nome": "Receita por categoria econômica/fonte",
        },
        {
            "id": "execucaoorcamentariadespesacategoria",
            "nome": "Despesa por categoria econômica/elemento",
        },
        {
            "id": "execucaoorcamentariadespesafinalidade",
            "nome": "Despesa por finalidade/programa de trabalho",
        },
    ]

    def __init__(
        self,
        anos: Optional[List[int]] = None,
        ufs: Optional[List[str]] = None,
        delay_requisicao: float = 0.05,
    ):
        super().__init__(
            name="SENAC",
            base_url=BASE_SENAC_URL,
            routes={
                "Execução Orçamentária": urljoin(
                    BASE_SENAC_URL, "/#/execucao-orcamentaria"
                )
            },
        )
        self.anos = anos or self.ANOS_PADRAO
        self.ufs = ufs or self.UFS
        self.delay_requisicao = delay_requisicao

    # ------------------------------------------------------------------
    # Contrato do BaseScraper
    # ------------------------------------------------------------------
    def extract_links(self) -> List[Dict[str, str]]:
        """Gera as rotas de download oficiais para auditoria do pipeline."""
        registros: List[Dict[str, str]] = []
        total_estimado = len(self.ufs) * len(self.anos) * len(self.LAYOUTS)

        print(
            f"      🚀 [SENAC] Gerando links de Execução Orçamentária "
            f"({len(self.ufs)} UFs x {len(self.anos)} Anos x {len(self.LAYOUTS)} Tabelas = {total_estimado} rotas)..."
        )

        for uf in self.ufs:
            uf_upper = uf.upper()
            uf_lower = uf.lower()

            for ano in self.anos:
                for layout in self.LAYOUTS:
                    layout_id = layout["id"]
                    layout_nome = layout["nome"]

                    # Parâmetros exatos extraídos do frontend Angular do Senac
                    params = {
                        "regional": uf_lower,
                        "secao": "execucaoorcamentaria",
                        "ano": ano,
                        "layout": layout_id,
                    }
                    download_url = f"{DOWNLOAD_API_URL}?{urlencode(params)}"

                    titulo = f"{layout_nome} - {uf_upper} ({ano})"
                    nome_arquivo = f"execucao_orcamentaria_{uf_lower}_{ano}_{layout_id}.csv"
                    contexto = f"SENAC {uf_upper} | Exercício {ano} | Execução Orçamentária"

                    registro = {
                        "source": self.name,
                        "entidade": f"SENAC-{uf_upper}",
                        "section": "Execução Orçamentária",
                        "title": titulo,
                        "context": contexto,
                        "download_url": download_url,
                        "file_name": nome_arquivo,
                        "file_type": "csv",  # Corrigido para CSV
                        "published_at": "",
                        # Metadados Hive/Parquet
                        "tcu_uf": uf_upper,
                        "tcu_ano": int(ano),
                        "tcu_tema": "execucao_orcamentaria",
                        "tcu_tipo_documento": "execucao_orcamentaria",
                    }

                    registros.append(registro)

                    if self.delay_requisicao > 0:
                        time.sleep(self.delay_requisicao)

        print(f"      📑 [SENAC] {len(registros)} link(s) gerados para auditoria.")
        return registros