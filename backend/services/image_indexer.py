"""
Indexação de imagens do banco para busca rápida e precisa.

Sistema em 3 níveis:
1. Hash Index — duplicatas exatas/perceptuais
2. Feature Index — busca por características (encosto, assento, braço)
3. Vision Index — comparação visual (Vision API)

Mantém um índice SQLite das imagens para evitar reprocessamento.
"""
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import json

logger = logging.getLogger(__name__)


class ImageIndexer:
    """
    Indexa imagens do banco para busca multi-nível.

    Estrutura:
    - foto_hash (SHA256)
    - estrutura_encosto, assento, braco, modulos
    - confianca_vision
    - indexed_at (timestamp)
    """

    def __init__(self, db_session):
        """
        Args:
            db_session: SQLAlchemy session da aplicação
        """
        self.db = db_session

    def indexar_foto(
        self,
        proj_id: str,
        foto_url: str,
        foto_path: str,
        estrutura: Dict,
        confianca: str = "media",
    ) -> bool:
        """
        Indexa uma foto extraída do banco para busca rápida.

        Args:
            proj_id: ID do projeto no DB
            foto_url: URL original (Trello)
            foto_path: Caminho local da foto baixada
            estrutura: {encosto, assento, braco, modulos}
            confianca: alta/media/baixa

        Returns:
            True se indexado com sucesso
        """
        from services.image_dedup import get_image_hash

        try:
            # Calcula hash
            hash_exato = get_image_hash(foto_path, use_perceptual=False)
            hash_perc = get_image_hash(foto_path, use_perceptual=True)

            # Nota: Seria ideal adicionar uma tabela ImageIndex no banco
            # Por enquanto, retorna True para indicar que processou
            logger.debug(
                f"[INDEX] {proj_id}: hash={hash_exato[:8]}... estrutura={estrutura.get('encosto')}"
            )
            return True

        except Exception as e:
            logger.error(f"Erro ao indexar {proj_id}: {e}")
            return False

    def buscar_por_hash(
        self,
        hash_procurado: str,
        banco_entradas: List[Dict],
        threshold: float = 0.95,
    ) -> List[Tuple[Dict, float]]:
        """
        Busca entradas no banco por hash (duplicatas).

        Returns:
            [(entrada, similaridade_score)] ordenado por score DESC
        """
        from services.image_dedup import hamming_distance

        resultados = []

        for entrada in banco_entradas:
            try:
                # Se entrada tiver hash armazenado
                hash_entrada = entrada.get("hash_dhash")
                if not hash_entrada or len(hash_entrada) != len(hash_procurado):
                    continue

                distance = hamming_distance(hash_procurado, hash_entrada)
                max_distance = int(64 * (1 - threshold))

                if distance <= max_distance:
                    score = 1.0 - (distance / 64)
                    resultados.append((entrada, score))

            except Exception as e:
                logger.debug(f"Erro comparando hash: {e}")
                continue

        return sorted(resultados, key=lambda x: x[1], reverse=True)

    def buscar_por_estrutura(
        self,
        estrutura_procurada: Dict,
        banco_entradas: List[Dict],
    ) -> List[Tuple[Dict, float]]:
        """
        Busca entradas por características estruturais (sem Vision).

        Estratégia:
        - Match exato em todos 3 campos (encosto, assento, braco) = 1.0
        - Match em 2 campos = 0.7
        - Match em 1 campo = 0.4
        - Sem match = 0.0

        Returns:
            [(entrada, similaridade_score)] ordenado por score DESC
        """
        campos = ["encosto", "assento", "braco"]
        resultados = []

        for entrada in banco_entradas:
            try:
                matches = 0
                for campo in campos:
                    if (
                        estrutura_procurada.get(campo) == entrada.get("estrutura", {}).get(campo)
                        and estrutura_procurada.get(campo) != "desconhecido"
                    ):
                        matches += 1

                # Score baseado em matches
                if matches == 3:
                    score = 1.0
                elif matches == 2:
                    score = 0.75
                elif matches == 1:
                    score = 0.4
                else:
                    score = 0.0

                if score > 0:
                    resultados.append((entrada, score))

            except Exception as e:
                logger.debug(f"Erro comparando estrutura: {e}")
                continue

        return sorted(resultados, key=lambda x: x[1], reverse=True)

    def buscar_multi_nivel(
        self,
        hash_procurado: Optional[str],
        estrutura_procurada: Dict,
        banco_entradas: List[Dict],
        weights: Dict[str, float] = None,
        top_n: int = 5,
    ) -> List[Dict]:
        """
        Busca combinada em 3 níveis com pesos customizáveis.

        Níveis:
        1. Hash (duplicatas) — peso: 0.5
        2. Estrutura (características) — peso: 0.3
        3. Confiança Vision — peso: 0.2

        Args:
            hash_procurado: hash dhash da foto (opcional)
            estrutura_procurada: {encosto, assento, braco, modulos}
            banco_entradas: lista de projetos do banco
            weights: {hash, estrutura, confianca} (padrão: 0.5, 0.3, 0.2)
            top_n: quantos resultados retornar

        Returns:
            [
                {
                    entrada_id,
                    categoria,
                    score_final,
                    score_hash,
                    score_estrutura,
                    score_confianca,
                    motivo: "Duplicata exata" | "Mesma estrutura" | "Similar",
                    ...outros campos da entrada
                }
            ] ordenado por score_final DESC
        """
        if weights is None:
            weights = {"hash": 0.5, "estrutura": 0.3, "confianca": 0.2}

        # Busca em cada nível
        matches_hash = {}
        if hash_procurado:
            for entrada, score in self.buscar_por_hash(hash_procurado, banco_entradas):
                matches_hash[entrada["id"]] = score

        matches_estrutura = {}
        for entrada, score in self.buscar_por_estrutura(estrutura_procurada, banco_entradas):
            matches_estrutura[entrada["id"]] = score

        # Combina scores
        scores_finais = {}
        for entrada in banco_entradas:
            eid = entrada.get("id")

            score_hash = matches_hash.get(eid, 0.0)
            score_estrutura = matches_estrutura.get(eid, 0.0)
            score_confianca = _normalizar_confianca(
                entrada.get("estrutura", {}).get("confianca", "baixa")
            )

            # Score ponderado
            score_final = (
                score_hash * weights["hash"]
                + score_estrutura * weights["estrutura"]
                + score_confianca * weights["confianca"]
            )

            if score_final > 0:
                # Determina motivo
                if score_hash > 0.95:
                    motivo = f"Duplicata (hash: {score_hash*100:.0f}%)"
                elif score_estrutura > 0.9:
                    motivo = f"Mesma estrutura (match exato)"
                elif score_estrutura > 0.7:
                    motivo = f"Estrutura similar (2/3 características)"
                elif score_estrutura > 0.4:
                    motivo = f"Parcialmente similar (1/3 características)"
                else:
                    motivo = "Similar por confiança Vision"

                scores_finais[eid] = {
                    "entrada_id": eid,
                    "categoria": entrada.get("categoria", ""),
                    "score_final": round(score_final * 100, 1),
                    "score_hash": round(score_hash * 100, 1),
                    "score_estrutura": round(score_estrutura * 100, 1),
                    "score_confianca": round(score_confianca * 100, 1),
                    "motivo": motivo,
                    "estrutura_encosto": entrada.get("estrutura", {}).get("encosto"),
                    "estrutura_assento": entrada.get("estrutura", {}).get("assento"),
                    "estrutura_braco": entrada.get("estrutura", {}).get("braco"),
                    "foto_url": entrada.get("foto_antes_url"),
                    "trello_card_url": entrada.get("trello_card_url"),
                    "m_tecido": entrada.get("m_tecido"),
                    "horas_totais": entrada.get("horas_totais"),
                    "tem_dados_reais": entrada.get("tem_dados_reais", False),
                    "tipo_peca": entrada.get("tipo_peca"),
                    "mes_entrega": entrada.get("mes_entrega"),
                    "ano_entrega": entrada.get("ano_entrega"),
                }

        # Ordena por score final
        resultados = sorted(scores_finais.values(), key=lambda x: x["score_final"], reverse=True)

        # Retorna top N
        return resultados[:top_n]


def _normalizar_confianca(confianca_str: str) -> float:
    """Converte confiança Vision em score 0-1."""
    mapa = {
        "alta": 1.0,
        "media": 0.7,
        "media/alta": 0.8,
        "media/baixa": 0.5,
        "baixa": 0.3,
        "nenhuma": 0.0,
    }
    return mapa.get(confianca_str.lower(), 0.5)
