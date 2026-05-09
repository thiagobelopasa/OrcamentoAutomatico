"""Wrapper na raiz do repo para deploys (Render, etc) que rodam do diretório raiz."""
import os
import sys
import importlib.util
from pathlib import Path

BACKEND = Path(__file__).resolve().parent / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

# Carrega backend/main.py sob nome diferente para evitar conflito com este wrapper
_spec = importlib.util.spec_from_file_location("orcamento_backend", BACKEND / "main.py")
_mod = importlib.util.module_from_spec(_spec)
sys.modules["orcamento_backend"] = _mod
_spec.loader.exec_module(_mod)

app = _mod.app
