# scrapers/senar.py
import logging
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from scrapers.base import BaseScraper

logger = logging.getLogger("scrapers.senar")


class SenarScraper(BaseScraper):

    def __init__(self, ano: Optional[int] = 2026):
        self.ano_alvo = ano
        url = "https://app3.cna.org.br/transparencia/?dadosAbertos-SENAR"
        super().__init__(
            name="SENAR",
            base_url=url,
            routes={"Dados Abertos": url},
        )

    def extract_links(self) -> List[Dict[str, str]]:
        """Varre iterativamente todos os estados e seus respectivos períodos no

        portal do SENAR, tratando o recarregamento dinâmico dos dropdowns.
        """
        extracted_data = []
        seen_urls = set()

        logger.info(
            "Iniciando varredura no portal SENAR via Playwright (URL: %s)",
            self.base_url,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = browser.new_context(
                ignore_https_errors=True,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            )
            page = context.new_page()

            try:
                page.goto(
                    self.base_url, wait_until="networkidle", timeout=60000
                )
                time.sleep(2)

                select_uf = page.locator('select[name="UF_EMPRESA"]')

                if select_uf.count() == 0:
                    logger.warning(
                        "Não foi possível localizar o select 'UF_EMPRESA'."
                    )
                    return extracted_data

                # 1. Mapeia todas as opções de UF / Empresas ativas
                uf_options = select_uf.locator("option").all()
                empresas = []
                for opt in uf_options:
                    val = opt.get_attribute("value")
                    text = opt.inner_text().strip()
                    if val and text:
                        empresas.append({"value": val, "text": text})

                logger.info(
                    "Mapeadas %d empresas/regionais para varredura.",
                    len(empresas),
                )

                # 2. Iteração Mestre-Detalhe (UF_EMPRESA -> COD_PERIODO dinâmico)
                for emp in empresas:
                    uf_val = emp["value"]
                    emp_text = emp["text"]

                    try:
                        page.select_option(
                            'select[name="UF_EMPRESA"]', value=uf_val
                        )
                        # Aguarda as requisições assíncronas do portal concluírem o recarregamento do segundo select
                        page.wait_for_load_state("networkidle")
                        time.sleep(1.5)
                    except Exception as e_sel_uf:
                        logger.error(
                            "Falha ao selecionar UF_EMPRESA '%s': %s",
                            uf_val,
                            e_sel_uf,
                        )
                        continue

                    uf_code = uf_val if len(uf_val) == 2 else "DN"

                    # 💡 FIX CRÍTICO: Re-extrai as opções do select COD_PERIODO especificamente para esta UF
                    select_periodo = page.locator('select[name="COD_PERIODO"]')
                    if select_periodo.count() == 0:
                        logger.warning(
                            "Select 'COD_PERIODO' não encontrado para UF '%s'.",
                            uf_val,
                        )
                        continue

                    periodo_options = select_periodo.locator("option").all()
                    periodos_da_uf = []
                    for opt in periodo_options:
                        val = opt.get_attribute("value")
                        text = opt.inner_text().strip()
                        if val and text:
                            periodos_da_uf.append({"value": val, "text": text})

                    # Filtra pelos períodos do ano desejado (se configurado)
                    if self.ano_alvo:
                        periodos_filtrados = [
                            p
                            for p in periodos_da_uf
                            if str(self.ano_alvo) in p["text"]
                            or str(self.ano_alvo) in p["value"]
                        ]
                        if not periodos_filtrados:
                            periodos_filtrados = periodos_da_uf
                    else:
                        periodos_filtrados = periodos_da_uf

                    logger.info(
                        "Regional [%s] (%s): Mapeados %d exercícios.",
                        uf_code,
                        emp_text,
                        len(periodos_filtrados),
                    )

                    # 3. Iteração sobre cada período disponível para a UF atual
                    for per in periodos_filtrados:
                        per_val = per["value"]
                        per_text = per["text"]

                        try:
                            page.select_option(
                                'select[name="COD_PERIODO"]', value=per_val
                            )
                            page.wait_for_load_state("networkidle")
                            time.sleep(1)
                        except Exception as e_sel_per:
                            logger.error(
                                "Falha ao selecionar COD_PERIODO '%s' na UF '%s': %s",
                                per_val,
                                uf_val,
                                e_sel_per,
                            )
                            continue

                        # Extrai o Ano do texto (ex: "2026 :: PRIMEIRO TRIMESTRE" -> 2026)
                        ano_num = self.ano_alvo or 2026
                        for part in per_text.split():
                            if part.isdigit() and len(part) == 4:
                                ano_num = int(part)
                                break

                        # Extrai todos os links de download disponíveis na página atualizada
                        anchors = page.locator("a[href]").all()

                        for anchor in anchors:
                            try:
                                href = anchor.get_attribute("href")
                                if (
                                    not href
                                    or href.startswith("#")
                                    or href.startswith("javascript:")
                                ):
                                    continue

                                full_url = urljoin(self.base_url, href)
                                href_lower = full_url.lower()

                                is_download = any(
                                    k in href_lower
                                    for k in [
                                        "csv",
                                        "pdf",
                                        "xlsx",
                                        "xls",
                                        "gestaoorcamentaria",
                                    ]
                                )

                                if is_download and full_url not in seen_urls:
                                    seen_urls.add(full_url)

                                    link_text = anchor.inner_text().strip()
                                    if not link_text:
                                        try:
                                            parent = anchor.locator("xpath=..")
                                            link_text = (
                                                parent.inner_text().strip()
                                            )
                                        except Exception:
                                            link_text = "Dado Aberto SENAR"

                                    title = f"{link_text} ({emp_text} - {per_text})"

                                    file_type = "csv"
                                    if "pdf" in href_lower:
                                        file_type = "pdf"
                                    elif (
                                        "xlsx" in href_lower
                                        or "xls" in href_lower
                                    ):
                                        file_type = "xlsx"

                                    extracted_data.append(
                                        {
                                            "source": self.name,
                                            "section": f"{emp_text} - {per_text}",
                                            "title": title,
                                            "download_url": full_url,
                                            "file_type": file_type,
                                            "tcu_entidade": "SENAR",
                                            "tcu_uf": uf_code,
                                            "tcu_ano": ano_num,
                                        }
                                    )
                            except Exception as e_link:
                                logger.debug(
                                    "Erro ao processar link individual: %s",
                                    e_link,
                                )

            except Exception as e_main:
                logger.error(
                    "Erro durante a execução do scraper do SENAR: %s",
                    e_main,
                )

            finally:
                browser.close()

        logger.info(
            "Varredura no SENAR concluída com sucesso. Total de links extraídos: %d",
            len(extracted_data),
        )
        return extracted_data