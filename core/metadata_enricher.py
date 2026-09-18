import json
import os
import glob
import duckdb
from google import genai  # Novo import oficial

# Nome do arquivo de cache de descrições
CACHE_DB = "outputs/metadata_cache.duckdb"


def inicializar_cache():
    """Cria a tabela de cache no DuckDB se não existir."""
    os.makedirs("outputs", exist_ok=True)
    with duckdb.connect(CACHE_DB) as conn:
        conn.execute("""
                     CREATE TABLE IF NOT EXISTS dicionario_cache
                     (
                         coluna
                         VARCHAR
                         PRIMARY
                         KEY,
                         descricao
                         VARCHAR
                     )
                     """)


def buscar_cache(colunas: list) -> dict:
    """Busca as descrições que já geramos anteriormente."""
    if not colunas: return {}
    with duckdb.connect(CACHE_DB) as conn:
        placeholders = ', '.join(['?'] * len(colunas))
        res = conn.execute(f"SELECT coluna, descricao FROM dicionario_cache WHERE coluna IN ({placeholders})",
                           colunas).fetchall()
        return {row[0]: row[1] for row in res}


def salvar_cache(novas_descricoes: dict):
    """Salva descrições novas aprendidas pela LLM."""
    if not novas_descricoes: return
    with duckdb.connect(CACHE_DB) as conn:
        for col, desc in novas_descricoes.items():
            conn.execute("INSERT OR IGNORE INTO dicionario_cache VALUES (?, ?)", (col, desc))


def processar_schemas_pendentes(api_key: str):
    """Lê todos os JSONs gerados pelo scraper, chama a LLM para colunas novas e atualiza os arquivos."""
    print("\n🧠 [Enriquecimento] Iniciando geração de Dicionário de Dados...")
    inicializar_cache()

    arquivos_json = glob.glob("outputs/parquet/**/*.json", recursive=True)
    if not arquivos_json:
        print("Nenhum schema encontrado para processar.")
        return

    # Extrai todas as colunas de todos os arquivos
    todas_colunas_pendentes = {}

    for caminho in arquivos_json:
        with open(caminho, "r", encoding="utf-8") as f:
            schema = json.load(f)

        for col in schema.get("columns", []):
            if col.get("descricao") is None:  # Se ainda não tem descrição
                nome = col["coluna"]
                todas_colunas_pendentes[nome] = col.get("amostra_temp", [])

    # 1. Verifica o Cache Local
    cache_atual = buscar_cache(list(todas_colunas_pendentes.keys()))
    colunas_para_llm = {k: v for k, v in todas_colunas_pendentes.items() if k not in cache_atual}

    # 2. Chama a LLM apenas para colunas inéditas no sistema
    if colunas_para_llm and api_key:
        print(f"🤖 Solicitando descrição para {len(colunas_para_llm)} nova(s) coluna(s) à LLM...")

        # Nova forma de inicializar o Client da API
        client = genai.Client(api_key=api_key)

        prompt = f"""
        Você é um auditor de transparência.
        Gere uma descrição clara (máx 15 palavras) para cada coluna abaixo, baseando-se no nome e nas amostras.
        Responda APENAS com um JSON válido: {{"nome_coluna": "descrição"}}.
        Dados:
        {json.dumps(colunas_para_llm, ensure_ascii=False)}
        """

        try:
            # Nova forma de chamar o modelo (usando gemini-3.6-flash)
            response = client.models.generate_content(
                model='gemini-3.6-flash',
                contents=prompt
            )

            texto_limpo = response.text.replace('```json', '').replace('```', '').strip()
            novas_desc = json.loads(texto_limpo)

            # Atualiza Cache e Memória
            salvar_cache(novas_desc)
            cache_atual.update(novas_desc)
            print("✅ Descrições geradas e oxigenadas no cache!")
        except Exception as e:
            print(f"❌ Erro na LLM: {e}")

    # 3. Atualiza os arquivos JSON removendo as amostras
    for caminho in arquivos_json:
        with open(caminho, "r", encoding="utf-8") as f:
            schema = json.load(f)

        for col in schema.get("columns", []):
            nome = col["coluna"]
            col["descricao"] = cache_atual.get(nome, "Descrição indisponível.")
            col.pop("amostra_temp", None)  # Limpa o lixo de amostragem

        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(schema, f, ensure_ascii=False, indent=2)

    print("🎯 Dicionários de Dados finalizados com sucesso!")