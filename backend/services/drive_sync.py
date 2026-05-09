"""
Integração com Google Drive para importar fotos de referência.
O nome do arquivo codifica metragem e horas: "1,5mt - 2,75hr.jpeg"
"""
import re
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


# ─── GOOGLE DRIVE API ───────────────────────────────────────────────────────────

_MIME_IMAGEM = {"image/jpeg", "image/png", "image/webp", "image/gif"}


async def listar_arquivos_drive(folder_id: str, api_key: str) -> list[dict]:
    """Lista todos os arquivos de imagem em uma pasta do Google Drive."""
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
                # Aceita imagens ou arquivos sem mime type (Drive às vezes omite)
                if mime in _MIME_IMAGEM or mime == "":
                    ext = Path(f["name"]).suffix.lower()
                    if ext in {".jpg", ".jpeg", ".png", ".webp"}:
                        arquivos.append(f)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

    return arquivos


def url_download_drive(file_id: str, api_key: str) -> str:
    """URL de download direto via Google Drive API."""
    return (
        f"https://www.googleapis.com/drive/v3/files/{file_id}"
        f"?alt=media&key={api_key}"
    )
