from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from datetime import datetime
from typing import List, Optional, Dict, Any
import uuid

from database import ProjetoORM, AnaliseProjeto
from models import Projeto, MaterialUtilizado, HorasTrabalhadas


class ProjetosService:
    """Serviço para gerenciar projetos no banco de dados"""

    @staticmethod
    def criar_projeto(db: Session, projeto: Projeto) -> ProjetoORM:
        """Cria um novo projeto"""
        projeto_id = f"proj_{uuid.uuid4().hex[:12]}"

        # Calcula total de horas
        total_horas = sum(h.horas for h in projeto.horas_trabalho) if projeto.horas_trabalho else 0

        db_projeto = ProjetoORM(
            id=projeto_id,
            nome=projeto.nome,
            cliente=projeto.cliente,
            mes_entrega=projeto.mes_entrega,
            ano_entrega=projeto.ano_entrega,
            data_entrega_estimada=projeto.data_entrega_estimada,
            foto_antes=projeto.foto_antes,
            foto_depois=projeto.foto_depois,
            urls_anexos=projeto.urls_anexos,
            materiais=[m.model_dump() for m in projeto.materiais] if projeto.materiais else [],
            horas_trabalho=[h.model_dump() for h in projeto.horas_trabalho] if projeto.horas_trabalho else [],
            total_horas=total_horas,
            comparacao=projeto.comparacao.model_dump() if projeto.comparacao else None,
            descricao=projeto.descricao,
            observacoes=projeto.observacoes,
            trello_card_id=projeto.trello_card_id,
            trello_card_url=projeto.trello_card_url,
        )

        db.add(db_projeto)
        db.commit()
        db.refresh(db_projeto)

        return db_projeto

    @staticmethod
    def obter_projeto(db: Session, projeto_id: str) -> Optional[ProjetoORM]:
        """Obtém um projeto por ID"""
        return db.query(ProjetoORM).filter(ProjetoORM.id == projeto_id).first()

    @staticmethod
    def obter_por_trello_card(db: Session, trello_card_id: str) -> Optional[ProjetoORM]:
        """Obtém um projeto por ID do card do Trello"""
        return db.query(ProjetoORM).filter(ProjetoORM.trello_card_id == trello_card_id).first()

    @staticmethod
    def listar_projetos(
        db: Session,
        cliente: Optional[str] = None,
        mes: Optional[str] = None,
        ano: Optional[int] = None,
        skip: int = 0,
        limit: int = 10
    ) -> tuple[List[ProjetoORM], int]:
        """Lista projetos com filtros opcionais"""
        query = db.query(ProjetoORM)

        if cliente:
            query = query.filter(ProjetoORM.cliente.ilike(f"%{cliente}%"))
        if mes:
            query = query.filter(ProjetoORM.mes_entrega.ilike(f"%{mes}%"))
        if ano:
            query = query.filter(ProjetoORM.ano_entrega == ano)

        total = query.count()
        projetos = query.offset(skip).limit(limit).all()

        return projetos, total

    @staticmethod
    def atualizar_projeto(db: Session, projeto_id: str, projeto: Projeto) -> Optional[ProjetoORM]:
        """Atualiza um projeto existente"""
        db_projeto = db.query(ProjetoORM).filter(ProjetoORM.id == projeto_id).first()

        if not db_projeto:
            return None

        # Atualiza campos
        db_projeto.nome = projeto.nome
        db_projeto.cliente = projeto.cliente
        db_projeto.mes_entrega = projeto.mes_entrega
        db_projeto.ano_entrega = projeto.ano_entrega
        db_projeto.data_entrega_estimada = projeto.data_entrega_estimada
        db_projeto.foto_antes = projeto.foto_antes
        db_projeto.foto_depois = projeto.foto_depois
        db_projeto.urls_anexos = projeto.urls_anexos
        db_projeto.materiais = [m.model_dump() for m in projeto.materiais] if projeto.materiais else []
        db_projeto.horas_trabalho = [h.model_dump() for h in projeto.horas_trabalho] if projeto.horas_trabalho else []
        db_projeto.total_horas = sum(h.horas for h in projeto.horas_trabalho) if projeto.horas_trabalho else 0
        db_projeto.comparacao = projeto.comparacao.model_dump() if projeto.comparacao else None
        db_projeto.descricao = projeto.descricao
        db_projeto.observacoes = projeto.observacoes
        db_projeto.data_atualizacao = datetime.now()

        db.commit()
        db.refresh(db_projeto)

        return db_projeto

    @staticmethod
    def deletar_projeto(db: Session, projeto_id: str) -> bool:
        """Deleta um projeto"""
        db_projeto = db.query(ProjetoORM).filter(ProjetoORM.id == projeto_id).first()

        if not db_projeto:
            return False

        db.delete(db_projeto)
        db.commit()

        return True

    @staticmethod
    def adicionar_material(db: Session, projeto_id: str, material: MaterialUtilizado) -> Optional[ProjetoORM]:
        """Adiciona um material a um projeto"""
        db_projeto = db.query(ProjetoORM).filter(ProjetoORM.id == projeto_id).first()

        if not db_projeto:
            return None

        db_projeto.materiais.append(material.model_dump())
        db_projeto.data_atualizacao = datetime.now()

        db.commit()
        db.refresh(db_projeto)

        return db_projeto

    @staticmethod
    def deletar_material(db: Session, projeto_id: str, indice: int) -> Optional[ProjetoORM]:
        """Deleta um material de um projeto"""
        db_projeto = db.query(ProjetoORM).filter(ProjetoORM.id == projeto_id).first()

        if not db_projeto or indice < 0 or indice >= len(db_projeto.materiais):
            return None

        del db_projeto.materiais[indice]
        db_projeto.data_atualizacao = datetime.now()

        db.commit()
        db.refresh(db_projeto)

        return db_projeto

    @staticmethod
    def adicionar_horas(db: Session, projeto_id: str, horas: HorasTrabalhadas) -> Optional[ProjetoORM]:
        """Adiciona um registro de horas a um projeto"""
        db_projeto = db.query(ProjetoORM).filter(ProjetoORM.id == projeto_id).first()

        if not db_projeto:
            return None

        db_projeto.horas_trabalho.append(horas.model_dump())
        db_projeto.total_horas = sum(h["horas"] for h in db_projeto.horas_trabalho)
        db_projeto.data_atualizacao = datetime.now()

        db.commit()
        db.refresh(db_projeto)

        return db_projeto

    @staticmethod
    def deletar_horas(db: Session, projeto_id: str, indice: int) -> Optional[ProjetoORM]:
        """Deleta um registro de horas de um projeto"""
        db_projeto = db.query(ProjetoORM).filter(ProjetoORM.id == projeto_id).first()

        if not db_projeto or indice < 0 or indice >= len(db_projeto.horas_trabalho):
            return None

        del db_projeto.horas_trabalho[indice]
        db_projeto.total_horas = sum(h["horas"] for h in db_projeto.horas_trabalho)
        db_projeto.data_atualizacao = datetime.now()

        db.commit()
        db.refresh(db_projeto)

        return db_projeto

    @staticmethod
    def registrar_analise(
        db: Session,
        projeto_id: str,
        materiais_extraidos: List[Dict[str, Any]],
        horas_extraidas: List[Dict[str, Any]],
        observacoes: Optional[str],
        urls_imagens: List[str]
    ) -> AnaliseProjeto:
        """Registra uma análise Vision no histórico"""
        analise = AnaliseProjeto(
            id=f"anl_{uuid.uuid4().hex[:12]}",
            projeto_id=projeto_id,
            materiais_extraidos=materiais_extraidos,
            horas_extraidas=horas_extraidas,
            observacoes_extraidas=observacoes,
            urls_imagens=urls_imagens,
        )

        db.add(analise)
        db.commit()
        db.refresh(analise)

        return analise

    @staticmethod
    def obter_analises_projeto(db: Session, projeto_id: str) -> List[AnaliseProjeto]:
        """Obtém todas as análises de um projeto"""
        return db.query(AnaliseProjeto).filter(AnaliseProjeto.projeto_id == projeto_id).all()
