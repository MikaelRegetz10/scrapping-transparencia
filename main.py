import sys

from core.config import Config, setup_logger
from core.pipeline import run_scraper_pipeline
from scrapers.abdi import ABDIScraper
from scrapers.abdi_pdf import ABDIPdfScraper
from scrapers.senai import SenaiScraper
from scrapers.senar import SenarScraper
from scrapers.sesc_api import SescApiScraper
from scrapers.sesi import SesiScraper
from scrapers.sesi_pdf import SesiPdfScraper
from scrapers.sesi_transparencia import SesiTransparenciaScraper


def configurar_saida_utf8() -> None:
    """Garante que os emojis dos logs não quebrem a execução.

    No Windows o stdout redirecionado (`python main.py > log.txt`) usa cp1252 e
    levanta UnicodeEncodeError no primeiro 🚀. Forçar UTF-8 resolve sem exigir
    variável de ambiente de cada pessoa do time.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main():
    configurar_saida_utf8()

    config = Config()
    logger = setup_logger(config)

    # Escolha aqui quais portais varrer.
    #
    # Coleta de PDFs via navegador headless (Playwright):
    #     ABDIPdfScraper(), SesiPdfScraper(), SenarScraper(config.ano)
    # Coleta estática (requests + BeautifulSoup), que também perfila datasets:
    #     ABDIScraper(), SesiScraper(), SenaiScraper(), SesiTransparenciaScraper()
    scrapers = [
        ABDIScraper(),
        SesiScraper(ano=config.ano),
        SenaiScraper(ano=config.ano),
        SenarScraper(ano=config.ano),
        SescApiScraper(ano=config.ano),
    ]

    for scraper in scrapers:
        run_scraper_pipeline(scraper, config, logger)


if __name__ == "__main__":
    main()

