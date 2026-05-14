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
- 1 foto do estofado (sofá/poltrona/puff/banco) — usada para comparação visual
- 1-2 fichas (impressas ou manuscritas) — contêm metragem, horas, custos

TAREFA: analise todas as imagens e retorne UM JSON consolidado.

⚠️ TIPOS ESPECIAIS:
- PUFF/BANCO: móvel baixo sem encosto (ou com encosto mínimo)
  → encosto = "liso" ou "duas_almofadas" se tiver dois paninhos empilhados
  → assento = "lisa_costura" (puffs são lisos)
  → braco = "sem_braco" (puffs modular/banco costumam não ter braço real)

- POLTRONA: 1 lugar com encosto + braços
  → braco = "quadrado_boxy" ou "reto_costura" (tem braços VISÍVEIS)

- SOFÁ: múltiplos lugares
  → modulos = "2", "3", "4+"

ESTRUTURA DE RESPOSTA:
{
  "foto_estofado_index": 0,  // 0-based; null se NENHUMA imagem é foto de estofado
  "estrutura": {
    "encosto": "duas_almofadas|capitone_diagonal|gomos_verticais|capitone_quadrado|liso|desconhecido",
    "assento": "capitone_quadrado|ondas_gomos|gomos_verticais|lisa_costura|desconhecido",
    "braco": "quadrado_boxy|reto_costura|aluminio|sem_braco|desconhecido",
    "modulos": "1|2|3|4+",
    "tipo_movel": "SOFA|POLTRONA|PUFF|BANCO|CHAISE|desconhecido",
    "descricao_resumida": "1-2 linhas descrevendo a estrutura visual",
    "confianca": "alta|média|baixa"
  },
  "dados_ficha": {
    "numero_orcamento": "string ou null",
    "cliente": "nome do cliente ou null",
    "data_orcamento": "DD/MM/AAAA ou null",
    "descricao_servico": "texto completo do serviço ou null",
    "metragem_tecido": número ou null,
    "horas_totais": número ou null,
    "valor_espuma": número ou null,
    "valor_mo": número ou null,
    "valor_total": número ou null,
    "quantidade_pecas": número ou null,
    "tipo_peca": "POLTRONA|SOFÁ|CADEIRA|ALMOFADA|PUFF|BANCO|CADEIRÃO|... ou null",
    "cor_tecido": "código/descrição ou null",
    "trabalhadores": [{"nome": "...", "horas": número}] ou [],
    "confianca": "alta|média|baixa|nenhuma"
  },
  "observacoes": "qualquer detalhe relevante"
}

REGRAS:
1. foto_estofado_index é o índice (0-based) da imagem que MELHOR mostra o estofado.
   Se houver várias fotos, escolha a que mostra estrutura mais completa
   (encosto + assento + braço visíveis para sofás/poltronas, ou encosto + assento para puffs).

2. Capitonê = padrão DIAGONAL/CRUZADO (XX, formando losangos).
   Duas almofadas empilhadas (costura horizontal única) NÃO é capitonê.

3. **PUFF/BANCO DETECTION** (IMPORTANTE):
   - PUFF: assento liso + encosto ausente/liso/mínimo + braço "sem_braco"
   - BANCO: como puff mas com possível estrutura de metal/madeira visível
   - Se a foto mostra um móvel SEM braços laterais visíveis E o assento é liso,
     marca como braco="sem_braco" (não "quadrado_boxy" ou "reto_costura")

4. EXTRAINDO DADOS DA FICHA — há dois modelos de ficha:

   MODELO A — Estofados Imperial (formulário manuscrito antigo):
   - Campo "ORÇAMENTO" ou número em destaque (ex: 21848) → numero_orcamento
   - Campo "DATA" → data_orcamento
   - Campo "Cliente" → cliente
   - Coluna "Discriminação": lista de itens com preços
     → linha com "Tec", "Tecido", "conjunto" + valor R$ → valor_tecido (não é horas)
     → linha com "Espuma", "ESPUMAS" + valor R$ → valor_espuma
     → linha com "M.O", "Mão de Obra" + valor R$ → valor_mo
     → campo "TOTAL R$" → valor_total
   - Não há campo de horas neste modelo — horas_totais = null

   MODELO B — Gilmar Pasa (formulário impresso digital):
   - Campo "Orçamento" (data) → data_orcamento
   - Campo "Cliente" → cliente
   - Campo "Descrição/Pedido:" (número) → numero_orcamento
   - Campo "Código Descrição": texto do serviço → descricao_servico
   - RODAPÉ (campos pré-impressos preenchidos a lápis ou caneta):
     → "Metragem Tecido: ___" → metragem_tecido (ex: "20 m.t." = 20, "11,15 mts" = 11.15)
     → "Início e Término Trabalho: ___" → ESTE É O CAMPO DE HORAS DOS FUNCIONÁRIOS
       Leia o valor escrito e o nome após ele. Ex: "24:00 hrs - 1pt 2/5:50 hrs Gilmari"
       ou "2:20 hs Karolyna" ou "0,30 min Karolyna"
       → converta para horas decimais: "2:20 hs" = 2.33, "0,30 min" = 0.5 (30 min)
       → extraia nome do funcionário para trabalhadores[]

5. horas_totais = SOMENTE horas de trabalho, NUNCA valor monetário.
   - Valores R$ após "M.O", "Espuma", preços unitários = DINHEIRO, ignore para horas
   - Horas válidas: campo "Início e Término Trabalho", "Horas", valores pequenos (< 200) próximos a nomes de pessoas
   - Soma de todos os trabalhadores listados

6. metragem_tecido: valor em metros. "20 m.t." = 20, "11,15 mts" = 11.15, "0,30 m" = 0.30
   Vírgula = separador decimal no Brasil: "1,5" = 1.5

7. Se NÃO houver foto de estofado: foto_estofado_index = null,
   estrutura.confianca = "nenhuma", todos os campos de estrutura = "desconhecido".

8. Se NÃO houver ficha: dados_ficha.confianca = "nenhuma", campos numéricos = null.

9. Se uma única imagem contém TANTO uma foto pequena do estofado QUANTO uma ficha
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
        "tipo_movel": estrutura.get("tipo_movel") or "desconhecido",
        "descricao_resumida": estrutura.get("descricao_resumida") or "",
        "confianca": estrutura.get("confianca") or "nenhuma",
    }

    df = data.get("dados_ficha") or {}
    dados_ficha = {
        "numero_orcamento": df.get("numero_orcamento"),
        "cliente": df.get("cliente"),
        "data_orcamento": df.get("data_orcamento"),
        "descricao_servico": df.get("descricao_servico"),
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
            "tipo_movel": "desconhecido",
            "descricao_resumida": "",
            "confianca": "nenhuma",
        },
        "dados_ficha": {
            "numero_orcamento": None,
            "cliente": None,
            "data_orcamento": None,
            "descricao_servico": None,
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


_COMPARISON_PROMPT = """Você é especialista em reforma de estofados. Compare a REFERÊNCIA com cada CANDIDATO.

REFERÊNCIA: primeira imagem (foto do cliente que precisa de orçamento).
CANDIDATOS: demais imagens (projetos históricos concluídos), numerados a partir de 1.

CRITÉRIO DE PONTUAÇÃO — Foque na ESTRUTURA DE FORMA, não em cor/material:

90-100:
- Mesma foto (diferentes ângulos/iluminação do MESMO objeto)
- Antes/depois do MESMO sofá/poltrona
- Variação mínima: mesma estrutura, só mudou cor/tecido

75-89:
- Mesmo modelo/design: mesmos padrões (encosto, assento, braço)
- Tolerância: pequenas variações em módulos ou dimensões

50-74:
- Estrutura similar mas com 1-2 diferenças nítidas
- Ex: mesmo assento, encosto diferente

25-49:
- Tipo genérico similar (ambos sofás/poltronas)
- Mas estrutura claramente diferente em vários aspectos

0-24:
- Diferente demais (ex: poltrona vs sofá retratilizável)

ANÁLISE ESTRUTURAL:
1. **ASSENTO**: capitonê diagonal (XX), gomos/ondas, liso com costura, lisa pura
2. **ENCOSTO**: 2 almofadas empilhadas, capitonê, gomos verticais, reto liso
3. **BRAÇO**: boxy quadrado, reto com costura, alumínio visível, sem braço
4. **MÓDULOS**: 1 lugar, 2 lugares, 3 lugares, 4+ lugares
5. **PORTE**: poltrona pequena, sofá médio, sofá grande, chaise

⚠️ **IMPORTANTE**:
- Mesma foto com compressão JPEG diferente = 95-100 (não reduza só porque pixel diferente)
- Foto do mesmo estofado em ângulos diferentes = 95-100
- Mesmo modelo mas cor diferente = 85-95 (mudou só o tecido)

Responda APENAS este JSON, sem markdown:
{"scores": [{"i": 1, "score": 85, "nota": "mesmo gomos assento + encosto empilhado, braço diferente"}, ...]}

"i" = número do candidato (1 = segundo image).
Inclua score para CADA candidato."""


def _comparar_lote_sync(
    uploaded_b64: str,
    uploaded_media_type: str,
    candidatos: list[dict],
    client,
) -> list[dict]:
    """Compara 1 referência com um lote de candidatos em 1 chamada Vision."""
    content = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": uploaded_media_type, "data": uploaded_b64},
        }
    ]
    for c in candidatos:
        with open(c["path"], "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode("utf-8")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": c.get("media_type", "image/jpeg"),
                "data": b64,
            },
        })
    content.append({"type": "text", "text": _COMPARISON_PROMPT})

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": content}],
    )
    text = message.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    data = json.loads(text)
    results = []
    for s in data.get("scores", []):
        idx = int(s.get("i", 0)) - 1
        if 0 <= idx < len(candidatos):
            results.append({
                "id": candidatos[idx]["id"],
                "score": float(s.get("score", 0)),
                "nota": s.get("nota", ""),
            })
    return results


def encontrar_hash_duplicatas(
    uploaded_path: str,
    candidatos: list[dict],
    threshold_exato: float = 0.98,
    threshold_perceptual: float = 0.85,
) -> list[dict]:
    """
    Encontra duplicatas exatas ou perceptuais (foto extraída do banco).
    Muito mais rápido que Vision — economiza chamadas à API.

    Estratégia:
    1. Hash exato (SHA256) — detecta arquivo idêntico
    2. Perceptual hash (dhash) — detecta mesma foto com compressão diferente

    Returns:
        [{id, score, nota, hash_method}] para duplicatas encontradas
        [] se nenhuma duplicata
    """
    from services.image_dedup import get_image_hash, hamming_distance

    try:
        uploaded_hash = get_image_hash(uploaded_path, use_perceptual=False)  # exato primeiro
        results = []

        for cand in candidatos:
            try:
                cand_path = cand.get("path")
                if not cand_path:
                    continue

                cand_hash = get_image_hash(cand_path, use_perceptual=False)
                if not cand_hash:
                    continue

                # Match exato?
                if uploaded_hash == cand_hash:
                    results.append({
                        "id": cand["id"],
                        "score": 100.0,
                        "nota": "duplicata exata (hash idêntico)",
                        "hash_method": "SHA256_exato",
                    })
                    continue

                # Perceptual hash
                uploaded_dhash = get_image_hash(uploaded_path, use_perceptual=True)
                cand_dhash = get_image_hash(cand_path, use_perceptual=True)
                distance = hamming_distance(uploaded_dhash, cand_dhash)
                max_distance = int(64 * (1 - threshold_perceptual))

                if distance <= max_distance:
                    score = 100.0 - (distance / 64 * 20)  # 100 → 80 conforme aumenta distância
                    results.append({
                        "id": cand["id"],
                        "score": min(score, 99.0),  # caps em 99% para não confundir com 100%
                        "nota": f"duplicata perceptual (distância {distance}/64 bits)",
                        "hash_method": "dhash_perceptual",
                    })
            except Exception as e:
                continue

        return results

    except Exception as e:
        return []


def comparar_foto_com_candidatos(
    uploaded_path: str,
    candidatos: list[dict],
    batch_size: int = 10,
    check_hash_first: bool = True,
) -> list[dict]:
    """
    Compara visualmente uploaded_path contra cada candidato.
    candidatos: [{id, path, media_type}]

    OTIMIZAÇÃO: Se check_hash_first=True, primeiro procura por duplicatas exatas/perceptuais.
    Candidatas encontradas retornam imediatamente com score 99-100 (sem Vision).

    Agrupa restantes em lotes de batch_size → 1 chamada Vision (Sonnet) por lote.
    Retorna: [{id, score, nota}] ordenado por score DESC
    """
    if not candidatos:
        return []

    # Passo 1: Check hash para duplicatas (rápido, sem API)
    resultados_hash = []
    candidatos_restantes = candidatos
    if check_hash_first:
        resultados_hash = encontrar_hash_duplicatas(uploaded_path, candidatos)
        if resultados_hash:
            # Remove candidatos que tiveram match de hash da lista para Vision
            ids_encontradas = {r["id"] for r in resultados_hash}
            candidatos_restantes = [c for c in candidatos if c["id"] not in ids_encontradas]

    # Passo 2: Comparação visual com Vision (Sonnet) para restantes
    client = _get_client()
    if not client:
        resultados_vision = [{"id": c["id"], "score": 0, "nota": "sem_api_key"} for c in candidatos_restantes]
    else:
        with open(uploaded_path, "rb") as f:
            uploaded_b64 = base64.standard_b64encode(f.read()).decode("utf-8")
        uploaded_media_type = _media_type(uploaded_path)

        resultados_vision: list[dict] = []
        for i in range(0, len(candidatos_restantes), batch_size):
            lote = candidatos_restantes[i : i + batch_size]
            try:
                results = _comparar_lote_sync(uploaded_b64, uploaded_media_type, lote, client)
                resultados_vision.extend(results)
                # Candidatos sem score na resposta → score 0
                ids_com_score = {r["id"] for r in results}
                for c in lote:
                    if c["id"] not in ids_com_score:
                        resultados_vision.append({"id": c["id"], "score": 0, "nota": "sem_resposta"})
            except Exception as e:
                for c in lote:
                    resultados_vision.append({"id": c["id"], "score": 0, "nota": f"erro: {e}"})

    # Combina resultados: hash (prioridade) + vision
    todos_resultados = resultados_hash + resultados_vision
    todos_resultados.sort(key=lambda x: x["score"], reverse=True)
    return todos_resultados
