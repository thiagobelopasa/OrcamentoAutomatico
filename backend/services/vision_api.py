import base64
import json
import re
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
import anthropic

class ClaudeVision:
    """Análise de imagens usando Claude Vision API"""

    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)

    def imagem_para_base64(self, caminho_imagem: str) -> str:
        """Converte arquivo de imagem para base64"""
        with open(caminho_imagem, 'rb') as arquivo:
            return base64.standard_b64encode(arquivo.read()).decode('utf-8')

    def detectar_mimetype(self, caminho_imagem: str) -> str:
        """Detecta tipo MIME da imagem baseado na extensão"""
        ext = Path(caminho_imagem).suffix.lower()
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp'
        }
        return mime_types.get(ext, 'image/jpeg')

    def analisar_orçamento_sofa(self, caminho_imagem: str,
                               contexto: Optional[str] = None) -> Dict[str, Any]:
        """
        Analisa imagem de orçamento de sofá/projeto.
        Extrai: metragem de tecido, horas por pessoa (JOEL, DOUGLAS, etc),
        materiais (espuma, acoplar, etc)
        """
        base64_imagem = self.imagem_para_base64(caminho_imagem)
        mime_type = self.detectar_mimetype(caminho_imagem)

        prompt = """Analise esta imagem de orçamento/gasto real de sofá ou projeto.

        Extraia EXATAMENTE estes dados em JSON:

        {
          "materiais": [
            {
              "nome": "TECIDO|ESPUMA|ACOPLAR|etc",
              "quantidade": número (ex: 17, 15.5),
              "unidade": "MT|KG|UN|DIAS|HR|etc",
              "observacoes": "detalhes adicionais se houver"
            }
          ],
          "horas_trabalho": [
            {
              "pessoa": "JOEL|DOUGLAS|PAULO|GABRIEL|EDYMAR|outro",
              "horas": número (ex: 30, 17.5, 2 dias e 2 horas = ~18),
              "descricao": "costura|corte|outros detalhes"
            }
          ],
          "observacoes_gerais": "qualquer informação adicional importante"
        }

        IMPORTANTE:
        - Procure por padrões como "17m de tecido", "32hr costura", "JOEL 30HR", "DOUGLAS 10HR"
        - Converta unidades quando necessário (ex: "2 dias e 2 horas" → ~18 horas)
        - Se não encontrar dados estruturados, tente extrair de anotações manuscritas
        - Retorne APENAS o JSON válido, sem marcação de código
        - Se algum campo não existir, coloque array vazio []
        """

        if contexto:
            prompt += f"\n\nContexto adicional: {contexto}"

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": base64_imagem
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        )

        # Extrai JSON da resposta
        conteudo = response.content[0].text
        try:
            dados = json.loads(conteudo)
        except json.JSONDecodeError:
            # Tenta extrair JSON de dentro do texto
            match = re.search(r'\{[\s\S]*\}', conteudo)
            if match:
                try:
                    dados = json.loads(match.group())
                except json.JSONDecodeError:
                    raise ValueError(f"JSON inválido: {match.group()}")
            else:
                raise ValueError(f"Não foi possível extrair JSON. Resposta: {conteudo}")

        return {
            "sucesso": True,
            "dados": dados,
            "timestamp": datetime.now().isoformat()
        }

    def analisar_multiplas_imagens(self, caminhos_imagens: list) -> Dict[str, Any]:
        """
        Analisa múltiplas imagens de um mesmo projeto (antes, depois, orçamento, etc)
        e consolida os dados
        """
        todos_dados = {
            "materiais": [],
            "horas_trabalho": [],
            "observacoes_gerais": ""
        }

        for caminho in caminhos_imagens:
            try:
                resultado = self.analisar_orçamento_sofa(caminho)
                dados = resultado["dados"]

                # Consolida materiais
                if dados.get("materiais"):
                    todos_dados["materiais"].extend(dados["materiais"])

                # Consolida horas
                if dados.get("horas_trabalho"):
                    todos_dados["horas_trabalho"].extend(dados["horas_trabalho"])

                # Combina observações
                if dados.get("observacoes_gerais"):
                    todos_dados["observacoes_gerais"] += " | " + dados["observacoes_gerais"]

            except Exception as e:
                print(f"Erro ao processar {caminho}: {e}")
                continue

        return {
            "sucesso": True,
            "dados": todos_dados,
            "imagens_processadas": len(caminhos_imagens),
            "timestamp": datetime.now().isoformat()
        }


def criar_vision(api_key: str) -> ClaudeVision:
    """Factory para criar instância de Vision API"""
    return ClaudeVision(api_key)
