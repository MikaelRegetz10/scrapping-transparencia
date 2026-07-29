# core/cleaner.py
import re
from typing import List, Optional
import pandas as pd

# ------------------------------------------
# 1. CONVERSÃO DE VALORES NUMÉRICOS
# ------------------------------------------


def clean_currency_to_float(val) -> Optional[float]:
    """Converte '101.711,14', '-3.143.391,01', '-' para float do Python/SQL."""
    if pd.isna(val) or val is None:
        return None

    val_str = str(val).strip()

    if val_str in ["-", "", "--", "None"]:
        return 0.0

    try:
        cleaned = val_str.replace(".", "").replace(",", ".")
        return float(cleaned)
    except ValueError:
        return None


# ------------------------------------------
# 2. REMOÇÃO DE METADADOS E CABEÇALHO DINÂMICO
# ------------------------------------------


def drop_metadata_rows(
    df: pd.DataFrame, target_columns: List[str]
) -> pd.DataFrame:
    """Elimina linhas institucionais e encontra a linha real de cabeçalho."""
    header_idx = None

    for idx, row in df.iterrows():
        row_values = [str(val).upper().strip() for val in row.values]
        if any(target in row_values for target in target_columns):
            header_idx = idx
            break

    if header_idx is not None:
        df.columns = df.iloc[header_idx].values
        df = df.iloc[header_idx + 1 :].reset_index(drop=True)

    df = df.dropna(how="all").dropna(how="all", axis=1)
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


# ------------------------------------------
# 3. SEPARAÇÃO DE TABELAS LADO A LADO
# ------------------------------------------


def split_side_by_side_table(df: pd.DataFrame) -> pd.DataFrame:
    """Separa tabelas do tipo ATIVO (esq) e PASSIVO (dir) em uma única tabela."""
    row_strings = " ".join(df.astype(str).values.flatten()).upper()

    if "ATIVO" in row_strings and "PASSIVO" in row_strings:
        mid = len(df.columns) // 2

        df_left = df.iloc[:, :mid].dropna(how="all").copy()
        df_right = df.iloc[:, mid:].dropna(how="all").copy()

        df_left["categoria_balanco"] = "ATIVO"
        df_right["categoria_balanco"] = "PASSIVO"

        cols_count = len(df_left.columns)
        new_cols = [f"col_{i}" for i in range(cols_count - 1)] + [
            "categoria_balanco"
        ]

        df_left.columns = new_cols
        df_right.columns = new_cols

        return pd.concat([df_left, df_right], ignore_index=True)

    return df


# ------------------------------------------
# 4. UNPIVOT / MELT DE ANOS E MESES
# ------------------------------------------


def unpivot_periods(
    df: pd.DataFrame, id_vars: List[str], value_name: str = "valor"
) -> pd.DataFrame:
    """Transforma colunas '2025', '2024', 'mar/24' em linhas da coluna

    'exercicio_periodo'.
    """
    period_cols = [
        c
        for c in df.columns
        if re.search(r"\b(19|20)\d{2}\b", str(c))
        or re.search(r"[a-z]{3}/\d{2}", str(c).lower())
    ]

    if not period_cols:
        return df

    id_vars_present = [c for c in id_vars if c in df.columns]

    return pd.melt(
        df,
        id_vars=id_vars_present,
        value_vars=period_cols,
        var_name="exercicio_periodo",
        value_name=value_name,
    )




# ------------------------------------------
# 5. MÁSCARA / PIPELINE DE LIMPEZA
# ------------------------------------------


def clean_dataframe(df_raw: pd.DataFrame) -> pd.DataFrame:
    """Orquestra a limpeza do DataFrame bruto aplicando todas as regras."""
    if df_raw.empty:
        return df_raw

    try:
        # 1. Trata tabelas lado a lado (Ativo/Passivo)
        df = split_side_by_side_table(df_raw)

        # 2. Remove metadados e localiza cabeçalho real
        df = drop_metadata_rows(
            df,
            target_columns=[
                "CONTA",
                "CONTAS",
                "CÓDIGO",
                "CODIGO",
                "DESCRICAO",
                "DESCRIÇÃO",
                "ATIVO",
                "PASSIVO",
            ],
        )

        # 3. Transforma anos/períodos em linhas (Unpivot)
        df = unpivot_periods(
            df, id_vars=["codigo", "conta", "descricao", "categoria_balanco"]
        )

        # 4. Normaliza colunas numéricas para float do SQL
        for col in df.columns:
            if col in [
                "valor",
                "executado",
                "saldo",
                "orc_inicial",
                "orc_reformulado",
            ] or any(year in str(col) for year in ["2023", "2024", "2025"]):
                df[col] = df[col].apply(clean_currency_to_float)

        return df

    except Exception as e:
        # Em caso de estrutura totalmente atípica, retorna o original sem quebrar o pipeline
        print(f" (Aviso: limpeza parcial aplicada -> {e})", end="")
        return df_raw