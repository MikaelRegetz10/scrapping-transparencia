# core/dictionary_generator.py
from typing import Any, Dict, List
import duckdb


def _get_column_letter(index: int) -> str:
    """Converte índice numérico (0, 1, 26, ...) na letra da coluna no padrão Excel/Tabela (A, B, AA...)."""
    result = ""
    while index >= 0:
        result = chr(index % 26 + 65) + result
        index = index // 26 - 1
    return result


def map_duckdb_type_to_label(duckdb_type: str) -> str:
    """Mapeia os tipos internos do DuckDB para nomes legíveis na interface."""
    dtype = duckdb_type.upper()
    if "VARCHAR" in dtype or "STRING" in dtype:
        return "VARCHAR"
    elif "DOUBLE" in dtype or "FLOAT" in dtype or "DECIMAL" in dtype:
        return "DOUBLE"
    elif "BIGINT" in dtype or "INTEGER" in dtype or "HUGEINT" in dtype:
        return "INTEGER"
    elif "DATE" in dtype or "TIMESTAMP" in dtype:
        return "DATE/TIME"
    elif "BOOLEAN" in dtype:
        return "BOOLEAN"
    return dtype


def generate_data_dictionary(parquet_path: str) -> Dict[str, Any]:
    """Lê um arquivo ou padrão de arquivos Parquet via DuckDB e gera o dicionário
    de dados estatístico formatado exatamente para o frontend.
    """
    conn = duckdb.connect(database=":memory:")

    try:
        # 1. Obtém a estrutura das colunas e seus tipos
        schema_info = conn.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{parquet_path}')"
        ).fetchall()

        if not schema_info:
            return {"total_rows": 0, "columns": []}

        # Lista de tuplas: (nome_coluna, tipo_dado)
        columns_schema = [(row[0], row[1]) for row in schema_info]

        # 2. Monta consulta SQL isolando os nomes das colunas com aliases numéricos seguros (val_0, dist_0, ...)
        agg_exprs = ["COUNT(*) AS _total_rows"]
        for idx, (col, _) in enumerate(columns_schema):
            col_escaped = col.replace('"', '""')
            agg_exprs.append(f'COUNT("{col_escaped}") AS "val_{idx}"')
            agg_exprs.append(f'COUNT(DISTINCT "{col_escaped}") AS "dist_{idx}"')

        sql_query = f"""
            SELECT {', '.join(agg_exprs)}
            FROM read_parquet('{parquet_path}')
        """

        result = conn.execute(sql_query).df().iloc[0]
        total_rows = int(result["_total_rows"])

        # 3. Formata os dados para o contrato exigido pelo Frontend
        columns_metadata: List[Dict[str, Any]] = []

        for idx, (col_name, col_type) in enumerate(columns_schema):
            valid_count = int(result[f"val_{idx}"])
            distinct_count = int(result[f"dist_{idx}"])

            pct_fill = (
                round((valid_count / total_rows) * 100, 1)
                if total_rows > 0
                else 0.0
            )

            columns_metadata.append({
                "col_letter": _get_column_letter(idx),
                "coluna": col_name,
                "tipo": map_duckdb_type_to_label(col_type),
                "preenchimento_pct": pct_fill,
                "linhas_com_valor_str": f"{valid_count} de {total_rows}",
                "linhas_validas": valid_count,
                "total_linhas": total_rows,
                "valores_distintos": distinct_count,
            })

        return {
            "disclaimer": (
                "As entidades não publicam dicionário de dados junto com os arquivos. "
                "O que está aqui foi medido no próprio conjunto: nada disto é descrição oficial da coluna."
            ),
            "total_rows": total_rows,
            "columns": columns_metadata,
        }

    finally:
        conn.close()