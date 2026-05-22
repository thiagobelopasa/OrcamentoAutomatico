from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from pathlib import Path
import os
import asyncio
import logging
from dotenv import load_dotenv

_DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

from routers import projetos, matching, drive
from database import init_db, SessionLocal, ProjetoORM

# Carrega variáveis de ambiente (.env.local tem prioridade)
load_dotenv(".env.local", override=True)
load_dotenv()

logger = logging.getLogger(__name__)

_sync_status = {"trello": None, "drive": None, "running": False}


async def _sync_trello_incremental() -> dict:
    """Sincroniza Trello — adiciona novos cards, nunca apaga existentes."""
    api_key = os.getenv("TRELLO_API_KEY")
    api_token = os.getenv("TRELLO_API_TOKEN")
    board_id = os.getenv("TRELLO_BOARD_ID")

    if not all([api_key, api_token, board_id]):
        return {"ok": False, "erro": "Credenciais Trello não configuradas"}

    from services.trello_sync import criar_sync_trello
    import uuid
    from datetime import datetime

    db = SessionLocal()
    try:
        sync = criar_sync_trello(api_key, api_token, board_id)
        dados = await sync.sincronizar_tudo(apenas_entrega=True)
        await sync.fechar()

        criados = 0
        for card in dados.get("cards", []):
            card_id = card["id"]
            if db.query(ProjetoORM).filter(ProjetoORM.trello_card_id == card_id).first():
                continue

            anexos = card.get("anexos", [])
            urls_todos = [a.get("url") for a in anexos if a.get("url")]

            db.add(ProjetoORM(
                id=f"proj_{uuid.uuid4().hex[:12]}",
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
        msg = f"{criados} novos cards importados do Trello"
        logger.info(f"Sync Trello: {msg}")
        return {"ok": True, "criados": criados, "total_cards": len(dados.get("cards", []))}

    except Exception as e:
        logger.error(f"Erro no sync Trello: {e}")
        return {"ok": False, "erro": str(e)}
    finally:
        db.close()


async def _sync_drive_incremental() -> dict:
    """Sincroniza Google Drive — adiciona novos arquivos, nunca apaga existentes."""
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    if not folder_id:
        return {"ok": False, "erro": "GOOGLE_DRIVE_FOLDER_ID não configurada"}

    from services.drive_sync import listar_arquivos_drive_publico, parse_filename
    import uuid

    db = SessionLocal()
    try:
        arquivos = await listar_arquivos_drive_publico(folder_id)
        criados = 0

        for arquivo in arquivos:
            drive_id = arquivo.get("id", "")
            if not drive_id:
                continue

            if db.query(ProjetoORM).filter(ProjetoORM.drive_file_id == drive_id).first():
                continue

            nome = arquivo.get("name", "")
            url = arquivo.get("url", "")
            metadata = parse_filename(nome)

            db.add(ProjetoORM(
                id=f"drive_{uuid.uuid4().hex[:12]}",
                nome=nome[:100],
                cliente="Google Drive",
                mes_entrega="INDEFINIDO",
                ano_entrega=2026,
                foto_estofado_url=url,
                descricao=f"Importado do Drive: {nome}",
                observacoes=f"Drive ID: {drive_id}",
                dados_ficha={
                    "metragem_tecido": metadata.get("metragem"),
                    "horas_totais": metadata.get("horas"),
                    "quantidade_pecas": metadata.get("quantidade"),
                },
                drive_file_id=drive_id,
                analise_unificada=0,
                materiais=[],
                horas_trabalho=[],
            ))
            criados += 1

        db.commit()
        msg = f"{criados} novos arquivos importados do Drive"
        logger.info(f"Sync Drive: {msg}")
        return {"ok": True, "criados": criados, "total_arquivos": len(arquivos)}

    except Exception as e:
        logger.error(f"Erro no sync Drive: {e}")
        return {"ok": False, "erro": str(e)}
    finally:
        db.close()


async def _run_full_sync():
    """Roda sync do Trello sem bloquear o servidor."""
    if _sync_status["running"]:
        logger.info("Sync já em andamento, ignorando")
        return

    _sync_status["running"] = True
    try:
        logger.info("Iniciando sync do Trello...")
        trello_result = await _sync_trello_incremental()
        _sync_status["trello"] = trello_result
        logger.info(f"Sync finalizado. Trello: {trello_result}")
    finally:
        _sync_status["running"] = False


async def _auto_vision_batch():
    """Inicia análise Vision em background para projetos pendentes com URLs de imagem."""
    from routers.matching import _run_batch, _url_eh_imagem

    db = SessionLocal()
    try:
        pendentes = [
            p.id for p in db.query(ProjetoORM).all()
            if not (p.analise_unificada == 1 and p.estrutura is not None)
            and p.urls_anexos
            and any(_url_eh_imagem(u) for u in p.urls_anexos)
        ]
        if not pendentes:
            logger.info("Auto-vision: nada pendente")
            return
        logger.info(f"Auto-vision: {len(pendentes)} projetos pendentes — iniciando em background")
        asyncio.create_task(_run_batch(pendentes))
    except Exception as e:
        logger.error(f"Erro no auto-vision: {e}")
    finally:
        db.close()


def _start_scheduler():
    """Configura APScheduler para sync periódico a cada 6 horas."""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()
        scheduler.add_job(_run_full_sync, "interval", hours=24, id="sync_periodico")
        scheduler.start()
        logger.info("Scheduler iniciado: sync a cada 6 horas")
        return scheduler
    except ImportError:
        logger.warning("APScheduler não instalado — sync periódico desabilitado")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = _start_scheduler()
    if os.getenv("AUTO_SYNC_TRELLO", "").lower() == "true":
        asyncio.create_task(_run_full_sync())
        await asyncio.sleep(0.1)  # deixa a task iniciar antes do servidor subir
    # Auto-vision desabilitado — análise só por demanda via /matching/analisar-historico
    yield
    if scheduler:
        scheduler.shutdown()


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


@app.post("/sync/run")
async def trigger_sync(background_tasks: BackgroundTasks):
    """Dispara sync manual de Trello + Drive em background."""
    if _sync_status["running"]:
        return {"status": "ja_rodando", "msg": "Sync já em andamento"}
    background_tasks.add_task(_run_full_sync)
    return {"status": "iniciado", "msg": "Sync iniciado em background"}


@app.get("/sync/status")
def sync_status():
    """Retorna resultado do último sync."""
    return {
        "running": _sync_status["running"],
        "ultimo_trello": _sync_status["trello"],
        "ultimo_drive": _sync_status["drive"],
    }


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
