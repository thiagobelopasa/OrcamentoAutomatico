"""
Sistema de deduplição de imagens por hash e similaridade perceptual.

Funciona em 2 níveis:
1. Hash exato (MD5/SHA256) — detecta a mesma imagem extraída do banco
2. Perceptual hash (dhash) — detecta variações menores (compressão, pequenas alterações)
"""
import hashlib
import base64
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)

# Cache global de hashes de imagens — {arquivo: hash}
_HASH_CACHE: Dict[str, str] = {}


def _dhash(image_path: str, hash_size: int = 8) -> str:
    """
    Calcula diferential hash (dhash) de uma imagem.
    Invariante a pequenas mudanças: compressão, redimensionamento, brilho.

    dhash é mais tolerante que hash exato — detecta a mesma foto
    mesmo com compressão JPEG diferente.
    """
    try:
        from PIL import Image
    except ImportError:
        # Fallback para hash exato se PIL não estiver disponível
        return _exact_hash(image_path)

    try:
        img = Image.open(image_path).convert("L")  # grayscale
        img = img.resize((hash_size + 1, hash_size))

        # Calcula diferenças horizontais
        dhash_str = ""
        for row in range(hash_size):
            for col in range(hash_size):
                if img.getpixel((col, row)) > img.getpixel((col + 1, row)):
                    dhash_str += "1"
                else:
                    dhash_str += "0"

        return dhash_str
    except Exception as e:
        logger.warning(f"Erro ao calcular dhash de {image_path}: {e}")
        return _exact_hash(image_path)


def _exact_hash(image_path: str) -> str:
    """
    Calcula hash exato (SHA256) do arquivo de imagem.
    Detecta a mesma arquivo byte-for-byte.
    """
    try:
        with open(image_path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception as e:
        logger.warning(f"Erro ao calcular hash exato de {image_path}: {e}")
        return ""


def get_image_hash(image_path: str, use_perceptual: bool = True) -> str:
    """
    Calcula hash de uma imagem.

    Args:
        image_path: caminho do arquivo
        use_perceptual: se True, usa dhash (tolerante); se False, usa SHA256 (exato)

    Returns:
        String do hash (64 chars para SHA256, 64 chars para dhash)
    """
    if image_path in _HASH_CACHE:
        return _HASH_CACHE[image_path]

    if use_perceptual:
        hash_val = _dhash(image_path)
    else:
        hash_val = _exact_hash(image_path)

    _HASH_CACHE[image_path] = hash_val
    return hash_val


def hamming_distance(hash1: str, hash2: str) -> int:
    """
    Calcula distância de Hamming entre dois hashes (quantos bits diferem).
    Usada para dhash — quanto menor, mais similar.
    """
    if len(hash1) != len(hash2):
        return 999
    return sum(c1 != c2 for c1, c2 in zip(hash1, hash2))


def are_images_similar(
    path1: str,
    path2: str,
    threshold: int = 5,  # máximo de bits diferentes para considerar igual
    use_perceptual: bool = True,
) -> Tuple[bool, int]:
    """
    Verifica se duas imagens são similares.

    Args:
        path1, path2: caminhos das imagens
        threshold: máximo de bits diferentes (0-64 para dhash)
        use_perceptual: se True, usa dhash; se False, compara SHA256

    Returns:
        (is_similar: bool, distance: int)
    """
    hash1 = get_image_hash(path1, use_perceptual=use_perceptual)
    hash2 = get_image_hash(path2, use_perceptual=use_perceptual)

    if not use_perceptual:
        # Hash exato — iguais se forem idênticos
        is_equal = hash1 == hash2
        distance = 0 if is_equal else 999
        return is_equal, distance

    # dhash — compara distância de Hamming
    distance = hamming_distance(hash1, hash2)
    is_similar = distance <= threshold

    return is_similar, distance


def find_duplicate_in_bank(
    uploaded_path: str,
    bank_entries: List[Dict],
    similarity_threshold: float = 0.95,
    use_perceptual: bool = True,
) -> Optional[Dict]:
    """
    Procura por duplicata exata/similar da foto enviada no banco de dados.

    Args:
        uploaded_path: caminho da foto enviada
        bank_entries: lista de projetos do banco [{id, foto_antes_url, ...}]
        similarity_threshold: % mínima de similaridade (0-1)
        use_perceptual: se True, usa dhash; se False, usa SHA256

    Returns:
        Dicionário do projeto mais similar, ou None se não houver match

    Exemplo:
        dup = find_duplicate_in_bank(
            "/tmp/foto.jpg",
            [{"id": "proj_123", "foto_antes_url": "url1.jpg", ...}],
            similarity_threshold=0.90
        )
        if dup:
            print(f"Encontrada duplicata: {dup['id']} com 100% confiança")
    """
    uploaded_hash = get_image_hash(uploaded_path, use_perceptual=use_perceptual)
    if not uploaded_hash:
        return None

    best_match = None
    best_score = 0

    for entry in bank_entries:
        foto_url = entry.get("foto_antes_url")
        if not foto_url:
            continue

        # Tenta carregar a foto do banco (estará em cache se já foi processada)
        try:
            # Se for URL, precisaria baixar — por enquanto assume caminhos locais
            # Em produção, adicionar cache de fotos baixadas
            if foto_url.startswith("http"):
                continue  # Skip URLs por enquanto

            entry_hash = get_image_hash(foto_url, use_perceptual=use_perceptual)
            if not entry_hash:
                continue

            if use_perceptual:
                # dhash — usa Hamming distance
                distance = hamming_distance(uploaded_hash, entry_hash)
                max_distance = int(64 * (1 - similarity_threshold))
                if distance <= max_distance:
                    score = 1.0 - (distance / 64)
                    if score > best_score:
                        best_score = score
                        best_match = entry
            else:
                # Hash exato
                if uploaded_hash == entry_hash:
                    return entry  # Match perfeito, retorna imediatamente

        except Exception as e:
            logger.debug(f"Erro ao comparar com {foto_url}: {e}")
            continue

    if best_score >= similarity_threshold:
        return best_match

    return None


def clear_cache():
    """Limpa cache de hashes (útil em testes)."""
    global _HASH_CACHE
    _HASH_CACHE.clear()
