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


# Colunas que a busca textual varre, em ordem de utilidade. As de metadado
# (tema, tipo_documento, uf) existem em toda partição; as de texto só nos
# catálogos de documento e de planilha, que são justamente onde procurar pelo
# nome do arquivo faz sentido. Uma coleta que ainda não gerou catálogo não tem
# essas colunas, e pedi-las quebraria a consulta inteira — daí a intersecção
# com o esquema real em `colunas_de_busca`.
COLUNAS_DE_BUSCA = (
    "tema",
    "tipo_documento",
    "uf",
    "titulo",
    "nome_arquivo",
    "fonte",
    "secao_rota",
)


def colunas_do_esquema(con, parquet_glob: str) -> set:
    """Nomes de coluna do esquema unificado de todos os Parquet do acervo.

    Nem toda coluna existe em toda partição: `titulo` e `tipo_arquivo` só
    aparecem nos catálogos de documento e de planilha. Como o `union_by_name`
    monta o esquema a partir do glob inteiro, basta uma partição de catálogo
    para a coluna existir — mas num acervo que só tenha conteúdo tabular ela
    não existe, e citá-la derrubaria a consulta inteira em vez de só ignorar o
    filtro.
    """
    try:
        describe = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{parquet_glob}', "
            "hive_partitioning=1, union_by_name=True)"
        ).fetchall()
        return {linha[0] for linha in describe}
    except Exception as e:
        logger.warning(f"Não foi possível ler o esquema dos Parquet: {e}")
        return set()


# Colunas por que a contagem agrupada aceita agrupar. É allowlist porque o
# nome entra cru no SQL — group by não aceita parâmetro ligado.
COLUNAS_AGRUPAVEIS = frozenset({
    "tema",
    "tipo_documento",
    "ano",
    "uf",
    "entidade",
    "tipo_arquivo",
    "ativo",
    "estruturado",
    "fonte",
})


def monta_filtros(
    con,
    parquet_glob: str,
    tema=None,
    tipo_documento=None,
    ano=None,
    uf=None,
    entidade=None,
    tipo_arquivo=None,
    ativo=None,
    estruturado=None,
    search=None,
    where_clauses: Optional[List[str]] = None,
    params: Optional[List[Any]] = None,
) -> Tuple[str, List[Any]]:
    """Cláusula WHERE e parâmetros ligados dos filtros da API.

    Sai daqui, e não de dentro da consulta, porque a listagem e a contagem
    agrupada precisam filtrar exatamente igual: um filtro que valesse só numa
    das duas faria o rótulo do filtro discordar do resultado que ele produz.
    """
    where_clauses = list(where_clauses or [])
    params = list(params or [])

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

    # 💡 2. FILTROS DO CATÁLOGO (colunas que só existem nos catálogos)
    #
    # `entidade` é partição Hive e existe sempre; `tipo_arquivo`, `ativo` e
    # `estruturado` vêm das linhas de catálogo. Um filtro sobre coluna ausente é ignorado — ver
    # `colunas_do_esquema`.
    esquema = colunas_do_esquema(con, parquet_glob)

    for coluna, valor in (
        ("entidade", entidade),
        ("tipo_arquivo", tipo_arquivo),
        ("ativo", ativo),
        ("estruturado", estruturado),
    ):
        valores = parse_multiselect(valor)
        if not valores or coluna not in esquema:
            continue
        placeholders = ", ".join(["?"] * len(valores))
        where_clauses.append(
            f"UPPER(CAST({coluna} AS VARCHAR)) IN ({placeholders})"
        )
        params.extend([v.upper() for v in valores])

    # 💡 3. BUSCA TEXTUAL ABRANGENTE
    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        colunas = [c for c in COLUNAS_DE_BUSCA if c in esquema] or ["tema"]
        where_clauses.append(
            "("
            + " OR ".join(
                f"LOWER(CAST({coluna} AS VARCHAR)) LIKE ?" for coluna in colunas
            )
            + ")"
        )
        params.extend([term] * len(colunas))

    return ("WHERE " + " AND ".join(where_clauses)) if where_clauses else "", params


def esta_vazio(valor) -> bool:
    """Se o valor não tem o que informar e deve sair do registro.

    O `union_by_name` dá a toda linha as colunas de todas as partições, e uma
    linha de catálogo não tem nada a dizer sobre as centenas de colunas que
    vieram das planilhas. Sem esta poda cada registro carregaria uma centena
    de nulos.

    O `pd.isna` é que faz o trabalho: numa coluna numérica o ausente volta
    como NaN, não como None, e um teste só por None deixaria todos passarem.
    Ele recusa valores não escalares — daí o try.
    """
    if valor is None:
        return True
    try:
        if pd.isna(valor):
            return True
    except (TypeError, ValueError):
        return False
    return str(valor).strip() in ["", "null", "None"]


def caminho_do_acervo(base_dir: str) -> str:
    """Glob dos Parquet do acervo, ou string vazia se o diretório não existe."""
    base_project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if not os.path.isabs(base_dir):
        parquet_base = os.path.join(base_project_dir, base_dir, "parquet")
    else:
        parquet_base = os.path.join(base_dir, "parquet")

    if not os.path.exists(parquet_base):
        logger.warning(f"Diretório não encontrado: {parquet_base}")
        return ""

    return os.path.join(parquet_base, "**", "*.parquet").replace("\\", "/")


def execute_parquet_counts(
    base_dir: str = "outputs",
    por: str = "tipo_documento",
    **filtros,
) -> List[Dict[str, Any]]:
    """Quantas linhas há em cada valor de `por`, sob os filtros dados.

    Existe para o portal montar um filtro de uma vez só. Contar valor a valor
    custava uma varredura do acervo inteiro por opção — treze formatos, sete
    entidades, nove tipos —, e a página levava quinze segundos para ficar
    utilizável. Um GROUP BY responde tudo numa passada.

    Volta vazio quando a coluna não existe no acervo: um filtro sem opções é
    melhor que uma consulta derrubada.
    """
    if por not in COLUNAS_AGRUPAVEIS:
        logger.warning(f"Coluna não agrupável: {por}")
        return []

    parquet_glob = caminho_do_acervo(base_dir)
    if not parquet_glob:
        return []

    con = get_db_connection()
    try:
        if por not in colunas_do_esquema(con, parquet_glob):
            return []

        where_str, params = monta_filtros(con, parquet_glob, **filtros)

        linhas = con.execute(
            f"""
            SELECT CAST({por} AS VARCHAR) AS valor, COUNT(*) AS total
            FROM read_parquet('{parquet_glob}', hive_partitioning=1, union_by_name=True)
            {where_str}
            GROUP BY valor
            HAVING valor IS NOT NULL
            ORDER BY total DESC
            """,
            params,
        ).fetchall()

        return [{"valor": linha[0], "total": linha[1]} for linha in linhas]

    except Exception as e:
        if "No files found" in str(e):
            return []
        logger.error(f"Erro na contagem por {por}: {e}")
        return []
    finally:
        con.close()


def execute_parquet_query(
    base_dir: str = "outputs",
    tema: Optional[Union[str, List[str]]] = "*",
    tipo_documento: Optional[Union[str, List[str]]] = "*",
    ano: Optional[Union[str, int, List[Any]]] = "*",
    uf: Optional[Union[str, List[str]]] = "*",
    entidade: Optional[Union[str, List[str]]] = "*",
    tipo_arquivo: Optional[Union[str, List[str]]] = "*",
    ativo: Optional[Union[str, List[str]]] = "*",
    estruturado: Optional[Union[str, List[str]]] = "*",
    search: Optional[str] = None,
    where_clauses: Optional[List[str]] = None,
    params: Optional[List[Any]] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:

    con = get_db_connection()
    params = params or []
    where_clauses = where_clauses or []

    parquet_glob = caminho_do_acervo(base_dir)
    if not parquet_glob:
        return [], 0

    where_str, params = monta_filtros(
        con,
        parquet_glob,
        tema=tema,
        tipo_documento=tipo_documento,
        ano=ano,
        uf=uf,
        entidade=entidade,
        tipo_arquivo=tipo_arquivo,
        ativo=ativo,
        estruturado=estruturado,
        search=search,
        where_clauses=where_clauses,
        params=params,
    )

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
                if esta_vazio(v):
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