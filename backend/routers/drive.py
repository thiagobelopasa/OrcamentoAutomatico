"""
Router para importação de fotos de referência do Google Drive.
Cada arquivo tem metragem e horas no nome: "1,5mt - 2,75hr.jpeg"
"""
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import datetime
import asyncio
import logging
import os
import uuid
import httpx

from database import get_db, ProjetoORM, SessionLocal
from services.drive_sync import (
    listar_arquivos_drive, listar_arquivos_drive_publico,
    parse_filename, url_download_drive, url_download_drive_publico,
    url_view_drive_publico,
)
from services.vision_matcher import VisionMatcher

router = APIRouter(prefix="/drive", tags=["drive"])
logger = logging.getLogger(__name__)

_batch_drive = {
    "running": False,
    "total": 0,
    "done": 0,
    "errors": 0,
    "skipped": 0,
    "start_time": None,
    "current_nome": None,
    "stop_requested": False,
}


async def _importar_drive(folder_id: str, api_key: str, analisar_vision: bool):
    global _batch_drive
    _batch_drive.update({
        "running": True,
        "total": 0,
        "done": 0,
        "errors": 0,
        "skipped": 0,
        "start_time": datetime.now().isoformat(),
        "current_nome": None,
        "stop_requested": False,
    })

    try:
        if api_key:
            arquivos = await listar_arquivos_drive(folder_id, api_key)
        else:
            logger.info("GOOGLE_API_KEY não configurada — usando listagem pública (scraping)")
            arquivos = await listar_arquivos_drive_publico(folder_id)
        _batch_drive["total"] = len(arquivos)
        logger.info(f"Drive: {len(arquivos)} arquivos encontrados")
    except Exception as e:
        logger.error(f"Erro ao listar Drive: {e}")
        _batch_drive["running"] = False
        return

    for arq in arquivos:
        if _batch_drive["stop_requested"]:
            break

        file_id = arq["id"]
        filename = arq["name"]
        _batch_drive["current_nome"] = filename[:60]

        db = SessionLocal()
        temp_path = None
        try:
            # Verifica se já importado
            existente = db.query(ProjetoORM).filter(
                ProjetoORM.drive_file_id == file_id
            ).first()
            if existente:
                _batch_drive["skipped"] += 1
                continue

            # Faz parse do nome do arquivo
            info = parse_filename(filename)
            metragem = info.get("metragem") or 0.0
            horas = info.get("horas") or 0.0
            quantidade = info.get("quantidade") or 1
            nome_display = info.get("nome_original", filename)

            # Baixa a imagem (com ou sem API key)
            dl_url = url_download_drive(file_id, api_key) if api_key else url_download_drive_publico(file_id)
            temp_path = Path(f"./temp_uploads/drive_{file_id}.jpg")
            temp_path.parent.mkdir(exist_ok=True)

            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(dl_url)
                if resp.status_code != 200:
                    logger.warning(f"Falha ao baixar {filename}: {resp.status_code}")
                    _batch_drive["errors"] += 1
                    continue
                with open(temp_path, "wb") as f:
                    f.write(resp.content)

            # URL pública de visualização do Drive
            foto_url = url_view_drive_publico(file_id)

            # Análise Vision (opcional)
            visao_fotos = []
            if analisar_vision:
                try:
                    descricao = await asyncio.to_thread(
                        VisionMatcher.descrever_foto, str(temp_path)
                    )
                    visao_fotos = [{
                        "url": foto_url,
                        "encosto": descricao.get("encosto", "desconhecido"),
                        "assento": descricao.get("assento", "desconhecido"),
                        "braco": descricao.get("braco", "desconhecido"),
                        "confianca": descricao.get("confianca", "média"),
                        "descricao": descricao.get("descricao_resumida", ""),
                    }]
                except Exception as e:
                    logger.warning(f"Vision falhou para {filename}: {e}")

            # Salva no banco
            proj_id = f"drive_{uuid.uuid4().hex[:12]}"
            materiais = []
            if metragem > 0:
                materiais = [{"nome": "TECIDO", "quantidade": metragem * quantidade, "unidade": "mt"}]

            horas_trabalho = []
            if horas > 0:
                horas_trabalho = [{"descricao": "Mão de obra", "horas": horas * quantidade}]

            db.add(ProjetoORM(
                id=proj_id,
                nome=nome_display,
                cliente=nome_display,
                mes_entrega="REFERÊNCIA",
                ano_entrega=0,
                drive_file_id=file_id,
                urls_anexos=[foto_url],
                materiais=materiais,
                horas_trabalho=horas_trabalho,
                total_horas=horas * quantidade,
                visao_fotos=visao_fotos,
                observacoes=f"Importado do Google Drive | Metragem: {metragem}mt | Horas: {horas}h | Qtd: {quantidade}",
            ))
            db.commit()
            _batch_drive["done"] += 1

        except Exception as e:
            logger.error(f"Erro ao importar {filename}: {e}")
            _batch_drive["errors"] += 1
        finally:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            db.close()

        await asyncio.sleep(0.5 if analisar_vision else 0.1)

    _batch_drive["running"] = False
    _batch_drive["current_nome"] = None


@router.post("/sync")
async def sync_drive(
    background_tasks: BackgroundTasks,
    analisar_vision: bool = True,
) -> dict:
    """
    Importa fotos do Google Drive para o banco de matching.
    Extrai metragem e horas do nome do arquivo.
    Se analisar_vision=True, também analisa estrutura visual de cada foto.
    """
    if _batch_drive["running"]:
        return {"mensagem": "Sincronização já em andamento", "status": _batch_drive}

    api_key = os.getenv("GOOGLE_API_KEY", "")
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")

    if not folder_id:
        return {"erro": "GOOGLE_DRIVE_FOLDER_ID não configurada no servidor"}

    modo = "API oficial" if api_key else "listagem pública (scraping)"
    background_tasks.add_task(_importar_drive, folder_id, api_key, analisar_vision)

    return {
        "mensagem": f"Sincronização do Google Drive iniciada em background ({modo})",
        "analisar_vision": analisar_vision,
        "modo": modo,
    }


@router.post("/sync/parar")
async def parar_sync() -> dict:
    _batch_drive["stop_requested"] = True
    return {"mensagem": "Parada solicitada"}


@router.get("/status")
async def status_drive(db: Session = Depends(get_db)) -> dict:
    total = db.query(ProjetoORM).filter(ProjetoORM.drive_file_id.isnot(None)).count()
    com_vision = db.query(ProjetoORM).filter(
        ProjetoORM.drive_file_id.isnot(None),
        ProjetoORM.visao_fotos.isnot(None),
    ).count()

    return {
        "batch": _batch_drive,
        "banco": {
            "total_importados": total,
            "com_vision": com_vision,
        }
    }
