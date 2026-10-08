from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import (
    conjuntos,
    documentos,
    estatisticas,
    exportar,
    filtros,
    planilha,
)

app = FastAPI(
    title="API RESTful - Scrapping Transparência",
    description="Servidor de consulta analítica seguindo rigorosamente o padrão RESTful.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# Rotas da API
app.include_router(filtros.router)
app.include_router(documentos.router)
app.include_router(estatisticas.router)
app.include_router(exportar.router)
app.include_router(planilha.router)
app.include_router(conjuntos.router)


@app.get("/health", tags=["Healthcheck"])
def healthcheck():
    return {
        "status": "online",
        "mensagem": "API RESTful de Transparência ativa."
    }


# Portal
app.mount("/", StaticFiles(directory="portal", html=True), name="portal")