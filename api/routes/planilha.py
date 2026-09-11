# api/routes/planilha.py
"""Representação em Excel de um item do catálogo de planilhas.

Boa parte dos links tabulares do Sistema S não aponta para um arquivo: aponta
para um endpoint de API que devolve JSON — 132 dos itens do catálogo, entre
SESC, SENAI e SESI. Clicar num deles no portal abria o JSON cru numa aba do
navegador, que é a forma menos legível possível de um dado que já é tabular.

Aqui o link é buscado na origem, desempacotado com o mesmo achatador que o
profiler usa durante a coleta (`flatten_nested_json_to_df`) e devolvido como
.xlsx. O que o portal entrega no clique passa a ser uma planilha, e não uma
página de JSON.

A conversão é feita ao vivo, contra a fonte, e não contra o acervo: o Parquet
guarda o dado no recorte que o pipeline decidiu (colunas podadas, linhas de
totais removidas, partição por tema), enquanto quem clica no item quer o que
a entidade publica hoje, inteiro.
"""
import io
import logging
import re
import unicodedata
from typing import Optional
from urllib.parse import urlparse

import pandas as pd
import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.database import execute_parquet_query
from core.pipeline import limpa_caracteres_ilegais
from core.profiler import flatten_nested_json_to_df
from core.validator import DEFAULT_HEADERS

logger = logging.getLogger("api.planilha")

# 💡 REST: a planilha de um item pertence ao domínio de /documentos, como a
# exportação da coleção inteira.
router = APIRouter(prefix="/api/v1/documentos", tags=["Documentos & Auditoria"])

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# Formatos que sabemos transformar em planilha. Os demais (pdf, zip, docx…) o
# portal continua servindo direto da fonte — converter um .zip para Excel não
# quer dizer nada.
FORMATOS_CONVERSIVEIS = frozenset({"json", "csv", "xlsx", "xls"})

# Teto do que aceitamos baixar para converter. A conversão passa pela memória
# do processo: sem limite, um único link grande derruba a API para todo mundo.
MAX_BYTES = 60 * 1024 * 1024

TIMEOUT_ORIGEM = 40

# Aba única do arquivo gerado. O Excel recusa >31 caracteres e os símbolos de
# `sanitize_sheet_name`; um nome fixo evita ter de higienizar título nenhum.
NOME_DA_ABA = "Dados"


def _baixa_com_teto(url: str) -> bytes:
    """Baixa a URL recusando corpos acima de `MAX_BYTES`.

    O `Content-Length` é dica, não garantia — servidor nenhum é obrigado a
    mandá-lo, e o do SESC não manda. Por isso o teto é conferido de novo
    enquanto os pedaços chegam, e não só no cabeçalho.
    """
    resposta = requests.get(
        url,
        headers=DEFAULT_HEADERS,
        timeout=TIMEOUT_ORIGEM,
        stream=True,
        allow_redirects=True,
    )
    resposta.raise_for_status()

    declarado = resposta.headers.get("Content-Length")
    if declarado and declarado.isdigit() and int(declarado) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                "O arquivo na origem é grande demais para converter "
                f"({int(declarado) // (1024 * 1024)} MB). Baixe direto da fonte."
            ),
        )

    corpo = bytearray()
    for pedaco in resposta.iter_content(chunk_size=64 * 1024):
        corpo.extend(pedaco)
        if len(corpo) > MAX_BYTES:
            resposta.close()
            raise HTTPException(
                status_code=413,
                detail=(
                    "O arquivo na origem passou do limite de conversão "
                    f"({MAX_BYTES // (1024 * 1024)} MB). Baixe direto da fonte."
                ),
            )

    return bytes(corpo)


def _le_csv(conteudo: bytes) -> pd.DataFrame:
    """Lê o CSV tentando os encodings e separadores que os portais usam.

    Mesma varredura do `analyze_dataset_quality`: os arquivos vêm em latin1
    tanto quanto em utf-8, e o separador é `;` com a mesma frequência que `,`.
    Só aceita a leitura que produziu mais de uma coluna — uma coluna só quase
    sempre é o separador errado, com a linha inteira num campo.
    """
    ultimo_erro: Optional[Exception] = None
    candidato: Optional[pd.DataFrame] = None

    for encoding in ("utf-8", "latin1", "iso-8859-1"):
        for separador in (";", ",", "\t"):
            try:
                df = pd.read_csv(
                    io.BytesIO(conteudo), encoding=encoding, sep=separador
                )
            except Exception as erro:
                ultimo_erro = erro
                continue

            if len(df.columns) > 1:
                return df
            if candidato is None:
                candidato = df

    if candidato is not None:
        return candidato

    raise HTTPException(
        status_code=422,
        detail=f"Não foi possível ler o CSV publicado na origem: {ultimo_erro}",
    )


def _tira_prefixo_do_envelope(df: pd.DataFrame) -> pd.DataFrame:
    """Remove o nome do envelope do começo de toda coluna, quando há um.

    A API do SESC devolve as linhas dentro de `registros`, e o achatador as
    entrega como `registros.ANO`, `registros.ORCADO`… O prefixo não distingue
    nada — está em todas as colunas — e só atrapalha quem abre a planilha.

    Só sai quando todas as colunas o compartilham e o corte não gera nome
    repetido: em qualquer outro caso o prefixo é a informação que diz de qual
    ramo do JSON veio cada coluna.
    """
    colunas = [str(c) for c in df.columns]
    if len(colunas) < 2 or not all("." in c for c in colunas):
        return df

    prefixos = {c.split(".", 1)[0] for c in colunas}
    if len(prefixos) != 1:
        return df

    curtas = [c.split(".", 1)[1] for c in colunas]
    if len(set(curtas)) != len(curtas) or not all(curtas):
        return df

    return df.set_axis(curtas, axis="columns")


def _para_dataframe(conteudo: bytes, formato: str) -> pd.DataFrame:
    """Converte o corpo baixado num DataFrame, conforme o formato catalogado."""
    if formato == "json":
        try:
            df = flatten_nested_json_to_df(conteudo.decode("utf-8"))
        except UnicodeDecodeError:
            df = flatten_nested_json_to_df(conteudo.decode("latin1"))

        if df.empty:
            raise HTTPException(
                status_code=422,
                detail=(
                    "A API de origem respondeu, mas sem registros para montar "
                    "uma planilha."
                ),
            )
        return _tira_prefixo_do_envelope(df)

    if formato == "csv":
        return _le_csv(conteudo)

    # xlsx/xls: já é planilha, mas passa pelo pandas mesmo assim para sair
    # daqui sempre no formato novo — o Excel de hoje reclama de .xls antigo.
    try:
        return pd.read_excel(io.BytesIO(conteudo))
    except Exception as erro:
        raise HTTPException(
            status_code=422,
            detail=f"Não foi possível ler a planilha publicada na origem: {erro}",
        )


def _nome_do_arquivo(item: dict, url: str) -> str:
    """Nome do .xlsx entregue, tirado do título do item.

    Sem acento e sem símbolo: o cabeçalho `Content-Disposition` é ASCII, e o
    Windows recusa `/` e `:` em nome de arquivo — os dois aparecem nos títulos
    do catálogo ("Plano de Contas / Demonstrativo").

    O `re.ASCII` fecha a saída em `[A-Za-z0-9_-]`. Sem ele o `\\w` do Python
    aceita letra Unicode, e o que a decomposição não separou em letra + acento
    passaria inteiro para um cabeçalho que não comporta byte alto.
    """
    bruto = str(item.get("titulo") or item.get("nome_arquivo") or "").strip()
    if not bruto:
        bruto = urlparse(url).path.rsplit("/", 1)[-1] or "planilha"

    sem_acento = "".join(
        c
        for c in unicodedata.normalize("NFKD", bruto)
        if not unicodedata.combining(c)
    )
    limpo = re.sub(r"[^\w\-]+", "_", sem_acento, flags=re.ASCII).strip("_")[:80]

    return f"{limpo or 'planilha'}.xlsx"


def _localiza_no_catalogo(url: str, output_dir: str) -> dict:
    """O item do acervo que tem esta URL, ou 404.

    Esta consulta é o que impede o recurso de virar um proxy aberto: sem ela,
    qualquer pessoa poderia mandar a API buscar qualquer endereço e devolver o
    corpo — inclusive endereços da rede interna de onde a API roda. Só se
    converte o que a coleta já catalogou.
    """
    registros, total = execute_parquet_query(
        base_dir=output_dir,
        where_clauses=["CAST(url_download AS VARCHAR) = ?"],
        params=[url],
        limit=1,
        offset=0,
    )

    if not total or not registros:
        raise HTTPException(
            status_code=404,
            detail="Esta URL não consta no acervo auditado.",
        )

    return registros[0]


def _formato_do_item(item: dict) -> str:
    formato = str(item.get("tipo_arquivo") or "").strip().lower()

    if formato not in FORMATOS_CONVERSIVEIS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Formato '{formato or 'desconhecido'}' não vira planilha. "
                "Baixe o arquivo direto da fonte."
            ),
        )

    return formato


def _monta_xlsx(df: pd.DataFrame) -> io.BytesIO:
    """Escreve o DataFrame num .xlsx em memória.

    A higienização é a mesma do relatório de qualidade: texto raspado de
    portal traz caracteres de controle que o formato XLSX recusa, e sem ela um
    único caractere numa célula derruba a gravação do arquivo inteiro.
    """
    limpo = df.map(limpa_caracteres_ilegais)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        limpo.to_excel(writer, index=False, sheet_name=NOME_DA_ABA)
    buffer.seek(0)
    return buffer


def _content_disposition(nome: str) -> str:
    """Cabeçalho que faz o navegador salvar em vez de exibir.

    Sem a forma estendida do RFC 5987 porque `_nome_do_arquivo` já entrega
    ASCII puro: a segunda cópia seria idêntica à primeira.
    """
    return f'attachment; filename="{nome}"'


@router.get("/planilha")
def baixar_como_planilha(
    url: str = Query(
        ...,
        description=(
            "URL do item, exatamente como consta em `url_download` no acervo. "
            "Endereços fora do catálogo são recusados."
        ),
    ),
    output_dir: str = "outputs",
):
    """[RESTful] O conteúdo de um item do catálogo, em Excel.

    Serve ao portal: os itens cuja fonte é uma API JSON apontam para cá, e o
    clique entrega um .xlsx em vez de abrir JSON cru numa aba.
    """
    if urlparse(url).scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400, detail="Só URLs http(s) podem ser convertidas."
        )

    item = _localiza_no_catalogo(url, output_dir)
    formato = _formato_do_item(item)

    try:
        conteudo = _baixa_com_teto(url)
    except HTTPException:
        raise
    except requests.RequestException as erro:
        logger.warning(f"Falha ao buscar {url} para converter: {erro}")
        raise HTTPException(
            status_code=502,
            detail=f"A fonte não respondeu ao pedido do arquivo: {erro}",
        )

    df = _para_dataframe(conteudo, formato)
    buffer = _monta_xlsx(df)

    resposta = StreamingResponse(buffer, media_type=XLSX_MEDIA_TYPE)
    resposta.headers["Content-Disposition"] = _content_disposition(
        _nome_do_arquivo(item, url)
    )
    return resposta
