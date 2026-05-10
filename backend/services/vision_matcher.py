import base64
import json
from typing import Optional, List, Dict, Any
from pathlib import Path
import os

try:
    from anthropic import Anthropic
    _ANTHROPIC_INSTALLED = True
except ImportError:
    _ANTHROPIC_INSTALLED = False

def _get_client():
    """Cria cliente Anthropic lazily, lendo a key no momento da chamada."""
    if not _ANTHROPIC_INSTALLED:
        return None
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        return Anthropic(api_key=key)
    except Exception:
        return None

class VisionMatcher:
    """Compara estrutura visual de sofás usando Claude Vision"""

    CALIBRATION_VOCAB = {
        "encosto": {
            "capitone_diagonal": "Padrão DIAGONAL ou CRUZADO em XX, formando losangos. Geralmente com botões.",
            "duas_almofadas": "Encosto dividido em DUAS partes por UMA costura HORIZONTAL única (NÃO é capitonê).",
            "gomos_verticais": "Linhas verticais paralelas dividindo encosto em colunas.",
            "capitone_quadrado": "Malha quadriculada regular (linhas H e V em ângulo reto, sem ser diagonal).",
            "liso": "Sem costuras nem padrão."
        },
        "assento": {
            "capitone_quadrado": "Padrão quadriculado regular nas almofadas.",
            "ondas_gomos": "Costuras horizontais em onda.",
            "gomos_verticais": "Linhas verticais dividindo almofada em colunas.",
            "lisa_costura": "Almofada cheia, costura apenas separando frente/trás."
        },
        "braco": {
            "quadrado_boxy": "Braço cúbico, faces planas, ângulos retos. Sem costura aparente.",
            "reto_costura": "Braço retangular com detalhe de costura visível na lateral.",
            "aluminio": "Tem peça de alumínio aparente.",
            "sem_braco": "Módulo lateral sem braço próprio."
        }
    }

    @staticmethod
    def descrever_foto(image_path: str) -> Dict[str, str]:
        """
        Analisa foto com Claude Vision e retorna descrição de estrutura.
        Ignora cor e tecido, foca em FORMA.
        Em modo demo, retorna análise de exemplo.
        """
        client = _get_client()
        if not client:
            return {
                "encosto": "duas_almofadas",
                "assento": "lisa_costura",
                "braco": "quadrado_boxy",
                "modulos": "3",
                "descricao_resumida": "Sofá três módulos com encosto de duas almofadas empilhadas, assento liso com costura separando frente/trás, braços quadrados/boxy.",
                "confianca": "média",
                "modo": "demo - sem API key configurada"
            }

        try:
            with open(image_path, "rb") as img_file:
                image_data = base64.standard_b64encode(img_file.read()).decode("utf-8")

            ext = Path(image_path).suffix.lower()
            media_type = "image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png"

            prompt = """Você é especialista em estofados. Analise esta FOTO DE SOFÁ e descreva APENAS a ESTRUTURA (forma), ignorando cor e tecido.

Resonda em JSON com esta estrutura:
{
  "encosto": "tipo identificado",
  "assento": "tipo identificado",
  "braco": "tipo identificado",
  "modulos": "número de módulos (1, 2, 3+)",
  "descricao_resumida": "1-2 linhas resumindo a estrutura visual",
  "confianca": "alta/média/baixa"
}

IMPORTANTE: Capitonê = padrão DIAGONAL/CRUZADO (XX). Duas almofadas empilhadas = costura horizontal única, NÃO é capitonê.

Tipos de encosto: capitone_diagonal, duas_almofadas, gomos_verticais, capitone_quadrado, liso
Tipos de assento: capitone_quadrado, ondas_gomos, gomos_verticais, lisa_costura
Tipos de braço: quadrado_boxy, reto_costura, aluminio, sem_braco

Responda APENAS com JSON, sem markdown.
"""

            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_data,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ],
                    }
                ],
            )

            response_text = message.content[0].text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]

            result = json.loads(response_text)
            return result

        except json.JSONDecodeError as e:
            return {
                "encosto": "desconhecido",
                "assento": "desconhecido",
                "braco": "desconhecido",
                "modulos": "desconhecido",
                "descricao_resumida": "Erro ao descrever",
                "confianca": "baixa",
                "erro": str(e)
            }
        except Exception as e:
            return {
                "encosto": "desconhecido",
                "assento": "desconhecido",
                "braco": "desconhecido",
                "modulos": "desconhecido",
                "descricao_resumida": f"Erro ao analisar foto: {str(e)}",
                "confianca": "baixa",
                "erro": str(e)
            }

    @staticmethod
    def extrair_dados_ficha(image_path: str) -> Dict[str, Any]:
        """
        Extrai dados de custo/produção de uma ficha ou orçamento.
        Chamado quando descrever_foto retorna tudo 'desconhecido' (provável ficha).
        """
        client = _get_client()
        if not client:
            return {"eh_ficha": False}

        try:
            with open(image_path, "rb") as img_file:
                image_data = base64.standard_b64encode(img_file.read()).decode("utf-8")

            ext = Path(image_path).suffix.lower()
            media_type = "image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png"

            prompt = """Analise esta imagem. Pode ser uma ficha de produção, orçamento ou pedido de estofado.

Se for uma ficha/formulário/orçamento (não uma foto de sofá), extraia:
{
  "eh_ficha": true,
  "metragem_tecido": número ou null,
  "horas_totais": número ou null,
  "valor_espuma": número ou null,
  "valor_mo": número ou null,
  "valor_total": número ou null,
  "quantidade_pecas": número ou null,
  "tipo_peca": "POLTRONA|SOFÁ|CADEIRA|etc ou null",
  "cor_tecido": "código ou descrição ou null",
  "trabalhadores": [{"nome": "...", "horas": número}]
}

Regras:
- "mt", "m", "metros" → metragem tecido. "Metragem Tecido: 5,28" → 5.28
- "hr", "h", "horas" → horas. Some todos os trabalhadores para horas_totais
- "Espuma", "Fibra" → valor_espuma. "M.O", "Mão de Obra" → valor_mo
- Na ficha impressa (digitada): campo "Metragem Tecido" é a fonte principal
- Na ficha manual (manuscrita): leia os valores escritos à mão
- Se for CLARAMENTE foto de sofá/poltrona (móvel), retorne {"eh_ficha": false}

Retorne APENAS JSON válido, sem markdown."""

            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=600,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            )

            text = message.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]

            return json.loads(text)

        except Exception as e:
            return {"eh_ficha": False, "erro": str(e)}

    @staticmethod
    def calcular_similaridade(descricao_foto: Dict[str, str],
                            entrada_historica: Dict[str, Any]) -> float:
        """
        Compara estrutura da foto com entrada histórica.
        Retorna % de similaridade (0-100).
        """
        pontos = 0
        max_pontos = 3

        # Encosto
        if descricao_foto.get("encosto") == entrada_historica.get("estrutura_encosto"):
            pontos += 1

        # Assento
        if descricao_foto.get("assento") == entrada_historica.get("estrutura_assento"):
            pontos += 1

        # Braço
        if descricao_foto.get("braco") == entrada_historica.get("estrutura_braco"):
            pontos += 1

        similaridade = (pontos / max_pontos) * 100

        confianca_map = {
            "alta": 1.0,
            "média": 0.8,
            "baixa": 0.5
        }
        confianca_multiplier = confianca_map.get(descricao_foto.get("confianca", "média"), 0.8)

        return similaridade * confianca_multiplier

    @staticmethod
    def encontrar_matches(descricao_foto: Dict[str, str],
                         banco_historico: List[Dict[str, Any]],
                         top_n: int = 3) -> List[Dict[str, Any]]:
        """
        Encontra top N matches do banco histórico.
        Retorna lista ordenada por similaridade decrescente.
        """
        matches = []

        for entrada in banco_historico:
            sim = VisionMatcher.calcular_similaridade(descricao_foto, entrada)
            matches.append({
                "entrada_id": entrada.get("id"),
                "categoria": entrada.get("categoria"),
                "similaridade_pct": round(sim, 1),
                "m_tecido": entrada.get("m_tecido"),
                "horas_totais": entrada.get("horas_totais"),
                "custo_historico": entrada.get("custo_historico"),
                # foto_url aponta para a foto ESPECÍFICA que foi analisada via Vision
                "foto_url": entrada.get("foto_antes_url"),
                "encosto": entrada.get("estrutura_encosto"),
                "assento": entrada.get("estrutura_assento"),
                "braco": entrada.get("estrutura_braco"),
                "trello_card_url": entrada.get("trello_card_url"),
                "mes_entrega": entrada.get("mes_entrega"),
                "ano_entrega": entrada.get("ano_entrega"),
            })

        matches.sort(key=lambda x: x["similaridade_pct"], reverse=True)

        # Deduplica por projeto (mantém só o melhor match por projeto)
        seen_projects = set()
        deduped = []
        for m in matches:
            pid = m["entrada_id"]
            if pid not in seen_projects:
                seen_projects.add(pid)
                deduped.append(m)
            if len(deduped) >= top_n:
                break
        return deduped


# Banco histórico de exemplo (formato esperado no DB)
BANCO_EXEMPLO = [
    {
        "id": "hist_001",
        "categoria": "SOFÁ TRÊS MÓDULOS",
        "estrutura_encosto": "duas_almofadas",
        "estrutura_assento": "lisa_costura",
        "estrutura_braco": "quadrado_boxy",
        "m_tecido": 17.0,
        "horas_totais": 32,
        "custo_historico": 2422.0,
        "foto_antes_url": "https://exemplo.com/sofa1_antes.jpg",
        "foto_depois_url": "https://exemplo.com/sofa1_depois.jpg"
    },
    {
        "id": "hist_002",
        "categoria": "SOFÁ TRÊS MÓDULOS",
        "estrutura_encosto": "capitone_diagonal",
        "estrutura_assento": "capitone_quadrado",
        "estrutura_braco": "quadrado_boxy",
        "m_tecido": 20.0,
        "horas_totais": 40,
        "custo_historico": 2850.0,
        "foto_antes_url": "https://exemplo.com/sofa2_antes.jpg",
        "foto_depois_url": "https://exemplo.com/sofa2_depois.jpg"
    },
    {
        "id": "hist_003",
        "categoria": "SOFÁ TRÊS MÓDULOS",
        "estrutura_encosto": "capitone_diagonal",
        "estrutura_assento": "ondas_gomos",
        "estrutura_braco": "quadrado_boxy",
        "m_tecido": 16.5,
        "horas_totais": 32,
        "custo_historico": 2350.0,
        "foto_antes_url": "https://exemplo.com/sofa3_antes.jpg",
        "foto_depois_url": "https://exemplo.com/sofa3_depois.jpg"
    }
]
