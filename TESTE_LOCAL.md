# 🧪 Teste End-to-End Local

Guia completo para testar o sistema localmente.

## ✅ Pré-requisitos

- Python 3.11+
- Pip
- Git

## 🚀 Passo 1: Instalar Dependências

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 🚀 Passo 2: Rodar o Backend (Terminal 1)

```bash
cd backend
.venv\Scripts\activate
uvicorn main:app --reload
```

**Esperado:**
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete
```

Você poderá acessar:
- API: http://localhost:8000
- Swagger Docs: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 🧪 Passo 3: Rodar os Testes (Terminal 2)

```bash
cd backend
.venv\Scripts\activate
python test_sistema.py
```

**Primeira execução:**
- Script perguntará quando você estiver pronto
- Pressione ENTER para iniciar

**O que será testado:**

1. ✅ Criar projeto
2. ✅ Listar projetos
3. ✅ Obter projeto específico
4. ✅ Adicionar material
5. ✅ Adicionar horas de trabalho
6. ✅ Deletar material
7. ✅ Atualizar projeto
8. ✅ Adicionar comparação orçado vs real
9. ✅ Filtrar por cliente
10. ✅ Deletar projeto

## 📊 Verificar Banco de Dados

Após rodar os testes, um arquivo `orcamento.db` será criado em `backend/`.

### Ver dados via SQLite CLI

```bash
# No Windows
cd backend
sqlite3 orcamento.db

# Dentro do SQLite:
.tables
SELECT * FROM projetos;
SELECT * FROM analises_projeto;
.quit
```

### Ver via Python

```bash
cd backend
.venv\Scripts\activate
python
```

```python
from database import SessionLocal
from database import ProjetoORM

db = SessionLocal()
projetos = db.query(ProjetoORM).all()
for p in projetos:
    print(f"{p.nome}: {p.total_horas}h")
db.close()
```

## 🔄 Testes com Imagens (Vision API)

Para testar com imagens:

1. Coloque imagens em `backend/uploads/`
2. Chamar endpoint com imagens:

```bash
curl -X POST "http://localhost:8000/projetos/{projeto_id}/upload-anexos" \
  -F "files=@uploads/imagem1.jpg" \
  -F "files=@uploads/imagem2.jpg"
```

Esperado: Claude Vision extrai automaticamente materiais e horas.

## 🐛 Troubleshooting

### Erro: "ModuleNotFoundError: No module named 'sqlalchemy'"

```bash
pip install -r requirements.txt
```

### Erro: "Connection refused" ao conectar na API

Certifique-se que o backend está rodando:
```bash
# Terminal 1: Backend rodando?
# Terminal 2: Testes
```

### Banco de dados bloqueado

Remova `orcamento.db` e rode novamente:
```bash
rm backend/orcamento.db
python test_sistema.py
```

## 📈 Iterações de Teste

Recomendado testar 3-5 vezes:

**Iteração 1:** Validar CRUD básico
```bash
python test_sistema.py
```

**Iteração 2:** Verificar dados no banco
```bash
sqlite3 backend/orcamento.db "SELECT COUNT(*) FROM projetos;"
```

**Iteração 3:** Testar com Trello sync (quando ativado)

**Iteração 4:** Testar com Vision API (com imagens)

**Iteração 5:** Teste de carga (múltiplos projetos)

## ✨ Próximo Passo

Depois de validar tudo localmente:

1. Push para o GitHub — GitHub Actions faz deploy automático no HF Space
2. Configurar `DATABASE_URL` (Supabase PostgreSQL) nas secrets do HF Space
3. Site disponível na Vercel (deploy automático via GitHub)

---

**Dúvidas?** Revise `CLAUDE.md` para arquitetura técnica.
