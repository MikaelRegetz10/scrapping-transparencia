# api/routes/exportar.py
import io
import logging
from typing import Optional
import pandas as pd
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from api.database import execute_parquet_query

logger = logging.getLogger("api.exportar")

# 💡 REST: O recurso de exportação pertence ao domínio de /documentos
router = APIRouter(prefix="/api/v1/documentos", tags=["Documentos & Auditoria"])


@router.get("/exportacao")
def exportar_documentos(
    tema: Optional[str] = Query(
        None, description="Filtrar por tema(s) separados por vírgula"
    ),
    tipo_documento: Optional[str] = Query(
        None, description="Filtrar por tipo(s) de documento"
    ),
    ano: Optional[str] = Query(None, description="Filtrar por ano(s)"),
    uf: Optional[str] = Query(None, description="Filtrar por UF(s)"),
    entidade: Optional[str] = Query(None, description="Filtrar por entidade(s)"),
    tipo_arquivo: Optional[str] = Query(
        None, description="Filtrar por formato(s) do arquivo (ex: csv,xlsx)"
    ),
    ativo: Optional[str] = Query(
        None, description="Situação do link na última verificação: SIM ou NÃO"
    ),
    estruturado: Optional[str] = Query(
        None, description="Se o profiler leu o conteúdo do arquivo: SIM ou NÃO"
    ),
    search: Optional[str] = Query(None, description="Busca textual genérica"),
    formato: str = Query(
        "csv",
        pattern="^(csv|xlsx|json)$",
        description="Formato de representação do recurso: csv, xlsx ou json",
    ),
    output_dir: str = "outputs",
):
    """[RESTful] Representação exportável da coleção de documentos auditados."""
    records, total = execute_parquet_query(
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
        limit=100000,
        offset=0,
    )

    if not records:
        df = pd.DataFrame(
            [{"aviso": "Nenhum dado encontrado para os filtros selecionados"}]
        )
    else:
        df = pd.DataFrame(records)

    nome_base = "documentos_auditados"

    if formato == "csv":
        stream = io.StringIO()
        df.to_csv(stream, index=False, encoding="utf-8-sig", sep=";")

        response = StreamingResponse(
            iter([stream.getvalue()]),
            media_type="text/csv",
        )
        response.headers[
            "Content-Disposition"
        ] = f"attachment; filename={nome_base}.csv"
        return response

    elif formato == "xlsx":
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Documentos_Auditados")
        output.seek(0)

        response = StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response.headers[
            "Content-Disposition"
        ] = f"attachment; filename={nome_base}.xlsx"
        return response

    elif formato == "json":
        output_json = df.to_json(orient="records", force_ascii=False, indent=2)
        response = StreamingResponse(
            io.BytesIO(output_json.encode("utf-8")),
            media_type="application/json",
        )
        response.headers[
            "Content-Disposition"
        ] = f"attachment; filename={nome_base}.json"
        return response