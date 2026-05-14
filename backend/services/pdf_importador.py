"""
Serviço de importação de PDF de orçamentos para o banco de dados.

Estratégia: Render-Then-Vision
1. pymupdf renderiza cada página como PNG
2. Claude Vision analisa a página e extrai dados estruturados
3. pymupdf extrai imagens embutidas
4. ProjetoORM é criado para cada serviço encontrado
"""
import fitz
import json
import uuid
import base64
import logging
from pathlib import Path
from typing import List, Dict, Tuple
from anthropic import Anthropic
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Cliente Anthropic (reutiliza a API key da env)
_client = None

def _get_client():
    global _client
    if not _client:
        _client = Anthropic()
    return _client


def extrair_paginas_como_imagens(pdf_path: str, dpi: int = 100) -> List[str]:
    """
    Renderiza cada página do PDF como imagem PNG.
    Retorna lista de caminhos dos PNGs salvos.
    """
    doc = fitz.open(pdf_path)
    output_dir = Path("./temp_uploads/pdf_import")
    output_dir.mkdir(parents=True, exist_ok=True)

    page_paths = []

    for page_num, page in enumerate(doc):
        try:
            # Renderiza página como imagem com DPI especificado
            pix = page.get_pixmap(dpi=dpi)
            output_path = output_dir / f"page_{page_num:04d}.png"
            pix.save(str(output_path))
            page_paths.append(str(output_path))
            logger.info(f"Página {page_num} renderizada: {output_path}")
        except Exception as e:
            logger.error(f"Erro ao renderizar página {page_num}: {e}")

    doc.close()
    return page_paths


def extrair_imagens_por_pagina(pdf_path: str) -> Dict[int, List[str]]:
    """
    Extrai todas as imagens embutidas do PDF, organizadas por página.
    Retorna {num_pagina: [path_imagem0, path_imagem1, ...]}
    """
    doc = fitz.open(pdf_path)
    output_dir = Path("./temp_uploads/pdf_import")
    output_dir.mkdir(parents=True, exist_ok=True)

    imagens_por_pagina = {}

    for page_num, page in enumerate(doc):
        imagens = []
        try:
            # Obtém lista de imagens na página
            image_list = page.get_images(full=True)

            for img_idx, (xref, *_) in enumerate(image_list):
                try:
                    # Extrai imagem bruta
                    pix = fitz.Pixmap(doc, xref)

                    # Converte para RGB se necessário
                    if pix.alpha:
                        pix = fitz.Pixmap(pix, fitz.csRGB)

                    # Salva com nome padrão
                    output_path = output_dir / f"p{page_num:04d}_img{img_idx:02d}.jpg"
                    pix.save(str(output_path))
                    imagens.append(str(output_path))
                    logger.info(f"Imagem extraída página {page_num}, índice {img_idx}: {output_path}")
                except Exception as e:
                    logger.warning(f"Erro ao extrair imagem xref={xref}: {e}")

            imagens_por_pagina[page_num] = imagens
        except Exception as e:
            logger.warning(f"Erro ao processar imagens da página {page_num}: {e}")
            imagens_por_pagina[page_num] = []

    doc.close()
    return imagens_por_pagina


def analisar_pagina_com_vision(
    page_image_path: str,
    imagens_da_pagina: List[str],
    max_retries: int = 3,
    initial_wait: int = 2
) -> List[Dict]:
    """
    Usa Claude Vision para analisar uma página renderizada do PDF.
    Retorna lista de serviços encontrados na página com dados extraídos.
    Com retry exponencial para rate limits.
    """
    import time
    client = _get_client()

    try:
        with open(page_image_path, "rb") as f:
            page_image_b64 = base64.standard_b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"Erro ao ler página {page_image_path}: {e}")
        return []

    # Carrega imagens embutidas também
    imagens_conteudo = []
    for img_path in imagens_da_pagina:
        try:
            with open(img_path, "rb") as f:
                img_b64 = base64.standard_b64encode(f.read()).decode("utf-8")
            imagens_conteudo.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": img_b64,
                },
            })
        except Exception as e:
            logger.warning(f"Erro ao carregar imagem {img_path}: {e}")

    # Monta o prompt
    prompt = """Você está analisando uma página de um PDF de orçamentos de estofados.

A página contém uma tabela com 3 colunas:
- Coluna 1 (esquerda): Foto do estofado (imagem)
- Coluna 2 (centro): Ficha técnica (imagem com dados de produção)
- Coluna 3 (direita): Resumo em texto livre

INSTRUÇÕES CRÍTICAS:
1. Conte quantas LINHAS (serviços) existem nesta página.
2. Para CADA LINHA, de cima para baixo:
   - Identifique qual imagem embutida é a FOTO (por posição: 0=primeira imagem, 1=segunda, etc)
   - Identifique qual imagem é a FICHA (mesma numeração)
   - Extraia TODO o texto da coluna de resumo (pode ser múltiplas linhas)
   - Na ficha, procure por: metragem de tecido em m² ou metros, horas totais, valor espuma, mão de obra, tipo de peça, quantidade

REGRAS PARA ÍNDICES:
- foto_indices e ficha_indices DEVEM SEMPRE ser ARRAYS (listas), NUNCA null
- Se só há 1 foto, use [0]. Se há 2 fotos, use [0,1], etc.
- Se não conseguir identificar a imagem, deixe o array VAZIO []
- EXEMPLO: "foto_indices": [0], "ficha_indices": [1]
- NUNCA use: "foto_indices": null ou "foto_indices": 0

CAMPOS NUMÉRICOS:
- metragem_tecido: número em metros quadrados (ex: 2.5)
- horas_totais: número (ex: 8.5)
- valor_espuma: número em reais (ex: 250.00)
- valor_mo: número em reais (ex: 400.00)
- tipo_peca: texto (sofa, poltrona, puff, banco, chaise, ou outro)
- quantidade_pecas: número inteiro (ex: 1 ou 3)
- Se um campo não estiver visível na ficha, use null (não use 0 ou string vazia)

RESPOSTA: Retorne APENAS JSON válido, sem markdown ou explicação. Exemplo:
{
  "total_servicos": 3,
  "servicos": [
    {
      "num_linha": 1,
      "foto_indices": [0],
      "ficha_indices": [1],
      "resumo": "Sofá cinza claro, tecido liso...",
      "dados_ficha": {
        "metragem_tecido": 2.5,
        "horas_totais": 8,
        "valor_espuma": 300,
        "valor_mo": 500,
        "tipo_peca": "sofa",
        "quantidade_pecas": 1
      }
    },
    {
      "num_linha": 2,
      "foto_indices": [2],
      "ficha_indices": [3],
      "resumo": "Poltrona vermelha...",
      "dados_ficha": {
        "metragem_tecido": 1.2,
        "horas_totais": 4,
        "valor_espuma": null,
        "valor_mo": 250,
        "tipo_peca": "poltrona",
        "quantidade_pecas": 1
      }
    }
  ]
}"""

    # Monta conteúdo: primeira a página renderizada, depois as imagens embutidas
    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": page_image_b64,
            },
        }
    ]
    content.extend(imagens_conteudo)
    content.append({
        "type": "text",
        "text": prompt,
    })

    # Retry com backoff exponencial para rate limits
    for attempt in range(max_retries):
        try:
            # Chama Sonnet
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": content,
                }],
            )

            response_text = message.content[0].text.strip()

            # Parse JSON
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            response_text = response_text.strip()

            data = json.loads(response_text)
            servicos = data.get("servicos", [])
            logger.info(f"Página analisada: {len(servicos)} serviços encontrados")
            return servicos

        except Exception as e:
            error_msg = str(e)
            is_rate_limit = "429" in error_msg or "rate_limit" in error_msg.lower()

            if is_rate_limit and attempt < max_retries - 1:
                # Rate limit: aguarda e tenta novamente
                wait_time = initial_wait * (2 ** attempt)  # 2s, 4s, 8s
                logger.warning(f"Rate limit. Aguardando {wait_time}s antes de retry {attempt + 1}/{max_retries}...")
                time.sleep(wait_time)
            else:
                # Erro definitivo ou última tentativa
                logger.error(f"Erro ao analisar página com Vision: {e}")
                return []

    return []


def importar_pdf(pdf_path: str, db: Session) -> Dict:
    """
    Orquestra a importação completa de um PDF para o banco.

    Retorna: {
        "total_servicos": int,
        "total_paginas": int,
        "projetos_criados": int,
        "erros": int
    }
    """
    logger.info(f"Iniciando importação do PDF: {pdf_path}")

    # 1) Renderiza páginas
    logger.info("Renderizando páginas...")
    page_paths = extrair_paginas_como_imagens(pdf_path)
    logger.info(f"Total de páginas renderizadas: {len(page_paths)}")

    # 2) Extrai imagens embutidas
    logger.info("Extraindo imagens embutidas...")
    imagens_por_pagina = extrair_imagens_por_pagina(pdf_path)

    # 3) Analisa cada página com Vision e coleta dados
    total_servicos = 0
    projetos_criados = 0
    erros = 0

    from database import ProjetoORM

    for page_num, page_path in enumerate(page_paths):
        logger.info(f"Analisando página {page_num} de {len(page_paths)}...")

        try:
            imagens = imagens_por_pagina.get(page_num, [])
            servicos = analisar_pagina_com_vision(page_path, imagens)
            total_servicos += len(servicos)

            # Delay entre páginas para evitar rate limit
            # Calcula: a cada 2 páginas, aguarda 5 segundos adicionais
            import time
            if page_num < len(page_paths) - 1:
                base_delay = 2.0  # 2 segundos base
                if page_num % 2 == 1:  # A cada 2 páginas
                    base_delay += 3.0  # +3 segundos adicionais
                logger.info(f"Aguardando {base_delay}s antes da próxima página...")
                time.sleep(base_delay)

            # 4) Para cada serviço, cria um ProjetoORM
            for servico in servicos:
                try:
                    # Garante que índices são sempre listas (Vision pode retornar null)
                    foto_indices = servico.get("foto_indices") or []
                    ficha_indices = servico.get("ficha_indices") or []

                    # Converte para lista se for número single
                    if isinstance(foto_indices, int):
                        foto_indices = [foto_indices]
                    if isinstance(ficha_indices, int):
                        ficha_indices = [ficha_indices]

                    resumo = servico.get("resumo", "")
                    dados_ficha = servico.get("dados_ficha") or {}

                    # Associa imagens aos índices
                    foto_url = None
                    if foto_indices and len(foto_indices) > 0:
                        idx = foto_indices[0]  # Pega primeira foto
                        if isinstance(idx, int) and 0 <= idx < len(imagens):
                            foto_url = imagens[idx]

                    urls_anexos = []
                    if foto_url:
                        urls_anexos.append(foto_url)

                    # Seguro contra None em ficha_indices
                    if ficha_indices:
                        for idx in ficha_indices:
                            if isinstance(idx, int) and 0 <= idx < len(imagens) and imagens[idx] != foto_url:
                                urls_anexos.append(imagens[idx])

                    # Extrai nome e cliente do resumo
                    nome = resumo[:100] if resumo else "Serviço do PDF"
                    cliente = resumo.split("\n")[0][:50] if resumo else "Não especificado"

                    # Cria ProjetoORM
                    proj_id = f"pdf_{uuid.uuid4().hex[:12]}"
                    projeto = ProjetoORM(
                        id=proj_id,
                        nome=nome,
                        cliente=cliente,
                        mes_entrega="INDEFINIDO",
                        ano_entrega=2026,
                        foto_estofado_url=foto_url,
                        urls_anexos=urls_anexos,
                        descricao=resumo,
                        observacoes=f"Importado do PDF na página {page_num}",
                        dados_ficha=dados_ficha if dados_ficha else None,
                        analise_unificada=0,  # Pendente para análise Vision
                        materiais=[],
                        horas_trabalho=[],
                    )
                    db.add(projeto)
                    projetos_criados += 1
                    logger.info(f"Projeto criado: {proj_id} - {nome}")

                except Exception as e:
                    logger.error(f"Erro ao criar projeto do serviço: {e}")
                    erros += 1

        except Exception as e:
            logger.error(f"Erro ao analisar página {page_num}: {e}")
            erros += 1

    # 5) Salva batch no banco
    try:
        db.commit()
        logger.info(f"Banco atualizado: {projetos_criados} projetos salvos")
    except Exception as e:
        logger.error(f"Erro ao salvar no banco: {e}")
        db.rollback()
        erros += len([s for p in imagens_por_pagina.values() for s in p])
        projetos_criados = 0

    resultado = {
        "total_servicos": total_servicos,
        "total_paginas": len(page_paths),
        "projetos_criados": projetos_criados,
        "erros": erros,
    }

    logger.info(f"Importação concluída: {resultado}")
    return resultado
