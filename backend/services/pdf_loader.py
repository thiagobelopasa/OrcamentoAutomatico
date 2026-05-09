import PyPDF2
import json
from pathlib import Path
from typing import List, Dict, Any

class PDFLoader:
    """Carrega e processa dados do PDF inicial de orçamentos"""

    def __init__(self, pdf_path: str):
        self.pdf_path = Path(pdf_path)

    def carregar_pdf(self) -> str:
        """Extrai texto do PDF"""
        texto = ""
        try:
            with open(self.pdf_path, 'rb') as arquivo:
                leitor = PyPDF2.PdfReader(arquivo)
                num_paginas = len(leitor.pages)

                for num_pagina in range(num_paginas):
                    pagina = leitor.pages[num_pagina]
                    texto += pagina.extract_text() + "\n"

            return texto
        except Exception as e:
            raise Exception(f"Erro ao ler PDF: {str(e)}")

    def processar_orcamentos(self) -> List[Dict[str, Any]]:
        """Parse do PDF e estruturação em JSON"""
        texto = self.carregar_pdf()
        # TODO: Implementar parser baseado na estrutura real do PDF
        return []

def carregar_orcamentos_inicial(pdf_path: str) -> List[Dict[str, Any]]:
    """Função wrapper para carregar dados iniciais do PDF"""
    loader = PDFLoader(pdf_path)
    return loader.processar_orcamentos()
