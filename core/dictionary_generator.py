# core/dictionary_generator.py
"""A documentação que o acervo consegue dar de si.

As entidades do Sistema S não publicam dicionário de dados junto com os
arquivos: vem a planilha, não vem o que cada coluna significa. O que dá para
dizer de uma coluna, então, é o que se mede nela — o tipo que o Parquet
guardou, quanto dela vem preenchido e quantos valores distintos tem.

Isto mora em `core/` e não em `api/` porque tem dois consumidores. O
`parquet_exporter` grava o resultado como um `_dictionary.json` ao lado de
cada arquivo, na hora da exportação, para quem consome o Parquet direto sem
passar pelo portal. E o `api/database.py` chama a mesma função para responder
o `/api/v1/conjuntos/colunas`, que é o que a página de dicionário lê.

São duas entregas da mesma medida, e é de propósito que seja uma função só:
duas implementações da mesma estatística divergem, e um acervo que se
descreve de dois jeitos diferentes não se descreve.
"""

from typing import Any, Dict, List, Optional

import duckdb

# O `export_to_parquet` grava estas cinco dentro de cada arquivo, além de
# usá-las como diretório Hive. Dentro de um conjunto são constantes — a mesma
# palavra repetida em toda linha —, e medi-las diria de nós, não da fonte:
# "100% preenchida, 1 valor distinto" em `tema` é sobre o nosso pipeline.
COLUNAS_INJETADAS = ("tema", "tipo_documento", "ano", "uf", "entidade")

RESSALVA = (
    "As entidades não publicam dicionário de dados junto com os arquivos. "
    "O que está aqui foi medido no próprio conjunto: nada disto é descrição "
    "oficial da coluna."
)


def literal_sql(texto: str) -> str:
    """Escapa uma string para entrar como literal no SQL.

    O caminho do arquivo não pode ir como parâmetro ligado: `read_parquet`
    exige o nome em tempo de planejamento.
    """
    return str(texto).replace("'", "''")


def identificador_sql(nome: str) -> str:
    """Escapa um nome de coluna para uso em SELECT e ORDER BY.

    Os nomes vêm das planilhas das entidades e trazem de tudo: acento, `º`,
    espaço, aspas. Só entram na consulta nomes que o esquema do próprio
    arquivo confirmou existir.
    """
    return '"' + str(nome).replace('"', '""') + '"'


def rotulo_da_coluna(nome: str) -> str:
    """O nome de uma coluna como se mostra a alguém.

    Boa parte dos CSV do Sistema S sai com BOM e com o cabeçalho entre aspas,
    e os dois foram parar dentro do Parquet como parte do nome da primeira
    coluna — `ï»¿"MEMBROS DO CORPO TÉCNICO"`. A limpeza não pode substituir o
    nome, que é o que identifica a coluna no arquivo e na consulta. Por isso
    são dois campos: `nome` consulta, `rotulo` aparece.
    """
    limpo = (
        str(nome).replace("﻿", "").replace("ï»¿", "").replace('"', "").strip()
    )
    return limpo or str(nome)


def letra_da_coluna(indice: int) -> str:
    """A posição da coluna na notação de planilha (A, B, … Z, AA, AB).

    Quem abre o arquivo no Excel para conferir o que o portal diz encontra a
    coluna por esta letra, não pelo índice.
    """
    letras = ""
    while indice >= 0:
        letras = chr(indice % 26 + 65) + letras
        indice = indice // 26 - 1
    return letras


def tipo_legivel(tipo_duckdb: str) -> str:
    """O tipo interno do DuckDB reduzido às famílias que interessam mostrar.

    `DECIMAL(18,2)` e `DOUBLE` são a mesma informação para quem lê a página —
    é número com casa decimal —, e `VARCHAR` cobre o texto todo. A largura e a
    precisão só apareceriam para não dizer nada.
    """
    tipo = str(tipo_duckdb).upper()
    if "VARCHAR" in tipo or "STRING" in tipo or "CHAR" in tipo:
        return "VARCHAR"
    if "DOUBLE" in tipo or "FLOAT" in tipo or "DECIMAL" in tipo or "REAL" in tipo:
        return "DOUBLE"
    if "INT" in tipo:
        return "INTEGER"
    if "DATE" in tipo or "TIMESTAMP" in tipo or "TIME" in tipo:
        return "DATE/TIME"
    if "BOOL" in tipo:
        return "BOOLEAN"
    return tipo


def colunas_do_parquet(con, caminho: str) -> List[Dict[str, str]]:
    """Nome, rótulo e tipo das colunas de um conjunto, na ordem publicada.

    Sem as cinco que o pipeline injeta — ver `COLUNAS_INJETADAS`.
    """
    descricao = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{literal_sql(caminho)}')"
    ).fetchall()

    return [
        {
            "nome": linha[0],
            "rotulo": rotulo_da_coluna(linha[0]),
            "tipo": tipo_legivel(linha[1]),
            "tipo_bruto": str(linha[1]),
        }
        for linha in descricao
        if linha[0] not in COLUNAS_INJETADAS
    ]


def medir_colunas(
    caminho: str, con=None, colunas: Optional[List[Dict[str, str]]] = None
) -> Optional[Dict[str, Any]]:
    """Tipo, preenchimento e variedade de cada coluna de um Parquet.

    Vai numa consulta só — um par de agregados por coluna, uma passada pelo
    arquivo. Medir coluna a coluna seria uma leitura completa por coluna, e os
    conjuntos maiores do acervo passam de cem mil linhas.

    `con` e `colunas` existem para quem já tem os dois em mãos: a API reaproveita
    a conexão aberta e o esquema que já leu, e não paga um `DESCRIBE` a mais.
    """
    proprio = con is None
    con = con or duckdb.connect(database=":memory:")

    try:
        if colunas is None:
            colunas = colunas_do_parquet(con, caminho)
        if not colunas:
            return None

        fonte = f"read_parquet('{literal_sql(caminho)}')"
        agregados = ["COUNT(*)"]
        for coluna in colunas:
            identificador = identificador_sql(coluna["nome"])
            agregados.append(f"COUNT({identificador})")
            agregados.append(f"COUNT(DISTINCT {identificador})")

        medidas = con.execute(
            f"SELECT {', '.join(agregados)} FROM {fonte}"
        ).fetchone()

        total = int(medidas[0])
        for indice, coluna in enumerate(colunas):
            preenchidas = int(medidas[1 + indice * 2])
            coluna["letra"] = letra_da_coluna(indice)
            coluna["preenchidas"] = preenchidas
            coluna["distintos"] = int(medidas[2 + indice * 2])
            coluna["preenchimento"] = (
                round(preenchidas / total, 4) if total else 0.0
            )

        return {"total": total, "colunas": colunas}

    finally:
        if proprio:
            con.close()


def generate_data_dictionary(parquet_path: str) -> Dict[str, Any]:
    """O dicionário de um arquivo, no formato que vai para o `_dictionary.json`.

    É o mesmo conteúdo que a API serve — `medir_colunas` é a medida única —,
    empacotado com a ressalva que precisa acompanhá-lo onde quer que ele vá.
    O JSON viaja junto do Parquet, longe do portal que explicaria de onde
    vieram estes números; sem a ressalva, ele se parece com um dicionário
    oficial da entidade, e não é.
    """
    medida = medir_colunas(parquet_path)

    if medida is None:
        return {"ressalva": RESSALVA, "total": 0, "colunas": []}

    return {"ressalva": RESSALVA, **medida}
