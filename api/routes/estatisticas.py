# api/routes/estatisticas.py
import logging
import os
from fastapi import APIRouter
from api.database import get_db_connection

logger = logging.getLogger("api.estatisticas")

router = APIRouter(prefix="/api/v1/estatisticas", tags=["Estatísticas & KPIs"])


@router.get("")
def get_estatisticas(output_dir: str = "outputs"):
    """[RESTful] Retorna o recurso de métricas e estatísticas consolidadas."""
    base_project_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    parquet_base = os.path.join(base_project_dir, output_dir, "parquet")
    parquet_glob = os.path.join(parquet_base, "**", "*.parquet").replace(
        "\\", "/"
    )

    if not os.path.exists(parquet_base):
        return {
            "total_registros": 0,
            "total_ufs": 0,
            "total_tipos_documento": 0,
            "total_temas": 0,
        }

    con = get_db_connection()
    try:
        query = f"""
            SELECT 
                COUNT(*) as total_registros,
                COUNT(DISTINCT uf) as total_ufs,
                COUNT(DISTINCT tipo_documento) as total_tipos_documento,
                COUNT(DISTINCT tema) as total_temas
            FROM read_parquet('{parquet_glob}', hive_partitioning=1, union_by_name=True)
        """
        res = con.execute(query).fetchone()
        return {
            "total_registros": res[0] or 0,
            "total_ufs": res[1] or 0,
            "total_tipos_documento": res[2] or 0,
            "total_temas": res[3] or 0,
        }
    except Exception as e:
        logger.error(f"Erro ao calcular estatísticas: {e}")
        return {
            "total_registros": 0,
            "total_ufs": 0,
            "total_tipos_documento": 0,
            "total_temas": 0,
        }
    finally:
        con.close()