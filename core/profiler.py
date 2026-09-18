import ast
import io
import json
import os
import re
import string
from typing import Any, Dict, List, Optional

import openpyxl
import pandas as pd


# =========================================================
# HELPER: DETECÇÃO DE TIPOS E DICIONÁRIO DE DADOS
# =========================================================

def inferir_tipo_coluna_rapido(serie: pd.Series) -> str:
    """Detecta o tipo da coluna via amostragem e Regex vetorizado (Zero chamadas LLM)."""
    amostra = serie.dropna().astype(str).head(100)
    if amostra.empty:
        return "VARCHAR"

    # 1. Regex de CNPJ (Com pontuação ou 14 dígitos numéricos)
    regex_cnpj = r"^\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$|^\d{14}$"
    if amostra.str.match(regex_cnpj).mean() > 0.8:
        return "CNPJ"

    # 2. Regex de Data (ISO 'YYYY-MM-DD' ou BR 'DD/MM/YYYY')
    regex_data = r"^\d{4}-\d{2}-\d{2}(\s\d{2}:\d{2}:\d{2})?$|^\d{1,2}/\d{1,2}/\d{4}$"
    if amostra.str.match(regex_data).mean() > 0.8:
        return "DATE"

    # 3. Numéricos nativos do Pandas
    if pd.api.types.is_numeric_dtype(serie):
        if pd.api.types.is_integer_dtype(serie):
            return "INTEGER"
        return "DECIMAL"

    return "VARCHAR"


def get_col_letter(col_index: int) -> str:
    """Converte índice (0, 1, 2) para letra de coluna (A, B, C... AA, AB)."""
    letters = string.ascii_uppercase
    if col_index < 26:
        return letters[col_index]
    return f"{letters[(col_index // 26) - 1]}{letters[col_index % 26]}"


def gerar_json_dicionario_base(df: pd.DataFrame, dataset_name: str, output_dir: str = "outputs/schemas") -> dict:
    """Gera a estrutura base do dicionário de dados inferindo tipos rapidamente."""
    total_linhas = len(df)
    columns_meta = []

    for idx, col in enumerate(df.columns):
        serie = df[col]
        linhas_validas = int(serie.notna().sum())
        pct = round((linhas_validas / total_linhas) * 100, 2) if total_linhas > 0 else 0.0

        columns_meta.append({
            "col_letter": get_col_letter(idx),
            "coluna": str(col).lower().strip(),
            "tipo": inferir_tipo_coluna_rapido(serie),
            "preenchimento_pct": pct,
            "linhas_com_valor_str": f"{linhas_validas} de {total_linhas}",
            "linhas_validas": linhas_validas,
            "total_linhas": total_linhas,
            "valores_distintos": int(serie.nunique()),
            "descricao": None,  # Será preenchido assincronamente pela LLM
            "amostra_temp": serie.dropna().astype(str).head(3).tolist() # Contexto para a LLM
        })

    schema_json = {
        "disclaimer": "As entidades não publicam dicionário de dados junto com os arquivos. O que está aqui foi medido no próprio conjunto: nada disto é descrição oficial da coluna.",
        "dataset_nome": dataset_name,
        "total_rows": total_linhas,
        "columns": columns_meta
    }

    # Salva o esqueleto JSON no disco para não onerar a memória do scraper
    os.makedirs(output_dir, exist_ok=True)
    caminho_arquivo = os.path.join(output_dir, f"{dataset_name}.json")
    with open(caminho_arquivo, "w", encoding="utf-8") as f:
        json.dump(schema_json, f, ensure_ascii=False, indent=2)

    return schema_json


# =========================================================
# FUNÇÕES ORIGINAIS DO PROFILER
# =========================================================

def is_text_column(serie: pd.Series) -> bool:
    """Identifica colunas de texto em qualquer versão do pandas."""
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


def _format_row_numbers(row_indices: List[int], offset: int = 1, max_show: int = 10) -> str:
    """Formata lista de índices de linha para números de linha do arquivo (1-based)."""
    lines = [str(i + offset) for i in row_indices]
    if len(lines) > max_show:
        return f"Linhas: {', '.join(lines[:max_show])}... (total {len(lines)})"
    return f"Linhas: {', '.join(lines)}"


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

    # 3. REMOÇÃO DE COLUNAS INDESEJADAS
    if drop_cols:
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
    """Realiza o perfilamento de dados indicando detalhadamente as linhas onde ocorrem inconformidades."""
    errors = []
    warnings = []
    df_valid = None
    is_json_api = file_type == "json" or "json" in file_type

    # ---------------------------------------------------------
    # PARTE 1: Análise Visual com openpyxl (Apenas Excel .xlsx/.xls)
    # ---------------------------------------------------------
    if not is_json_api and (
        file_type in ["xlsx", "xls"] or "excel" in file_type
    ):
        try:
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
            sheet = wb.active

            if len(sheet.merged_cells.ranges) > 0:
                merged_list = [str(r) for r in list(sheet.merged_cells.ranges)[:5]]
                errors.append(
                    f"Células Mescladas: {len(sheet.merged_cells.ranges)} ocorrência(s) "
                    f"(ex: {', '.join(merged_list)})"
                )

            celulas_coloridas = []
            for row in sheet.iter_rows(min_row=1, max_row=100, max_col=10):
                for cell in row:
                    if (
                        cell.fill
                        and cell.fill.start_color
                        and cell.fill.start_color.index != "00000000"
                    ):
                        celulas_coloridas.append(f"{cell.coordinate} (Linha {cell.row})")

            if celulas_coloridas:
                samples = celulas_coloridas[:5]
                warnings.append(
                    f"Uso de Cores: {len(celulas_coloridas)} célula(s) com preenchimento visual "
                    f"(ex: {', '.join(samples)})"
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
            df_std = flatten_nested_json_to_df(json_data)

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

        # --- B) TRATAMENTO PARA PLANILHAS EXCEL (.xlsx, .xls) ---
        elif file_type in ["xlsx", "xls"] or "excel" in file_type:
            file_stream_raw = io.BytesIO(file_bytes)
            file_stream_std = io.BytesIO(file_bytes)
            df_bruto = pd.read_excel(file_stream_raw, header=None)
            df_std = pd.read_excel(file_stream_std, header=0)

        # --- C) TRATAMENTO EXCLUSIVO PARA ODS (.ods) ---
        elif file_type == "ods":
            file_stream_raw = io.BytesIO(file_bytes)
            file_stream_std = io.BytesIO(file_bytes)
            df_bruto = pd.read_excel(file_stream_raw, header=None, engine="odf")
            df_std = pd.read_excel(file_stream_std, header=0, engine="odf")

        # --- D) TRATAMENTO PARA CSV ---
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
                str(col)
                for col in df_std.columns
                if str(col).startswith("Unnamed")
            ]
            if colunas_unnamed:
                errors.append(
                    f"Nomes de Colunas: {len(colunas_unnamed)} coluna(s) sem nome definido (ex: {colunas_unnamed[0]})."
                )

            # Regras estéticas para planilhas físicas (Excel/ODS/CSV)
            if not is_json_api:
                # 1. Linhas e Colunas 100% vazias
                idx_linhas_vazias = df_bruto[df_bruto.isnull().all(axis=1)].index.tolist()
                idx_colunas_vazias = df_bruto.columns[df_bruto.isnull().all(axis=0)].tolist()

                if idx_linhas_vazias:
                    formatted_rows = _format_row_numbers(idx_linhas_vazias, offset=1)
                    errors.append(
                        f"Espaçamento Visual: {len(idx_linhas_vazias)} linha(s) totalmente em branco ({formatted_rows})."
                    )
                if idx_colunas_vazias:
                    errors.append(
                        f"Espaçamento Visual: {len(idx_colunas_vazias)} coluna(s) totalmente em branco (Colunas: {idx_colunas_vazias[:5]})."
                    )

                # 2. Formato Pivotado Wide
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

                # 3. Checagem Refinada de Linhas de Totais e Hierarquia Visual
                total_rows_df = len(df_std)
                for col in df_std.columns:
                    if is_text_column(df_std[col]):
                        serie_str = df_std[col].dropna().astype(str)

                        # --- DETECÇÃO REFINADA DE LINHAS DE TOTAIS ---
                        textos_curtos = serie_str[serie_str.str.len() < 35]
                        match_totais = textos_curtos[
                            textos_curtos.str.contains(r"(?i)^\s*(?:total|subtotal|totais)\b", regex=True)
                        ]

                        if not match_totais.empty:
                            pct_ocorrencia = len(match_totais) / total_rows_df
                            if pct_ocorrencia <= 0.05 and len(match_totais) <= 15:
                                formatted_totais = _format_row_numbers(
                                    match_totais.index.tolist(), offset=2
                                )
                                errors.append(
                                    f"Linhas de Totais: Uso de 'Total/Subtotal' na coluna '{col}' ({formatted_totais})."
                                )

                        # --- HIERARQUIA VISUAL E POLUIÇÃO ---
                        match_hierarquia = serie_str[serie_str.str.contains(r"^\s{2,}", regex=True)]
                        if not match_hierarquia.empty:
                            formatted_hier = _format_row_numbers(match_hierarquia.index.tolist(), offset=2)
                            errors.append(f"Hierarquia Visual: Texto com recuo na coluna '{col}' ({formatted_hier}).")

                        match_poluicao = serie_str[serie_str.str.match(r"^-?\d+(?:[\.,]\d+)?[a-zA-Z\*]+")]
                        if not match_poluicao.empty:
                            formatted_poluicao = _format_row_numbers(match_poluicao.index.tolist(), offset=2)
                            errors.append(f"Poluição de Célula: Coluna '{col}' contém números misturados diretamente com texto ({formatted_poluicao}).")

            if not errors:
                df_valid = df_std

    except Exception as e:
        errors.append(f"Erro de Processamento: Falha ao validar estrutura ({e}).")

    is_structured = len(errors) == 0

    return {
        "is_structured": is_structured,
        "errors": errors,
        "warnings": warnings,
        "df_valid": df_valid,
    }