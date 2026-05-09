# Setup — Orçamento Automático

## Pré-requisitos

- Python 3.11+
- Node.js 18+ (para frontend)
- Git

## Backend Setup

```bash
cd backend

# Criar ambiente virtual
python -m venv .venv

# Ativar (Windows)
.venv\Scripts\activate

# Ativar (Linux/Mac)
source .venv/bin/activate

# Instalar dependências
pip install -r requirements.txt

# Rodar servidor
uvicorn main:app --reload
```

Backend estará em: **http://localhost:8000**

### Verificar saúde
```bash
curl http://localhost:8000/health
```

## Frontend Setup

```bash
cd frontend

# Instalar dependências
npm install

# Rodar servidor de dev (sem localhost)
npm run dev
```

Frontend será servido em: **http://localhost:3000**

## Build para Produção

### Frontend (static)
```bash
cd frontend
npm run build
npm start
```

### Backend
```bash
cd backend
# Usar gunicorn ou outro production server
pip install gunicorn
gunicorn main:app --workers 4
```

## Estrutura

```
OrcamentoAutomatico/
├── backend/
│   ├── main.py              ← App FastAPI
│   ├── models.py            ← Schemas Pydantic
│   ├── requirements.txt
│   └── .venv/               ← Ambiente virtual (não commitar)
│
├── frontend/
│   ├── src/
│   │   ├── app/             ← App Next.js (layout, page)
│   │   ├── components/      ← Componentes React reutilizáveis
│   │   ├── lib/             ← Utilitários
│   │   └── services/        ← Chamadas API (axios)
│   ├── package.json
│   └── node_modules/        ← Não commitar
│
└── README.md
```

## Padrões

### Backend
- Endpoints em `routers/` (criar conforme necessidade)
- Lógica em `services/`
- Modelos em `models.py`

### Frontend
- Páginas em `src/app/`
- Componentes reutilizáveis em `src/components/`
- Todas as chamadas HTTP em `src/services/api.ts`

## Próximos passos

Passa os detalhes de negócio e vamos estruturar:
- Routers específicos (orçamento, projetos, etc)
- Modelos de dados
- Interfaces React
