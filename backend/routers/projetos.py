from fastapi import APIRouter, UploadFile, File, HTTPException, Query, BackgroundTasks, Depends
from typing import List, Optional
from datetime import datetime, timedelta
import os
from pathlib import Path
from sqlalchemy.orm import Session

from models import Projeto, HorasTrabalhadas, MaterialUtilizado, OrcamentoVsRealidade
from services.trello_sync import criar_sync_trello
from services.vision_api import criar_vision
from services.projetos_service import ProjetosService
from database import get_db

router = APIRouter(prefix="/projetos", tags=["projetos"])

@router.get("/")
async def listar_projetos(
    cliente: Optional[str] = None,
    mes: Optional[str] = None,
    ano: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db)
) -> dict:
    """Lista todos os projetos com filtros opcionais"""
    projetos, total = ProjetosService.listar_projetos(
        db, cliente=cliente, mes=mes, ano=ano, skip=skip, limit=limit
    )

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "projetos": [p.to_dict() for p in projetos]
    }

@router.get("/{projeto_id}")
async def obter_projeto(projeto_id: str, db: Session = Depends(get_db)) -> dict:
    """Obtém detalhes de um projeto específico"""
    projeto = ProjetosService.obter_projeto(db, projeto_id)

    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")

    return projeto.to_dict()

@router.post("/")
async def criar_projeto(projeto: Projeto, db: Session = Depends(get_db)) -> dict:
    """Cria um novo projeto manualmente"""
    db_projeto = ProjetosService.criar_projeto(db, projeto)
    return db_projeto.to_dict()

@router.patch("/{projeto_id}")
async def atualizar_projeto(projeto_id: str, projeto: Projeto, db: Session = Depends(get_db)) -> dict:
    """Atualiza um projeto existente"""
    db_projeto = ProjetosService.atualizar_projeto(db, projeto_id, projeto)

    if not db_projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")

    return db_projeto.to_dict()

@router.delete("/{projeto_id}")
async def deletar_projeto(projeto_id: str, db: Session = Depends(get_db)) -> dict:
    """Deleta um projeto"""
    sucesso = ProjetosService.deletar_projeto(db, projeto_id)

    if not sucesso:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")

    return {"mensagem": "Projeto deletado"}

@router.post("/{projeto_id}/materiais")
async def adicionar_material(projeto_id: str, material: MaterialUtilizado, db: Session = Depends(get_db)) -> dict:
    """Adiciona um material a um projeto"""
    db_projeto = ProjetosService.adicionar_material(db, projeto_id, material)

    if not db_projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")

    return {"mensagem": "Material adicionado", "projeto": db_projeto.to_dict()}

@router.post("/{projeto_id}/horas")
async def adicionar_horas(projeto_id: str, horas: HorasTrabalhadas, db: Session = Depends(get_db)) -> dict:
    """Adiciona registro de horas a um projeto"""
    db_projeto = ProjetosService.adicionar_horas(db, projeto_id, horas)

    if not db_projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")

    return {"mensagem": "Horas adicionadas", "projeto": db_projeto.to_dict()}

@router.delete("/{projeto_id}/materiais/{indice}")
async def deletar_material(projeto_id: str, indice: int, db: Session = Depends(get_db)) -> dict:
    """Deleta um material de um projeto"""
    db_projeto = ProjetosService.deletar_material(db, projeto_id, indice)

    if not db_projeto:
        raise HTTPException(status_code=404, detail="Projeto ou material não encontrado")

    return {"mensagem": "Material deletado", "projeto": db_projeto.to_dict()}

@router.delete("/{projeto_id}/horas/{indice}")
async def deletar_horas(projeto_id: str, indice: int, db: Session = Depends(get_db)) -> dict:
    """Deleta um registro de horas de um projeto"""
    db_projeto = ProjetosService.deletar_horas(db, projeto_id, indice)

    if not db_projeto:
        raise HTTPException(status_code=404, detail="Projeto ou horas não encontrado")

    return {"mensagem": "Horas deletadas", "projeto": db_projeto.to_dict()}

@router.post("/{projeto_id}/upload-anexos")
async def analisar_anexos_projeto(
    projeto_id: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db)
) -> dict:
    """
    Faz upload e análise de múltiplas imagens (orçamento, antes/depois, etc).
    Usa Claude Vision para extrair dados automaticamente.
    """
    db_projeto = ProjetosService.obter_projeto(db, projeto_id)

    if not db_projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")

    upload_dir = Path("./uploads")
    upload_dir.mkdir(exist_ok=True)

    caminhos_arquivos = []
    try:
        # Salva todos os arquivos
        for file in files:
            file_path = upload_dir / file.filename
            with open(file_path, "wb") as f:
                f.write(await file.read())
            caminhos_arquivos.append(str(file_path))

        # Usa Vision API para analisar
        vision = criar_vision(os.getenv("ANTHROPIC_API_KEY"))
        resultado = vision.analisar_multiplas_imagens(caminhos_arquivos)

        dados_extraidos = resultado["dados"]

        # Adiciona materiais extraídos
        if dados_extraidos.get("materiais"):
            for material_dict in dados_extraidos["materiais"]:
                material = MaterialUtilizado(**material_dict)
                db_projeto = ProjetosService.adicionar_material(db, projeto_id, material)

        # Adiciona horas extraídas
        if dados_extraidos.get("horas_trabalho"):
            for horas_dict in dados_extraidos["horas_trabalho"]:
                horas = HorasTrabalhadas(**horas_dict)
                db_projeto = ProjetosService.adicionar_horas(db, projeto_id, horas)

        # Adiciona observações
        if dados_extraidos.get("observacoes_gerais"):
            db_projeto.observacoes = (db_projeto.observacoes or "") + " | " + dados_extraidos["observacoes_gerais"]
            db_projeto.observacoes = db_projeto.observacoes.strip(" | ")

        # Registra análise no histórico
        ProjetosService.registrar_analise(
            db,
            projeto_id,
            dados_extraidos.get("materiais", []),
            dados_extraidos.get("horas_trabalho", []),
            dados_extraidos.get("observacoes_gerais"),
            caminhos_arquivos
        )

        db.commit()
        db.refresh(db_projeto)

        return {
            "sucesso": True,
            "mensagem": "Anexos analisados e dados extraídos",
            "imagens_processadas": len(caminhos_arquivos),
            "projeto": db_projeto.to_dict(),
            "dados_extraidos": dados_extraidos
        }

    finally:
        # Remove arquivos temporários
        for caminho in caminhos_arquivos:
            if Path(caminho).exists():
                Path(caminho).unlink()

@router.post("/sincronizar-trello")
async def sincronizar_projetos_trello(
    api_key: Optional[str] = None,
    api_token: Optional[str] = None,
    board_id: Optional[str] = None,
    db: Session = Depends(get_db)
) -> dict:
    """
    Sincroniza projetos do Trello.
    Busca apenas novos anexos adicionados nos últimos 24h.
    """
    if not api_key:
        api_key = os.getenv("TRELLO_API_KEY")
    if not api_token:
        api_token = os.getenv("TRELLO_API_TOKEN")
    if not board_id:
        board_id = os.getenv("TRELLO_BOARD_ID")

    if not all([api_key, api_token, board_id]):
        raise HTTPException(status_code=400,
                          detail="Credenciais Trello não configuradas")

    try:
        sync = criar_sync_trello(api_key, api_token, board_id)

        # Sincroniza APENAS novos anexos dos últimas 24h
        dados = await sync.sincronizar_novos_anexos(
            ultimo_check=datetime.now() - timedelta(hours=24)
        )

        projetos_criados = 0
        projetos_atualizados = 0

        # Processa cards com novos anexos
        for card_data in dados.get("cards_com_novos_anexos", []):
            card_id = card_data["id"]
            card_name = card_data["name"]

            # Busca ou cria projeto para este card
            db_projeto = ProjetosService.obter_por_trello_card(db, card_id)

            if not db_projeto:
                # Cria novo projeto
                novo_projeto = Projeto(
                    nome=card_name,
                    cliente=card_name,
                    mes_entrega="INDEFINIDO",
                    ano_entrega=datetime.now().year,
                    trello_card_id=card_id,
                    trello_card_url=card_data.get("url"),
                    descricao=card_data.get("desc", "")
                )
                db_projeto = ProjetosService.criar_projeto(db, novo_projeto)
                projetos_criados += 1
            else:
                projetos_atualizados += 1
                db_projeto.data_atualizacao = datetime.now()

            # Registra URLs dos novos anexos
            for anexo in card_data.get("novos_anexos", []):
                url = anexo.get("url")
                if url and url not in db_projeto.urls_anexos:
                    db_projeto.urls_anexos.append(url)

            db_projeto.ultimo_anexo_coletado = datetime.now()
            db.commit()

        await sync.fechar()

        return {
            "sucesso": True,
            "timestamp": dados["timestamp"],
            "novos_anexos_encontrados": dados["total_novos_anexos"],
            "projetos_criados": projetos_criados,
            "projetos_atualizados": projetos_atualizados,
            "cards_processados": len(dados.get("cards_com_novos_anexos", []))
        }

    except Exception as e:
        raise HTTPException(status_code=400,
                          detail=f"Erro ao sincronizar Trello: {str(e)}")

@router.post("/{projeto_id}/comparacao")
async def atualizar_comparacao(projeto_id: str, comparacao: OrcamentoVsRealidade, db: Session = Depends(get_db)) -> dict:
    """Atualiza ou cria comparação orçado vs real"""
    db_projeto = ProjetosService.obter_projeto(db, projeto_id)

    if not db_projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")

    db_projeto.comparacao = comparacao.model_dump()
    db_projeto.data_atualizacao = datetime.now()

    db.commit()
    db.refresh(db_projeto)

    return {"mensagem": "Comparação atualizada", "projeto": db_projeto.to_dict()}
