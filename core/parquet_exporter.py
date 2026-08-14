import logging
import os
import re
import unicodedata
from typing import Optional
import pandas as pd

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


# As categorias que o `inferir_tipo_documento` sabe nomear. Fora delas ele cai
# no título sanitizado, que serve para dataset tabular (poucos títulos, todos
# repetidos entre as regionais) mas não para documento avulso: cada PDF viraria
# uma categoria só sua. Quem precisa de um vocabulário fechado confere aqui.
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
    """Salva um DataFrame no formato Parquet na estrutura Hive:

    `base_dir/parquet/tema={tema}/tipo_documento={tipo}/ano={ano}/uf={uf}/{prefixo}.parquet`
    """
    if df is None or df.empty:
        logger.warning(
            "DataFrame vazio ou nulo recebido para exportação em Parquet. Ignorando."
        )
        return None

    # Sanitização e padronização dos metadados
    tema_clean = sanitize_name(tema or "dados_abertos")
    tipo_doc_clean = inferir_tipo_documento(tipo_documento or prefixo_nome)
    ano_clean = str(ano or 2026)
    uf_clean = sanitize_name(uf or "DN").upper()
    prefixo_clean = sanitize_name(prefixo_nome)

    # Estrutura Hive atualizada: tema -> tipo_documento -> ano -> uf
    partition_dir = os.path.join(
        base_dir,
        "parquet",
        f"tema={tema_clean}",
        f"entidade={entidade}",
        f"tipo_documento={tipo_doc_clean}",
        f"ano={ano_clean}",
        f"uf={uf_clean}",
    )

    try:
        os.makedirs(partition_dir, exist_ok=True)
        file_path = os.path.join(partition_dir, f"{prefixo_clean}.parquet")

        # Injeta os metadados das partições como colunas no arquivo Parquet
        df["tema"] = tema_clean
        df["tipo_documento"] = tipo_doc_clean
        df["ano"] = int(ano_clean) if ano_clean.isdigit() else ano_clean
        df["uf"] = uf_clean

        df.to_parquet(
            file_path, engine="pyarrow", compression="snappy", index=False
        )
        logger.info(
            "[Parquet] Salvo em: %s (%d linhas)",
            file_path,
            len(df),
        )
        return file_path

    except Exception as e:
        logger.error(
            "[Parquet] Falha ao exportar arquivo '%s': %s", prefixo_clean, e
        )
        return None