"""
Integração com Google Drive para importar fotos de referência.
O nome do arquivo codifica metragem e horas: "1,5mt - 2,75hr.jpeg"
Funciona com pastas públicas sem precisar de API key (scraping fallback).
"""
import re
import asyncio
import httpx
from pathlib import Path
from typing import Optional


# ─── PARSER DE FILENAME ─────────────────────────────────────────────────────────

_NUM = r'(\d*[,\.]\d+|\d+)'  # número com vírgula ou ponto, com ou sem zero inicial


def parse_filename(filename: str) -> dict:
    """
    Extrai metragem, horas e quantidade do nome do arquivo.

    Formatos suportados (qualquer ordem):
      1,5mt - 2,75hr.jpeg
      0,4m - ,75hr.jpeg
      2hrs - 1,2mt.jpeg
      2 und, 5,25 mts - 7h.jpeg
      2 cadeiras - 3mt - 1hr.jpeg
      1,20 mt - 2,30hr.jpeg
    """
    nome = Path(filename).stem

    # Quantidade de peças (X und / X cadeiras / X poltronas)
    qtd_match = re.search(
        r'(\d+)\s*(?:und[s]?|cadeira[s]?|poltrona[s]?|pe[cç][a]?[s]?)',
        nome, re.IGNORECASE
    )
    quantidade = int(qtd_match.group(1)) if qtd_match else 1

    metragem: Optional[float] = None
    horas: Optional[float] = None

    # Padrão 1 — metragem antes das horas: "1,5 mt - 2hr"
    m = re.search(
        rf'{_NUM}\s*m[t]?[s]?\s*[-–,]\s*{_NUM}\s*h',
        nome, re.IGNORECASE
    )
    if m:
        metragem = _to_float(m.group(1))
        horas = _to_float(m.group(2))

    # Padrão 2 — horas antes da metragem: "1,5hr - 1mt"
    if metragem is None:
        m = re.search(
            rf'{_NUM}\s*h[r]?[s]?\s*[-–]\s*{_NUM}\s*m',
            nome, re.IGNORECASE
        )
        if m:
            horas = _to_float(m.group(1))
            metragem = _to_float(m.group(2))

    # Padrão 3 — só horas sem metragem (ex: peças de couro)
    if horas is None:
        m = re.search(rf'{_NUM}\s*h[r]?[s]?', nome, re.IGNORECASE)
        if m:
            horas = _to_float(m.group(1))

    # Padrão 4 — só metragem
    if metragem is None:
        m = re.search(rf'{_NUM}\s*m[t]?[s]?', nome, re.IGNORECASE)
        if m:
            metragem = _to_float(m.group(1))

    return {
        "metragem": metragem,
        "horas": horas,
        "quantidade": quantidade,
        "nome_original": Path(filename).stem,
    }


def _to_float(s: str) -> Optional[float]:
    if not s:
        return None
    s = s.strip().replace(',', '.')
    if s.startswith('.'):
        s = '0' + s
    try:
        return float(s)
    except ValueError:
        return None


# ─── GOOGLE DRIVE API (com API key) ─────────────────────────────────────────────

_MIME_IMAGEM = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_EXT_IMAGEM = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


async def listar_arquivos_drive(folder_id: str, api_key: str) -> list[dict]:
    """Lista todos os arquivos de imagem em uma pasta do Google Drive (requer API key)."""
    url = "https://www.googleapis.com/drive/v3/files"
    arquivos = []
    page_token = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime)",
                "pageSize": 1000,
                "key": api_key,
            }
            if page_token:
                params["pageToken"] = page_token

            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            for f in data.get("files", []):
                mime = f.get("mimeType", "")
                if mime in _MIME_IMAGEM or mime == "":
                    ext = Path(f["name"]).suffix.lower()
                    if ext in _EXT_IMAGEM:
                        arquivos.append(f)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

    return arquivos


def url_download_drive(file_id: str, api_key: str) -> str:
    """URL de download direto via Google Drive API (requer API key)."""
    return (
        f"https://www.googleapis.com/drive/v3/files/{file_id}"
        f"?alt=media&key={api_key}"
    )


# ─── GOOGLE DRIVE PÚBLICO (sem API key) ─────────────────────────────────────────

async def listar_arquivos_drive_publico(folder_id: str) -> list[dict]:
    """
    Lista arquivos de imagem em pasta pública do Drive sem precisar de API key.
    Usa embeddedfolderview (HTML server-rendered) com recursão em subpastas.
    Retorna lista de {id, name, mimeType}.
    """
    return await _listar_embeddedfolderview(folder_id, _depth=0)


# Padrão de ID do Drive (25–50 chars alfanuméricos)
_ID_RE = re.compile(r'[A-Za-z0-9_-]{25,50}')

# Padrão de entrada no embeddedfolderview:
# <div class="flip-entry" id="entry-{ID}"> ... href="{HREF}" ... flip-entry-title>{NAME}<
_ENTRY_RE = re.compile(
    r'id="entry-([A-Za-z0-9_-]{25,50})".*?'
    r'href="([^"]+)".*?'
    r'class="flip-entry-title">([^<]+)<',
    re.DOTALL
)


async def _listar_embeddedfolderview(folder_id: str, _depth: int = 0) -> list[dict]:
    """Recursivo: lista arquivos de pasta pública via embeddedfolderview."""
    if _depth > 3:
        return []

    url = f"https://drive.google.com/embeddedfolderview?id={folder_id}#list"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)

    if resp.status_code != 200:
        return []

    html = resp.text
    arquivos: list[dict] = []

    for m in _ENTRY_RE.finditer(html):
        entry_id = m.group(1)
        href = m.group(2)
        name = m.group(3).strip()

        if "/drive/folders/" in href:
            # É uma subpasta — recursão
            sub = await _listar_embeddedfolderview(entry_id, _depth + 1)
            arquivos.extend(sub)
            await asyncio.sleep(0.3)  # respeita rate do Drive
        else:
            # É um arquivo — aceita só imagens
            ext = Path(name).suffix.lower()
            if ext in _EXT_IMAGEM:
                arquivos.append({
                    "id": entry_id,
                    "name": name,
                    "mimeType": "image/jpeg",
                })

    return arquivos


def url_download_drive_publico(file_id: str) -> str:
    """URL de download para arquivo público do Drive (sem API key)."""
    return f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"


def url_view_drive_publico(file_id: str) -> str:
    """URL de visualização pública do Drive (para armazenar como foto_url)."""
    return f"https://drive.google.com/uc?id={file_id}&export=view"
