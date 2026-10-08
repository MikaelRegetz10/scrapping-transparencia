# api/routes/estatisticas.py
import logging
from fastapi import APIRouter
from api.database import caminho_do_acervo, get_db_connection, leitura_do_acervo

logger = logging.getLogger("api.estatisticas")

router = APIRouter(prefix="/api/v1/estatisticas", tags=["Estatísticas & KPIs"])


@router.get("")
def get_estatisticas(output_dir: str = "outputs"):
    """[RESTful] Retorna o recurso de métricas e estatísticas consolidadas."""
    # O caminho e a expressão de leitura saem do `api.database`: é de lá que
    # vem a conexão com o cache de metadados já quente, e um glob montado
    # aqui à mão leria os mesmos arquivos por outro nome — o que, para o
    # cache, são arquivos diferentes.
    parquet_glob = caminho_do_acervo(output_dir)

    if not parquet_glob:
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
            FROM {leitura_do_acervo(parquet_glob)}
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