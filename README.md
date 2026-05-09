# 💼 Orçamento Automático

Sistema profissional de gestão de projetos de sofás com integração Trello, geração de PDFs e análise inteligente de orçamentos.

**Status**: 9 de 12 fases implementadas e testadas ✅

## 🎯 Características Principais

### ✨ Funcionalidades Implementadas
- **CRUD Completo** de projetos com materiais, horas e fotos
- **Edição em Linha** (inline) de materiais e horas sem recarregar
- **Comparação Visual** orçado vs real com color-coding automático
- **Upload de Fotos** antes/depois com slider comparativo
- **Geração de PDFs** profissionais de orçamentos
- **Sincronização Automática** com Trello (a cada 30 minutos)
- **Sistema de Alertas** estruturado (ERRO, AVISO, INFO)
- **Checklist de Entrega** com progresso percentual
- **Testes Automatizados** - 21 testes pytest (100% passando)

## 📊 Fases Completadas

| Fase | Nome | Status |
|------|------|--------|
| 1 | E2E Básico | ✅ Completa |
| 2 | Edição em Linha | ✅ Completa |
| 3 | Comparação Visual | ✅ Completa |
| 4 | Fotos Antes/Depois | ✅ Completa |
| 5 | Dashboard React | ✅ Completa |
| 6 | Sync Trello (APScheduler) | ✅ Completa |
| 7 | PDF Loader | ✅ Completa |
| 8 | Validação c/ Alertas | ✅ Completa |
| 9 | Testes Automatizados | ✅ Completa (21 testes) |

## 🚀 Quick Start

### Requisitos
- Python 3.11+
- pip
- Git

### Instalação

```bash
# Clonar repositório
git clone https://github.com/thiagobelopasa/OrcamentoAutomatico.git
cd OrcamentoAutomatico

# Instalar dependências
cd backend
pip install -r requirements.txt

# Rodar servidor
uvicorn main:app --reload
# API disponível em http://localhost:8000
```

### Rodar Testes

```bash
cd backend
pytest tests/ -v
# 21 testes, 100% passando
```

## 📚 Documentação

- **[IMPLEMENTACAO_COMPLETA.md](./IMPLEMENTACAO_COMPLETA.md)** - Detalhamento técnico completo
- **[backend/pytest.ini](./backend/pytest.ini)** - Configuração de testes
- **[backend/requirements.txt](./backend/requirements.txt)** - Dependências Python

## 🔌 API Endpoints

### Projetos
```
GET    /projetos/                 Lista todos
GET    /projetos/{id}             Obter detalhe
POST   /projetos/                 Criar novo
PATCH  /projetos/{id}             Atualizar
DELETE /projetos/{id}             Deletar
```

### Materiais e Horas
```
POST   /projetos/{id}/materiais       Adicionar
DELETE /projetos/{id}/materiais/{idx} Deletar
POST   /projetos/{id}/horas           Adicionar
DELETE /projetos/{id}/horas/{idx}     Deletar
```

### PDFs
```
POST /pdf/{id}/gerar       Gerar PDF (retorna data URI)
POST /pdf/{id}/exportar    Exportar para download
POST /pdf/parsear          Fazer upload e parsear
```

### Validação
```
POST /validation/{id}/validar                 Validar projeto
GET  /validation/{id}/alertas                 Listar alertas
POST /validation/{id}/preparar-para-entrega   Checklist entrega
```

### Scheduler
```
POST /scheduler/start      Iniciar agendador
POST /scheduler/stop       Parar agendador
GET  /scheduler/status     Status
POST /scheduler/sync-now   Sincronizar agora
```

### Sistema
```
GET  /health               Health check
GET  /                     Info da API
```

## 🏗️ Stack Tecnológico

### Backend
- FastAPI 0.110.0
- SQLAlchemy 2.0.48
- Pydantic 2.11.0
- SQLite (com JSON fields)
- APScheduler 3.10.4
- ReportLab 4.0.9
- PyPDF2 3.0.1
- pytest 7.4.4

## 👤 Desenvolvedor

**Thiago Belo** - [@thiagobelopasa](https://github.com/thiagobelopasa)

Desenvolvido com ❤️ usando **Claude Code**

---

**Última atualização**: Maio 2026  
**Versão**: 0.9.0 (9 fases completadas)
