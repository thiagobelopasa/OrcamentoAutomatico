from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Query
from fastapi.responses import StreamingResponse
from pathlib import Path
from typing import List, Optional
import json
import traceback
import logging
import os
import httpx
from sqlalchemy.orm import Session

from services.vision_matcher import VisionMatcher, BANCO_EXEMPLO
from database import get_db, ProjetoORM

router = APIRouter(prefix="/matching", tags=["matching"])
logger = logging.getLogger(__name__)


@router.get("/proxy/imagem")
async def proxy_imagem(url: str):
    """
    Proxy para imagens do Trello (requer autenticação).
    Uso: /matching/proxy/imagem?url=<trello_url>
    """
    try:
        headers = {}
        if "trello.com" in url:
            key = os.getenv("TRELLO_API_KEY", "")
            token = os.getenv("TRELLO_API_TOKEN", "")
            # Trello ATTA tokens requerem OAuth header, não query params
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


def _projetos_para_banco(projetos: List[ProjetoORM]) -> List[dict]:
    """Converte ProjetoORM para o formato esperado pelo VisionMatcher."""
    banco = []
    for p in projetos:
        # Tenta extrair dados de estrutura Vision do campo observacoes
        estrutura = {
            "estrutura_encosto": "desconhecido",
            "estrutura_assento": "desconhecido",
            "estrutura_braco": "desconhecido",
        }
        if p.observacoes:
            obs = p.observacoes
            # Formato: "Análise Vision: {json}" ou só json
            if "Análise Vision:" in obs:
                obs = obs.split("Análise Vision:")[-1].strip()
            if obs.startswith("{"):
                try:
                    v = json.loads(obs)
                    estrutura = {
                        "estrutura_encosto": v.get("encosto", "desconhecido"),
                        "estrutura_assento": v.get("assento", "desconhecido"),
                        "estrutura_braco": v.get("braco", "desconhecido"),
                    }
                except Exception:
                    pass

        # Foto: primeira URL de anexo
        urls = p.urls_anexos or []
        foto_url = None
        if urls:
            # Preferencialmente a primeira foto (não ficha de OS)
            foto_url = urls[0]

        # Metragem de tecido
        m_tecido = 0.0
        for m in (p.materiais or []):
            nome_mat = m.get("nome", "").upper()
            if "TECIDO" in nome_mat:
                m_tecido += float(m.get("quantidade", 0))
        if not m_tecido:
            m_tecido = 17.0  # fallback padrão

        # Total horas
        total_horas = float(p.total_horas or 0)
        if not total_horas:
            total_horas = sum(float(h.get("horas", 0)) for h in (p.horas_trabalho or []))
        if not total_horas:
            total_horas = 32.0  # fallback padrão

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
    """
    Analisa foto com Claude Vision.
    Retorna descrição de estrutura (encosto, assento, braço).
    """
    try:
        temp_path = Path(f"./temp_uploads/{file.filename}")
        temp_path.parent.mkdir(exist_ok=True)

        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)

        descricao = VisionMatcher.descrever_foto(str(temp_path))

        if temp_path.exists():
            temp_path.unlink()

        return {
            "sucesso": True,
            "descricao": descricao
        }

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
    """
    Encontra top N matches no banco histórico baseado em estrutura.
    """
    try:
        descricao_foto = {
            "encosto": encosto,
            "assento": assento,
            "braco": braco,
            "modulos": modulos,
            "confianca": confianca
        }

        matches = VisionMatcher.encontrar_matches(
            descricao_foto,
            BANCO_EXEMPLO,
            top_n=top_n
        )

        return {
            "sucesso": True,
            "descricao_entrada": descricao_foto,
            "matches_encontrados": len(matches),
            "matches": matches
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao procurar matches: {str(e)}")


@router.post("/analisar-completo")
async def analisar_completo(
    file: UploadFile = File(...),
    top_n: int = 3,
    db: Session = Depends(get_db)
) -> dict:
    """
    Pipeline completo: analisa foto + encontra matches no banco histórico real.
    Usa projetos do Trello como base de comparação.
    """
    temp_path = None
    try:
        temp_path = Path(f"./temp_uploads/{file.filename}")
        temp_path.parent.mkdir(exist_ok=True)

        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)

        logger.info(f"Arquivo salvo: {temp_path}")

        # 1. Analisa estrutura da foto
        logger.info("Iniciando análise com Vision...")
        descricao = VisionMatcher.descrever_foto(str(temp_path))
        logger.info(f"Análise concluída: {descricao}")

        # 2. Busca projetos reais do banco (Trello)
        projetos = (
            db.query(ProjetoORM)
            .filter(ProjetoORM.trello_card_id.isnot(None))
            .order_by(ProjetoORM.data_criacao.desc())
            .limit(200)
            .all()
        )

        # 3. Converte para formato de matching
        banco_real = _projetos_para_banco(projetos)
        logger.info(f"Banco real: {len(banco_real)} projetos")

        # 4. Projetos COM estrutura conhecida vão primeiro no ranking
        banco_com_estrutura = [b for b in banco_real if b.get("tem_estrutura")]
        banco_sem_estrutura = [b for b in banco_real if not b.get("tem_estrutura")]

        # Busca matches estruturais nos que têm análise
        if banco_com_estrutura:
            matches_estruturais = VisionMatcher.encontrar_matches(
                descricao, banco_com_estrutura, top_n=top_n
            )
        else:
            matches_estruturais = []

        # Preenche com os mais recentes se precisar de mais
        ids_ja_incluidos = {m["entrada_id"] for m in matches_estruturais}
        candidatos_recentes = [
            b for b in banco_sem_estrutura
            if b["id"] not in ids_ja_incluidos
        ][:top_n]

        # Monta lista final: estruturais primeiro, recentes depois
        matches = matches_estruturais.copy()
        for c in candidatos_recentes:
            if len(matches) >= top_n:
                break
            matches.append({
                "entrada_id": c["id"],
                "categoria": c["categoria"],
                "similaridade_pct": 45.0,  # base por ser do mesmo acervo
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

        # Se banco real vazio, usa demo
        if not matches:
            logger.warning("Banco real vazio, usando BANCO_EXEMPLO")
            matches = VisionMatcher.encontrar_matches(descricao, BANCO_EXEMPLO, top_n=top_n)

        return {
            "sucesso": True,
            "analise_foto": descricao,
            "matches_encontrados": len(matches),
            "top_matches": matches,
            "recomendacao": matches[0] if matches else None,
            "total_no_banco": len(banco_real),
        }

    except Exception as e:
        error_msg = f"Erro na análise completa: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception as e:
                logger.warning(f"Erro ao deletar arquivo temporário: {e}")


@router.get("/banco-exemplo")
async def obter_banco_exemplo() -> dict:
    """Retorna banco histórico de exemplo para fins de teste."""
    return {
        "total_entradas": len(BANCO_EXEMPLO),
        "entradas": BANCO_EXEMPLO
    }
