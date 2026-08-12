# api/database.py
import logging
import os
from typing import Any, Dict, List, Tuple
import duckdb
import pandas as pd

logger = logging.getLogger("api.database")


def get_db_connection():
    return duckdb.connect(database=":memory:")


def execute_parquet_query(
    base_dir: str = "outputs",
    tema: str = "*",
    tipo_documento: str = "*",
    ano: str = "*",
    uf: str = "*",
    where_clauses: List[str] = None,
    params: List[Any] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:

    con = get_db_connection()
    params = params or []
    where_clauses = where_clauses or []

    base_project_dir = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )

    if not os.path.isabs(base_dir):
        parquet_base = os.path.join(base_project_dir, base_dir, "parquet")
    else:
        parquet_base = os.path.join(base_dir, "parquet")

    if not os.path.exists(parquet_base):
        logger.warning(f"Diretório não encontrado: {parquet_base}")
        return [], 0

    # 💡 Usa busca recursiva universal para garantir a leitura de todos os parquets
    parquet_glob = os.path.join(parquet_base, "**", "*.parquet").replace(
        "\\", "/"
    )

    # 💡 Apenas adiciona ao WHERE se o filtro for diferente do wildcard '*'
    if tema and tema != "*":
        where_clauses.append("LOWER(CAST(tema AS VARCHAR)) = ?")
        params.append(tema.lower())

    if tipo_documento and tipo_documento != "*":
        where_clauses.append("LOWER(CAST(tipo_documento AS VARCHAR)) = ?")
        params.append(tipo_documento.lower())

    if ano and str(ano) != "*":
        where_clauses.append("CAST(ano AS VARCHAR) = ?")
        params.append(str(ano))

    if uf and uf != "*":
        where_clauses.append("UPPER(CAST(uf AS VARCHAR)) = ?")
        params.append(uf.upper())

    where_str = ""
    if where_clauses:
        where_str = "WHERE " + " AND ".join(where_clauses)

    try:
        # 1. Total de linhas
        count_query = f"""
            SELECT COUNT(*) 
            FROM read_parquet('{parquet_glob}', hive_partitioning=1, union_by_name=True)
            {where_str}
        """
        total = con.execute(count_query, params).fetchone()[0]

        if total == 0:
            return [], 0

        # 2. Dados paginados
        data_query = f"""
            SELECT * 
            FROM read_parquet('{parquet_glob}', hive_partitioning=1, union_by_name=True)
            {where_str}
            LIMIT {limit} OFFSET {offset}
        """
        df = con.execute(data_query, params).df()

        # Substitui NaN por None
        df = df.where(pd.notnull(df), None)
        raw_records = df.to_dict(orient="records")

        # 💡 Expurgo de nulos, lixo RTF e sanitização de chaves
        cleaned_records = []
        for row in raw_records:
            record_limpo = {}
            for k, v in row.items():
                if v is None or str(v).strip() in ["", "null", "None"]:
                    continue
                if "rtf1" in k:
                    continue

                key_clean = str(k).replace("ï»¿", "").replace('"', "").strip()
                record_limpo[key_clean] = v

            cleaned_records.append(record_limpo)

        return cleaned_records, total

    except Exception as e:
        if "No files found" in str(e):
            return [], 0
        logger.error(f"Erro na consulta Parquet: {e}")
        return [], 0
    finally:
        con.close()