from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv

from routers import projetos, matching
from database import init_db

# Carrega variáveis de ambiente (.env.local tem prioridade)
load_dotenv(".env.local", override=True)
load_dotenv()

# Inicializa banco de dados
init_db()

app = FastAPI(
    title="Orçamento Automático API",
    description="Sistema de gestão de projetos (sofás) com integração Trello e Claude Vision API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(projetos.router)
app.include_router(matching.router)

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "timestamp": "2025-05-08"
    }

@app.get("/")
def root():
    return {
        "nome": "Orçamento Automático API",
        "versao": "0.1.0",
        "descricao": "Sistema de gestão de projetos de sofás com integração Trello",
        "endpoints": {
            "health": "/health",
            "projetos": "/projetos",
            "docs": "/docs",
            "redoc": "/redoc"
        },
        "features": [
            "CRUD de projetos",
            "Upload e análise de imagens com Claude Vision",
            "Sincronização com Trello (polling 24h)",
            "Rastreamento de materiais e horas de trabalho",
            "Comparação orçado vs realizado"
        ]
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
