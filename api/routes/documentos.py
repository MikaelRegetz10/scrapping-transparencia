# api/routes/documentos.py
import math
from typing import Optional
from fastapi import APIRouter, Query
from api.database import execute_parquet_query
from api.schemas import PaginatedResponse

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