import logging
import os
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from core.validator import DEFAULT_HEADERS
from scrapers.base import BaseScraper

logger = logging.getLogger("scrapers.senac")

BASE_SENAC_URL = "https://transparencia.senac.br"


class SenacScraper(BaseScraper):
    """Scraper automatizado para o SENAC focado exclusivamente em planilhas/dados (filtrando PDFs)."""

    UFS = [
        "dn", "ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma",
        "mg", "ms", "mt", "pa", "pb", "pe", "pi", "pr", "rj", "rn",
        "ro", "rr", "rs", "sc", "se", "sp", "to"
    ]

    SECOES = [
        "gestao-de-pessoas",
        "execucao-orcamentaria",
        "demonstracoes-contabeis",
        "licitacoes",
        "contratos-parcerias-convenios-patrocinios",
        "transferencias-regulamentares",
        "dados-de-producao",
        "programa-senac-de-gratuidade",
        "controle-interno-e-externo",
        "publicacoes"
    ]

    ANOS_PADRAO = [2024, 2025, 2026]

    def __init__(
            self,
            anos: Optional[List[int]] = None,
            ufs: Optional[List[str]] = None,
            delay_requisicao: float = 0.2,
    ):
        super().__init__(
            name="SENAC",
            base_url=BASE_SENAC_URL,
            routes={
                "Transparência": BASE_SENAC_URL
            },
        )
        self.anos = anos or self.ANOS_PADRAO
        self.ufs = ufs or self.UFS
        self.delay_requisicao = delay_requisicao

    def _tipo_arquivo(self, url: str) -> str:
        """Determina a extensão do arquivo a partir da URL ou rota de API."""
        path = urlparse(url).path.lower()
        ext = os.path.splitext(path)[1].lstrip(".")
        if ext in ["csv", "xlsx", "xls", "json", "ods", "zip"]:
            return ext
        if "download" in url.lower() or "service/api" in url.lower():
            return "csv"
        return "desconhecido"

    def _limpar_texto(self, texto: str) -> str:
        """Remoção de quebras de linha e espaços duplos."""
        return " ".join((texto or "").split()).strip()

    def extract_links(self) -> List[Dict[str, str]]:
        registros: List[Dict[str, str]] = []
        vistos: set = set()

        logger.info(
            "🚀 Iniciando Playwright Otimizado para SENAC (Excluindo PDFs): %d UFs x %d Seções x %d Anos...",
            len(self.ufs),
            len(self.SECOES),
            len(self.anos)
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 800}
            )

            # Aborta carregamento de mídia, fontes e CSS para maior velocidade
            context.route(
                "**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,ttf,eot}",
                lambda route: route.abort()
            )

            page = context.new_page()

            try:
                for uf in self.ufs:
                    uf_hive = "DN" if uf.lower() == "dn" else uf.upper()

                    for secao in self.SECOES:
                        url_rota = f"{BASE_SENAC_URL}/#/{uf.lower()}/{secao}"

                        try:
                            page.goto(url_rota, wait_until="domcontentloaded", timeout=20000)
                            page.wait_for_timeout(800)

                            select_ano = page.locator("select").first
                            anos_disponiveis = [str(a) for a in self.anos]

                            for ano in anos_disponiveis:
                                try:
                                    if select_ano.is_visible():
                                        select_ano.select_option(value=str(ano), force=True)
                                        page.wait_for_timeout(500)
                                except Exception:
                                    pass

                                # Simula clique em sub-abas
                                sub_abas = page.locator(".nav-tabs a, ul.tabs a, .btn-group button").all()
                                if sub_abas:
                                    for aba in sub_abas[:8]:
                                        try:
                                            if aba.is_visible():
                                                aba.click(force=True)
                                                page.wait_for_timeout(300)
                                        except Exception:
                                            continue

                                html_content = page.content()
                                soup = BeautifulSoup(html_content, "html.parser")

                                for link in soup.find_all("a", href=True):
                                    href = link["href"].strip()
                                    if not href or href.startswith("#") or href.startswith("javascript:"):
                                        continue

                                    href_lower = href.lower()
                                    texto_link = self._limpar_texto(link.get_text())
                                    title_attr = self._limpar_texto(link.get("title", ""))
                                    aria_label = self._limpar_texto(link.get("aria-label", ""))

                                    # 🛑 FILTRO ANTI-PDF (4 CAMADAS)
                                    # 1. Extensão explícita no link
                                    if ".pdf" in href_lower:
                                        continue

                                    # 2. Endpoints de API que entregam relatórios em PDF
                                    if "/download-arquivo/" in href_lower:
                                        continue

                                    # 3. Metadados do DOM contendo 'pdf'
                                    contexto_dom = f"{texto_link} {title_attr} {aria_label}".lower()
                                    if "pdf" in contexto_dom:
                                        continue

                                    # Valida se é link de download de planilha/dados
                                    eh_download = (
                                        "service/api/download" in href_lower
                                        or "download" in href_lower
                                        or any(ext in href_lower for ext in [".csv", ".xlsx", ".xls", ".ods", ".zip"])
                                    )

                                    if not eh_download:
                                        continue

                                    full_url = urljoin(BASE_SENAC_URL, href)

                                    chave_unica = (uf_hive, ano, full_url)
                                    if chave_unica in vistos:
                                        continue
                                    vistos.add(chave_unica)

                                    titulo_h1 = soup.find(["h1", "h2", "h3"])
                                    titulo_secao = self._limpar_texto(
                                        titulo_h1.get_text()) if titulo_h1 else secao.replace("-", " ").title()

                                    nome_arquivo = os.path.basename(urlparse(full_url).path)
                                    if not texto_link or "download" in texto_link.lower():
                                        texto_link = nome_arquivo or f"{titulo_secao} - Arquivo"

                                    registros.append({
                                        "source": self.name,
                                        "entidade": f"SENAC-{uf_hive}",
                                        "section": titulo_secao,
                                        "title": f"{titulo_secao} - {texto_link}",
                                        "context": f"SENAC {uf_hive} | Exercício {ano} | {titulo_secao}",
                                        "download_url": full_url,
                                        "file_name": nome_arquivo,
                                        "file_type": self._tipo_arquivo(full_url),
                                        "published_at": "",
                                        "tcu_uf": uf_hive,
                                        "tcu_ano": int(ano),
                                        "tcu_tema": "senac_transparencia",
                                        "tcu_tipo_documento": secao.replace("-", "_"),
                                    })

                        except Exception as e:
                            logger.warning("⚠️ Falha ao acessar rota %s (%s): %s", url_rota, uf, e)

                        if self.delay_requisicao > 0:
                            time.sleep(self.delay_requisicao)

            except Exception as e:
                logger.error("❌ Erro crítico no Playwright (SENAC): %s", e)
            finally:
                browser.close()

        logger.info("Varredura do SENAC concluída. Total de planilhas/tabelas capturadas: %d", len(registros))
        return registros