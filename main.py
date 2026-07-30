import sys

import pandas as pd
from core.pipeline import run_scraper_pipeline
from scrapers.abdi import ABDIScraper
from scrapers.senai import SenaiScraper
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

    # Lista de scrapers a serem executados
    # Disponíveis: ABDIScraper(), SesiScraper(), SenaiScraper(),
    #              SesiTransparenciaScraper()
    scrapers_to_run = [
        SesiTransparenciaScraper()
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