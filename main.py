from core.config import Config, setup_logger
from core.pipeline import run_scraper_pipeline
from scrapers.abdi import ABDIScraper
from scrapers.senai import SenaiScraper
from scrapers.senar import SenarScraper
from scrapers.sesc_api import SescApiScraper
from scrapers.sesi import SesiScraper


def main():
    config = Config()

    logger = setup_logger(config)

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

