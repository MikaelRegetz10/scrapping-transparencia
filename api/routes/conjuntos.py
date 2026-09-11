# api/routes/conjuntos.py
"""Leitura de um conjunto de dados por vez, para a visualização em planilha.

O recurso `/documentos` responde pelo acervo inteiro: que links existem, o que
a auditoria apurou de cada um. Ele é o catálogo. Este responde pelo conteúdo —
as linhas de dentro de uma planilha coletada, com as colunas que ela tem, na
ordem em que a entidade as publicou.

São dois recursos porque são duas perguntas. A do catálogo se faz sobre o
acervo todo de uma vez; a daqui, sobre um arquivo só. Misturá-las custaria a
cada consulta de célula a união dos milhares de Parquet do acervo.
"""

import io
import logging
import math
import re
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.database import (
    conjunto_como_dataframe,
    dicionario_do_conjunto,
    le_conjunto,
    lista_conjuntos,
    parse_multiselect,
)
from api.schemas import ConjuntosResponse, DicionarioResponse, GradeResponse
from core.parquet_exporter import remover_acentos

logger = logging.getLogger("api.conjuntos")

router = APIRouter(prefix="/api/v1/conjuntos", tags=["Conjuntos de Dados"])

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# Teto de linhas por página da grade. A visualização pagina de verdade — não é
# uma tabela inteira escondida no navegador —, e mil células de largura já é
# mais do que qualquer tela mostra de uma vez.
MAX_POR_PAGINA = 1000

# Teto da exportação. Nenhum conjunto do acervo chega perto disso; existe para
# que um acervo futuro maior falhe devolvendo demais, e não travando.
MAX_EXPORTACAO = 200000

# Campos do conjunto que a busca textual varre.
CAMPOS_DE_BUSCA = ("nome", "tema", "tipo_documento", "entidade", "uf", "ano")


def _texto_de_busca(conjunto: dict) -> str:
    """Tudo o que identifica um conjunto, numa linha comparável.

    Os nomes vêm do disco, e no disco são caminhos: sem acento e com
    underline no lugar do espaço. Quem procura digita "corpo técnico" —
    com espaço e com acento —, e sem esta normalização não acharia
    `senar_corpo_tecnico_...`.
    """
    partes = " ".join(str(conjunto[campo]) for campo in CAMPOS_DE_BUSCA)
    return remover_acentos(partes.replace("_", " ").lower())


def _procura(termo: str, conjuntos: list) -> list:
    """Os conjuntos em que todas as palavras do termo aparecem.

    Palavra a palavra, e não a frase inteira: "contratos bahia" descreve bem
    um conjunto cujo nome traz as duas separadas por meia dúzia de outras.

    A pontuação é descartada em vez de procurada. O termo costuma chegar de um
    título do catálogo — "Licitações (últimos 5 anos)" —, e exigir que os
    parênteses também constem do nome sanitizado não acharia nada.
    """
    palavras = re.findall(r"[a-z0-9]+", remover_acentos(termo.lower()))
    if not palavras:
        return conjuntos

    return [
        conjunto
        for conjunto in conjuntos
        if all(palavra in _texto_de_busca(conjunto) for palavra in palavras)
    ]


def _combina(valor: Optional[str], conjunto_valor: str) -> bool:
    """Se o valor do conjunto está entre os pedidos (vazio = todos)."""
    escolhidos = parse_multiselect(valor)
    if not escolhidos:
        return True
    return str(conjunto_valor).lower() in {e.lower() for e in escolhidos}


@router.get("", response_model=ConjuntosResponse)
def get_conjuntos(
    search: Optional[str] = Query(
        None, description="Busca no nome do conjunto, tema, tipo, entidade e UF"
    ),
    tema: Optional[str] = Query(None, description="Filtrar por tema(s)"),
    entidade: Optional[str] = Query(None, description="Filtrar por entidade(s)"),
    tipo_documento: Optional[str] = Query(None, description="Filtrar por tipo(s)"),
    ano: Optional[str] = Query(None, description="Filtrar por ano(s)"),
    uf: Optional[str] = Query(None, description="Filtrar por UF(s)"),
    ordenar: str = Query(
        "nome",
        pattern="^(nome|linhas|entidade|tema)$",
        description="Campo de ordenação da lista",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
    output_dir: str = "outputs",
):
    """[RESTful] Coleção dos conjuntos de dados abríveis em planilha.

    Cada item é o conteúdo de uma planilha que passou pelo profiler. Os dois
    temas de catálogo — `documentos` e `planilhas` — ficam de fora: são
    inventário de link, e as páginas de catálogo os mostram melhor.
    """
    conjuntos = lista_conjuntos(base_dir=output_dir)

    if termo := (search or "").strip():
        conjuntos = _procura(termo, conjuntos)

    for campo, valor in (
        ("tema", tema),
        ("entidade", entidade),
        ("tipo_documento", tipo_documento),
        ("ano", ano),
        ("uf", uf),
    ):
        conjuntos = [c for c in conjuntos if _combina(valor, c[campo])]

    if ordenar == "linhas":
        conjuntos = sorted(conjuntos, key=lambda c: c["linhas"] or 0, reverse=True)
    elif ordenar in ("entidade", "tema"):
        conjuntos = sorted(conjuntos, key=lambda c: (c[ordenar], c["nome"]))

    total = len(conjuntos)
    inicio = (page - 1) * page_size

    return ConjuntosResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0,
        data=conjuntos[inicio : inicio + page_size],
    )


@router.get("/dados", response_model=GradeResponse)
def get_dados(
    arquivo: str = Query(
        ...,
        description="Conjunto a abrir, no caminho relativo que a listagem devolve",
    ),
    search: Optional[str] = Query(
        None, description="Filtra as linhas em que o termo aparece em qualquer coluna"
    ),
    ordenar: Optional[str] = Query(
        None, description="Coluna de ordenação; ignorada se o conjunto não a tiver"
    ),
    direcao: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=MAX_POR_PAGINA),
    output_dir: str = "outputs",
):
    """[RESTful] Uma fatia de um conjunto: as células que a grade desenha."""
    conjunto = le_conjunto(
        base_dir=output_dir,
        arquivo=arquivo,
        search=search,
        ordenar=ordenar,
        direcao=direcao,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    if conjunto is None:
        raise HTTPException(
            status_code=404,
            detail="Conjunto não encontrado no acervo.",
        )

    filtrado = conjunto["total_filtrado"]
    return GradeResponse(
        **conjunto,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(filtrado / page_size) if filtrado else 0,
    )


@router.get("/colunas", response_model=DicionarioResponse)
def get_colunas(
    arquivo: str = Query(..., description="Conjunto a descrever"),
    output_dir: str = "outputs",
):
    """[RESTful] O que se sabe das colunas de um conjunto.

    Os portais do Sistema S não publicam dicionário de dados; o que dá para
    dizer de uma coluna é o que se mede nela — tipo, quanto vem preenchido e
    quantos valores distintos tem.
    """
    dicionario = dicionario_do_conjunto(base_dir=output_dir, arquivo=arquivo)

    if dicionario is None:
        raise HTTPException(
            status_code=404,
            detail="Conjunto não encontrado no acervo.",
        )

    return DicionarioResponse(**dicionario)


@router.get("/exportacao")
def exportar_conjunto(
    arquivo: str = Query(..., description="Conjunto a exportar"),
    search: Optional[str] = Query(None, description="Mesma busca aplicada na grade"),
    ordenar: Optional[str] = Query(None, description="Mesma ordenação da grade"),
    direcao: str = Query("asc", pattern="^(asc|desc)$"),
    formato: str = Query(
        "csv",
        pattern="^(csv|xlsx|json)$",
        description="Formato de representação do conjunto: csv, xlsx ou json",
    ),
    output_dir: str = "outputs",
):
    """[RESTful] O conjunto inteiro, no formato pedido.

    Sem paginação e com a busca e a ordenação da tela: o arquivo que sai é o
    que a pessoa estava vendo, inteiro.
    """
    df = conjunto_como_dataframe(
        base_dir=output_dir,
        arquivo=arquivo,
        search=search,
        ordenar=ordenar,
        direcao=direcao,
        limit=MAX_EXPORTACAO,
    )

    if df is None:
        raise HTTPException(
            status_code=404,
            detail="Conjunto não encontrado no acervo.",
        )

    # O nome do arquivo vai para um cabeçalho HTTP. Os nomes do acervo já
    # saem sanitizados do `export_to_parquet`, mas quem monta o cabeçalho é
    # quem responde por ele: fora de [A-Za-z0-9._-] nada passa.
    nome_base = re.sub(
        r"[^A-Za-z0-9._-]", "_", arquivo.rsplit("/", 1)[-1][: -len(".parquet")]
    ) or "conjunto"

    if formato == "csv":
        stream = io.StringIO()
        df.to_csv(stream, index=False, sep=";")
        resposta = StreamingResponse(
            # O BOM é o que faz o Excel em português abrir o arquivo em UTF-8;
            # sem ele os acentos chegam quebrados na planilha.
            iter(["\ufeff" + stream.getvalue()]),
            media_type="text/csv; charset=utf-8",
        )

    elif formato == "xlsx":
        saida = io.BytesIO()
        with pd.ExcelWriter(saida, engine="openpyxl") as writer:
            # O nome da aba tem teto de 31 caracteres no formato, e os nomes
            # dos conjuntos passam disso com folga.
            df.to_excel(writer, index=False, sheet_name=nome_base[:31])
        saida.seek(0)
        resposta = StreamingResponse(saida, media_type=XLSX_MEDIA_TYPE)

    else:
        corpo = df.to_json(orient="records", force_ascii=False, indent=2)
        resposta = StreamingResponse(
            io.BytesIO(corpo.encode("utf-8")),
            media_type="application/json",
        )

    resposta.headers["Content-Disposition"] = (
        f'attachment; filename="{nome_base}.{formato}"'
    )
    return resposta
