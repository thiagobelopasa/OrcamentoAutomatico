# Orçamento Automático

Sistema de gestão e automação de orçamentos.

## Estrutura

```
OrcamentoAutomatico/
├── backend/          ← FastAPI (Python 3.11+)
│   ├── main.py
│   └── requirements.txt
├── frontend/         ← Next.js + TypeScript
│   ├── src/
│   ├── package.json
│   └── tsconfig.json
└── README.md
```

## Quick Start

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Backend rodará em `http://localhost:8000`

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend rodará em `http://localhost:3000`

## API

- Health check: `GET /health`

## Desenvolvimento

- Sem localhost — frontend será buildado como estático
- Backend exposto em porta padrão (8000)
