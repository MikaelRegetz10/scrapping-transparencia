import os
import re
from fastapi import APIRouter
from api.schemas import FilterOptionsResponse

router = APIRouter(prefix="/api/v1/filtros", tags=["Filtros"])


@router.get("", response_model=FilterOptionsResponse)
def get_filter_options(output_dir: str = "outputs"):
    # Caminho absoluto para a raiz do projeto
    base_project_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    if not os.path.isabs(output_dir):
        parquet_base = os.path.join(base_project_dir, output_dir, "parquet")
    else:
        parquet_base = os.path.join(output_dir, "parquet")

    temas = set()
    tipos = set()
    anos = set()
    ufs = set()

    if os.path.exists(parquet_base):
        for root, dirs, files in os.walk(parquet_base):
            for d in dirs:
                if d.startswith("tema="):
                    temas.add(d.split("=")[1])
                elif d.startswith("tipo_documento="):
                    tipos.add(d.split("=")[1])
                elif d.startswith("ano="):
                    val = d.split("=")[1]
                    if val.isdigit():
                        anos.add(int(val))
                elif d.startswith("uf="):
                    ufs.add(d.split("=")[1])

    return FilterOptionsResponse(
        temas=sorted(list(temas)),
        tipos_documento=sorted(list(tipos)),
        anos=sorted(list(anos), reverse=True),
        ufs=sorted(list(ufs)),
    )