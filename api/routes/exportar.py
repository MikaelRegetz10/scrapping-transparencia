# api/routes/exportar.py
import io
import logging
from typing import Optional
import pandas as pd
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from api.database import execute_parquet_query

logger = logging.getLogger("api.exportar")

router = APIRouter(prefix="/api/v1/exportar", tags=["Exportação de Dados"])


@router.get("")
def exportar_dados(
    tema: Optional[str] = Query(None, description="Filtrar por tema"),
    tipo_documento: Optional[str] = Query(
        None, description="Filtrar por tipo de documento"
    ),
    ano: Optional[int] = Query(None, description="Filtrar por ano"),
    uf: Optional[str] = Query(None, description="Filtrar por UF"),
    search: Optional[str] = Query(None, description="Busca textual genérica"),
    formato: str = Query(
        "csv",
        pattern="^(csv|xlsx|json)$",  # <--- ALTERADO DE regex PARA pattern
        description="Formato do arquivo: csv, xlsx ou json",
    ),
    output_dir: str = "outputs",
):
    """Executa a consulta com os filtros aplicados e gera o download direto dos dados

    no formato desejado (csv, xlsx, json).
    """
    hive_tema = tema if tema else "*"
    hive_tipo = tipo_documento if tipo_documento else "*"
    hive_ano = str(ano) if ano else "*"
    hive_uf = uf.upper() if uf else "*"

    where_clauses = []
    params = []

    if search:
        where_clauses.append(
            "(LOWER(CAST(tcu_tipo_documento AS VARCHAR)) LIKE ? OR LOWER(CAST(tcu_tema AS VARCHAR)) LIKE ?)"
        )
        params.extend([f"%{search.lower()}%", f"%{search.lower()}%"])

    # Executa a query trazendo até 100.000 registros para o relatório/exportação
    records, total = execute_parquet_query(
        base_dir=output_dir,
        tema=hive_tema,
        tipo_documento=hive_tipo,
        ano=hive_ano,
        uf=hive_uf,
        where_clauses=where_clauses,
        params=params,
        limit=100000,
        offset=0,
    )

    if not records:
        df = pd.DataFrame([{"Aviso": "Nenhum dado encontrado para os filtros selecionados"}])
    else:
        df = pd.DataFrame(records)

    nome_base = f"exportacao_{hive_tipo}_{hive_uf}_{hive_ano}"

    # ==========================================
    # 1. EXPORTAÇÃO EM CSV
    # ==========================================
    if formato == "csv":
        stream = io.StringIO()
        # utf-8-sig garante que acentos abram corretamente no Excel/Windows
        df.to_csv(stream, index=False, encoding="utf-8-sig", sep=";")

        response = StreamingResponse(
            iter([stream.getvalue()]),
            media_type="text/csv",
        )
        response.headers["Content-Disposition"] = f"attachment; filename={nome_base}.csv"
        return response

    # ==========================================
    # 2. EXPORTAÇÃO EM EXCEL (.XLSX)
    # ==========================================
    elif formato == "xlsx":
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Dados_Auditados")
        output.seek(0)

        response = StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response.headers["Content-Disposition"] = f"attachment; filename={nome_base}.xlsx"
        return response

    # ==========================================
    # 3. EXPORTAÇÃO EM JSON
    # ==========================================
    elif formato == "json":
        output_json = df.to_json(orient="records", force_ascii=False, indent=2)
        response = StreamingResponse(
            io.BytesIO(output_json.encode("utf-8")),
            media_type="application/json",
        )
        response.headers["Content-Disposition"] = f"attachment; filename={nome_base}.json"
        return response