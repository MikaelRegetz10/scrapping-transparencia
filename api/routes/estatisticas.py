import os
from fastapi import APIRouter
from api.database import get_db_connection

router = APIRouter(prefix="/api/v1/estatisticas", tags=["Estatísticas & Kpis"])


@router.get("")
def get_kpis(output_dir: str = "outputs"):
    """Retorna indicadores consolidados para alimentar os cards do dashboard."""
    base_project_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    parquet_base = os.path.join(base_project_dir, output_dir, "parquet")
    parquet_glob = os.path.join(parquet_base, "**", "*.parquet").replace(
        "\\", "/"
    )

    if not os.path.exists(parquet_base):
        return {"total_registros": 0, "total_ufs": 0, "total_tipos_doc": 0}

    con = get_db_connection()
    try:
        query = f"""
            SELECT 
                COUNT(*) as total_registros,
                COUNT(DISTINCT uf) as total_ufs,
                COUNT(DISTINCT tipo_documento) as total_tipos_doc,
                COUNT(DISTINCT tema) as total_temas
            FROM read_parquet('{parquet_glob}', hive_partitioning=1, union_by_name=True)
        """
        res = con.execute(query).fetchone()
        return {
            "total_registros": res[0],
            "total_ufs": res[1],
            "total_tipos_doc": res[2],
            "total_temas": res[3],
        }
    except Exception:
        return {"total_registros": 0, "total_ufs": 0, "total_tipos_doc": 0}
    finally:
        con.close()