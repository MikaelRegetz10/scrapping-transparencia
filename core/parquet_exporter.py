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


# As 27 unidades federativas pelo nome, para o caminho inverso: do texto de uma
# regional para a sigla. Sem acento e em minúsculas porque é assim que chegam ao
# `_uf_no_texto`, já passados pelo `remover_acentos`.
UFS_POR_NOME = {
    "acre": "AC",
    "alagoas": "AL",
    "amapa": "AP",
    "amazonas": "AM",
    "bahia": "BA",
    "ceara": "CE",
    "distrito federal": "DF",
    "espirito santo": "ES",
    "goias": "GO",
    "maranhao": "MA",
    "mato grosso": "MT",
    "mato grosso do sul": "MS",
    "minas gerais": "MG",
    "para": "PA",
    "paraiba": "PB",
    "parana": "PR",
    "pernambuco": "PE",
    "piaui": "PI",
    "rio de janeiro": "RJ",
    "rio grande do norte": "RN",
    "rio grande do sul": "RS",
    "rondonia": "RO",
    "roraima": "RR",
    "santa catarina": "SC",
    "sao paulo": "SP",
    "sergipe": "SE",
    "tocantins": "TO",
}

SIGLAS_DE_UF = frozenset(UFS_POR_NOME.values())

# Os nomes do maior para o menor, porque uns começam dentro dos outros: "Mato
# Grosso do Sul" começa exatamente como "Mato Grosso". Procurar o mais longo
# primeiro é o que impede que a regional do MS seja catalogada como MT.
NOMES_DE_UF_POR_TAMANHO = sorted(UFS_POR_NOME, key=len, reverse=True)

# O nome do estado precedido de preposição: é como toda regional se apresenta
# ("Administração Regional do Acre", "Regional de Alagoas"). A exigência existe
# por causa dos nomes que também são palavra comum — sem ela, "planilha para
# contratos" viraria uma regional do Pará.
PREPOSICAO = r"(?:de|do|da|das|dos)\s+"

# A sigla, mas só onde ela é mesmo uma sigla de estado: entre parênteses
# ("Administração Regional do Pará (PA)") ou colada ao nome da entidade
# ("SESC AC - Plano de Contas", "SESI-SP").
#
# Duas letras maiúsculas soltas não bastam, e o acervo diz por quê: o catálogo
# do SESI tem 161 editais cujo título começa em "PE 01/2021" — pregão
# eletrônico, não Pernambuco. Sigla sem contexto cataloga esses 161 no estado
# errado, o que é pior do que deixá-los em DN.
SIGLA_COM_CONTEXTO = re.compile(
    r"\(([A-Z]{2})\)"
    r"|(?:SESC|SESI|SENAI|SENAR|SEST|SENAT|ABDI|REGIONAL)[\s\-–—]+([A-Z]{2})(?![A-Za-z])"
)


def uf_do_texto(*textos: Optional[str]) -> Optional[str]:
    """A sigla da UF que o texto nomeia, ou None se ele não nomear nenhuma.

    Existe porque a UF nem sempre vem do scraper. O SENAR e o SESC a informam
    (`tcu_uf`), mas quem cataloga a partir do Excel de qualidade não tem esse
    campo — e o Excel foi a origem de todo o `tema=planilhas` que está em disco
    (ver scripts/backfill_planilhas.py). O que sobra é o que a entidade
    escreveu: a seção diz "Administração Regional do Acre", o título diz
    "SESC AC".

    Os textos são consultados na ordem recebida, e o primeiro que nomear uma UF
    decide. Nada nomeando nenhuma devolve None — o chamador é quem sabe se o
    caso é "Departamento Nacional" ou dado faltando.
    """
    for texto in textos:
        if not texto:
            continue

        sigla = _uf_no_texto(str(texto))
        if sigla:
            return sigla

    return None


def _uf_no_texto(texto: str) -> Optional[str]:
    """A primeira UF nomeada num texto, por nome de estado ou por sigla."""
    normalizado = remover_acentos(texto).lower()

    for nome in NOMES_DE_UF_POR_TAMANHO:
        if re.search(rf"\b{PREPOSICAO}{re.escape(nome)}\b", normalizado):
            return UFS_POR_NOME[nome]

    # Percorre todas as ocorrências, e não só a primeira: "Execução
    # Orçamentária (SENAI - SENAI-DN)" casa com o padrão duas vezes, e a
    # primeira entrega "DN", que não é estado nenhum.
    for parenteses, apos_entidade in SIGLA_COM_CONTEXTO.findall(texto):
        candidata = parenteses or apos_entidade
        if candidata in SIGLAS_DE_UF:
            return candidata

    return None


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