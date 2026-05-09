from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class HorasTrabalhadas(BaseModel):
    """Registro de horas por pessoa"""
    pessoa: str  # JOEL, DOUGLAS, PAULO, GABRIEL, EDYMAR, etc
    horas: float
    descricao: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "pessoa": "JOEL",
                "horas": 30.5,
                "descricao": "Costura e corte"
            }
        }

class MaterialUtilizado(BaseModel):
    """Materiais como tecido, espuma, etc"""
    nome: str  # "TECIDO", "ESPUMA", "ACOPLAR", etc
    quantidade: float
    unidade: str  # "MT", "KG", "UN", "DIAS", etc
    observacoes: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "nome": "TECIDO",
                "quantidade": 17,
                "unidade": "MT",
                "observacoes": "Tecido bege 100% algodão"
            }
        }

class OrcamentoVsRealidade(BaseModel):
    """Comparação entre orçado e realizado"""
    # Orçado
    horas_orcadas: Optional[float] = None
    metragem_orcada: Optional[float] = None
    valor_orcado: Optional[float] = None

    # Realidade
    horas_reais: Optional[float] = None
    metragem_real: Optional[float] = None
    valor_real: Optional[float] = None

    # Variação
    variacao_horas_pct: Optional[float] = None  # (real - orçado) / orçado * 100
    variacao_metragem_pct: Optional[float] = None
    variacao_valor_pct: Optional[float] = None

class Projeto(BaseModel):
    """Modelo principal: cada Card do Trello é um Projeto (cliente + sofá)"""
    id: Optional[str] = None
    nome: str  # Nome do projeto/sofá
    cliente: str  # Nome do cliente

    # Entrega
    mes_entrega: str  # "MAIO", "ABRIL", "MARÇO"
    ano_entrega: int  # 2026, 2025, etc
    data_entrega_estimada: Optional[datetime] = None

    # Fotos e documentação
    foto_antes: Optional[str] = None  # URL ou path da foto antes
    foto_depois: Optional[str] = None  # URL ou path da foto depois
    urls_anexos: List[str] = Field(default_factory=list)  # URLs dos anexos no Trello

    # Materiais utilizados
    materiais: List[MaterialUtilizado] = Field(default_factory=list)

    # Horas de trabalho
    horas_trabalho: List[HorasTrabalhadas] = Field(default_factory=list)
    total_horas: float = 0  # Calculado automaticamente

    # Orçamento vs Realidade
    comparacao: Optional[OrcamentoVsRealidade] = None

    # Observações gerais
    descricao: Optional[str] = None
    observacoes: Optional[str] = None

    # Rastreabilidade
    data_criacao: datetime = Field(default_factory=datetime.now)
    data_atualizacao: datetime = Field(default_factory=datetime.now)
    trello_card_id: str  # ID do card no Trello
    trello_card_url: Optional[str] = None
    ultimo_anexo_coletado: Optional[datetime] = None  # Para polling de 24h

    class Config:
        json_schema_extra = {
            "example": {
                "nome": "Sofá Três Módulos",
                "cliente": "João Silva",
                "mes_entrega": "MAIO",
                "ano_entrega": 2026,
                "materiais": [
                    {
                        "nome": "TECIDO",
                        "quantidade": 17,
                        "unidade": "MT",
                        "observacoes": "Tecido bege"
                    },
                    {
                        "nome": "ESPUMA",
                        "quantidade": 5,
                        "unidade": "KG"
                    }
                ],
                "horas_trabalho": [
                    {
                        "pessoa": "JOEL",
                        "horas": 30,
                        "descricao": "Costura"
                    },
                    {
                        "pessoa": "DOUGLAS",
                        "horas": 10,
                        "descricao": "Corte"
                    }
                ],
                "total_horas": 40.0,
                "trello_card_id": "abc123xyz"
            }
        }

class HealthResponse(BaseModel):
    status: str
