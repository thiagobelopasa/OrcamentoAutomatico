from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from pathlib import Path
import os
import logging
from dotenv import load_dotenv

_DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

from routers import projetos, matching, drive
from database import init_db, SessionLocal, ProjetoORM

# Carrega variáveis de ambiente (.env.local tem prioridade)
load_dotenv(".env.local", override=True)
load_dotenv()

logger = logging.getLogger(__name__)


async def _auto_sync_trello():
    """Importa projetos do Trello se o banco estiver vazio."""
    api_key = os.getenv("TRELLO_API_KEY")
    api_token = os.getenv("TRELLO_API_TOKEN")
    board_id = os.getenv("TRELLO_BOARD_ID")

    if not all([api_key, api_token, board_id]):
        logger.info("Auto-sync Trello ignorado: credenciais não configuradas")
        return

    db = SessionLocal()
    try:
        total = db.query(ProjetoORM).filter(ProjetoORM.trello_card_id.isnot(None)).count()
        if total > 0:
            logger.info(f"Auto-sync Trello ignorado: banco já tem {total} projetos")
            return

        logger.info("Banco vazio — iniciando auto-sync do Trello...")
        from services.trello_sync import criar_sync_trello
        import json
        import uuid
        from datetime import datetime

        sync = criar_sync_trello(api_key, api_token, board_id)
        dados = await sync.sincronizar_tudo(apenas_entrega=True)
        await sync.fechar()

        criados = 0
        for card in dados.get("cards", []):
            card_id = card["id"]
            existe = db.query(ProjetoORM).filter(ProjetoORM.trello_card_id == card_id).first()
            if existe:
                continue

            anexos = card.get("anexos", [])
            # Todos os URLs — Vision classifica o que é foto vs ficha
            urls_todos = [a.get("url") for a in anexos if a.get("url")]

            proj_id = f"proj_{uuid.uuid4().hex[:12]}"
            db.add(ProjetoORM(
                id=proj_id,
                nome=card["name"],
                cliente=card["name"],
                mes_entrega=card.get("mes_entrega", "INDEFINIDO"),
                ano_entrega=card.get("ano_entrega", datetime.now().year),
                trello_card_id=card_id,
                trello_card_url=card.get("url"),
                descricao=card.get("desc", ""),
                urls_anexos=urls_todos,
                materiais=[],
                horas_trabalho=[],
            ))
            criados += 1

        db.commit()
        logger.info(f"Auto-sync concluído: {criados} projetos importados do Trello")

    except Exception as e:
        logger.error(f"Erro no auto-sync Trello: {e}")
    finally:
        db.close()


async def _auto_vision_batch():
    """Inicia análise unificada em background se há projetos pendentes."""
    import asyncio
    from routers.matching import _run_batch, _batch
    from routers.matching import _url_eh_imagem

    db = SessionLocal()
    try:
        from database import ProjetoORM
        todos = db.query(ProjetoORM).all()
        if not todos:
            return
        pendentes = []
        for p in todos:
            if p.analise_unificada == 1 and p.estrutura is not None:
                continue  # já feito
            if not p.urls_anexos:
                continue
            if not any(_url_eh_imagem(u) for u in p.urls_anexos):
                continue
            pendentes.append(p.id)
        if not pendentes:
            logger.info("Auto-vision: nada a fazer (tudo já analisado)")
            return
        logger.info(f"Auto-vision: {len(pendentes)} cards pendentes — iniciando em background")
        asyncio.create_task(_run_batch(pendentes))
    except Exception as e:
        logger.error(f"Erro no auto-vision: {e}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if os.getenv("AUTO_SYNC_TRELLO", "").lower() == "true":
        await _auto_sync_trello()
        await _auto_vision_batch()
    yield


app = FastAPI(
    title="Orçamento Automático API",
    description="Sistema de orçamentos de estofados com Claude Vision e Trello",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projetos.router)
app.include_router(matching.router)
app.include_router(drive.router)


@app.get("/health")
def health():
    db = SessionLocal()
    try:
        total = db.query(ProjetoORM).count()
    finally:
        db.close()
    return {"status": "ok", "version": "0.2.0", "total_projetos": total}


@app.get("/")
def root():
    html = _DOCS_DIR / "calculadora.html"
    if html.exists():
        return FileResponse(str(html), media_type="text/html")
    return {"nome": "Orçamento Automático API", "versao": "0.2.0", "docs": "/docs"}


@app.get("/calculadora")
def calculadora():
    html = _DOCS_DIR / "calculadora.html"
    if html.exists():
        return FileResponse(str(html), media_type="text/html")
    return {"erro": "calculadora.html não encontrada"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
