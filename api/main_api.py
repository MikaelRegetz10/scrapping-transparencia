# main_api.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import documentos, estatisticas, exportar, filtros, planilha

app = FastAPI(
    title="API RESTful - Scrapping Transparência",
    description="Servidor de consulta analítica seguindo rigorosamente o padrão RESTful.",
    version="1.0.0",
)

# Configuração de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # O portal roda em outra porta que a API, então todo download daqui é
    # cross-origin. Sem expor este cabeçalho o navegador o esconde de quem
    # busca o arquivo por fetch, e o nome do .xlsx se perde no caminho.
    expose_headers=["Content-Disposition"],
)

# Inclusão dos Roteadores RESTful
app.include_router(filtros.router)
app.include_router(documentos.router)
app.include_router(estatisticas.router)
app.include_router(exportar.router)
app.include_router(planilha.router)


@app.get("/", tags=["Healthcheck"])
def healthcheck():
    return {"status": "online", "mensagem": "API RESTful de Transparência ativa."}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main_api:app", host="0.0.0.0", port=8000, reload=True)