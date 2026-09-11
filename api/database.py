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


def raiz_dos_parquet(base_dir: str) -> str:
    """Diretório `parquet/` do acervo, resolvido contra a raiz do projeto."""
    base_project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if not os.path.isabs(base_dir):
        return os.path.join(base_project_dir, base_dir, "parquet")
    return os.path.join(base_dir, "parquet")


def caminho_do_acervo(base_dir: str) -> str:
    """Glob dos Parquet do acervo, ou string vazia se o diretório não existe."""
    parquet_base = raiz_dos_parquet(base_dir)

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


# ==========================================================================
# CONJUNTOS DE DADOS: UM PARQUET DE CADA VEZ
#
# Tudo acima consulta o acervo inteiro de uma vez, unindo os milhares de
# arquivos pelo nome das colunas. Isso responde "que links existem", que é o
# que os catálogos precisam — e é o oposto do que uma visualização em planilha
# quer. Lá interessa um arquivo só, com as colunas que ele de fato tem, na
# ordem em que a entidade as publicou. Um SELECT * do acervo unificado
# devolveria as 291 colunas de todos os datasets somados, quase todas nulas
# para a linha em questão, e sem ordem que signifique alguma coisa.
#
# Daí esta segunda porta de entrada: o cliente diz qual arquivo quer e a
# consulta lê só ele.
# ==========================================================================

# O `export_to_parquet` grava estas quatro dentro de cada arquivo, além de
# usá-las como diretório Hive. Dentro de um conjunto elas são constantes — a
# mesma palavra repetida em todas as linhas —, então ficam fora da grade e
# aparecem uma vez só, no cabeçalho do conjunto.
COLUNAS_INJETADAS = ("tema", "tipo_documento", "ano", "uf", "entidade")

# Os dois temas de catálogo guardam inventário de link, não conteúdo de
# planilha. Quem os quer ver tem as páginas de catálogo, que mostram cada
# registro com o que ele significa; abri-los aqui como grade de células só
# duplicaria aquilo pior.
TEMAS_DE_CATALOGO = frozenset({"documentos", "planilhas"})

# A varredura do acervo custa ~1s, e a página do visualizador a repete a cada
# tecla digitada na busca por conjunto. O cache guarda o resultado até que os
# arquivos mudem — a assinatura é a contagem e a data de modificação mais
# recente, que uma coleta nova altera.
_cache_conjuntos: Dict[str, Any] = {"assinatura": None, "conjuntos": []}


def _literal_sql(texto: str) -> str:
    """Escapa uma string para entrar como literal no SQL.

    O caminho do arquivo não pode ir como parâmetro ligado: `read_parquet`
    exige o nome em tempo de planejamento. O caminho já foi validado contra a
    raiz do acervo em `caminho_do_conjunto` — isto é o cinto além do
    suspensório.
    """
    return str(texto).replace("'", "''")


def _identificador_sql(nome: str) -> str:
    """Escapa um nome de coluna para uso em SELECT e ORDER BY.

    Os nomes vêm das planilhas das entidades e trazem de tudo: acento, `º`,
    espaço, aspas. Só entram na consulta nomes que o esquema do próprio
    arquivo confirmou existir.
    """
    return '"' + str(nome).replace('"', '""') + '"'


def caminho_do_conjunto(base_dir: str, arquivo: str) -> Optional[str]:
    """Caminho absoluto de um Parquet do acervo, ou None se não for um.

    `arquivo` chega do cliente, e é o único parâmetro desta API que vira
    caminho de disco. Recusa o que não termina em `.parquet`, o que não
    existe e — o que importa — o que escapa da pasta do acervo: `realpath`
    resolve `..` e link simbólico antes da comparação, então nem
    `../../etc/passwd` nem um atalho plantado dentro de `outputs/` saem de lá.
    """
    if not arquivo or not str(arquivo).endswith(".parquet"):
        return None

    raiz = os.path.realpath(raiz_dos_parquet(base_dir))
    caminho = os.path.realpath(os.path.join(raiz, str(arquivo)))

    if not caminho.startswith(raiz + os.sep):
        logger.warning(f"Caminho fora do acervo recusado: {arquivo}")
        return None

    return caminho if os.path.isfile(caminho) else None


def _particoes_do_caminho(relativo: str) -> Dict[str, str]:
    """Os `chave=valor` das pastas Hive de um caminho relativo."""
    partes = relativo.split("/")[:-1]
    return dict(
        parte.split("=", 1) for parte in partes if "=" in parte
    )


def _varre_conjuntos(raiz: str) -> Tuple[List[Dict[str, Any]], Tuple[int, float]]:
    """Um registro por Parquet do acervo, montado a partir do caminho.

    Sai da árvore de diretórios, e não de uma consulta, porque tema, entidade,
    tipo, ano e UF são justamente os nomes das pastas — ler o arquivo para
    descobri-los seria pagar I/O por um dado que está no caminho.
    """
    conjuntos = []
    mais_recente = 0.0

    for pasta, _, arquivos in os.walk(raiz):
        for nome in arquivos:
            if not nome.endswith(".parquet"):
                continue

            absoluto = os.path.join(pasta, nome)
            relativo = os.path.relpath(absoluto, raiz).replace("\\", "/")
            particoes = _particoes_do_caminho(relativo)

            if particoes.get("tema") in TEMAS_DE_CATALOGO:
                continue

            try:
                mais_recente = max(mais_recente, os.path.getmtime(absoluto))
            except OSError:
                continue

            conjuntos.append({
                "arquivo": relativo,
                "nome": nome[: -len(".parquet")],
                "tema": particoes.get("tema", ""),
                "entidade": particoes.get("entidade", ""),
                "tipo_documento": particoes.get("tipo_documento", ""),
                "ano": particoes.get("ano", ""),
                "uf": particoes.get("uf", ""),
                "linhas": None,
                "colunas": None,
            })

    return conjuntos, (len(conjuntos), mais_recente)


def _mede_conjuntos(con, raiz: str, conjuntos: List[Dict[str, Any]]) -> None:
    """Preenche linhas e colunas de cada conjunto, no lugar.

    As duas medidas saem do rodapé de metadados dos Parquet, não dos dados:
    `parquet_file_metadata` e `parquet_schema` respondem pelo acervo inteiro
    em cerca de um segundo, sem ler uma linha sequer. Contar com um
    `COUNT(*)` por arquivo levaria minutos.
    """
    glob = os.path.join(raiz, "**", "*.parquet").replace("\\", "/")
    por_arquivo = {c["arquivo"]: c for c in conjuntos}

    def relativo(caminho: str) -> str:
        return os.path.relpath(caminho, raiz).replace("\\", "/")

    try:
        for caminho, linhas in con.execute(
            f"SELECT file_name, num_rows FROM parquet_file_metadata('{_literal_sql(glob)}')"
        ).fetchall():
            conjunto = por_arquivo.get(relativo(caminho))
            if conjunto is not None:
                conjunto["linhas"] = int(linhas)

        # A raiz do esquema também é uma linha em `parquet_schema`, e as
        # colunas de partição não entram na grade: nenhuma das duas conta.
        injetadas = ", ".join(f"'{c}'" for c in COLUNAS_INJETADAS)
        for caminho, colunas in con.execute(
            f"""
            SELECT file_name, COUNT(*) AS colunas
            FROM parquet_schema('{_literal_sql(glob)}')
            WHERE type IS NOT NULL AND name NOT IN ({injetadas})
            GROUP BY file_name
            """
        ).fetchall():
            conjunto = por_arquivo.get(relativo(caminho))
            if conjunto is not None:
                conjunto["colunas"] = int(colunas)
    except Exception as e:
        logger.warning(f"Não foi possível medir os conjuntos: {e}")


def lista_conjuntos(base_dir: str = "outputs") -> List[Dict[str, Any]]:
    """Todo dataset tabular do acervo, com seu tamanho.

    Um conjunto é um arquivo Parquet: o conteúdo de uma planilha coletada,
    como o profiler a aprovou. É esta a unidade que a visualização abre.
    """
    raiz = raiz_dos_parquet(base_dir)
    if not os.path.exists(raiz):
        logger.warning(f"Diretório não encontrado: {raiz}")
        return []

    conjuntos, assinatura = _varre_conjuntos(raiz)
    if _cache_conjuntos["assinatura"] == assinatura:
        return _cache_conjuntos["conjuntos"]

    con = get_db_connection()
    try:
        _mede_conjuntos(con, raiz, conjuntos)
    finally:
        con.close()

    conjuntos.sort(key=lambda c: (c["entidade"], c["tema"], c["nome"]))
    _cache_conjuntos.update(assinatura=assinatura, conjuntos=conjuntos)
    return conjuntos


def rotulo_da_coluna(nome: str) -> str:
    """O nome de uma coluna como se mostra a alguém.

    Boa parte dos CSV do Sistema S sai com BOM e com o cabeçalho entre aspas,
    e os dois foram parar dentro do Parquet como parte do nome da primeira
    coluna — `ï»¿"MEMBROS DO CORPO TÉCNICO"`. O `execute_parquet_query` já
    faz esta limpeza nos catálogos; aqui ela não pode substituir o nome, que
    é o que identifica a coluna no arquivo e na consulta. Por isso são dois
    campos: `nome` consulta, `rotulo` aparece.
    """
    limpo = (
        str(nome).replace("﻿", "").replace("ï»¿", "").replace('"', "").strip()
    )
    return limpo or str(nome)


def colunas_do_conjunto(con, caminho: str) -> List[Dict[str, str]]:
    """Nome, rótulo e tipo das colunas de um conjunto, na ordem publicada.

    Sem as quatro que o pipeline injeta: repetir "tema" em toda linha de uma
    grade é gastar uma coluna para dizer o que o cabeçalho já diz.
    """
    descricao = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{_literal_sql(caminho)}')"
    ).fetchall()

    return [
        {"nome": linha[0], "rotulo": rotulo_da_coluna(linha[0]), "tipo": linha[1]}
        for linha in descricao
        if linha[0] not in COLUNAS_INJETADAS
    ]


def _valor_json(valor):
    """Uma célula pronta para virar JSON.

    O DuckDB devolve o dado em tipos do numpy e do pandas, que o serializador
    do FastAPI não conhece. `esta_vazio` cuida do ausente — que numa coluna
    numérica chega como NaN, e não como None.
    """
    if esta_vazio(valor):
        return None
    if isinstance(valor, (str, int, float, bool)):
        return valor
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    if hasattr(valor, "item"):
        return valor.item()
    return str(valor)


def le_conjunto(
    base_dir: str = "outputs",
    arquivo: str = "",
    search: Optional[str] = None,
    ordenar: Optional[str] = None,
    direcao: str = "asc",
    limit: int = 100,
    offset: int = 0,
) -> Optional[Dict[str, Any]]:
    """Uma fatia de um conjunto, pronta para a grade.

    As linhas saem como listas, e não como dicionários: numa planilha de 27
    colunas por 500 linhas, repetir o nome da coluna em cada célula é a maior
    parte do corpo da resposta. A ordem é a das colunas devolvidas junto.

    Devolve None quando o arquivo pedido não é um conjunto do acervo.
    """
    caminho = caminho_do_conjunto(base_dir, arquivo)
    if caminho is None:
        return None

    con = get_db_connection()
    try:
        colunas = colunas_do_conjunto(con, caminho)
        nomes = [coluna["nome"] for coluna in colunas]
        if not nomes:
            return None

        fonte = f"read_parquet('{_literal_sql(caminho)}')"
        selecao = ", ".join(_identificador_sql(nome) for nome in nomes)

        where, params = "", []
        if search and search.strip():
            termo = f"%{search.strip().lower()}%"
            where = "WHERE " + " OR ".join(
                f"LOWER(CAST({_identificador_sql(nome)} AS VARCHAR)) LIKE ?"
                for nome in nomes
            )
            params = [termo] * len(nomes)

        # Só ordena por coluna que este arquivo tem: o nome entra cru no SQL,
        # e ORDER BY não aceita parâmetro ligado.
        ordem = ""
        if ordenar in nomes:
            sentido = "DESC" if str(direcao).lower() == "desc" else "ASC"
            ordem = f"ORDER BY {_identificador_sql(ordenar)} {sentido} NULLS LAST"

        total = con.execute(f"SELECT COUNT(*) FROM {fonte}").fetchone()[0]
        filtrado = (
            con.execute(f"SELECT COUNT(*) FROM {fonte} {where}", params).fetchone()[0]
            if where
            else total
        )

        linhas = con.execute(
            f"""
            SELECT {selecao} FROM {fonte}
            {where}
            {ordem}
            LIMIT {int(limit)} OFFSET {int(offset)}
            """,
            params,
        ).fetchall()

        # As partições saem do caminho já resolvido, não do que o cliente
        # mandou: `a/../b.parquet` aponta para um conjunto legítimo, mas lido
        # como texto daria uma pasta Hive que não existe.
        relativo = os.path.relpath(
            caminho, os.path.realpath(raiz_dos_parquet(base_dir))
        ).replace("\\", "/")
        particoes = _particoes_do_caminho(relativo)

        return {
            "arquivo": relativo,
            "nome": os.path.basename(relativo)[: -len(".parquet")],
            "tema": particoes.get("tema", ""),
            "entidade": particoes.get("entidade", ""),
            "tipo_documento": particoes.get("tipo_documento", ""),
            "ano": particoes.get("ano", ""),
            "uf": particoes.get("uf", ""),
            "colunas": colunas,
            "linhas": [[_valor_json(celula) for celula in linha] for linha in linhas],
            "total": total,
            "total_filtrado": filtrado,
        }

    except Exception as e:
        logger.error(f"Erro ao ler o conjunto {arquivo}: {e}")
        return None
    finally:
        con.close()


def conjunto_como_dataframe(
    base_dir: str = "outputs",
    arquivo: str = "",
    search: Optional[str] = None,
    ordenar: Optional[str] = None,
    direcao: str = "asc",
    limit: int = 100000,
) -> Optional[pd.DataFrame]:
    """O conjunto inteiro sob os mesmos filtros da grade, para exportação.

    Reaproveita o `le_conjunto` em vez de repetir a consulta: assim o CSV que
    a pessoa baixa é exatamente o que ela estava vendo, com a mesma busca e a
    mesma ordenação, e não uma segunda versão da regra que possa divergir.
    """
    conjunto = le_conjunto(
        base_dir=base_dir,
        arquivo=arquivo,
        search=search,
        ordenar=ordenar,
        direcao=direcao,
        limit=limit,
        offset=0,
    )
    if conjunto is None:
        return None

    # O cabeçalho exportado leva o rótulo, e não o nome cru: um CSV cujo
    # primeiro campo se chama `ï»¿"MEMBROS…"` nasce com o defeito que o
    # acervo herdou da fonte.
    return pd.DataFrame(
        conjunto["linhas"],
        columns=[coluna["rotulo"] for coluna in conjunto["colunas"]],
    )


def dicionario_do_conjunto(
    base_dir: str = "outputs", arquivo: str = ""
) -> Optional[Dict[str, Any]]:
    """Colunas de um conjunto com tipo, preenchimento e valores distintos.

    É a documentação que o acervo consegue dar de si: os portais não publicam
    dicionário de dados, então o que dá para dizer de uma coluna é o que se
    mede nela. Vai numa consulta só — um agregado por coluna, uma passada
    pelo arquivo.
    """
    caminho = caminho_do_conjunto(base_dir, arquivo)
    if caminho is None:
        return None

    con = get_db_connection()
    try:
        colunas = colunas_do_conjunto(con, caminho)
        if not colunas:
            return None

        fonte = f"read_parquet('{_literal_sql(caminho)}')"
        agregados = ["COUNT(*)"]
        for coluna in colunas:
            identificador = _identificador_sql(coluna["nome"])
            agregados.append(f"COUNT({identificador})")
            agregados.append(f"COUNT(DISTINCT {identificador})")

        medidas = con.execute(
            f"SELECT {', '.join(agregados)} FROM {fonte}"
        ).fetchone()

        total = int(medidas[0])
        for indice, coluna in enumerate(colunas):
            preenchidas = int(medidas[1 + indice * 2])
            coluna["preenchidas"] = preenchidas
            coluna["distintos"] = int(medidas[2 + indice * 2])
            coluna["preenchimento"] = round(preenchidas / total, 4) if total else 0.0

        return {"arquivo": arquivo, "total": total, "colunas": colunas}

    except Exception as e:
        logger.error(f"Erro ao descrever o conjunto {arquivo}: {e}")
        return None
    finally:
        con.close()