from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from typing import List, Optional
from datetime import datetime
import os
from pathlib import Path

from models import Orcamento, StatusOrcamento
from services.pdf_loader import carregar_orcamentos_inicial
from services.trello_sync import criar_sync_trello
from services.vision_api import criar_vision

router = APIRouter(prefix="/orcamentos", tags=["orcamentos"])

# TODO: Implementar banco de dados (SQLAlchemy)
orcamentos_db = {}  # Simulação em memória

@router.get("/")
async def listar_orcamentos(
    status: Optional[StatusOrcamento] = None,
    cliente: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100)
) -> dict:
    """Lista orçamentos com filtros opcionais"""
    resultado = list(orcamentos_db.values())

    if status:
        resultado = [o for o in resultado if o.get("status") == status]
    if cliente:
        resultado = [o for o in resultado if cliente.lower() in o.get("cliente", "").lower()]

    return {
        "total": len(resultado),
        "skip": skip,
        "limit": limit,
        "orcamentos": resultado[skip:skip + limit]
    }

@router.get("/{orcamento_id}")
async def obter_orcamento(orcamento_id: str) -> dict:
    """Obtém detalhes de um orçamento específico"""
    if orcamento_id not in orcamentos_db:
        raise HTTPException(status_code=404, detail="Orçamento não encontrado")
    return orcamentos_db[orcamento_id]

@router.post("/")
async def criar_orcamento(orcamento: Orcamento) -> dict:
    """Cria um novo orçamento manualmente"""
    orcamento_id = f"orc_{datetime.now().timestamp()}"
    orcamento_dict = orcamento.model_dump()
    orcamento_dict["id"] = orcamento_id
    orcamentos_db[orcamento_id] = orcamento_dict
    return orcamento_dict

@router.patch("/{orcamento_id}")
async def atualizar_orcamento(orcamento_id: str, orcamento: Orcamento) -> dict:
    """Atualiza um orçamento existente"""
    if orcamento_id not in orcamentos_db:
        raise HTTPException(status_code=404, detail="Orçamento não encontrado")

    orcamento_dict = orcamento.model_dump()
    orcamento_dict["id"] = orcamento_id
    orcamento_dict["data_atualizacao"] = datetime.now()
    orcamentos_db[orcamento_id] = orcamento_dict
    return orcamento_dict

@router.delete("/{orcamento_id}")
async def deletar_orcamento(orcamento_id: str) -> dict:
    """Deleta um orçamento"""
    if orcamento_id not in orcamentos_db:
        raise HTTPException(status_code=404, detail="Orçamento não encontrado")
    del orcamentos_db[orcamento_id]
    return {"mensagem": "Orçamento deletado"}

@router.post("/upload-imagem")
async def analisar_imagem_orcamento(file: UploadFile = File(...)) -> dict:
    """
    Faz upload de imagem de orçamento e analisa com Claude Vision
    Retorna dados estruturados extraídos da imagem
    """
    # Salva arquivo temporariamente
    upload_dir = Path("./uploads")
    upload_dir.mkdir(exist_ok=True)

    file_path = upload_dir / file.filename
    with open(file_path, "wb") as f:
        f.write(await file.read())

    try:
        # Usa Vision API para analisar
        vision = criar_vision(os.getenv("ANTHROPIC_API_KEY"))
        resultado = vision.analisar_orçamento_sofa(str(file_path))

        # Cria orçamento a partir dos dados extraídos
        dados_orcamento = resultado["dados"]
        orcamento = Orcamento(**dados_orcamento, fonte="vision")
        orcamento_salvo = await criar_orcamento(orcamento)

        return {
            "sucesso": True,
            "orcamento": orcamento_salvo,
            "analise": resultado
        }

    finally:
        # Remove arquivo temporário
        if file_path.exists():
            file_path.unlink()

@router.post("/sincronizar-pdf")
async def sincronizar_pdf(pdf_path: str = None) -> dict:
    """
    Carrega orçamentos iniciais do PDF
    Se não informar path, usa o padrão do desktop
    """
    if not pdf_path:
        pdf_path = r"C:\Users\thiag\OneDrive\Área de Trabalho\Orçamentos - Google Docs.pdf"

    try:
        orcamentos_carregados = carregar_orcamentos_inicial(pdf_path)
        # Salva no DB
        for orc in orcamentos_carregados:
            orc_obj = Orcamento(**orc, fonte="pdf")
            await criar_orcamento(orc_obj)

        return {
            "sucesso": True,
            "quantidade": len(orcamentos_carregados),
            "orcamentos": orcamentos_carregados
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao carregar PDF: {str(e)}")

@router.post("/sincronizar-trello")
async def sincronizar_trello(
    api_key: str,
    api_token: str,
    board_id: str
) -> dict:
    """Sincroniza orçamentos do Trello"""
    try:
        sync = criar_sync_trello(api_key, api_token, board_id)
        dados_trello = await sync.sincronizar_tudo()

        # Processa cards do Trello e converte para orçamentos
        orcamentos_criados = 0
        for lista in dados_trello.get("listas", []):
            for card in lista.get("cards", []):
                # TODO: Implementar mapeamento de card Trello → Orcamento
                orcamentos_criados += 1

        await sync.fechar()

        return {
            "sucesso": True,
            "quantidade": orcamentos_criados,
            "dados": dados_trello
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao sincronizar Trello: {str(e)}")
