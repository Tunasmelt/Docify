# Docify

Multi-tenant SaaS: upload documents, ask questions, get answers with verified inline citations back to the exact source page and element.

See [AGENT.md](AGENT.md) and [.agent/ARCHITECTURE.md](.agent/ARCHITECTURE.md) for full architecture and stack details.

## Quick start

### Frontend (`apps/web`)

```
cd apps/web
pnpm install
cp .env.example .env.local   # fill in Supabase + API URL values
pnpm dev                     # http://localhost:3000
```

### Backend (`apps/api`)

```
cd apps/api
uv sync
cp .env.example .env         # fill in Supabase, Voyage, Gemini keys
uv run uvicorn main:app --reload   # http://localhost:8000
```

## Stack

Next.js 14 + FastAPI + Docling + Voyage (embeddings) + Gemini (generation, verification, OCR) + Supabase (Postgres/pgvector/Auth/Storage). Full rationale in `.agent/MEMORY.md §Decision log`.
