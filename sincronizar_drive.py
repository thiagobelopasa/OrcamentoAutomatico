#!/usr/bin/env python3
"""
Script de sincronização do Google Drive.
Importa fotos de uma pasta do Drive para o banco de dados.
"""
import sys
import os
from pathlib import Path
from datetime import datetime

# Adiciona backend ao path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from dotenv import load_dotenv
load_dotenv("backend/.env.local", override=True)
load_dotenv("backend/.env")

from database import init_db, SessionLocal
from services.drive_sync import listar_arquivos_drive_publico, parse_filename
import asyncio

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

async def main():
    print_header("SINCRONIZADOR DO GOOGLE DRIVE")

    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    api_key = os.getenv("GOOGLE_API_KEY")

    if not folder_id:
        print_error("GOOGLE_DRIVE_FOLDER_ID não configurada")
        return 1

    print_info(f"Folder ID: {folder_id}")
    print_info(f"Modo: Público (scraping HTML, sem API key)")
    print_info(f"Data/hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")

    # Inicializa banco
    print_header("INICIALIZANDO BANCO")
    try:
        init_db()
        print_success("Banco de dados inicializado")
    except Exception as e:
        print_error(f"Erro ao inicializar banco: {e}")
        return 1

    # Sincroniza Drive
    print_header("SINCRONIZANDO GOOGLE DRIVE")
    try:
        print_info("Usando modo público (scraping, sem API key)")
        arquivos = await listar_arquivos_drive_publico(folder_id)
        print_success(f"Total de fotos encontradas: {len(arquivos)}")

        if len(arquivos) == 0:
            print_warning("Nenhuma foto encontrada no Drive")
            return 0

        # Processa cada arquivo
        importados = 0
        erros = 0

        db = SessionLocal()
        from database import ProjetoORM
        import uuid

        for arquivo in arquivos:
            try:
                nome = arquivo.get("name", "")
                drive_id = arquivo.get("id", "")
                url = arquivo.get("url", "")

                # Parse nome para extrair metragem e horas
                metadata = parse_filename(nome)

                # Cria projeto
                proj_id = f"drive_{uuid.uuid4().hex[:12]}"
                projeto = ProjetoORM(
                    id=proj_id,
                    nome=nome[:100],
                    cliente="Google Drive",
                    mes_entrega="INDEFINIDO",
                    ano_entrega=2026,
                    foto_estofado_url=url,
                    descricao=f"Importado do Drive: {nome}",
                    observacoes=f"Drive ID: {drive_id}",
                    dados_ficha={
                        "metragem_tecido": metadata.get("metragem"),
                        "horas_totais": metadata.get("horas"),
                        "quantidade_pecas": metadata.get("quantidade"),
                    },
                    drive_file_id=drive_id,
                    analise_unificada=0,
                    materiais=[],
                    horas_trabalho=[],
                )
                db.add(projeto)
                importados += 1

                if importados % 10 == 0:
                    print_info(f"Processados {importados} arquivos...")

            except Exception as e:
                print_warning(f"Erro ao processar {nome}: {e}")
                erros += 1

        # Salva batch
        try:
            db.commit()
            print_success(f"Banco atualizado: {importados} fotos importadas")
        except Exception as e:
            print_error(f"Erro ao salvar no banco: {e}")
            db.rollback()
            return 1
        finally:
            db.close()

        print_header("RESULTADO DA SINCRONIZAÇÃO")
        print_info(f"Total de fotos no Drive: {len(arquivos)}")
        print_success(f"Fotos importadas: {importados}")
        if erros > 0:
            print_warning(f"Erros: {erros}")

        return 0

    except Exception as e:
        print_error(f"Erro na sincronização: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n[AVISO] Interrompido pelo usuário")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERRO] Erro fatal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
