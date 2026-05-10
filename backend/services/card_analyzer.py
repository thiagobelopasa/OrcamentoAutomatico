"""
Analisador unificado de card do Trello.

UMA chamada Vision por card recebe TODAS as imagens (foto do estofado +
fichas) e retorna estrutura + dados de ficha em um só payload.

Substitui o pipeline antigo de 2 passes (que rodava Vision em CADA imagem
separadamente e depois tentava re-classificar fichas).
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

try:
    from anthropic import Anthropic
    _ANTHROPIC_INSTALLED = True
except ImportError:
    _ANTHROPIC_INSTALLED = False


_PROMPT = """Você analisa TODAS as imagens de UM card do Trello (projeto de reforma de estofado).

Cada card costuma ter:
- 1 foto do estofado (sofá/poltrona) — usada para comparação visual
- 1-2 fichas (impressas ou manuscritas) — contêm metragem, horas, custos

TAREFA: analise todas as imagens e retorne UM JSON consolidado.

ESTRUTURA DE RESPOSTA:
{
  "foto_estofado_index": 0,  // 0-based; null se NENHUMA imagem é foto de estofado
  "estrutura": {
    "encosto": "duas_almofadas|capitone_diagonal|gomos_verticais|capitone_quadrado|liso|desconhecido",
    "assento": "capitone_quadrado|ondas_gomos|gomos_verticais|lisa_costura|desconhecido",
    "braco": "quadrado_boxy|reto_costura|aluminio|sem_braco|desconhecido",
    "modulos": "1|2|3|4+",
    "descricao_resumida": "1-2 linhas descrevendo a estrutura visual",
    "confianca": "alta|média|baixa"
  },
  "dados_ficha": {
    "metragem_tecido": número ou null,
    "horas_totais": número ou null,
    "valor_espuma": número ou null,
    "valor_mo": número ou null,
    "valor_total": número ou null,
    "quantidade_pecas": número ou null,
    "tipo_peca": "POLTRONA|SOFÁ|CADEIRA|... ou null",
    "cor_tecido": "código/descrição ou null",
    "trabalhadores": [{"nome": "...", "horas": número}] ou [],
    "confianca": "alta|média|baixa|nenhuma"
  },
  "observacoes": "qualquer detalhe relevante"
}

REGRAS:
1. foto_estofado_index é o índice (0-based) da imagem que MELHOR mostra o estofado.
   Se houver várias fotos, escolha a que mostra estrutura mais completa
   (encosto + assento + braço visíveis).

2. Capitonê = padrão DIAGONAL/CRUZADO (XX, formando losangos).
   Duas almofadas empilhadas (costura horizontal única) NÃO é capitonê.

3. Em fichas digitadas (impressas):
   - "Metragem Tecido: 5,28" → metragem_tecido = 5.28
   - "M.O", "Mão de Obra" → valor_mo
   - "Espuma" → valor_espuma

4. Em fichas manuscritas: leia os valores escritos à mão.

5. horas_totais = soma de horas de TODOS os trabalhadores listados.

6. Se NÃO houver foto de estofado: foto_estofado_index = null,
   estrutura.confianca = "nenhuma", todos os campos de estrutura = "desconhecido".

7. Se NÃO houver ficha: dados_ficha.confianca = "nenhuma", campos numéricos = null.

8. Se uma única imagem contém TANTO uma foto pequena do estofado QUANTO uma ficha
   (montagem), priorize ela como foto_estofado_index e ainda extraia dados_ficha.

Responda APENAS o JSON, sem markdown, sem ``` blocks."""


def _get_client():
    if not _ANTHROPIC_INSTALLED:
        return None
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        return Anthropic(api_key=key)
    except Exception:
        return None


def _media_type(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")


def analyze_card(image_paths: list[str], image_urls: list[str] | None = None) -> dict[str, Any]:
    """
    Analisa todas as imagens de UM card em uma única chamada Vision.

    Args:
        image_paths: caminhos locais das imagens (já baixadas).
        image_urls: URLs originais correspondentes (mesma ordem). Usadas para
            preencher foto_estofado_url no resultado.

    Returns:
        {
            "foto_estofado_url": str | None,
            "estrutura": {encosto, assento, braco, modulos, descricao_resumida, confianca},
            "dados_ficha": {metragem_tecido, horas_totais, valor_espuma, ..., confianca},
            "observacoes": str,
            "_raw": resposta crua para debug,
        }

    Em modo demo (sem ANTHROPIC_API_KEY): retorna estrutura padrão.
    Em erro: retorna estrutura de fallback com confianca="nenhuma".
    """
    if not image_paths:
        return _empty_result(reason="sem_imagens")

    image_urls = image_urls or [""] * len(image_paths)

    client = _get_client()
    if not client:
        return _empty_result(reason="sem_api_key")

    # Limita a 20 imagens (limite seguro de Claude para uma chamada)
    if len(image_paths) > 20:
        image_paths = image_paths[:20]
        image_urls = image_urls[:20]

    try:
        content = []
        for path in image_paths:
            with open(path, "rb") as f:
                b64 = base64.standard_b64encode(f.read()).decode("utf-8")
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _media_type(path),
                    "data": b64,
                },
            })
        content.append({"type": "text", "text": _PROMPT})

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": content}],
        )

        text = message.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        data = json.loads(text)
        return _normalize(data, image_urls)

    except json.JSONDecodeError as e:
        return _empty_result(reason=f"json_invalido: {e}")
    except Exception as e:
        return _empty_result(reason=f"erro_vision: {e}")


def _normalize(data: dict, image_urls: list[str]) -> dict[str, Any]:
    """Normaliza a resposta do Vision pra schema estável."""
    idx = data.get("foto_estofado_index")
    foto_url = None
    if idx is not None and isinstance(idx, int) and 0 <= idx < len(image_urls):
        foto_url = image_urls[idx]

    estrutura = data.get("estrutura") or {}
    estrutura = {
        "encosto": estrutura.get("encosto") or "desconhecido",
        "assento": estrutura.get("assento") or "desconhecido",
        "braco": estrutura.get("braco") or "desconhecido",
        "modulos": str(estrutura.get("modulos") or ""),
        "descricao_resumida": estrutura.get("descricao_resumida") or "",
        "confianca": estrutura.get("confianca") or "nenhuma",
    }

    df = data.get("dados_ficha") or {}
    dados_ficha = {
        "metragem_tecido": _safe_num(df.get("metragem_tecido")),
        "horas_totais": _safe_num(df.get("horas_totais")),
        "valor_espuma": _safe_num(df.get("valor_espuma")),
        "valor_mo": _safe_num(df.get("valor_mo")),
        "valor_total": _safe_num(df.get("valor_total")),
        "quantidade_pecas": _safe_num(df.get("quantidade_pecas")),
        "tipo_peca": df.get("tipo_peca"),
        "cor_tecido": df.get("cor_tecido"),
        "trabalhadores": df.get("trabalhadores") or [],
        "confianca": df.get("confianca") or "nenhuma",
    }

    return {
        "foto_estofado_url": foto_url,
        "estrutura": estrutura,
        "dados_ficha": dados_ficha,
        "observacoes": data.get("observacoes") or "",
    }


def _safe_num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _empty_result(reason: str) -> dict[str, Any]:
    return {
        "foto_estofado_url": None,
        "estrutura": {
            "encosto": "desconhecido",
            "assento": "desconhecido",
            "braco": "desconhecido",
            "modulos": "",
            "descricao_resumida": "",
            "confianca": "nenhuma",
        },
        "dados_ficha": {
            "metragem_tecido": None,
            "horas_totais": None,
            "valor_espuma": None,
            "valor_mo": None,
            "valor_total": None,
            "quantidade_pecas": None,
            "tipo_peca": None,
            "cor_tecido": None,
            "trabalhadores": [],
            "confianca": "nenhuma",
        },
        "observacoes": f"erro: {reason}",
    }


def calcular_similaridade(estrutura_foto: dict, estrutura_projeto: dict) -> float:
    """
    Compara estrutura da foto enviada com estrutura de projeto histórico.
    Retorna % (0-100).
    """
    pontos = 0
    if estrutura_foto.get("encosto") == estrutura_projeto.get("encosto"):
        pontos += 1
    if estrutura_foto.get("assento") == estrutura_projeto.get("assento"):
        pontos += 1
    if estrutura_foto.get("braco") == estrutura_projeto.get("braco"):
        pontos += 1
    sim = (pontos / 3) * 100

    confianca_map = {"alta": 1.0, "média": 0.85, "media": 0.85, "baixa": 0.6, "nenhuma": 0.4}
    mult = confianca_map.get(estrutura_foto.get("confianca", "média"), 0.85)
    return sim * mult
