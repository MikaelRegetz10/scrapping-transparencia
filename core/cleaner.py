# core/cleaner.py
import re
from typing import Any, List, Optional
import pandas as pd

# ------------------------------------------
# HELPER: DEDUPLICAÇÃO DE COLUNAS
# ------------------------------------------


def _make_columns_unique(cols: List[Any]) -> List[str]:
    """Garante que todas as colunas tenham nomes únicos (ex: 'codigo', 'codigo_1')."""
    seen = {}
    new_cols = []
    for col in cols:
        col_str = str(col).strip().lower()
        if col_str in seen:
            seen[col_str] += 1
            new_cols.append(f"{col_str}_{seen[col_str]}")
        else:
            seen[col_str] = 0
            new_cols.append(col_str)
    return new_cols


# ------------------------------------------
# 1. CONVERSÃO E LIMPEZA DE DATAS
# ------------------------------------------


def clean_date(val) -> Optional[str]:
    """Extrai e padroniza datas para o formato ISO 'YYYY-MM-DD' (ou 'YYYY-MM-DD HH:MM:SS').

    Trata textos como 'Publicado em 15/03/2024', formatos BR e ISO.
    """
    if pd.isna(val) or val is None:
        return None

    val_str = str(val).strip()
    if not val_str or val_str.lower() in ["none", "nan", "null", "-", "--"]:
        return None

    # 1. Tenta extrair padrões de data com regex (DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD)
    # Ex: Captura '15/03/2024' dentro de 'Publicado em 15/03/2024'
    match_br = re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b", val_str)
    match_iso = re.search(r"\b(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})\b", val_str)

    target_str = val_str
    if match_br:
        day, month, year = match_br.groups()
        if len(year) == 2:
            year = f"20{year}"
        target_str = f"{day.zfill(2)}/{month.zfill(2)}/{year}"
    elif match_iso:
        year, month, day = match_iso.groups()
        target_str = f"{year}-{month.zfill(2)}-{day.zfill(2)}"

    # 2. Converte via pandas com dayfirst=True para tratar o padrão brasileiro corretamente
    try:
        dt = pd.to_datetime(target_str, dayfirst=True, errors="coerce")
        if pd.notna(dt):
            # Se a hora for meia-noite exata (sem hora definida), retorna apenas 'YYYY-MM-DD'
            if dt.time() == pd.Timestamp("00:00:00").time():
                return dt.strftime("%Y-%m-%d")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    return None


# ------------------------------------------
# 2. CONVERSÃO DE VALORES NUMÉRICOS, CNPJ E TEXTO
# ------------------------------------------


def clean_currency_to_float(val) -> Optional[float]:
    """Converte '101.711,14', '-3.143.391,01', '1234.56', '-', etc., para float."""
    if pd.isna(val) or val is None:
        return None

    if isinstance(val, (int, float)):
        return float(val)

    val_str = str(val).strip()

    if val_str in ["-", "", "--", "None", "nan", "NaN"]:
        return 0.0

    val_str = re.sub(r"[R$\s]", "", val_str)

    try:
        if "," in val_str:
            val_str = val_str.replace(".", "").replace(",", ".")
        return float(val_str)
    except ValueError:
        return None


def clean_cnpj(value) -> Optional[str]:
    """Aplica a máscara XX.XXX.XXX/XXXX-XX no CNPJ."""
    if pd.isna(value) or value is None:
        return None

    val_str = str(value).strip()
    if val_str.endswith(".0"):
        val_str = val_str[:-2]

    digits = re.sub(r"\D", "", val_str)
    if not digits:
        return None

    digits = digits.zfill(14)

    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return digits


def clean_text(value) -> Optional[str]:
    """Limpa espaços extras e mapeia vazios para None."""
    if pd.isna(value) or value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in ["none", "nan", "null"] else None


# ------------------------------------------
# 3. REMOÇÃO DE METADADOS E CABEÇALHO DINÂMICO
# ------------------------------------------


# O preâmbulo institucional — nome da entidade, título do balanço, data de
# emissão — fica no alto do arquivo. Procurar o cabeçalho para além disso é
# procurar no meio dos dados, e lá qualquer casamento é coincidência.
MAXIMO_LINHAS_DE_PREAMBULO = 20


def _e_linha_de_cabecalho(
    df: pd.DataFrame, posicao: int, target_columns: List[str]
) -> bool:
    """Se a linha que casou com o vocabulário é mesmo o cabeçalho, ou é dado.

    Casar com a palavra não basta. `ATIVO`, `PASSIVO` e `CONTA` são vocabulário
    de balanço, mas também são valores plausíveis de uma coluna de situação —
    e numa tabela de contratos cujo status é "Ativo" a primeira linha de dados
    seria promovida a cabeçalho, levando embora o cabeçalho verdadeiro e toda
    linha acima dela. Sem aviso: o conjunto exportado fica com uma coluna
    chamada `001` e uma linha a menos.

    O que separa um caso do outro é a repetição. A célula de um cabeçalho é
    única na sua coluna, porque tudo abaixo dela é dado; um "Ativo" que é
    status reaparece nas outras linhas da mesma coluna. Então o casamento só
    vale quando nenhum dos valores que casaram se repete coluna abaixo.
    """
    alvos = {str(alvo).upper().strip() for alvo in target_columns}
    linha = df.iloc[posicao]
    casou = False

    for indice_coluna, valor in enumerate(linha.values):
        texto = str(valor).upper().strip()
        if texto not in alvos:
            continue

        casou = True
        coluna = df.iloc[:, indice_coluna].astype(str).str.upper().str.strip()
        if int((coluna == texto).sum()) > 1:
            return False

    return casou


def drop_metadata_rows(
    df: pd.DataFrame, target_columns: List[str]
) -> pd.DataFrame:
    """Elimina linhas institucionais e encontra a linha real de cabeçalho."""
    # A promoção do cabeçalho reescreve `df.columns`; sem a cópia isso voltaria
    # para o DataFrame de quem chamou, que passaria a ver o próprio cabeçalho
    # trocado por uma linha de dados.
    df = df.copy()
    header_pos = None

    # Por posição, e não pelo rótulo do índice: `iterrows` devolve o rótulo, e
    # o `iloc` logo abaixo o consumia como posição. Num frame cujo índice não
    # começa em zero — o que um `dropna` anterior já produz — as duas coisas
    # apontam para linhas diferentes.
    for posicao in range(min(len(df), MAXIMO_LINHAS_DE_PREAMBULO)):
        if _e_linha_de_cabecalho(df, posicao, target_columns):
            header_pos = posicao
            break

    if header_pos is not None:
        df.columns = df.iloc[header_pos].values
        df = df.iloc[header_pos + 1 :].reset_index(drop=True)

    df = df.dropna(how="all").dropna(how="all", axis=1)
    df.columns = _make_columns_unique(list(df.columns))
    return df


# ------------------------------------------
# 4. SEPARAÇÃO DE TABELAS LADO A LADO
# ------------------------------------------


def split_side_by_side_table(df: pd.DataFrame) -> pd.DataFrame:
    """Separa tabelas do tipo ATIVO (esq) e PASSIVO (dir) em uma única tabela."""
    row_strings = " ".join(
        str(x) for x in df.values.flatten() if pd.notna(x)
    ).upper()

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
# 5. UNPIVOT / MELT DE ANOS E MESES
# ------------------------------------------


def unpivot_periods(
    df: pd.DataFrame, id_vars: List[str], value_name: str = "valor"
) -> pd.DataFrame:
    """Transforma colunas '2025', '2024', 'mar/24' em linhas da coluna 'exercicio_periodo'."""
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
# 6. PIPELINE DE LIMPEZA
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

        # Garante colunas únicas novamente
        df.columns = _make_columns_unique(list(df.columns))

        # 4. Aplica limpeza específica por tipo/coluna
        for col in df.columns:
            col_lower = str(col).lower()

            # Trata CNPJ
            if "cnpj" in col_lower:
                df[col] = df[col].apply(clean_cnpj)

            # Trata DATAS (ex: publicado_em, data_publicacao, dt_emissao)
            elif any(
                term in col_lower
                for term in [
                    "publicado",
                    "data",
                    "dt_",
                    "emissao",
                    "vencimento",
                    "criado_em",
                    "atualizado_em",
                ]
            ):
                df[col] = df[col].apply(clean_date)

            # Trata VALORES NUMÉRICOS / MONETÁRIOS
            elif col_lower in [
                "valor",
                "executado",
                "saldo",
                "orc_inicial",
                "orc_reformulado",
            ] or any(year in col_lower for year in ["2023", "2024", "2025"]):
                df[col] = df[col].apply(clean_currency_to_float)

            # Limpeza genérica de TEXTO
            elif df[col].dtype == "object":
                df[col] = df[col].apply(clean_text)

        return df

    except Exception as e:
        print(f" (Aviso: limpeza parcial aplicada -> {e})", end="")
        return df_raw