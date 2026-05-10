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
from services import card_analyzer
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


async def _baixar_imagens_card(urls: List[str], oauth: str, proj_id: str) -> tuple[List[str], List[str]]:
    """
    Baixa todas as imagens de um card. Retorna (paths_locais, urls_correspondentes).
    Pula URLs que não respondem ou não são imagens.
    """
    paths: List[str] = []
    urls_ok: List[str] = []
    base = Path("./temp_uploads"); base.mkdir(exist_ok=True)

    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        for idx, foto_url in enumerate(urls):
            if not _url_eh_imagem(foto_url):
                continue
            try:
                headers = {"Authorization": oauth} if "trello.com" in foto_url else {}
                resp = await client.get(foto_url, headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"Foto indisponível ({resp.status_code}): {foto_url[:60]}")
                    continue
                ct = resp.headers.get("content-type", "")
                if ct and not ct.startswith("image/"):
                    continue
                p = base / f"card_{proj_id}_{idx}.jpg"
                with open(p, "wb") as f:
                    f.write(resp.content)
                paths.append(str(p))
                urls_ok.append(foto_url)
            except Exception as e:
                logger.warning(f"Falha download {foto_url[:60]}: {e}")
                continue
    return paths, urls_ok


async def _run_batch(projeto_ids: List[str]):
    """
    Analisa cada card em UMA chamada Vision (foto + fichas combinadas).
    Salva foto_estofado_url, estrutura e dados_ficha.
    """
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
        downloaded: List[str] = []
        try:
            projeto = db.query(ProjetoORM).filter(ProjetoORM.id == proj_id).first()
            if not projeto:
                _batch["skipped"] += 1
                continue

            _batch["current_nome"] = projeto.nome[:60]

            # Já analisado? Pula
            if projeto.analise_unificada == 1 and projeto.estrutura is not None:
                _batch["skipped"] += 1
                continue

            urls = [u for u in (projeto.urls_anexos or []) if _url_eh_imagem(u)]
            if not urls:
                projeto.analise_unificada = -1
                db.commit()
                _batch["skipped"] += 1
                continue

            # Baixa todas as imagens do card
            paths, urls_ok = await _baixar_imagens_card(urls, oauth, proj_id)
            downloaded = paths
            if not paths:
                projeto.analise_unificada = -1
                db.commit()
                _batch["skipped"] += 1
                continue

            # UMA chamada Vision por card — recebe todas as imagens juntas
            resultado = await asyncio.to_thread(
                card_analyzer.analyze_card, paths, urls_ok
            )

            projeto.foto_estofado_url = resultado.get("foto_estofado_url")
            projeto.estrutura = resultado.get("estrutura")
            projeto.dados_ficha = resultado.get("dados_ficha")
            projeto.analise_unificada = 1

            # Sincroniza materiais/horas com o que veio da ficha (se vier algo)
            df = resultado.get("dados_ficha") or {}
            mats = list(projeto.materiais or [])
            horas = list(projeto.horas_trabalho or [])
            mudou = False
            if df.get("metragem_tecido") and not any("TECIDO" in m.get("nome","").upper() for m in mats):
                mats.append({"nome": "TECIDO", "quantidade": df["metragem_tecido"], "unidade": "MT"})
                mudou = True
            if df.get("valor_espuma") and not any("ESPUMA" in m.get("nome","").upper() for m in mats):
                mats.append({"nome": "ESPUMA", "quantidade": 1, "unidade": "UN",
                             "observacoes": f"R${df['valor_espuma']:.0f}"})
                mudou = True
            if mudou:
                projeto.materiais = mats

            trabalhadores = df.get("trabalhadores") or []
            if trabalhadores and not horas:
                for t in trabalhadores:
                    horas.append({"pessoa": t.get("nome","EQUIPE"),
                                  "horas": float(t.get("horas", 0) or 0)})
                projeto.horas_trabalho = horas
                projeto.total_horas = sum(h["horas"] for h in horas)
            elif df.get("horas_totais") and not horas:
                projeto.horas_trabalho = [{"pessoa": "EQUIPE", "horas": float(df["horas_totais"])}]
                projeto.total_horas = float(df["horas_totais"])

            projeto.data_atualizacao = datetime.now()
            db.commit()
            _batch["done"] += 1

        except Exception as e:
            logger.error(f"Erro no batch [{proj_id}]: {e}")
            _batch["errors"] += 1
            try:
                if projeto:
                    projeto.analise_unificada = -1
                    db.commit()
            except Exception:
                pass
        finally:
            for p in downloaded:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass
            db.close()

        await asyncio.sleep(0.5)

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

    # Pendentes = analise_unificada != 1 e tem URLs de imagem
    pendentes = []
    for p in todos:
        if p.analise_unificada == 1 and p.estrutura is not None:
            continue
        if not p.urls_anexos:
            continue
        if not any(_url_eh_imagem(u) for u in p.urls_anexos):
            continue
        pendentes.append(p)

    if not pendentes:
        return {"mensagem": "Todos os projetos já foram analisados"}

    ids = [p.id for p in pendentes]
    background_tasks.add_task(_run_batch, ids)

    return {
        "mensagem": f"Iniciando análise de {len(ids)} projetos em background",
        "total_a_analisar": len(ids),
        "estimativa_minutos": round(len(ids) * 0.5, 1),  # ~30s por card (1 chamada Vision)
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
    ja = sum(1 for p in todos if p.analise_unificada == 1)
    erro = sum(1 for p in todos if p.analise_unificada == -1)
    com_estrutura = sum(1 for p in todos if p.estrutura and p.estrutura.get("encosto") not in (None, "desconhecido"))
    com_ficha = sum(1 for p in todos if p.dados_ficha and p.dados_ficha.get("metragem_tecido"))
    pct = round(ja / total_banco * 100, 1) if total_banco else 0

    return {
        "batch": _batch,
        "banco": {
            "total": total_banco,
            "analisados": ja,
            "com_erro": erro,
            "com_estrutura": com_estrutura,
            "com_ficha_completa": com_ficha,
            "pendentes": total_banco - ja - erro,
            "pct_completo": pct,
        }
    }


# ─── MATCHING ───────────────────────────────────────────────────────────────────

def _projeto_metricas(p: ProjetoORM) -> dict:
    """
    Extrai métricas de orçamento de um projeto.
    Prioridade: dados_ficha (Vision) → materiais/horas (legado) → defaults.
    Retorna também flag indicando se vieram da ficha.
    """
    df = p.dados_ficha or {}

    m_tecido = df.get("metragem_tecido")
    if not m_tecido:
        m_tecido = 0.0
        for m in (p.materiais or []):
            if "TECIDO" in m.get("nome", "").upper():
                m_tecido += float(m.get("quantidade", 0))
    m_tecido = float(m_tecido) if m_tecido else 17.0

    horas = df.get("horas_totais")
    if not horas:
        horas = float(p.total_horas or 0)
        if not horas:
            horas = sum(float(h.get("horas", 0)) for h in (p.horas_trabalho or []))
    horas = float(horas) if horas else 32.0

    tem_dados_reais = bool(df.get("metragem_tecido") or df.get("horas_totais"))

    return {
        "m_tecido": m_tecido,
        "horas_totais": horas,
        "tem_dados_reais": tem_dados_reais,
        "tipo_peca": df.get("tipo_peca"),
        "quantidade_pecas": df.get("quantidade_pecas"),
        "valor_espuma": df.get("valor_espuma"),
        "valor_mo": df.get("valor_mo"),
    }


def _projetos_para_banco(projetos: List[ProjetoORM]) -> List[dict]:
    """
    UMA entrada por projeto (não por foto). Usa estrutura unificada do card.
    Só projetos com estrutura identificada entram no pool de matching estrutural.
    """
    banco = []
    for p in projetos:
        metricas = _projeto_metricas(p)
        est = p.estrutura or {}
        encosto = est.get("encosto", "desconhecido")
        tem_estrutura = encosto not in (None, "", "desconhecido")

        # Foto a exibir: a foto do estofado escolhida pelo Vision (não fichas)
        foto_url = p.foto_estofado_url
        if not foto_url and p.urls_anexos:
            foto_url = p.urls_anexos[0]

        banco.append({
            "id": p.id,
            "categoria": p.nome,
            "estrutura_encosto": encosto,
            "estrutura_assento": est.get("assento", "desconhecido"),
            "estrutura_braco": est.get("braco", "desconhecido"),
            "confianca_vision": est.get("confianca", "média"),
            "descricao_estrutura": est.get("descricao_resumida", ""),
            "foto_antes_url": foto_url,
            "trello_card_url": p.trello_card_url,
            "mes_entrega": p.mes_entrega,
            "ano_entrega": p.ano_entrega,
            "m_tecido": metricas["m_tecido"],
            "horas_totais": metricas["horas_totais"],
            "tem_dados_reais": metricas["tem_dados_reais"],
            "tipo_peca": metricas["tipo_peca"],
            "quantidade_pecas": metricas["quantidade_pecas"],
            "valor_espuma": metricas["valor_espuma"],
            "valor_mo": metricas["valor_mo"],
            "tem_estrutura": tem_estrutura,
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
    Pipeline completo: analisa foto do cliente → comparação visual real com banco histórico.
    Cada candidato é comparado visualmente (Vision) com a foto enviada — sem tag matching.
    """
    temp_path = None
    candidatos_paths: List[str] = []
    try:
        temp_path = Path(f"./temp_uploads/{file.filename}")
        temp_path.parent.mkdir(exist_ok=True)
        with open(temp_path, "wb") as f:
            f.write(await file.read())

        # 1) Analisa a foto enviada (obtém tipo_peca para pré-filtro e dados de exibição)
        logger.info("Analisando estrutura da foto enviada...")
        resultado_upload = await asyncio.to_thread(
            card_analyzer.analyze_card, [str(temp_path)], [""]
        )
        estrutura_upload = resultado_upload.get("estrutura", {})
        dados_ficha_upload = resultado_upload.get("dados_ficha", {})
        tipo_peca_upload = (dados_ficha_upload.get("tipo_peca") or "").upper().strip()
        logger.info(f"Estrutura: {estrutura_upload}, tipo_peca: {tipo_peca_upload}")

        # 2) Pool de candidatos: projetos analisados com foto do estofado identificada
        projetos_todos = (
            db.query(ProjetoORM)
            .filter(ProjetoORM.analise_unificada == 1)
            .order_by(ProjetoORM.data_criacao.desc())
            .all()
        )
        banco_real = _projetos_para_banco(projetos_todos)
        banco_map = {b["id"]: b for b in banco_real}

        # Apenas candidatos com foto do estofado para comparação visual
        candidatos_pool = [p for p in projetos_todos if p.foto_estofado_url]

        # Pré-filtro por tipo_peca: se sabemos que é POLTRONA, só compara com POLTRONA
        if tipo_peca_upload and len(candidatos_pool) > 10:
            filtrados = [
                p for p in candidatos_pool
                if p.dados_ficha and (p.dados_ficha.get("tipo_peca") or "").upper() == tipo_peca_upload
            ]
            if len(filtrados) >= 3:
                candidatos_pool = filtrados
                logger.info(f"Pré-filtro tipo_peca={tipo_peca_upload}: {len(filtrados)} candidatos")

        # Limita a 60 candidatos para controlar custo/latência
        if len(candidatos_pool) > 60:
            candidatos_pool = candidatos_pool[:60]

        # 3) Baixa fotos dos candidatos em paralelo
        oauth = _trello_oauth_header()
        base_dir = Path("./temp_uploads")
        base_dir.mkdir(exist_ok=True)
        sem = asyncio.Semaphore(8)

        async def _baixar_candidato(proj: ProjetoORM, idx: int):
            url = proj.foto_estofado_url
            async with sem:
                try:
                    headers = {"Authorization": oauth} if "trello.com" in url else {}
                    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as hc:
                        resp = await hc.get(url, headers=headers)
                    if resp.status_code != 200:
                        return None
                    ct = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                    if ct and not ct.startswith("image/"):
                        return None
                    p = base_dir / f"cand_{proj.id}_{idx}.jpg"
                    with open(p, "wb") as fh:
                        fh.write(resp.content)
                    return {"id": proj.id, "path": str(p), "media_type": ct or "image/jpeg"}
                except Exception as e:
                    logger.warning(f"Falha download candidato {url[:50]}: {e}")
                    return None

        tasks = [_baixar_candidato(p, i) for i, p in enumerate(candidatos_pool)]
        raw_results = await asyncio.gather(*tasks)
        candidatos_com_path = [r for r in raw_results if r is not None]
        candidatos_paths = [c["path"] for c in candidatos_com_path]
        logger.info(f"Comparação visual: {len(candidatos_com_path)} candidatos baixados")

        # 4) Comparação visual em lotes (1 chamada Vision por lote de 14)
        scores_list = await asyncio.to_thread(
            card_analyzer.comparar_foto_com_candidatos,
            str(temp_path),
            candidatos_com_path,
        )
        score_map = {s["id"]: s for s in scores_list}

        # 5) Monta resultados
        matches = []
        for proj in candidatos_pool:
            score_data = score_map.get(proj.id)
            if not score_data:
                continue
            b = banco_map.get(proj.id, {})
            metricas = _projeto_metricas(proj)
            matches.append({
                "entrada_id": proj.id,
                "categoria": proj.nome,
                "similaridade_pct": round(score_data["score"], 1),
                "similaridade_nota": score_data.get("nota", ""),
                "m_tecido": metricas["m_tecido"],
                "horas_totais": metricas["horas_totais"],
                "tem_dados_reais": metricas["tem_dados_reais"],
                "tipo_peca": metricas["tipo_peca"],
                "quantidade_pecas": metricas["quantidade_pecas"],
                "valor_espuma": metricas["valor_espuma"],
                "valor_mo": metricas["valor_mo"],
                "custo_historico": 0,
                "foto_url": b.get("foto_antes_url"),
                "encosto": b.get("estrutura_encosto"),
                "assento": b.get("estrutura_assento"),
                "braco": b.get("estrutura_braco"),
                "descricao_estrutura": b.get("descricao_estrutura", ""),
                "trello_card_url": b.get("trello_card_url"),
                "mes_entrega": b.get("mes_entrega"),
                "ano_entrega": b.get("ano_entrega"),
            })

        matches.sort(key=lambda x: (-x["similaridade_pct"], 0 if x["tem_dados_reais"] else 1))
        matches = matches[:top_n]

        if not matches:
            matches = [{
                "entrada_id": "exemplo",
                "categoria": "Banco vazio — rode /matching/analisar-historico",
                "similaridade_pct": 0,
                "m_tecido": 17.0, "horas_totais": 32.0,
                "tem_dados_reais": False,
                "foto_url": None,
                "encosto": "desconhecido", "assento": "desconhecido", "braco": "desconhecido",
            }]

        return {
            "sucesso": True,
            "analise_foto": {
                "encosto": estrutura_upload.get("encosto"),
                "assento": estrutura_upload.get("assento"),
                "braco": estrutura_upload.get("braco"),
                "modulos": estrutura_upload.get("modulos"),
                "descricao_resumida": estrutura_upload.get("descricao_resumida"),
                "confianca": estrutura_upload.get("confianca"),
                "tipo_peca": tipo_peca_upload or None,
            },
            "matches_encontrados": len(matches),
            "top_matches": matches,
            "recomendacao": matches[0] if matches else None,
            "total_no_banco": len(projetos_todos),
            "total_com_foto": len(candidatos_com_path),
            "total_analisados_estruturalmente": len([p for p in projetos_todos if p.estrutura and p.estrutura.get("encosto") not in (None, "desconhecido")]),
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
        for cp in candidatos_paths:
            try:
                Path(cp).unlink(missing_ok=True)
            except Exception:
                pass


@router.get("/banco-exemplo")
async def obter_banco_exemplo() -> dict:
    return {"total_entradas": len(BANCO_EXEMPLO), "entradas": BANCO_EXEMPLO}
