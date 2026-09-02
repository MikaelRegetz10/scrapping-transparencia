# api/routes/documentos.py
import math
from typing import Optional
from fastapi import APIRouter, Query
from api.database import execute_parquet_counts, execute_parquet_query
from api.schemas import CountsResponse, PaginatedResponse

router = APIRouter(prefix="/api/v1/documentos", tags=["Documentos & Auditoria"])


@router.get("", response_model=PaginatedResponse)
def get_documentos(
    tema: Optional[str] = Query(
        None, description="Filtrar por tema ou múltiplos temas separados por vírgula (ex: geral,dados_abertos)"
    ),
    tipo_documento: Optional[str] = Query(
        None, description="Filtrar por tipo ou múltiplos tipos (ex: contratos,convenios)"
    ),
    ano: Optional[str] = Query(None, description="Filtrar por ano ou múltiplos anos (ex: 2026,2025)"),
    uf: Optional[str] = Query(
        None, description="Filtrar por UF ou múltiplas UFs (ex: BA,SP,RJ)"
    ),
    entidade: Optional[str] = Query(
        None, description="Filtrar por entidade ou múltiplas entidades (ex: ABDI,SESI)"
    ),
    tipo_arquivo: Optional[str] = Query(
        None, description="Filtrar por formato do arquivo (ex: csv,xlsx). Só vale nos catálogos"
    ),
    ativo: Optional[str] = Query(
        None, description="Situação do link na última verificação: SIM ou NÃO"
    ),
    estruturado: Optional[str] = Query(
        None, description="Se o profiler leu o conteúdo do arquivo: SIM ou NÃO"
    ),
    search: Optional[str] = Query(
        None, description="Busca textual genérica nas colunas"
    ),
    page: int = Query(1, ge=1, description="Número da página"),
    page_size: int = Query(20, ge=1, le=100, description="Registros por página"),
    output_dir: str = "outputs",
):
    offset = (page - 1) * page_size

    data, total = execute_parquet_query(
        base_dir=output_dir,
        tema=tema,
        tipo_documento=tipo_documento,
        ano=ano,
        uf=uf,
        entidade=entidade,
        tipo_arquivo=tipo_arquivo,
        ativo=ativo,
        estruturado=estruturado,
        search=search,
        limit=page_size,
        offset=offset,
    )

    total_pages = math.ceil(total / page_size) if total > 0 else 0

    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        data=data,
    )


@router.get("/contagens", response_model=CountsResponse)
def get_contagens(
    por: str = Query(
        "tipo_documento",
        description="Coluna a agrupar: tema, tipo_documento, ano, uf, entidade, "
        "tipo_arquivo, ativo, estruturado ou fonte",
    ),
    tema: Optional[str] = Query(None, description="Filtrar por tema(s)"),
    tipo_documento: Optional[str] = Query(None, description="Filtrar por tipo(s)"),
    ano: Optional[str] = Query(None, description="Filtrar por ano(s)"),
    uf: Optional[str] = Query(None, description="Filtrar por UF(s)"),
    entidade: Optional[str] = Query(None, description="Filtrar por entidade(s)"),
    tipo_arquivo: Optional[str] = Query(None, description="Filtrar por formato(s)"),
    ativo: Optional[str] = Query(None, description="Situação do link: SIM ou NÃO"),
    estruturado: Optional[str] = Query(
        None, description="Se o profiler leu o conteúdo: SIM ou NÃO"
    ),
    search: Optional[str] = Query(None, description="Busca textual genérica"),
    output_dir: str = "outputs",
):
    """[RESTful] Distribuição da coleção por uma de suas facetas.

    Serve ao portal, que monta cada filtro com a contagem de cada opção. Uma
    resposta daqui substitui uma consulta por opção — que era o que fazia a
    página levar quinze segundos para ficar utilizável.
    """
    contagens = execute_parquet_counts(
        base_dir=output_dir,
        por=por,
        tema=tema,
        tipo_documento=tipo_documento,
        ano=ano,
        uf=uf,
        entidade=entidade,
        tipo_arquivo=tipo_arquivo,
        ativo=ativo,
        estruturado=estruturado,
        search=search,
    )

    return CountsResponse(
        por=por,
        total=sum(item["total"] for item in contagens),
        contagens=contagens,
    )
