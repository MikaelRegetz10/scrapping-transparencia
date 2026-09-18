import sys
import os

from dotenv import load_dotenv

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
from scrapers.apexbrasil import ApexBrasilScraper
from scrapers.senac import SenacScraper

from core.metadata_enricher import processar_schemas_pendentes


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

    load_dotenv()

    config = Config()
    logger = setup_logger(config)

    scrapers = [
        #ABDIScraper(),
        SesiScraper(ano=config.ano),
        #SenaiScraper(ano=config.ano),
        #SenarScraper(ano=config.ano),
        #SescApiScraper(ano=config.ano),
        #ABDIPdfScraper(),
        #SesiPdfScraper(),
        #ApexBrasilScraper(),
        #SenacScraper(),
    ]


    for scraper in scrapers:
        run_scraper_pipeline(scraper, config, logger)

    api_key = os.getenv("GEMINI_API_KEY")

    if api_key:
        processar_schemas_pendentes(api_key=api_key)
    else:
        logger.warning("GEMINI_API_KEY não encontrada. Os dicionários json ficarão sem descrição.")


if __name__ == "__main__":
    main()
