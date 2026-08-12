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
        None, description="Filtrar por tema (ex: dados_abertos)"
    ),
    tipo_documento: Optional[str] = Query(
        None, description="Filtrar por tipo (ex: contratos, convenios)"
    ),
    ano: Optional[int] = Query(None, description="Filtrar por ano (ex: 2026)"),
    uf: Optional[str] = Query(
        None, description="Filtrar por UF (ex: SP, RJ, DN)"
    ),
    search: Optional[str] = Query(
        None, description="Busca textual genérica nas colunas"
    ),
    page: int = Query(1, ge=1, description="Número da página"),
    page_size: int = Query(20, ge=1, le=100, description="Registros por página"),
    output_dir: str = "outputs",
):
    offset = (page - 1) * page_size

    # Monta os wildcards de partição do Hive
    hive_tema = tema if tema else "*"
    hive_tipo = tipo_documento if tipo_documento else "*"
    hive_ano = str(ano) if ano else "*"
    hive_uf = uf.upper() if uf else "*"

    where_clauses = []
    params = []

    # Exemplo de filtro de busca simples (pode ser expandido conforme o schema das planilhas)
    if search:
        # Busca case-insensitive no título ou conteúdo se existir
        where_clauses.append(
            "(LOWER(CAST(tcu_tipo_documento AS VARCHAR)) LIKE ? OR LOWER(CAST(tcu_tema AS VARCHAR)) LIKE ?)"
        )
        params.extend([f"%{search.lower()}%", f"%{search.lower()}%"])

    data, total = execute_parquet_query(
        base_dir=output_dir,
        tema=hive_tema,
        tipo_documento=hive_tipo,
        ano=hive_ano,
        uf=hive_uf,
        where_clauses=where_clauses,
        params=params,
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