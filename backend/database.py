from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os

# URL do banco de dados
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///orcamento.db")

# Criar engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base para modelos ORM
Base = declarative_base()


class ProjetoORM(Base):
    """Modelo ORM para projetos (cards do Trello)"""
    __tablename__ = "projetos"

    id = Column(String, primary_key=True)
    nome = Column(String, nullable=False)
    cliente = Column(String, nullable=False)

    # Entrega
    mes_entrega = Column(String)
    ano_entrega = Column(Integer)
    data_entrega_estimada = Column(DateTime, nullable=True)

    # Fotos
    foto_antes = Column(String, nullable=True)
    foto_depois = Column(String, nullable=True)
    urls_anexos = Column(JSON, default=[])

    # Materiais (JSON array)
    materiais = Column(JSON, default=[])

    # Horas (JSON array)
    horas_trabalho = Column(JSON, default=[])
    total_horas = Column(Float, default=0.0)

    # Comparação (JSON object)
    comparacao = Column(JSON, nullable=True)

    # Observações
    descricao = Column(String, nullable=True)
    observacoes = Column(String, nullable=True)

    # Rastreabilidade
    data_criacao = Column(DateTime, default=datetime.now)
    data_atualizacao = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    trello_card_id = Column(String, unique=True, nullable=True)
    trello_card_url = Column(String, nullable=True)
    ultimo_anexo_coletado = Column(DateTime, nullable=True)

    # Relacionamentos
    analises_vision = relationship("AnaliseProjeto", back_populates="projeto", cascade="all, delete-orphan")

    def to_dict(self):
        """Converte para dicionário"""
        return {
            "id": self.id,
            "nome": self.nome,
            "cliente": self.cliente,
            "mes_entrega": self.mes_entrega,
            "ano_entrega": self.ano_entrega,
            "data_entrega_estimada": self.data_entrega_estimada.isoformat() if self.data_entrega_estimada else None,
            "foto_antes": self.foto_antes,
            "foto_depois": self.foto_depois,
            "urls_anexos": self.urls_anexos,
            "materiais": self.materiais,
            "horas_trabalho": self.horas_trabalho,
            "total_horas": self.total_horas,
            "comparacao": self.comparacao,
            "descricao": self.descricao,
            "observacoes": self.observacoes,
            "data_criacao": self.data_criacao.isoformat(),
            "data_atualizacao": self.data_atualizacao.isoformat(),
            "trello_card_id": self.trello_card_id,
            "trello_card_url": self.trello_card_url,
            "ultimo_anexo_coletado": self.ultimo_anexo_coletado.isoformat() if self.ultimo_anexo_coletado else None,
        }


class AnaliseProjeto(Base):
    """Histórico de análises Vision de um projeto"""
    __tablename__ = "analises_projeto"

    id = Column(String, primary_key=True)
    projeto_id = Column(String, ForeignKey("projetos.id"), nullable=False)

    # Dados extraídos
    materiais_extraidos = Column(JSON)
    horas_extraidas = Column(JSON)
    observacoes_extraidas = Column(String)

    # URLs das imagens analisadas
    urls_imagens = Column(JSON, default=[])

    # Metadata
    data_analise = Column(DateTime, default=datetime.now)
    modelo_vision = Column(String, default="claude-opus-4-1-20250805")

    # Relacionamento
    projeto = relationship("ProjetoORM", back_populates="analises_vision")

    def to_dict(self):
        return {
            "id": self.id,
            "projeto_id": self.projeto_id,
            "materiais_extraidos": self.materiais_extraidos,
            "horas_extraidas": self.horas_extraidas,
            "observacoes_extraidas": self.observacoes_extraidas,
            "urls_imagens": self.urls_imagens,
            "data_analise": self.data_analise.isoformat(),
            "modelo_vision": self.modelo_vision,
        }


def init_db():
    """Inicializa o banco de dados (cria todas as tabelas)"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency para FastAPI — fornece sessão do banco"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
