"""Popula o catálogo de planilhas a partir dos Excel de qualidade já gerados.

A gravação do `tema=planilhas` nasceu depois das coletas: os links tabulares
que já estão em disco só existem na aba `Resumo_Geral` dos
`outputs/*_relatorio_qualidade.xlsx`. Este script os lê de lá e escreve as
mesmas partições que `run_scraper_pipeline` escreveria, para o portal abrir com
acervo sem depender de uma nova varredura dos portais (a ABDI, em particular,
barra sessões repetidas).

Só é preciso rodar uma vez. Depois de uma coleta nova o próprio pipeline
mantém o catálogo em dia, e rodar de novo apenas reescreve os mesmos arquivos.

    .venv/bin/python -m scripts.backfill_planilhas

Duas colunas saem diferentes do que sairia numa coleta de verdade: `ano` e
`uf`. O Excel não guarda o `tcu_ano`/`tcu_uf` do item bruto, então tudo cai no
exercício do config.json e em DN — os mesmos padrões que o pipeline usa quando
o scraper não informa nada. Uma coleta nova corrige ambos.
"""

import glob
import logging
import os

import pandas as pd

from core.config import Config, PROJECT_ROOT
from core.pipeline import (
    TEMA_PLANILHAS,
    exporta_catalogo_para_parquet,
    particao_do_link,
)

SUFIXO_RELATORIO = "_relatorio_qualidade.xlsx"
ABA_TABELAS = "Resumo_Geral"


def entidade_do_arquivo(caminho: str) -> str:
    """SENAR de `outputs/senar_relatorio_qualidade.xlsx`.

    O pipeline nomeia o relatório com `scraper.name.lower()`, então o caminho
    de volta é só desfazer isso — e é o que garante que o backfill grave na
    mesma partição `entidade=` que a coleta gravaria.
    """
    nome = os.path.basename(caminho)
    return nome[: -len(SUFIXO_RELATORIO)].upper()


def registros_do_relatorio(caminho: str) -> list:
    """Linhas da aba de planilhas, ou lista vazia se o relatório não tiver uma.

    Um scraper só de PDF grava um `Resumo_Geral` de uma linha só, com a coluna
    `Aviso` explicando que não houve tabela; `url_download` é o que separa um
    catálogo de verdade desse recado.
    """
    try:
        df = pd.read_excel(caminho, sheet_name=ABA_TABELAS)
    except ValueError:
        return []

    if df.empty or "url_download" not in df.columns:
        return []

    df = df.where(pd.notnull(df), None)
    return df.to_dict(orient="records")


def backfill(config: Config, logger) -> int:
    padrao = os.path.join(config.output_dir, f"*{SUFIXO_RELATORIO}")
    relatorios = sorted(glob.glob(padrao))

    if not relatorios:
        logger.warning(
            f"Nenhum relatório de qualidade em {padrao}. Rode a coleta primeiro."
        )
        return 0

    total = 0
    for caminho in relatorios:
        entidade = entidade_do_arquivo(caminho)
        registros = registros_do_relatorio(caminho)

        if not registros:
            logger.info(f"⏭️  [{entidade}] sem planilhas catalogadas — ignorado.")
            continue

        # O `item` bruto não sobreviveu à coleta; o que o `tipo_do_documento`
        # consegue usar do Excel é a seção e o título, na mesma ordem de
        # preferência que ele já aplica.
        particoes = [
            particao_do_link(
                {},
                str(registro.get("titulo") or ""),
                str(registro.get("secao_rota") or "Geral"),
                config,
                TEMA_PLANILHAS,
            )
            for registro in registros
        ]

        exporta_catalogo_para_parquet(
            registros, particoes, entidade, config, logger, "planilhas"
        )
        total += len(registros)

    logger.info(f"✅ Backfill concluído: {total} link(s) de planilha catalogado(s).")
    return total


def main():
    logging.basicConfig(
        level=logging.INFO, format="[%(levelname)s] %(message)s"
    )
    logger = logging.getLogger("backfill.planilhas")

    config = Config(os.path.join(PROJECT_ROOT, "config.json"))
    backfill(config, logger)


if __name__ == "__main__":
    main()
