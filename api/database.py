# api/database.py
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple, Union
import duckdb
import pandas as pd

# A medição das colunas mora em `core/` porque tem dois consumidores: a
# exportação, que grava o `_dictionary.json` ao lado de cada Parquet, e esta
# API, que responde o `/conjuntos/colunas`. Os primitivos de escape e de
# rótulo vêm de lá pelo mesmo motivo — o nome de coluna que a grade consulta
# tem de ser o mesmo que o dicionário mede.
from core.dictionary_generator import (
    COLUNAS_INJETADAS,
    RESSALVA,
    colunas_do_parquet,
    identificador_sql as _identificador_sql,
    literal_sql as _literal_sql,
    medir_colunas,
    rotulo_da_coluna,
)

logger = logging.getLogger("api.database")


# O cache de rodapé dos Parquet é o que paga a maior parte do custo de ler o
# acervo: são milhares de arquivos pequenos, e abrir cada um para descobrir
# seu esquema custa mais que ler as linhas que interessam. Esse cache mora no
# banco, não na conexão — então um banco em memória novo por chamada, como
# havia aqui, jogava fora a cada requisição o que a anterior tinha aprendido.
_CACHE_DE_METADADOS = "SET parquet_metadata_cache=true"

_banco = None
_trava_do_banco = threading.Lock()


def _liga_o_cache(con) -> None:
    """Liga o cache de metadados, avisando se o DuckDB não o conhecer.

    O nome da configuração é do DuckDB, não nosso: uma versão que o renomeie
    faria o `SET` levantar, e aí a consulta cairia por causa de uma
    otimização — o acervo ainda é legível sem ela, só mais devagar.
    """
    try:
        con.execute(_CACHE_DE_METADADOS)
    except Exception as e:
        logger.warning(f"Cache de metadados do Parquet indisponível: {e}")


def get_db_connection():
    """Uma conexão sobre o banco em memória do processo.

    É um cursor, e não o banco em si, porque os endpoints síncronos do
    FastAPI correm em threads diferentes: cada um precisa da sua sessão. O
    que eles compartilham é o banco — e com ele o rodapé dos Parquet já
    lido. Quem chama fecha a sua conexão como antes; o banco fica.

    O `SET` repete a cada cursor porque a configuração é por conexão, ainda
    que o cache que ela liga seja do banco.
    """
    global _banco

    with _trava_do_banco:
        if _banco is None:
            _banco = duckdb.connect(database=":memory:")
            _liga_o_cache(_banco)

    con = _banco.cursor()
    _liga_o_cache(con)
    return con


def leitura_do_acervo(parquet_glob: str) -> str:
    """A expressão que lê o acervo inteiro como uma tabela só.

    Mora numa função porque são seis consultas em três módulos lendo o mesmo
    acervo, e as opções têm de ser as mesmas em todas: `hive_partitioning`
    traz tema, entidade, tipo, ano e UF dos nomes de pasta, e
    `union_by_name` casa pelo nome colunas que não estão na mesma posição em
    todos os arquivos. Uma consulta que esquecesse uma das duas leria um
    acervo diferente do que as outras leem.
    """
    return (
        f"read_parquet('{_literal_sql(parquet_glob)}', "
        "hive_partitioning=1, union_by_name=True)"
    )


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


# O esquema unificado custa de 0,3 a 0,5 s para montar — é o rodapé dos
# milhares de Parquet do acervo sendo lido —, e toda consulta filtrada da API
# passa por aqui. O cache guarda o resultado até que os arquivos mudem, pela
# mesma assinatura do `lista_conjuntos`: contagem e data de modificação mais
# recente, que uma coleta nova altera. Caminhar a árvore para conferir isso
# custa uns 60 ms, uma fração do que custa a leitura que ele evita.
_cache_esquema: Dict[str, Tuple[Any, set]] = {}


def _assinatura_do_acervo(parquet_glob: str) -> Tuple[int, float]:
    """Quantos Parquet há sob o glob e quando o mais recente mudou."""
    raiz = parquet_glob.split("/**/", 1)[0]
    total = 0
    mais_recente = 0.0

    for pasta, _, arquivos in os.walk(raiz):
        for nome in arquivos:
            if not nome.endswith(".parquet"):
                continue
            total += 1
            try:
                mais_recente = max(
                    mais_recente, os.path.getmtime(os.path.join(pasta, nome))
                )
            except OSError:
                continue

    return total, mais_recente


def colunas_do_esquema(con, parquet_glob: str) -> set:
    """Nomes de coluna do esquema unificado de todos os Parquet do acervo.

    Nem toda coluna existe em toda partição: `titulo` e `tipo_arquivo` só
    aparecem nos catálogos de documento e de planilha. Como o `union_by_name`
    monta o esquema a partir do glob inteiro, basta uma partição de catálogo
    para a coluna existir — mas num acervo que só tenha conteúdo tabular ela
    não existe, e citá-la derrubaria a consulta inteira em vez de só ignorar o
    filtro.

    O resultado fica em cache até o acervo mudar — ver `_cache_esquema`. A
    falha não fica: um erro de leitura transitório não deve calar o aviso nem
    congelar um esquema vazio.
    """
    assinatura = _assinatura_do_acervo(parquet_glob)
    em_cache = _cache_esquema.get(parquet_glob)
    if em_cache is not None and em_cache[0] == assinatura:
        return em_cache[1]

    try:
        describe = con.execute(
            f"DESCRIBE SELECT * FROM {leitura_do_acervo(parquet_glob)}"
        ).fetchall()
    except Exception as e:
        logger.warning(f"Não foi possível ler o esquema dos Parquet: {e}")
        return set()

    colunas = {linha[0] for linha in describe}
    _cache_esquema[parquet_glob] = (assinatura, colunas)
    return colunas


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
    #
    # O esquema é lido só se algum filtro depender dele. A consulta que
    # filtra apenas por partição Hive — o caso comum do portal — não precisa
    # dele para nada, e lê-lo à toa era pagar a varredura do acervo duas
    # vezes: uma para saber que colunas existem, outra para consultá-las.
    esquema: Optional[set] = None

    def esquema_do_acervo() -> set:
        nonlocal esquema
        if esquema is None:
            esquema = colunas_do_esquema(con, parquet_glob)
        return esquema

    for coluna, valor in (
        ("entidade", entidade),
        ("tipo_arquivo", tipo_arquivo),
        ("ativo", ativo),
        ("estruturado", estruturado),
    ):
        valores = parse_multiselect(valor)
        if not valores or coluna not in esquema_do_acervo():
            continue
        placeholders = ", ".join(["?"] * len(valores))
        where_clauses.append(
            f"UPPER(CAST({coluna} AS VARCHAR)) IN ({placeholders})"
        )
        params.extend([v.upper() for v in valores])

    # 💡 3. BUSCA TEXTUAL ABRANGENTE
    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        colunas = [c for c in COLUNAS_DE_BUSCA if c in esquema_do_acervo()] or ["tema"]
        where_clauses.append(
            "("
            + " OR ".join(
                f"LOWER(CAST({coluna} AS VARCHAR)) LIKE ?" for coluna in colunas
            )
            + ")"
        )
        params.extend([term] * len(colunas))

    return ("WHERE " + " AND ".join(where_clauses)) if where_clauses else "", params


# O que conta como vazio depois do `strip`. É constante de módulo porque a
# poda do `execute_parquet_query` usa o mesmo conjunto célula a célula: duas
# listas do que é "vazio" divergiriam, e aí a mesma célula sairia do catálogo
# e ficaria na exportação.
VAZIOS = frozenset({"", "null", "None"})


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
    return str(valor).strip() in VAZIOS


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

    O agrupamento é pela posição, e não pelo apelido `valor`. O `union_by_name`
    junta o esquema de todo o acervo, e basta um arquivo com uma coluna
    chamada `valor` — o balanço patrimonial da ABDI tem uma — para o `GROUP BY
    valor` passar a casar com a coluna real em vez do apelido. A consulta
    inteira cai então por `entidade` não estar no agrupamento, e o portal
    perde todos os filtros de uma vez.
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
            FROM {leitura_do_acervo(parquet_glob)}
            {where_str}
            GROUP BY 1
            HAVING CAST({por} AS VARCHAR) IS NOT NULL
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

    fonte = leitura_do_acervo(parquet_glob)

    try:
        # 1. Total de linhas
        count_query = f"""
            SELECT COUNT(*) 
            FROM {fonte}
            {where_str}
        """
        total = con.execute(count_query, params).fetchone()[0]

        if total == 0:
            return [], 0

        # 2. Dados paginados
        data_query = f"""
            SELECT * 
            FROM {fonte}
            {where_str}
            LIMIT {limit} OFFSET {offset}
        """
        df = con.execute(data_query, params).df()

        # 3. Poda dos campos vazios, coluna a coluna e não célula a célula.
        #
        # O esquema unificado tem quase quatrocentas colunas e a exportação
        # leva cem mil linhas — são milhões de células, e quase todas vazias:
        # uma linha de catálogo não tem o que dizer sobre as colunas que
        # vieram das planilhas. O laço de antes limpava o nome da coluna e
        # chamava `pd.isna` uma vez por célula, repetindo por linha um
        # trabalho que é da coluna.
        #
        # Aqui o nome da coluna é limpo uma vez, o teste de nulo sai
        # vetorizado e o de texto vazio corre só nas colunas de texto — são
        # as únicas em que `str(valor)` pode dar "", "null" ou "None", que é
        # o que `esta_vazio` procura depois do nulo. O `tolist` devolve os
        # valores em tipo nativo do Python, como o `to_dict` devolvia.
        cleaned_records: List[Dict[str, Any]] = [{} for _ in range(len(df))]

        for coluna in df.columns:
            if "rtf1" in str(coluna):
                continue

            key_clean = str(coluna).replace("ï»¿", "").replace('"', "").strip()
            serie = df[coluna]
            nulos = serie.isna().to_numpy()
            valores = serie.tolist()
            e_texto = serie.dtype == object

            for indice, record_limpo in enumerate(cleaned_records):
                if nulos[indice]:
                    continue
                valor = valores[indice]
                if e_texto and str(valor).strip() in VAZIOS:
                    continue
                record_limpo[key_clean] = valor

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

# As COLUNAS_INJETADAS vêm do core: o `export_to_parquet` grava estas cinco
# dentro de cada arquivo, além de usá-las como diretório Hive. Dentro de um
# conjunto elas são constantes — a mesma palavra repetida em todas as linhas
# —, então ficam fora da grade e aparecem uma vez só, no cabeçalho.

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
    `parquet_file_metadata` e `parquet_schema` respondem por todos eles de
    uma vez, sem ler uma linha sequer. Contar com um `COUNT(*)` por arquivo
    levaria minutos.

    A lista de arquivos vai explícita, em vez de um `**/*.parquet`. São duas
    economias: expandir o glob custa mais que ler os rodapés — ele percorre a
    árvore inteira a cada consulta, e são duas —, e quase metade do acervo é
    catálogo, cujos arquivos o `_varre_conjuntos` já descartou e cujas
    medidas seriam lidas para serem jogadas fora.
    """
    if not conjuntos:
        return

    por_arquivo = {c["arquivo"]: c for c in conjuntos}
    arquivos = ", ".join(
        f"'{_literal_sql(os.path.join(raiz, c['arquivo']))}'" for c in conjuntos
    )
    fonte = f"[{arquivos}]"

    def relativo(caminho: str) -> str:
        return os.path.relpath(caminho, raiz).replace("\\", "/")

    try:
        for caminho, linhas in con.execute(
            f"SELECT file_name, num_rows FROM parquet_file_metadata({fonte})"
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
            FROM parquet_schema({fonte})
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


def colunas_do_conjunto(con, caminho: str) -> List[Dict[str, str]]:
    """Nome, rótulo e tipo das colunas de um conjunto, na ordem publicada.

    Sem as cinco que o pipeline injeta: repetir "tema" em toda linha de uma
    grade é gastar uma coluna para dizer o que o cabeçalho já diz. Quem faz a
    leitura é o `core`, que é de onde sai também o dicionário — assim a grade
    e a documentação enxergam exatamente o mesmo conjunto de colunas.
    """
    return colunas_do_parquet(con, caminho)


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

        predicado, params = "", []
        if search and search.strip():
            termo = f"%{search.strip().lower()}%"
            predicado = " OR ".join(
                f"LOWER(CAST({_identificador_sql(nome)} AS VARCHAR)) LIKE ?"
                for nome in nomes
            )
            params = [termo] * len(nomes)

        where = f"WHERE {predicado}" if predicado else ""

        # Só ordena por coluna que este arquivo tem: o nome entra cru no SQL,
        # e ORDER BY não aceita parâmetro ligado.
        ordem = ""
        if ordenar in nomes:
            sentido = "DESC" if str(direcao).lower() == "desc" else "ASC"
            ordem = f"ORDER BY {_identificador_sql(ordenar)} {sentido} NULLS LAST"

        # O total e o filtrado saem da mesma passada: o `FILTER` conta as
        # linhas que casam com a busca sem uma segunda leitura do arquivo.
        # Sem busca não há o que contar — o `COUNT(*)` sozinho vem do rodapé
        # do Parquet, sem ler linha nenhuma.
        if predicado:
            total, filtrado = con.execute(
                f"SELECT COUNT(*), COUNT(*) FILTER (WHERE {predicado}) FROM {fonte}",
                params,
            ).fetchone()
        else:
            total = con.execute(f"SELECT COUNT(*) FROM {fonte}").fetchone()[0]
            filtrado = total

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
    mede nela.

    Quem mede é o `core.dictionary_generator`, o mesmo que grava o
    `_dictionary.json` ao lado de cada Parquet na exportação. Aqui só se
    acrescenta o nome do arquivo e a ressalva, que são de apresentação.
    """
    caminho = caminho_do_conjunto(base_dir, arquivo)
    if caminho is None:
        return None

    con = get_db_connection()
    try:
        medida = medir_colunas(caminho, con=con)
        if medida is None:
            return None

        return {"arquivo": arquivo, "ressalva": RESSALVA, **medida}

    except Exception as e:
        logger.error(f"Erro ao descrever o conjunto {arquivo}: {e}")
        return None
    finally:
        con.close()