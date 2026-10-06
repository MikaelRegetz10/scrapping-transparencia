import logging
import time
import os
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from core.validator import DEFAULT_HEADERS
from scrapers.base import BaseScraper

# Inicializa o logger específico para este scraper
logger = logging.getLogger("scrapers.sescoop")

BASE_SESCOOP_URL = "https://somoscooperativismo.coop.br"


class SescoopScraper(BaseScraper):
    """Scraper para o Portal de Transparência do SESCOOP usando Playwright."""

    UFS = [
        "nacionais", "consolidados",
        "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA",
        "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN",
        "RO", "RR", "RS", "SC", "SE", "SP", "TO"
    ]

    ANOS_PADRAO = [2024, 2025, 2026]

    def __init__(
            self,
            anos: Optional[List[int]] = None,
            ufs: Optional[List[str]] = None,
            delay_requisicao: float = 0.5,
    ):
        super().__init__(
            name="SESCOOP",
            base_url=BASE_SESCOOP_URL,
            routes={
                "Transparência": urljoin(
                    BASE_SESCOOP_URL, "/institucional/sescoop/transparencia-e-prestacao-de-contas"
                )
            },
        )
        self.anos = anos or self.ANOS_PADRAO
        self.ufs = ufs or self.UFS
        self.delay_requisicao = delay_requisicao

    def _tipo_arquivo(self, url: str) -> str:
        path = urlparse(url).path.lower()
        ext = os.path.splitext(path)[1].lstrip(".")
        return ext if ext else "desconhecido"

    def _eh_alvo(self, titulo_subgrupo: str) -> bool:
        """Filtra rigorosamente com base nas numerações exatas dos grupos solicitados."""
        t = titulo_subgrupo.strip()

        alvos_exatos = ["3.1", "3.2", "4.2", "6.1", "6.2", "7.2", "8.1", "8.2", "8.4", "8.5", "9.1"]
        for alvo in alvos_exatos:
            if t.startswith(f"{alvo}.") or t.startswith(f"{alvo} "):
                return True

        if t.startswith("5.") or t.startswith("5 "):
            return True

        return False

    def _inferir_documento_pelo_titulo(self, titulo: str) -> str:
        """Gera a taxonomia padrão para o diretório Hive baseado no número."""
        t = titulo.strip()
        if t.startswith("3."): return "pessoal"
        if t.startswith("4."): return "planejamento"
        if t.startswith("5."): return "orcamento"
        if t.startswith("6."): return "financeiro"
        if t.startswith("7."): return "contabil"
        if t.startswith("8."): return "fornecedores"
        if t.startswith("9."): return "transferencias"
        return "outros"

    def extract_links(self) -> List[Dict[str, str]]:
        registros: List[Dict[str, str]] = []
        vistos: set = set()

        logger.info(
            "🚀 Iniciando Playwright (Navegador Oculto) para SESCOOP: %d UFs x %d Anos...",
            len(self.ufs),
            len(self.anos)
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()

            try:
                logger.info("🌐 Carregando a página base do portal SESCOOP...")
                page.goto(self.routes["Transparência"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                for uf in self.ufs:
                    uf_hive = "DN" if uf in ["nacionais", "consolidados"] else uf.upper()

                    try:
                        page.locator("select[name='uf']").select_option(value=uf, force=True)
                        page.wait_for_timeout(1500)
                    except Exception as e:
                        logger.warning("⚠️ Falha ao selecionar UF %s: %s", uf, e)
                        continue

                    for ano in self.anos:
                        try:
                            page.locator("select[name='ano']").select_option(value=str(ano), force=True)
                            page.wait_for_timeout(3500)
                        except Exception as e:
                            logger.warning("⚠️ Falha ao selecionar ano %s na UF %s: %s", ano, uf, e)
                            continue

                        html_content = page.content()
                        soup = BeautifulSoup(html_content, "html.parser")

                        blocos_subgrupo = soup.find_all("div", class_="dados-detalhes-unidade-transparencia")
                        contador_links_uf_ano = 0

                        for bloco in blocos_subgrupo:
                            titulo_div = bloco.find("div", class_="titulo-subgrupo-unidade-transparencia")
                            if not titulo_div:
                                continue

                            titulo_subgrupo = titulo_div.get_text(strip=True)

                            if not self._eh_alvo(titulo_subgrupo):
                                continue

                            caixas_arquivo = bloco.find_all("div", class_="box-arquivos-transparencia")

                            for caixa in caixas_arquivo:
                                link_tag = caixa.find("a", href=True)
                                if not link_tag:
                                    continue

                                href = link_tag["href"].strip()
                                if not href or href.startswith("#") or href.startswith("javascript:"):
                                    continue

                                if not any(ext in href.lower() for ext in [".csv", ".xlsx", ".xls", ".pdf", ".ods"]):
                                    continue

                                full_url = urljoin(BASE_SESCOOP_URL, href)

                                chave_unica = (uf_hive, ano, full_url)
                                if chave_unica in vistos:
                                    continue
                                vistos.add(chave_unica)

                                texto_link = link_tag.get_text(strip=True)
                                nome_arquivo = os.path.basename(urlparse(full_url).path)

                                if not texto_link or "download" in texto_link.lower():
                                    texto_link = nome_arquivo or "Arquivo de Dados"

                                registros.append({
                                    "source": self.name,
                                    "entidade": f"SESCOOP-{uf_hive}",
                                    "section": titulo_subgrupo,
                                    "title": f"{titulo_subgrupo} - {texto_link}",
                                    "context": f"SESCOOP {uf_hive} | Exercício {ano} | {titulo_subgrupo}",
                                    "download_url": full_url,
                                    "file_name": nome_arquivo,
                                    "file_type": self._tipo_arquivo(full_url),
                                    "published_at": "",
                                    "tcu_uf": uf_hive,
                                    "tcu_ano": int(ano),
                                    "tcu_tema": "sescoop_transparencia",
                                    "tcu_tipo_documento": self._inferir_documento_pelo_titulo(titulo_subgrupo),
                                })
                                contador_links_uf_ano += 1

                        if contador_links_uf_ano > 0:
                            logger.info("✅ [SESCOOP-%s] Exercício %s: %d links capturados.", uf.upper(), ano,
                                        contador_links_uf_ano)
                        else:
                            logger.debug("ℹ️ [SESCOOP-%s] Exercício %s: 0 links encontrados.", uf.upper(), ano)

            except Exception as e:
                logger.error("❌ Erro crítico no Playwright (SESCOOP): %s", e)
            finally:
                browser.close()

        logger.info(
            "Varredura do SESCOOP concluída. Total de links capturados: %d",
            len(registros)
        )
        return registros