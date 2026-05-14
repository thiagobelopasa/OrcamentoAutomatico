#!/usr/bin/env python3
"""
Sincronização direta do banco sem depender da API.
Executa sincronização do Trello e análise Vision diretamente.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from pathlib import Path
import asyncio
from datetime import datetime
import uuid
from dotenv import load_dotenv

# Load environment
load_dotenv("backend/.env.local", override=True)
load_dotenv("backend/.env")

from database import init_db, SessionLocal, ProjetoORM, AnaliseProjeto
from services.trello_sync import criar_sync_trello


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


async def sincronizar_banco():
    """Sincroniza banco com Trello e executa análise Vision."""

    print("\n" + "=" * 70)
    print("SINCRONIZACIÓN DIRETA DO BANCO DE DADOS COM TRELLO".center(70))
    print("=" * 70 + "\n")

    # Step 1: Verificar credenciais
    print_header("1. VERIFICANDO CREDENCIAIS")
    api_key = os.getenv("TRELLO_API_KEY")
    api_token = os.getenv("TRELLO_API_TOKEN")
    board_id = os.getenv("TRELLO_BOARD_ID")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    if not all([api_key, api_token, board_id]):
        print_error("Credenciais Trello não configuradas!")
        print_info(f"  TRELLO_API_KEY: {'OK' if api_key else 'FALTANDO'}")
        print_info(f"  TRELLO_API_TOKEN: {'OK' if api_token else 'FALTANDO'}")
        print_info(f"  TRELLO_BOARD_ID: {'OK' if board_id else 'FALTANDO'}")
        return False

    if not anthropic_key:
        print_warning("ANTHROPIC_API_KEY não configurada (Vision não funcionará)")
    else:
        print_success("ANTHROPIC_API_KEY configurada")

    print_success("Credenciais Trello OK")

    # Step 2: Inicializar banco
    print_header("2. INICIALIZANDO BANCO DE DADOS")
    init_db()
    db = SessionLocal()

    # Contar projetos atuais
    count_antes = db.query(ProjetoORM).count()
    print_info(f"Projetos atuais no banco: {count_antes}")

    # Limpar banco se tiver dados antigos
    if count_antes > 0:
        print_warning("Limpando banco antigo...")
        db.query(AnaliseProjeto).delete(synchronize_session=False)
        db.query(ProjetoORM).delete(synchronize_session=False)
        db.commit()
        print_success("Banco limpo")

    # Step 3: Importar do Trello
    print_header("3. IMPORTANDO DO TRELLO")
    print_info("Conectando ao Trello e buscando cards...")

    try:
        sync = criar_sync_trello(api_key, api_token, board_id)
        dados = await sync.sincronizar_tudo(apenas_entrega=True)
        await sync.fechar()

        cards = dados.get("cards", [])
        listas = dados.get("listas_importadas", [])

        print_success(f"Encontrados {len(cards)} cards nas listas: {', '.join(listas)}")

    except Exception as e:
        print_error(f"Erro ao buscar Trello: {e}")
        db.close()
        return False

    # Step 4: Criar projetos
    print_header("4. CRIANDO PROJETOS")

    criados = 0
    for i, card in enumerate(cards, 1):
        try:
            card_id = card["id"]
            # Verificar se já existe
            existe = db.query(ProjetoORM).filter(ProjetoORM.trello_card_id == card_id).first()
            if existe:
                print_info(f"  [{i}/{len(cards)}] {card['name'][:40]:40} (já existe)")
                continue

            anexos = card.get("anexos", [])
            urls_todos = [a.get("url") for a in anexos if a.get("url")]

            proj_id = f"proj_{uuid.uuid4().hex[:12]}"
            db.add(ProjetoORM(
                id=proj_id,
                nome=card["name"],
                cliente=card["name"],
                mes_entrega=card.get("mes_entrega", "INDEFINIDO"),
                ano_entrega=card.get("ano_entrega", datetime.now().year),
                trello_card_id=card_id,
                trello_card_url=card.get("url"),
                descricao=card.get("desc", ""),
                urls_anexos=urls_todos,
                materiais=[],
                horas_trabalho=[],
            ))
            criados += 1
            print_info(f"  [{i}/{len(cards)}] {card['name'][:40]:40} ({len(urls_todos)} anexos)")

        except Exception as e:
            print_warning(f"  [{i}/{len(cards)}] Erro ao processar {card.get('name', 'desconhecido')}: {e}")
            continue

    db.commit()
    print_success(f"Total de novos projetos criados: {criados}")

    # Step 5: Resumo final
    print_header("5. RESUMO DA SINCRONIZAÇÃO")

    count_depois = db.query(ProjetoORM).count()
    print_info(f"Total no banco agora: {count_depois}")
    print_info(f"Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")

    db.close()

    if criados > 0:
        print_success("SINCRONIZAÇÃO COMPLETADA COM SUCESSO!")
        print_info(f"Total de projetos: {count_depois}")
        print_info(f"Novos projetos: {criados}")
        return True
    else:
        print_warning("Nenhum novo projeto foi importado")
        return False


if __name__ == "__main__":
    try:
        sucesso = asyncio.run(sincronizar_banco())
        sys.exit(0 if sucesso else 1)
    except KeyboardInterrupt:
        print(f"\n[AVISO] Interrompido pelo usuário")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERRO] Erro fatal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
