# api/database.py
import logging
import os
from typing import Any, Dict, List, Optional, Tuple, Union
import duckdb
import pandas as pd

logger = logging.getLogger("api.database")


def get_db_connection():
    return duckdb.connect(database=":memory:")


def parse_multiselect(val: Optional[Union[str, List[Any]]]) -> List[str]:
    """Converte valores únicos, listas ou strings separadas por vírgula em uma lista limpa."""
    if not val or val == "*":
        return []
    if isinstance(val, list):
        return [str(item).strip() for item in val if str(item).strip() and str(item) != "*"]
    if isinstance(val, str):
        return [item.strip() for item in val.split(",") if item.strip() and item.strip() != "*"]
    return [str(val).strip()]


def execute_parquet_query(
    base_dir: str = "outputs",
    tema: Optional[Union[str, List[str]]] = "*",
    tipo_documento: Optional[Union[str, List[str]]] = "*",
    ano: Optional[Union[str, int, List[Any]]] = "*",
    uf: Optional[Union[str, List[str]]] = "*",
    search: Optional[str] = None,
    where_clauses: Optional[List[str]] = None,
    params: Optional[List[Any]] = None,
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

    parquet_glob = os.path.join(parquet_base, "**", "*.parquet").replace("\\", "/")

    # 💡 1. SUPORTE A MÚLTIPLA SELEÇÃO (IN (?, ?))
    temas = parse_multiselect(tema)
    if temas:
        placeholders = ", ".join(["?"] * len(temas))
        where_clauses.append(f"LOWER(CAST(tema AS VARCHAR)) IN ({placeholders})")
        params.extend([t.lower() for t in temas])

    tipos = parse_multiselect(tipo_documento)
    if tipos:
        placeholders = ", ".join(["?"] * len(tipos))
        where_clauses.append(f"LOWER(CAST(tipo_documento AS VARCHAR)) IN ({placeholders})")
        params.extend([t.lower() for t in tipos])

    anos = parse_multiselect(ano)
    if anos:
        placeholders = ", ".join(["?"] * len(anos))
        where_clauses.append(f"CAST(ano AS VARCHAR) IN ({placeholders})")
        params.extend([str(a) for a in anos])

    ufs = parse_multiselect(uf)
    if ufs:
        placeholders = ", ".join(["?"] * len(ufs))
        where_clauses.append(f"UPPER(CAST(uf AS VARCHAR)) IN ({placeholders})")
        params.extend([u.upper() for u in ufs])

    # 💡 2. BUSCA TEXTUAL ABRANGENTE
    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        # Busca em metadados padrão e converte a linha inteira para string para pesquisa ampla
        where_clauses.append(
            "(LOWER(CAST(tema AS VARCHAR)) LIKE ? OR LOWER(CAST(tipo_documento AS VARCHAR)) LIKE ? OR LOWER(CAST(uf AS VARCHAR)) LIKE ?)"
        )
        params.extend([term, term, term])

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

        df = df.where(pd.notnull(df), None)
        raw_records = df.to_dict(orient="records")

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