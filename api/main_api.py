# main_api.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import documentos, filtros, estatisticas, exportar

app = FastAPI(
    title="API - Scrapping Transparência (TCU / Sistema S)",
    description="Servidor de consulta analítica de alta performance baseado em DuckDB e Parquet.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inclusão das Rotas
app.include_router(filtros.router)
app.include_router(documentos.router)
app.include_router(estatisticas.router)
app.include_router(exportar.router)


@app.get("/", tags=["Healthcheck"])
def healthcheck():
    return {"status": "online", "message": "API de Transparência ativa."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main_api:app", host="0.0.0.0", port=8000, reload=True)