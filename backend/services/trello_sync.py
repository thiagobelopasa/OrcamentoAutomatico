import httpx
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import asyncio

class TrelloSync:
    """
    Integração com Trello para sincronização dinâmica de projetos (cards).
    Cada card = um cliente/projeto de sofá.
    Busca apenas novos anexos adicionados nos últimas 24h.
    """

    def __init__(self, api_key: str, api_token: str, board_id: str):
        self.api_key = api_key
        self.api_token = api_token
        self.board_id = board_id
        self.base_url = "https://api.trello.com/1"
        self.client = httpx.AsyncClient()

    async def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Faz request para API do Trello com autenticação"""
        params = kwargs.get('params', {})
        params['key'] = self.api_key
        params['token'] = self.api_token
        kwargs['params'] = params

        url = f"{self.base_url}{endpoint}"
        response = await self.client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    async def obter_listas(self) -> List[Dict[str, Any]]:
        """Obtém todas as listas do board"""
        return await self._request('GET', f'/boards/{self.board_id}/lists')

    async def obter_cards(self, list_id: str = None) -> List[Dict[str, Any]]:
        """
        Obtém cards do board.
        Se list_id fornecido, só cards dessa lista.
        Caso contrário, todos os cards do board.
        """
        if list_id:
            return await self._request('GET', f'/lists/{list_id}/cards')
        else:
            return await self._request('GET', f'/boards/{self.board_id}/cards',
                                     params={'fields': 'all'})

    async def obter_anexos_card(self, card_id: str) -> List[Dict[str, Any]]:
        """Obtém todos os anexos de um card"""
        return await self._request('GET', f'/cards/{card_id}/attachments')

    async def obter_detalhes_card(self, card_id: str) -> Dict[str, Any]:
        """Obtém detalhes completos de um card"""
        return await self._request('GET', f'/cards/{card_id}',
                                  params={'fields': 'all', 'attachments': 'open'})

    async def obter_novos_anexos(self, card_id: str, desde: datetime) -> List[Dict[str, Any]]:
        """
        Obtém apenas anexos adicionados desde a data fornecida.
        Útil para polling de 24h.
        """
        anexos = await self.obter_anexos_card(card_id)

        # Filtra apenas anexos criados após 'desde'
        from datetime import timezone
        # normaliza desde para UTC-aware
        if desde.tzinfo is None:
            desde = desde.replace(tzinfo=timezone.utc)

        novos = []
        for anexo in anexos:
            date_str = anexo.get('date', '')
            try:
                date_anexo = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                if date_anexo > desde:
                    novos.append(anexo)
            except (ValueError, AttributeError):
                novos.append(anexo)

        return novos

    @staticmethod
    def _lista_e_entrega(nome: str) -> bool:
        """Retorna True se a lista for do tipo ENTREGA MÊS ANO."""
        import re
        return bool(re.match(r'^ENTREGA\s+\w+\s+\d{4}$', nome.strip(), re.IGNORECASE))

    async def sincronizar_tudo(self, apenas_entrega: bool = True) -> Dict[str, Any]:
        """
        Sincroniza cards do board.
        Com apenas_entrega=True (padrão), importa só das listas 'ENTREGA MÊS ANO'.
        """
        listas = await self.obter_listas()

        if apenas_entrega:
            listas_alvo = [l for l in listas if self._lista_e_entrega(l['name'])]
        else:
            listas_alvo = listas

        resultado = {
            'timestamp': datetime.now().isoformat(),
            'listas_importadas': [l['name'] for l in listas_alvo],
            'total_listas': len(listas_alvo),
            'total_cards': 0,
            'cards': []
        }

        for lista in listas_alvo:
            # extrai mês e ano do nome da lista (ex: "ENTREGA MAIO 2026")
            parts = lista['name'].split()
            mes_lista = parts[1] if len(parts) > 1 else 'INDEFINIDO'
            ano_lista = parts[2] if len(parts) > 2 else str(datetime.now().year)

            cards = await self.obter_cards(lista['id'])
            resultado['total_cards'] += len(cards)

            for card in cards:
                anexos = await self.obter_anexos_card(card['id'])
                card_data = {
                    'id': card['id'],
                    'name': card['name'],
                    'lista_nome': lista['name'],
                    'mes_entrega': mes_lista.upper(),
                    'ano_entrega': int(ano_lista) if ano_lista.isdigit() else datetime.now().year,
                    'url': card.get('url'),
                    'desc': card.get('desc', ''),
                    'anexos': anexos,
                    'labels': [lb.get('name', '') for lb in card.get('labels', [])]
                }
                resultado['cards'].append(card_data)

        return resultado

    async def sincronizar_novos_anexos(self, ultimo_check: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Sincroniza APENAS novos anexos adicionados desde ultimo_check.
        Se ultimo_check não fornecido, usa 24h atrás.
        Retorna apenas cards com novos anexos.
        """
        if ultimo_check is None:
            ultimo_check = datetime.now() - timedelta(hours=24)

        cards = await self.obter_cards()

        resultado = {
            'timestamp': datetime.now().isoformat(),
            'check_desde': ultimo_check.isoformat(),
            'cards_com_novos_anexos': [],
            'total_novos_anexos': 0
        }

        for card in cards:
            novos_anexos = await self.obter_novos_anexos(card['id'], ultimo_check)

            if novos_anexos:
                card_data = {
                    'id': card['id'],
                    'name': card['name'],
                    'url': card.get('url'),
                    'desc': card.get('desc', ''),
                    'novos_anexos': novos_anexos,
                    'total_novos': len(novos_anexos)
                }
                resultado['cards_com_novos_anexos'].append(card_data)
                resultado['total_novos_anexos'] += len(novos_anexos)

        return resultado

    async def monitorar_novos_anexos(self, callback, intervalo_horas: int = 24):
        """
        Monitora novos anexos a cada intervalo_horas.
        Chama callback(dados) quando encontra novos anexos.
        """
        ultimo_check = datetime.now()

        while True:
            try:
                dados = await self.sincronizar_novos_anexos(ultimo_check)

                if dados['total_novos_anexos'] > 0:
                    await callback(dados)
                    ultimo_check = datetime.now()

            except Exception as e:
                print(f"Erro ao sincronizar Trello: {str(e)}")

            await asyncio.sleep(intervalo_horas * 3600)

    async def fechar(self):
        """Fecha a conexão do cliente HTTP"""
        await self.client.aclose()


def criar_sync_trello(api_key: str, api_token: str, board_id: str) -> TrelloSync:
    """Factory para criar instância de sincronização Trello"""
    return TrelloSync(api_key, api_token, board_id)
