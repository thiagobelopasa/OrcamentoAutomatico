#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Script de importação do PDF de orçamentos para o banco de dados.

Uso:
    python importar_pdf.py [caminho_pdf]

Exemplo:
    python importar_pdf.py "C:\Users\thiag\Downloads\Orçamentos.pdf"
    python importar_pdf.py  # Usa default
"""
import sys
import os
from pathlib import Path
from datetime import datetime
import io

# Força UTF-8 no Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Adiciona backend ao path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from dotenv import load_dotenv

# Carrega variáveis de ambiente
load_dotenv("backend/.env.local", override=True)
load_dotenv("backend/.env")

from database import init_db, SessionLocal
from services.pdf_importador import importar_pdf


def print_header(text):
    print(f"\n{'=' * 70}")
    print(f"{text:^70}")
    print(f"{'=' * 70}\n")


def print_success(text):
    print(f"[OK] {text}")


def print_info(text):
    print(f"[INFO] {text}")


def print_warning(text):
    print(f"[AVISO] {text}")


def print_error(text):
    print(f"[ERRO] {text}")


def main():
    print_header("IMPORTADOR DE PDF DE ORÇAMENTOS")

    # Determina caminho do PDF
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\thiag\Downloads\Orçamentos.pdf"

    # Verifica arquivo
    if not Path(pdf_path).exists():
        print_error(f"Arquivo não encontrado: {pdf_path}")
        return 1

    print_info(f"PDF a importar: {pdf_path}")
    print_info(f"Tamanho: {Path(pdf_path).stat().st_size / (1024*1024):.1f} MB")
    print_info(f"Data/hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")

    # Inicializa banco
    print_header("INICIALIZANDO BANCO")
    try:
        init_db()
        print_success("Banco de dados inicializado")
    except Exception as e:
        print_error(f"Erro ao inicializar banco: {e}")
        return 1

    # Importa PDF
    print_header("IMPORTANDO PDF")
    db = SessionLocal()
    try:
        resultado = importar_pdf(pdf_path, db)

        print_header("RESULTADO DA IMPORTAÇÃO")
        print_info(f"Total de páginas processadas: {resultado['total_paginas']}")
        print_info(f"Total de serviços encontrados: {resultado['total_servicos']}")
        print_success(f"Projetos criados: {resultado['projetos_criados']}")

        if resultado['erros'] > 0:
            print_warning(f"Erros durante importação: {resultado['erros']}")

        # Próximos passos
        print_header("PRÓXIMAS ETAPAS")
        print_info("1. Os projetos foram importados com analise_unificada=0 (pendente)")
        print_info("2. Para analisar com Vision, execute:")
        print_info("   POST /matching/analisar-historico?limite=500")
        print_info("")
        print_info("3. Abra o frontend para testar:")
        print_info("   file:///c:/Users/thiag/OrcamentoAutomatico/frontend_teste.html")
        print_info("")
        print_info("4. Ou consulte a API:")
        print_info("   GET /health → deve mostrar total_projetos aumentado")

        return 0

    except Exception as e:
        print_error(f"Erro na importação: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        db.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n[AVISO] Interrompido pelo usuário")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERRO] Erro fatal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
