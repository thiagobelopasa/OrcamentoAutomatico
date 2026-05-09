from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from pathlib import Path
from typing import List, Optional
from sqlalchemy import or_
import json
import traceback
import logging
import os
import asyncio
import httpx
from datetime import datetime
from sqlalchemy.orm import Session

from services.vision_matcher import VisionMatcher, BANCO_EXEMPLO
from database import get_db, ProjetoORM, SessionLocal

router = APIRouter(prefix="/matching", tags=["matching"])
logger = logging.getLogger(__name__)

# ─── Estado global do batch analysis ───────────────────────────────────────────
_batch = {
    "running": False,
    "total": 0,
    "done": 0,
    "errors": 0,
    "skipped": 0,
    "start_time": None,
    "current_nome": None,
    "stop_requested": False,
}


# ─── PROXY DE IMAGENS TRELLO ────────────────────────────────────────────────────

@router.get("/proxy/imagem")
async def proxy_imagem(url: str):
    """
    Proxy para imagens do Trello (requer autenticação OAuth).
    Uso: /matching/proxy/imagem?url=<trello_url>
    """
    try:
        headers = {}
        if "trello.com" in url:
            key = os.getenv("TRELLO_API_KEY", "")
            token = os.getenv("TRELLO_API_TOKEN", "")
            headers["Authorization"] = f'OAuth oauth_consumer_key="{key}", oauth_token="{token}"'

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="Imagem não encontrada")

            content_type = resp.headers.get("content-type", "image/jpeg")
            return StreamingResponse(
                iter([resp.content]),
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=3600"}
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar imagem: {str(e)}")


# ─── BATCH ANALYSIS ─────────────────────────────────────────────────────────────

def _trello_oauth_header() -> str:
    key = os.getenv("TRELLO_API_KEY", "")
    token = os.getenv("TRELLO_API_TOKEN", "")
    return f'OAuth oauth_consumer_key="{key}", oauth_token="{token}"'


async def _run_batch(projeto_ids: List[str]):
    """Roda análise Vision em background para cada projeto."""
    global _batch
    _batch.update({
        "running": True,
        "total": len(projeto_ids),
        "done": 0,
        "errors": 0,
        "skipped": 0,
        "start_time": datetime.now().isoformat(),
        "current_nome": None,
        "stop_requested": False,
    })

    oauth = _trello_oauth_header()

    for proj_id in projeto_ids:
        if _batch["stop_requested"]:
            break

        db = SessionLocal()
        temp_path = None
        try:
            projeto = db.query(ProjetoORM).filter(ProjetoORM.id == proj_id).first()
            if not projeto:
                _batch["skipped"] += 1
                continue

            _batch["current_nome"] = projeto.nome[:60]

            urls = projeto.urls_anexos or []
            if not urls:
                _batch["skipped"] += 1
                continue

            # Baixa primeira foto
            foto_url = urls[0]
            temp_path = Path(f"./temp_uploads/hist_{proj_id}.jpg")
            temp_path.parent.mkdir(exist_ok=True)

            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                headers = {"Authorization": oauth} if "trello.com" in foto_url else {}
                resp = await client.get(foto_url, headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"Foto indisponível ({resp.status_code}): {proj_id}")
                    _batch["errors"] += 1
                    continue
                with open(temp_path, "wb") as f:
                    f.write(resp.content)

            # Analisa com Vision em thread separada (API síncrona)
            descricao = await asyncio.to_thread(
                VisionMatcher.descrever_foto, str(temp_path)
            )

            # Salva no banco
            vis_json = json.dumps(descricao, ensure_ascii=False)
            obs_nova = f"Análise Vision: {vis_json}"
            if projeto.observacoes and "Fichas OS:" in projeto.observacoes:
                obs_nova = projeto.observacoes + " | " + obs_nova
            projeto.observacoes = obs_nova
            projeto.data_atualizacao = datetime.now()
            db.commit()

            _batch["done"] += 1

        except Exception as e:
            logger.error(f"Erro no batch [{proj_id}]: {e}")
            _batch["errors"] += 1
        finally:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            db.close()

        # Pausa entre chamadas para não saturar a API
        await asyncio.sleep(0.8)

    _batch["running"] = False
    _batch["current_nome"] = None


@router.post("/analisar-historico")
async def analisar_historico(
    background_tasks: BackgroundTasks,
    limite: int = 300,
    db: Session = Depends(get_db)
) -> dict:
    """
    Inicia análise Vision em batch das fotos históricas do Trello.
    Processa projetos que ainda não têm análise estrutural.
    """
    if _batch["running"]:
        return {
            "mensagem": "Análise já em andamento",
            "status": _batch
        }

    # Projetos com foto mas sem análise Vision
    projetos = (
        db.query(ProjetoORM)
        .filter(ProjetoORM.trello_card_id.isnot(None))
        .filter(ProjetoORM.urls_anexos.isnot(None))
        .filter(
            or_(
                ProjetoORM.observacoes.is_(None),
                ~ProjetoORM.observacoes.contains("Análise Vision:")
            )
        )
        .order_by(ProjetoORM.data_criacao.desc())
        .limit(limite)
        .all()
    )

    # Só os que têm pelo menos 1 URL de foto
    com_foto = [p for p in projetos if p.urls_anexos]
    if not com_foto:
        return {"mensagem": "Todos os projetos já foram analisados"}

    ids = [p.id for p in com_foto]
    background_tasks.add_task(_run_batch, ids)

    return {
        "mensagem": f"Iniciando análise de {len(ids)} projetos em background",
        "total_a_analisar": len(ids),
        "estimativa_minutos": round(len(ids) * 1.5 / 60, 1),
    }


@router.post("/analisar-historico/parar")
async def parar_analise() -> dict:
    """Para o batch de análise após o projeto atual."""
    _batch["stop_requested"] = True
    return {"mensagem": "Parada solicitada — aguardando projeto atual finalizar"}


@router.get("/analisar-historico/status")
async def status_analise(db: Session = Depends(get_db)) -> dict:
    """Retorna progresso da análise batch + total já analisado no banco."""
    total_banco = db.query(ProjetoORM).filter(
        ProjetoORM.trello_card_id.isnot(None)
    ).count()
    ja_analisados = db.query(ProjetoORM).filter(
        ProjetoORM.trello_card_id.isnot(None),
        ProjetoORM.observacoes.contains("Análise Vision:")
    ).count()
    pendentes = total_banco - ja_analisados

    pct = round(ja_analisados / total_banco * 100, 1) if total_banco else 0

    return {
        "batch": _batch,
        "banco": {
            "total": total_banco,
            "analisados": ja_analisados,
            "pendentes": pendentes,
            "pct_completo": pct,
        }
    }


# ─── MATCHING ───────────────────────────────────────────────────────────────────

def _projetos_para_banco(projetos: List[ProjetoORM]) -> List[dict]:
    banco = []
    for p in projetos:
        estrutura = {
            "estrutura_encosto": "desconhecido",
            "estrutura_assento": "desconhecido",
            "estrutura_braco": "desconhecido",
        }
        if p.observacoes and "Análise Vision:" in p.observacoes:
            raw = p.observacoes.split("Análise Vision:")[-1].strip()
            if " | " in raw:
                raw = raw.split(" | ")[0].strip()
            try:
                v = json.loads(raw)
                estrutura = {
                    "estrutura_encosto": v.get("encosto", "desconhecido"),
                    "estrutura_assento": v.get("assento", "desconhecido"),
                    "estrutura_braco": v.get("braco", "desconhecido"),
                }
            except Exception:
                pass

        urls = p.urls_anexos or []
        foto_url = urls[0] if urls else None

        m_tecido = 0.0
        for m in (p.materiais or []):
            if "TECIDO" in m.get("nome", "").upper():
                m_tecido += float(m.get("quantidade", 0))
        if not m_tecido:
            m_tecido = 17.0

        total_horas = float(p.total_horas or 0)
        if not total_horas:
            total_horas = sum(float(h.get("horas", 0)) for h in (p.horas_trabalho or []))
        if not total_horas:
            total_horas = 32.0

        banco.append({
            "id": p.id,
            "categoria": p.nome,
            "estrutura_encosto": estrutura["estrutura_encosto"],
            "estrutura_assento": estrutura["estrutura_assento"],
            "estrutura_braco": estrutura["estrutura_braco"],
            "m_tecido": m_tecido,
            "horas_totais": total_horas,
            "custo_historico": 0,
            "foto_antes_url": foto_url,
            "trello_card_url": p.trello_card_url,
            "mes_entrega": p.mes_entrega,
            "ano_entrega": p.ano_entrega,
            "tem_estrutura": estrutura["estrutura_encosto"] != "desconhecido",
        })
    return banco


@router.post("/analisar-foto")
async def analisar_foto(file: UploadFile = File(...)) -> dict:
    try:
        temp_path = Path(f"./temp_uploads/{file.filename}")
        temp_path.parent.mkdir(exist_ok=True)
        with open(temp_path, "wb") as f:
            f.write(await file.read())
        descricao = VisionMatcher.descrever_foto(str(temp_path))
        if temp_path.exists():
            temp_path.unlink()
        return {"sucesso": True, "descricao": descricao}
    except Exception as e:
        logger.error(f"Erro ao analisar foto: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Erro ao analisar foto: {str(e)}")


@router.post("/encontrar-matches")
async def encontrar_matches(
    encosto: str,
    assento: str,
    braco: str,
    modulos: str = "desconhecido",
    confianca: str = "média",
    top_n: int = 3
) -> dict:
    descricao_foto = {"encosto": encosto, "assento": assento, "braco": braco,
                      "modulos": modulos, "confianca": confianca}
    matches = VisionMatcher.encontrar_matches(descricao_foto, BANCO_EXEMPLO, top_n=top_n)
    return {"sucesso": True, "descricao_entrada": descricao_foto,
            "matches_encontrados": len(matches), "matches": matches}


@router.post("/analisar-completo")
async def analisar_completo(
    file: UploadFile = File(...),
    top_n: int = 3,
    db: Session = Depends(get_db)
) -> dict:
    """
    Pipeline completo: analisa foto + encontra matches no banco histórico real.
    Prioriza projetos com análise estrutural já feita.
    """
    temp_path = None
    try:
        temp_path = Path(f"./temp_uploads/{file.filename}")
        temp_path.parent.mkdir(exist_ok=True)
        with open(temp_path, "wb") as f:
            f.write(await file.read())

        logger.info("Iniciando análise com Vision...")
        descricao = await asyncio.to_thread(VisionMatcher.descrever_foto, str(temp_path))
        logger.info(f"Análise concluída: {descricao}")

        # Busca projetos reais (máx 300, mais recentes primeiro)
        projetos = (
            db.query(ProjetoORM)
            .filter(ProjetoORM.trello_card_id.isnot(None))
            .order_by(ProjetoORM.data_criacao.desc())
            .limit(300)
            .all()
        )

        banco_real = _projetos_para_banco(projetos)
        banco_com = [b for b in banco_real if b.get("tem_estrutura")]
        banco_sem = [b for b in banco_real if not b.get("tem_estrutura")]

        # Matches estruturais
        matches_est = VisionMatcher.encontrar_matches(descricao, banco_com, top_n=top_n) if banco_com else []

        # Complementa com recentes sem estrutura
        ids_incl = {m["entrada_id"] for m in matches_est}
        matches = matches_est.copy()
        for c in banco_sem:
            if len(matches) >= top_n:
                break
            if c["id"] in ids_incl:
                continue
            matches.append({
                "entrada_id": c["id"],
                "categoria": c["categoria"],
                "similaridade_pct": 45.0,
                "m_tecido": c["m_tecido"],
                "horas_totais": c["horas_totais"],
                "custo_historico": c["custo_historico"],
                "foto_url": c["foto_antes_url"],
                "encosto": c["estrutura_encosto"],
                "assento": c["estrutura_assento"],
                "braco": c["estrutura_braco"],
                "trello_card_url": c.get("trello_card_url"),
                "mes_entrega": c.get("mes_entrega"),
                "sem_estrutura": True,
            })

        if not matches:
            matches = VisionMatcher.encontrar_matches(descricao, BANCO_EXEMPLO, top_n=top_n)

        total_analisados = sum(1 for b in banco_real if b.get("tem_estrutura"))

        return {
            "sucesso": True,
            "analise_foto": descricao,
            "matches_encontrados": len(matches),
            "top_matches": matches,
            "recomendacao": matches[0] if matches else None,
            "total_no_banco": len(banco_real),
            "total_analisados_estruturalmente": total_analisados,
        }

    except Exception as e:
        error_msg = f"Erro na análise completa: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


@router.get("/banco-exemplo")
async def obter_banco_exemplo() -> dict:
    return {"total_entradas": len(BANCO_EXEMPLO), "entradas": BANCO_EXEMPLO}
