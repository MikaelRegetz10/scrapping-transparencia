import sys

from core.config import Config, setup_logger
from core.pipeline import run_scraper_pipeline
from scrapers.abdi import ABDIScraper
from scrapers.senai import SenaiScraper
from scrapers.senar import SenarScraper
from scrapers.sesi import SesiScraper
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
    # Disponíveis: ABDIScraper(), SesiScraper(), SenaiScraper(),
    #              SesiTransparenciaScraper(), SenarScraper(config.ano)
    scrapers = [
        SenarScraper(config.ano)
    ]

    for scraper in scrapers:
        run_scraper_pipeline(scraper, config, logger)


if __name__ == "__main__":
    main()
