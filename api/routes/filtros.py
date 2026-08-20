import logging
import os
from fastapi import APIRouter
from api.database import colunas_do_esquema, get_db_connection
from api.schemas import FilterOptionsResponse

logger = logging.getLogger("api.filtros")

router = APIRouter(prefix="/api/v1/filtros", tags=["Filtros"])


def tipos_de_arquivo(parquet_base: str) -> list:
    """Formatos distintos dos catálogos (csv, xlsx, json, zip…).

    Diferente dos demais, `tipo_arquivo` não é partição Hive: está dentro dos
    arquivos, e só nos de catálogo. Sai daqui por consulta, não por leitura de
    diretório, e volta vazio num acervo que ainda não tenha catálogo nenhum.
    """
    parquet_glob = os.path.join(parquet_base, "**", "*.parquet").replace(
        "\\", "/"
    )
    con = get_db_connection()
    try:
        if "tipo_arquivo" not in colunas_do_esquema(con, parquet_glob):
            return []

        linhas = con.execute(
            f"""
            SELECT DISTINCT LOWER(CAST(tipo_arquivo AS VARCHAR)) AS tipo
            FROM read_parquet('{parquet_glob}', hive_partitioning=1, union_by_name=True)
            WHERE tipo_arquivo IS NOT NULL
            ORDER BY tipo
            """
        ).fetchall()
        return [linha[0] for linha in linhas if linha[0]]
    except Exception as e:
        logger.error(f"Erro ao listar tipos de arquivo: {e}")
        return []
    finally:
        con.close()


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
    entidades = set()

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
                elif d.startswith("entidade="):
                    entidades.add(d.split("=")[1])

    return FilterOptionsResponse(
        temas=sorted(list(temas)),
        tipos_documento=sorted(list(tipos)),
        anos=sorted(list(anos), reverse=True),
        ufs=sorted(list(ufs)),
        entidades=sorted(list(entidades)),
        tipos_arquivo=tipos_de_arquivo(parquet_base),
    )