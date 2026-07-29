import pandas as pd
from core.pipeline import run_scraper_pipeline
from scrapers.abdi import ABDIScraper
from scrapers.senai import SenaiScraper
from scrapers.sesi import SesiScraper


def main():
    # Lista de scrapers a serem executados
    # Disponíveis: ABDIScraper(), SesiScraper(), SenaiScraper()
    scrapers_to_run = [
        ABDIScraper()
    ]

    all_dfs = []

    for scraper in scrapers_to_run:
        df_result = run_scraper_pipeline(scraper)
        all_dfs.append(df_result)

    # Consolida tudo num DataFrame único final com todos os portais
    if all_dfs:
        df_consolidado = pd.concat(all_dfs, ignore_index=True)
        print("\n=== RESUMO GERAL ===")
        print(f"Total de links verificados: {len(df_consolidado)}")
        print(df_consolidado["ativo"].value_counts(dropna=False))



if __name__ == "__main__":
    main()