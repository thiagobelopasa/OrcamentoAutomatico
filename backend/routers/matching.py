from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from pathlib import Path
from typing import List, Optional
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
    """Proxy para imagens do Trello (requer autenticação OAuth)."""
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


_IMAGEM_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_DOC_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt"}


def _url_eh_imagem(url: str) -> bool:
    """Heurística: verifica se a URL parece ser uma imagem."""
    path = url.split("?")[0].lower()
    ext = Path(path).suffix
    if ext in _DOC_EXTS:
        return False
    if ext in _IMAGEM_EXTS:
        return True
    # URLs do Trello sem extensão mas com /attachments/ ou /cards/
    if "trello.com" in url and ("/attachments/" in url or "/cards/" in url):
        return True
    return True  # assume imagem por padrão


async def _run_batch(projeto_ids: List[str]):
    """Analisa Vision de TODAS as fotos de cada projeto."""
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

            # Carrega análises já feitas para não re-processar
            visao_fotos = list(projeto.visao_fotos or [])
            urls_ja = {v["url"] for v in visao_fotos}

            fotos_novas = [u for u in urls if u not in urls_ja and _url_eh_imagem(u)]
            if not fotos_novas:
                _batch["skipped"] += 1
                continue

            for idx, foto_url in enumerate(fotos_novas):
                if _batch["stop_requested"]:
                    break

                temp_path = Path(f"./temp_uploads/hist_{proj_id}_{idx}.jpg")
                try:
                    temp_path.parent.mkdir(exist_ok=True)

                    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                        headers = {"Authorization": oauth} if "trello.com" in foto_url else {}
                        resp = await client.get(foto_url, headers=headers)
                        if resp.status_code != 200:
                            logger.warning(f"Foto indisponível ({resp.status_code}): {foto_url[:60]}")
                            continue
                        ct = resp.headers.get("content-type", "")
                        if ct and not ct.startswith("image/"):
                            logger.info(f"Ignorado (não imagem, ct={ct}): {foto_url[:60]}")
                            continue
                        with open(temp_path, "wb") as f:
                            f.write(resp.content)

                    descricao = await asyncio.to_thread(
                        VisionMatcher.descrever_foto, str(temp_path)
                    )

                    enc = descricao.get("encosto", "desconhecido")
                    ass = descricao.get("assento", "desconhecido")
                    brc = descricao.get("braco", "desconhecido")

                    visao_fotos.append({
                        "url": foto_url,
                        "encosto": enc,
                        "assento": ass,
                        "braco": brc,
                        "confianca": descricao.get("confianca", "média"),
                        "descricao": descricao.get("descricao_resumida", ""),
                    })

                    # Se todos campos "desconhecido" → provável ficha de produção
                    # Tenta extrair metragem, horas e custos reais
                    if enc == "desconhecido" and ass == "desconhecido" and brc == "desconhecido":
                        try:
                            ficha = await asyncio.to_thread(
                                VisionMatcher.extrair_dados_ficha, str(temp_path)
                            )
                            if ficha.get("eh_ficha"):
                                mats = list(projeto.materiais or [])
                                horas = list(projeto.horas_trabalho or [])

                                # Metragem de tecido
                                m_tecido = ficha.get("metragem_tecido")
                                if m_tecido and not any("TECIDO" in m.get("nome","").upper() for m in mats):
                                    mats.append({"nome": "TECIDO", "quantidade": m_tecido, "unidade": "MT"})

                                # Espuma
                                val_espuma = ficha.get("valor_espuma")
                                if val_espuma and not any("ESPUMA" in m.get("nome","").upper() for m in mats):
                                    mats.append({"nome": "ESPUMA", "quantidade": 1, "unidade": "UN",
                                                 "observacoes": f"R${val_espuma:.0f}"})

                                # Horas de trabalho
                                trabalhadores = ficha.get("trabalhadores") or []
                                if trabalhadores and not horas:
                                    for t in trabalhadores:
                                        horas.append({"pessoa": t.get("nome","EQUIPE"),
                                                      "horas": float(t.get("horas", 0))})
                                elif ficha.get("horas_totais") and not horas:
                                    horas.append({"pessoa": "EQUIPE",
                                                  "horas": float(ficha["horas_totais"])})

                                if mats != list(projeto.materiais or []):
                                    projeto.materiais = mats
                                if horas != list(projeto.horas_trabalho or []):
                                    projeto.horas_trabalho = horas
                                    projeto.total_horas = sum(h["horas"] for h in horas)
                        except Exception as ef:
                            logger.warning(f"Falha ao extrair ficha: {ef}")

                    await asyncio.sleep(0.5)

                except Exception as e:
                    logger.error(f"Erro ao analisar foto {idx} de {proj_id}: {e}")
                finally:
                    if temp_path.exists():
                        try:
                            temp_path.unlink()
                        except Exception:
                            pass

            projeto.visao_fotos = visao_fotos
            projeto.data_atualizacao = datetime.now()
            db.commit()
            _batch["done"] += 1

        except Exception as e:
            logger.error(f"Erro no batch [{proj_id}]: {e}")
            _batch["errors"] += 1
        finally:
            db.close()

        await asyncio.sleep(0.3)

    _batch["running"] = False
    _batch["current_nome"] = None


@router.post("/analisar-historico")
async def analisar_historico(
    background_tasks: BackgroundTasks,
    limite: int = 300,
    db: Session = Depends(get_db)
) -> dict:
    """Inicia análise Vision em batch — processa projetos sem visao_fotos."""
    if _batch["running"]:
        return {"mensagem": "Análise já em andamento", "status": _batch}

    todos = (
        db.query(ProjetoORM)
        .order_by(ProjetoORM.data_criacao.desc())
        .limit(limite)
        .all()
    )

    # Inclui projetos sem visao_fotos OU com fotos ainda não analisadas
    pendentes = []
    for p in todos:
        if not p.urls_anexos:
            continue
        ja_analisadas = {v["url"] for v in (p.visao_fotos or [])}
        fotos_novas = [u for u in p.urls_anexos if u not in ja_analisadas and _url_eh_imagem(u)]
        if fotos_novas:
            pendentes.append(p)

    if not pendentes:
        return {"mensagem": "Todos os projetos já foram analisados"}

    ids = [p.id for p in pendentes]
    background_tasks.add_task(_run_batch, ids)

    return {
        "mensagem": f"Iniciando análise de {len(ids)} projetos em background",
        "total_a_analisar": len(ids),
        "estimativa_minutos": round(len(ids) * 2 / 60, 1),
    }


@router.post("/analisar-historico/parar")
async def parar_analise() -> dict:
    _batch["stop_requested"] = True
    return {"mensagem": "Parada solicitada — aguardando projeto atual finalizar"}


@router.get("/analisar-historico/status")
async def status_analise(db: Session = Depends(get_db)) -> dict:
    """Retorna progresso do batch + totais do banco."""
    todos = db.query(ProjetoORM).all()
    total_banco = len(todos)
    ja_analisados = sum(1 for p in todos if p.visao_fotos)
    total_fotos = sum(len(p.visao_fotos or []) for p in todos)
    pendentes = total_banco - ja_analisados
    pct = round(ja_analisados / total_banco * 100, 1) if total_banco else 0

    return {
        "batch": _batch,
        "banco": {
            "total": total_banco,
            "analisados": ja_analisados,
            "pendentes": pendentes,
            "total_fotos_analisadas": total_fotos,
            "pct_completo": pct,
        }
    }


# ─── MATCHING ───────────────────────────────────────────────────────────────────

def _projeto_metricas(p: ProjetoORM) -> dict:
    """Extrai métricas de orçamento de um projeto."""
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

    return {"m_tecido": m_tecido, "horas_totais": total_horas}


def _projetos_para_banco(projetos: List[ProjetoORM]) -> List[dict]:
    """
    Expande cada projeto em UMA entrada por foto analisada via Vision.
    Assim o matching aponta para a foto exata que gerou a similaridade.
    """
    banco = []
    for p in projetos:
        metricas = _projeto_metricas(p)

        visao_fotos = p.visao_fotos or []

        if visao_fotos:
            # Uma entrada por foto analisada
            for vf in visao_fotos:
                encosto = vf.get("encosto", "desconhecido")
                banco.append({
                    "id": p.id,
                    "foto_key": vf["url"],  # chave única: projeto + foto específica
                    "categoria": p.nome,
                    "estrutura_encosto": encosto,
                    "estrutura_assento": vf.get("assento", "desconhecido"),
                    "estrutura_braco": vf.get("braco", "desconhecido"),
                    "confianca_vision": vf.get("confianca", "média"),
                    "foto_antes_url": vf["url"],  # ← foto específica desta análise
                    "trello_card_url": p.trello_card_url,
                    "mes_entrega": p.mes_entrega,
                    "ano_entrega": p.ano_entrega,
                    "m_tecido": metricas["m_tecido"],
                    "horas_totais": metricas["horas_totais"],
                    "custo_historico": 0,
                    "tem_estrutura": encosto != "desconhecido",
                })
        else:
            # Fallback legado: usa primeira URL sem análise
            urls = p.urls_anexos or []
            foto_url = urls[0] if urls else None
            banco.append({
                "id": p.id,
                "foto_key": foto_url,
                "categoria": p.nome,
                "estrutura_encosto": "desconhecido",
                "estrutura_assento": "desconhecido",
                "estrutura_braco": "desconhecido",
                "confianca_vision": "baixa",
                "foto_antes_url": foto_url,
                "trello_card_url": p.trello_card_url,
                "mes_entrega": p.mes_entrega,
                "ano_entrega": p.ano_entrega,
                "m_tecido": metricas["m_tecido"],
                "horas_totais": metricas["horas_totais"],
                "custo_historico": 0,
                "tem_estrutura": False,
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


@router.post("/analisar-completo")
async def analisar_completo(
    file: UploadFile = File(...),
    top_n: int = 3,
    db: Session = Depends(get_db)
) -> dict:
    """
    Pipeline completo: analisa foto do cliente → encontra matches no banco histórico.
    Retorna a foto histórica ESPECÍFICA que mais se assemelha (não só o projeto).
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

        projetos = (
            db.query(ProjetoORM)
            .order_by(ProjetoORM.data_criacao.desc())
            .limit(500)
            .all()
        )

        # Expande: uma entrada por foto analisada em cada projeto
        banco_real = _projetos_para_banco(projetos)
        banco_com = [b for b in banco_real if b.get("tem_estrutura")]
        banco_sem = [b for b in banco_real if not b.get("tem_estrutura")]

        # Matches estruturais (compara contra todas as fotos analisadas)
        matches = VisionMatcher.encontrar_matches(descricao, banco_com, top_n=top_n) if banco_com else []

        # Complementa com projetos sem estrutura se necessário
        projetos_incl = {m["entrada_id"] for m in matches}
        for c in banco_sem:
            if len(matches) >= top_n:
                break
            if c["id"] in projetos_incl:
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
                "ano_entrega": c.get("ano_entrega"),
                "sem_estrutura": True,
            })
            projetos_incl.add(c["id"])

        if not matches:
            matches = VisionMatcher.encontrar_matches(descricao, BANCO_EXEMPLO, top_n=top_n)

        total_fotos = sum(len(p.visao_fotos or []) for p in projetos)
        total_analisados = sum(1 for p in projetos if p.visao_fotos)

        return {
            "sucesso": True,
            "analise_foto": descricao,
            "matches_encontrados": len(matches),
            "top_matches": matches,
            "recomendacao": matches[0] if matches else None,
            "total_no_banco": len(projetos),
            "total_analisados_estruturalmente": total_analisados,
            "total_fotos_no_banco": total_fotos,
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
