import ast
import io
import json
import re
from typing import Any, List, Optional, Dict
import openpyxl
import pandas as pd


def is_text_column(serie: pd.Series) -> bool:
    """Identifica colunas de texto em qualquer versão do pandas.

    Até o pandas 2.x texto vira dtype 'object'; a partir do 3.0 vira o dtype
    'str' nativo. Testar só por 'object' faria as regras de qualidade abaixo
    silenciarem em quem estiver numa versão mais nova.
    """
    return serie.dtype == "object" or pd.api.types.is_string_dtype(serie)


COLUNAS_IGNORAR_PADRAO = [
    "pagina_total",
    "pagina_atual",
    "pagina_anterior",
    "pagina_proxima",
    "registro_total",
    "registro_atual",
    "mensagens",
    "mensagens.page_size",
]


def flatten_nested_json_to_df(
    json_data: Any,
    max_depth: int = 5,
    drop_cols: Optional[List[str]] = COLUNAS_IGNORAR_PADRAO,
) -> pd.DataFrame:
    """Desempacota estruturas JSON e remove automaticamente colunas indesejadas."""

    def _parse_if_string(val):
        if isinstance(val, str) and (val.startswith("[") or val.startswith("{")):
            try:
                return json.loads(val)
            except Exception:
                try:
                    return ast.literal_eval(val)
                except Exception:
                    pass
        return val

    # 1. Parse e normalização inicial
    if isinstance(json_data, str):
        json_data = _parse_if_string(json_data)

    if isinstance(json_data, (list, dict)):
        df = pd.json_normalize(json_data)
    else:
        return pd.DataFrame()

    if df.empty:
        return df

    # 2. Descompactação de dicionários e listas aninhadas
    for _ in range(max_depth):
        complex_col_found = False

        for col in list(df.columns):
            if df[col].dtype == "object":
                df[col] = df[col].apply(_parse_if_string)

            non_null = df[col].dropna()
            if non_null.empty:
                continue

            has_dict = non_null.apply(lambda x: isinstance(x, dict)).any()
            has_list = non_null.apply(lambda x: isinstance(x, list)).any()

            if has_list:
                complex_col_found = True
                df = df.explode(col).reset_index(drop=True)
                non_null = df[col].dropna()
                has_dict = non_null.apply(lambda x: isinstance(x, dict)).any()

            if has_dict:
                complex_col_found = True
                dicts_to_expand = df[col].apply(
                    lambda x: x if isinstance(x, dict) else {}
                )
                expanded = pd.json_normalize(dicts_to_expand.tolist())
                expanded.index = df.index
                expanded.columns = [f"{col}.{subcol}" for subcol in expanded.columns]
                df = df.drop(columns=[col]).join(expanded)

        if not complex_col_found:
            break

    # 3. 💡 REMOÇÃO DE COLUNAS INDESEJADAS
    if drop_cols:
        # Remove por nome exato ou se o nome da coluna terminar com o termo (ex: "meta.pagina_total")
        cols_para_remover = [
            c
            for c in df.columns
            if c in drop_cols or any(c.endswith(f".{dc}") for dc in drop_cols)
        ]
        df = df.drop(columns=cols_para_remover, errors="ignore")

    return df

def analyze_dataset_quality(
    file_bytes: bytes, file_type: str
) -> Dict[str, Any]:
    """Realiza o perfilamento de dados ajustado para planilhas e APIs."""
    errors = []
    warnings = []
    df_valid = None
    is_json_api = file_type == "json" or "json" in file_type

    # ---------------------------------------------------------
    # PARTE 1: Análise Visual com openpyxl (Apenas Excel)
    # ---------------------------------------------------------
    if not is_json_api and (
        file_type in ["xlsx", "xls"] or "excel" in file_type
    ):
        try:
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
            sheet = wb.active

            if len(sheet.merged_cells.ranges) > 0:
                errors.append(
                    f"Células Mescladas: Encontradas {len(sheet.merged_cells.ranges)} ocorrências (ex: {list(sheet.merged_cells.ranges)[0]})."
                )

            celulas_coloridas = 0
            for row in sheet.iter_rows(min_row=1, max_row=100, max_col=10):
                for cell in row:
                    if (
                        cell.fill
                        and cell.fill.start_color
                        and cell.fill.start_color.index != "00000000"
                    ):
                        celulas_coloridas += 1

            if celulas_coloridas > 0:
                warnings.append(
                    f"Uso de Cores: {celulas_coloridas} células com preenchimento visual detectadas."
                )

        except Exception as e:
            warnings.append(
                f"Aviso de Leitura OpenPyXL: Não foi possível checar metadados visuais ({e})."
            )

    # ---------------------------------------------------------
    # PARTE 2: Análise de Conteúdo (Pandas)
    # ---------------------------------------------------------
    try:
        df_bruto = None
        df_std = None

        # --- A) TRATAMENTO PARA APIS JSON ---
        if is_json_api:
            raw_text = file_bytes.decode("utf-8")
            json_data = json.loads(raw_text)

            # Aplica o desempacotador genérico multinível
            df_std = flatten_nested_json_to_df(json_data)

            # Remove linhas sintéticas de TOTAIS da API
            if not df_std.empty:
                for name_col in [
                    "NMItemTransp",
                    "nome",
                    "descricao",
                    "title",
                    "Objeto",
                    "Tipo",
                ]:
                    if name_col in df_std.columns:
                        df_std = df_std[
                            ~df_std[name_col]
                            .astype(str)
                            .str.upper()
                            .isin(["TOTAIS", "TOTAL"])
                        ].reset_index(drop=True)

            df_bruto = df_std.copy()

        # --- B) TRATAMENTO PARA PLANILHAS EXCEL ---
        elif file_type in ["xlsx", "xls"] or "excel" in file_type:
            file_stream_raw = io.BytesIO(file_bytes)
            file_stream_std = io.BytesIO(file_bytes)
            df_bruto = pd.read_excel(file_stream_raw, header=None)
            df_std = pd.read_excel(file_stream_std, header=0)

        # --- C) TRATAMENTO PARA CSV ---
        else:
            file_stream_raw = io.BytesIO(file_bytes)
            file_stream_std = io.BytesIO(file_bytes)
            for enc in ["utf-8", "latin1", "iso-8859-1"]:
                for sep in [";", ",", "\t"]:
                    try:
                        file_stream_raw.seek(0)
                        df_bruto = pd.read_csv(
                            file_stream_raw, header=None, encoding=enc, sep=sep
                        )
                        file_stream_std.seek(0)
                        df_std = pd.read_csv(
                            file_stream_std, header=0, encoding=enc, sep=sep
                        )
                        if len(df_std.columns) > 1:
                            break
                    except Exception:
                        continue
                if df_std is not None and len(df_std.columns) > 1:
                    break

        # ---------------------------------------------------------
        # PARTE 3: Validação Adaptativa de Qualidade
        # ---------------------------------------------------------
        if df_bruto is None or df_std is None or df_std.empty:
            errors.append(
                "Erro de Leitura: Conteúdo vazio, formato ou encoding incompatível."
            )
        else:
            colunas_unnamed = [
                col
                for col in df_std.columns
                if str(col).startswith("Unnamed")
            ]
            if colunas_unnamed:
                errors.append(
                    f"Nomes de Colunas: {len(colunas_unnamed)} coluna(s) sem nome definido (ex: {colunas_unnamed[0]})."
                )

            # Regras estéticas exclusivas para planilhas físicas (Excel/CSV)
            if not is_json_api:
                linhas_vazias = df_bruto.isnull().all(axis=1).sum()
                colunas_vazias = df_bruto.isnull().all(axis=0).sum()

                if linhas_vazias > 0:
                    errors.append(
                        f"Espaçamento Visual: {linhas_vazias} linha(s) totalmente em branco."
                    )
                if colunas_vazias > 0:
                    errors.append(
                        f"Espaçamento Visual: {colunas_vazias} coluna(s) totalmente em branco."
                    )

                colunas_numericas = [
                    col
                    for col in df_std.columns
                    if str(col).isdigit()
                    or re.search(r"\b(19|20)\d{2}\b", str(col))
                ]
                if len(colunas_numericas) >= 2:
                    warnings.append(
                        f"Formato Wide Pivotado: Colunas identificadas como anos/períodos ({colunas_numericas[:3]})."
                    )

                tem_totais = False
                tem_hierarquia = False

                for col in df_std.columns:
                    if is_text_column(df_std[col]):
                        valores_texto = df_std[col].dropna().astype(str)
                        if valores_texto.str.contains(
                            r"(?i)\b(?:total|subtotal)\b", regex=True
                        ).any():
                            tem_totais = True
                        if valores_texto.str.contains(r"^\s{2,}").any():
                            tem_hierarquia = True

                if tem_totais:
                    errors.append(
                        "Linhas de Totais: Uso de 'Total' ou 'Subtotal' no meio dos registros."
                    )
                if tem_hierarquia:
                    errors.append(
                        "Hierarquia Visual: Textos iniciados com recuos/espaços em branco."
                    )

                for col in df_std.columns:
                    if is_text_column(df_std[col]):
                        amostra = df_std[col].dropna().astype(str)
                        contaminados = amostra[
                            amostra.str.match(
                                r"^-?\d+(?:[\.,]\d+)?\s*[a-zA-Z\*\(\)]+"
                            )
                        ]
                        if not contaminados.empty:
                            errors.append(
                                f"Poluição de Célula: Coluna '{col}' contém números misturados com texto/símbolos."
                            )

            if not errors:
                df_valid = df_std

    except Exception as e:
        errors.append(
            f"Erro de Processamento: Falha ao validar estrutura ({e})."
        )

    is_structured = len(errors) == 0

    return {
        "is_structured": is_structured,
        "errors": errors,
        "warnings": warnings,
        "df_valid": df_valid,
    }