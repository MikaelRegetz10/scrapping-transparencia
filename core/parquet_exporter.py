# core/parquet_exporter.py
import json
import logging
import os
import re
import unicodedata
from typing import Optional
import pandas as pd

from core.cleaner import clean_dataframe
from core.dictionary_generator import generate_data_dictionary

logger = logging.getLogger("core.parquet_exporter")


def remover_acentos(texto: str) -> str:
    """Remove acentos e caracteres diacríticos de uma string."""
    if not texto:
        return ""
    nfkd_form = unicodedata.normalize("NFKD", str(texto))
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])


def sanitize_name(text: str) -> str:
    """Sanitiza strings para caminhos de diretórios sem acentos, sem anos e sem
    caracteres especiais.
    """
    if not text:
        return "desconhecido"

    # 1. Remove acentos e converte para minúsculas
    cleaned = remover_acentos(text).lower()

    # 2. Remove anos isolados (ex: 2024, 2025, 2026)
    cleaned = re.sub(r"\b(19|20)\d{2}\b", "", cleaned)

    # 3. Substitui pontuações e símbolos por underline
    cleaned = re.sub(r"[^\w\-_]", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")

    return cleaned or "outros"


# As categorias que o `inferir_tipo_documento` sabe nomear.
TIPOS_CONHECIDOS = frozenset({
    "acordos",
    "contratos",
    "convenios",
    "demonstracoes_contabeis",
    "execucao_orcamentaria",
    "licitacoes",
    "outros",
    "pessoal",
})


def inferir_tipo_documento(texto: str) -> str:
    """Classifica o tipo de documento em uma categoria limpa e padronizada."""
    if not texto:
        return "outros"

    texto_clean = remover_acentos(texto.lower())

    if "acordo" in texto_clean:
        return "acordos"
    elif "contrato" in texto_clean:
        return "contratos"
    elif "convenio" in texto_clean:
        return "convenios"
    elif (
        "demonstra" in texto_clean
        or "balan" in texto_clean
        or "demonstrativ" in texto_clean
    ):
        return "demonstracoes_contabeis"
    elif (
        "corpo t" in texto_clean
        or "pessoal" in texto_clean
        or "remunerac" in texto_clean
    ):
        return "pessoal"
    elif "licita" in texto_clean or "edital" in texto_clean:
        return "licitacoes"
    elif (
        "receita" in texto_clean
        or "despesa" in texto_clean
        or "orcam" in texto_clean
    ):
        return "execucao_orcamentaria"

    return sanitize_name(texto)


def export_to_parquet(
    df: pd.DataFrame,
    entidade: str,
    base_dir: str,
    tema: Optional[str],
    tipo_documento: Optional[str],
    ano: Optional[int],
    uf: Optional[str],
    prefixo_nome: str,
) -> Optional[str]:
    """Limpa o DataFrame e salva no formato Parquet no particionamento Hive:

    `base_dir/parquet/tema={tema}/entidade={entidade}/tipo_documento={tipo}/ano={ano}/uf={uf}/{prefixo}.parquet`
    """
    if df is None or df.empty:
        logger.warning(
            "DataFrame vazio ou nulo recebido para exportação em Parquet. Ignorando."
        )
        return None

    # 1. Executa a limpeza e padronização dos dados (CNPJ, moeda, unpivot, metadados de cabeçalho)
    df_clean = clean_dataframe(df)

    if df_clean is None or df_clean.empty:
        logger.warning(
            "DataFrame ficou vazio após o processo de limpeza. Ignorando exportação."
        )
        return None

    # 2. Sanitização e padronização dos metadados de partição
    tema_clean = sanitize_name(tema or "dados_abertos")
    entidade_clean = sanitize_name(entidade or "desconhecido")
    tipo_doc_clean = inferir_tipo_documento(tipo_documento or prefixo_nome)
    ano_clean = str(ano or 2026)
    uf_clean = sanitize_name(uf or "DN").upper()
    prefixo_clean = sanitize_name(prefixo_nome)

    # 3. Estrutura de diretórios no formato Hive Partitioning
    partition_dir = os.path.join(
        base_dir,
        "parquet",
        f"tema={tema_clean}",
        f"entidade={entidade_clean}",
        f"tipo_documento={tipo_doc_clean}",
        f"ano={ano_clean}",
        f"uf={uf_clean}",
    )

    try:
        os.makedirs(partition_dir, exist_ok=True)
        file_path = os.path.join(partition_dir, f"{prefixo_clean}.parquet")

        # 4. Injeta os metadados das partições como colunas no DataFrame
        df_clean = df_clean.copy()
        df_clean["tema"] = tema_clean
        df_clean["entidade"] = entidade_clean
        df_clean["tipo_documento"] = tipo_doc_clean
        df_clean["ano"] = int(ano_clean) if ano_clean.isdigit() else ano_clean
        df_clean["uf"] = uf_clean

        # 5. Grava em arquivo Parquet
        df_clean.to_parquet(
            file_path, engine="pyarrow", compression="snappy", index=False
        )
        logger.info(
            "[Parquet] Salvo em: %s (%d linhas)",
            file_path,
            len(df_clean),
        )

        # 6. Gera e salva o dicionário de dados estatístico em formato JSON
        try:
            dict_file_path = file_path.replace(".parquet", "_dictionary.json")
            dict_data = generate_data_dictionary(file_path)

            with open(dict_file_path, "w", encoding="utf-8") as f:
                json.dump(dict_data, f, ensure_ascii=False, indent=2)

            logger.info("[Dicionário] Salvo em: %s", dict_file_path)
        except Exception as dict_err:
            logger.warning("[Dicionário] Falha ao gerar dicionário: %s", dict_err)

        return file_path

    except Exception as e:
        logger.error(
            "[Parquet] Falha ao exportar arquivo '%s': %s", prefixo_clean, e
        )
        return None