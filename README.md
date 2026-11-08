# RepoLexicon

**The lexicon of your codebase.**

RepoLexicon turns a GitHub repository into something you can actually read: a deterministic architecture manifest, interactive diagrams, and a grounded chat that answers questions about how the code fits together.

Paste a repository URL, and RepoLexicon shallow-clones it, parses the source, indexes it for retrieval, and builds a structural summary — then lets you explore the result as a directory tree, a language breakdown, an entrypoint map, or a conversation.

---

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [How indexing works](#how-indexing-works)
- [Supported languages](#supported-languages)
- [API reference](#api-reference)
- [Data and storage](#data-and-storage)
- [Database migrations](#database-migrations)
- [Tests and linting](#tests-and-linting)
- [Security and secrets](#security-and-secrets)
- [Troubleshooting](#troubleshooting)
- [Production checklist](#production-checklist)

---

## Features

- **Import any public GitHub repository** — submit a URL and indexing runs in the background with live status and progress.
- **Deterministic architecture manifest** — languages, directories, entrypoints, symbol index, and documentation inventory built from the parsed tree. No LLM involved, so the output is stable and reproducible.
- **Architecture views** — language breakdown, directory graph, and entrypoint map, rendered as structured SVG graphs (with Mermaid source available for export).
- **Grounded code chat** — retrieval over indexed source chunks, plus manifest context automatically injected for architecture questions. Streaming responses are supported.
- **Manifest-only repositories** — docs- and config-only repositories produce zero code chunks but still get a complete manifest, so they remain queryable instead of being rejected.
- **Honest parse states** — a file is reported as structurally parsed only when its grammar was available and parsing succeeded; otherwise it is chunked as raw text and labelled accordingly.
- **Practical file filtering** — respects `.gitignore` and skips lockfiles, build output, binaries, minified assets, and oversized files.
- **Multi-user auth** — JWT access tokens with an HttpOnly refresh cookie; repositories and chat history are scoped per user.
- **Auth rate limiting** — in-process sliding-window limits on login, registration, and refresh.

---

## Architecture

```mermaid
flowchart LR
    U[Browser] --> F[React + Vite frontend]
    F -->|REST + SSE| A[FastAPI backend]
    A --> P[(PostgreSQL)]
    A --> C[(ChromaDB)]
    A --> D[Local .data storage]
    A -->|clone| G[GitHub]
    A -->|embeddings| E[Gemini / Hugging Face / local]
    A -->|chat| L[Groq / NVIDIA]
```

The backend does the heavy lifting. The frontend is a single-page app that talks to it over `/api/v1`, polls repository status while indexing, and streams chat responses.

---

## Tech stack

| Layer | Technologies |
|---|---|
| **Backend** | FastAPI, SQLAlchemy, Alembic, PostgreSQL, Pydantic |
| **Retrieval** | ChromaDB, LangChain, sentence-transformers, Gemini embeddings, Hugging Face Inference API |
| **Parsing** | tree-sitter (11 language grammars), GitPython, pathspec |
| **LLMs** | Groq, NVIDIA AI Endpoints |
| **Auth** | PyJWT, bcrypt, HttpOnly refresh cookies |
| **Frontend** | React 19, TypeScript, Vite, Tailwind CSS 4, shadcn/Radix UI, d3 + dagre, axios |

---

## Repository layout

```text
.
├── backend/
│   ├── app/
│   │   ├── ai/            # manifest construction, diagrams, question routing
│   │   ├── api/v1/        # auth, repositories, chat routes
│   │   ├── cli/           # manifest backfill CLI
│   │   ├── core/          # settings, security, rate limiting
│   │   ├── db/            # SQLAlchemy engine and session
│   │   ├── models/        # users, repositories, files, chunks, chat, manifests
│   │   ├── schemas/       # Pydantic request/response models
│   │   └── services/      # clone, filter, parse, embed, RAG, manifests
│   ├── alembic/           # database migrations
│   ├── tests/             # unittest suite (no database or network required)
│   ├── alembic.ini
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── src/
│   │   ├── api/           # axios client and auth refresh handling
│   │   ├── components/    # landing, auth, onboarding, chat, architecture
│   │   └── lib/           # graph layout and architecture helpers
│   └── package.json
├── .githooks/pre-commit   # local secret guard
└── .github/workflows/     # gitleaks secret scan
```

---

## Prerequisites

- **Python 3.11+** (3.13 is used in development)
- **Node.js 20.19+ or 22.12+** (required by Vite 8) and npm
- **PostgreSQL 14+**
- **Git**
- A **Groq** and/or **NVIDIA** API key for chat, and a **Gemini** or **Hugging Face** key for embeddings — or use the fully local embedding backend (see below)
- Roughly 2 GB of free disk space for dependencies, plus space for cloned repositories and vector indexes

---

## Quick start

### 1. Clone the repository

```bash
git clone https://github.com/AbhiramCh56/Repomind.git
cd Repomind
```

> The GitHub repository slug is still `Repomind`; the product name is RepoLexicon.

### 2. Create the database

```bash
createdb repolexicon
```

Or with `psql`:

```bash
psql -U postgres -c "CREATE DATABASE repolexicon;"
```

### 3. Create the Python environment

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements-dev.txt
```

`requirements-dev.txt` includes the runtime dependencies plus `httpx` for the test client. Use `backend/requirements.txt` if you only need to run the app.

### 4. Configure the backend

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env` and fill in real values. A minimal local configuration looks like this:

```dotenv
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/repolexicon
SECRET_KEY=replace-with-a-generated-secret
FRONTEND_URL=http://localhost:5173
ENVIRONMENT=development

LLM_PROVIDER=groq
GROQ_API_KEY=your-groq-key

EMBEDDING_BACKEND=gemini
GEMINI_API_KEY=your-gemini-key
```

Generate a secret key with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**Never commit `backend/.env`.** It is git-ignored; only `.env.example` belongs in the repository.

#### Running without API keys

Embeddings can run entirely locally:

```dotenv
EMBEDDING_BACKEND=local
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_BATCH_SIZE=32
```

The model is downloaded and loaded in-process on first use, so the first import is slower and uses more memory. Chat still requires an LLM provider; set `CHAT_ENABLED=false` if you only want architecture exploration.

### 5. Start the backend

```bash
cd backend
uvicorn app.main:app --reload --reload-dir app --port 8000
```

The `--reload-dir app` flag is intentional: it keeps the watcher away from `backend/.data`, where repository clones are written. Watching that directory can trigger reloads in the middle of an indexing job.

On first boot the app creates any missing tables. Once it is running:

- API: <http://localhost:8000>
- Interactive docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

On Windows, the bundled launcher can be used instead:

```powershell
.\backend\run_dev.ps1
```

It expects the virtual environment at the repository root (`.venv`), as created above.

### 6. Start the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

The frontend expects the backend at `http://localhost:8000`. The axios client's base URL is set in `frontend/src/api/axiosConfig.ts`; `VITE_API_URL` only overrides the health check and streaming origin in `App.tsx`. If you host the backend elsewhere, update both.

### 7. Use it

1. Register an account or sign in.
2. Paste a public GitHub repository URL.
3. Wait for the status to reach **completed** — the architecture views build live as files are indexed.
4. Open the **Architecture** tab for diagrams, or the **Chat** tab to ask questions about the code.

---

## Configuration

All backend settings live in `backend/.env` and are read by `backend/app/core/config.py`. Variables omitted from `.env` fall back to the defaults shown here.

### Core

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | **required** | PostgreSQL connection string |
| `SECRET_KEY` | **required** | JWT signing key; generate a random 64-hex-character value |
| `ALGORITHM` | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access-token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh-cookie lifetime |
| `FRONTEND_URL` | `http://localhost:5173` | Exact allowed CORS origin, no trailing slash |
| `ENVIRONMENT` | `development` | `production` marks the refresh cookie `Secure` |
| `CHAT_ENABLED` | `true` | `false` returns `503` on ask/stream routes while keeping history readable |

### Rate limiting

| Variable | Default | Notes |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | In-process sliding-window limiter |
| `RATE_LIMIT_MAX_KEYS` | `10000` | Hard cap on tracked keys |
| `CLIENT_IP_HEADER` | unset | Set to `cf-connecting-ip` behind Cloudflare/Render; leave blank when directly reachable |
| `LOGIN_IP_LIMIT` | `20` | Login attempts per IP per window |
| `LOGIN_IP_WINDOW_SECONDS` | `300` | Login IP window |
| `LOGIN_ACCOUNT_LIMIT` | `5` | Login attempts per account per window |
| `LOGIN_ACCOUNT_WINDOW_SECONDS` | `900` | Login account window |
| `REGISTER_IP_LIMIT` | `5` | Registrations per IP per window |
| `REGISTER_IP_WINDOW_SECONDS` | `3600` | Registration window |
| `REFRESH_IP_LIMIT` | `60` | Refreshes per IP per window |
| `REFRESH_IP_WINDOW_SECONDS` | `3600` | Refresh window |

> The limiter stores counters in one process's memory. Running multiple workers or instances multiplies the effective budget; move the store to Redis before scaling out.

### LLM

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `groq` | `groq` or `nvidia` |
| `GROQ_API_KEY` | — | Required when `LLM_PROVIDER=groq` |
| `NVIDIA_API_KEY` | — | Required when `LLM_PROVIDER=nvidia` |
| `OPENAI_API_KEY` | — | Reserved; the current provider switch supports `groq` and `nvidia` only |
| `LLM_MODEL_GROQ` | `openai/gpt-oss-20b` | Groq model ID |
| `LLM_MODEL_NVIDIA` | `nvidia/nemotron-3.5-lightning-30b-a3b` | NVIDIA model ID |
| `LLM_TEMPERATURE` | `0.2` | Generation temperature |

### Embeddings

| Variable | Default | Notes |
|---|---|---|
| `EMBEDDING_BACKEND` | `gemini` | `gemini`, `huggingface`, or `local` |
| `GEMINI_API_KEY` | — | Required for the Gemini backend |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-2` | Gemini embedding model |
| `GEMINI_EMBEDDING_DIMENSIONS` | `1536` | Gemini supports 128–3072 |
| `HF_TOKEN` | — | Required for the Hugging Face backend |
| `HF_INFERENCE_PROVIDER` | `hf-inference` | Hugging Face inference provider |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Used by `huggingface` and `local` |
| `EMBEDDING_BATCH_SIZE` | `100` | Lower to `32` for local inference on small machines |

> Changing `EMBEDDING_BACKEND` changes the vector dimensions. Repositories indexed with the previous backend must be reprocessed before they can be queried.

### Storage and ingestion

| Variable | Default | Notes |
|---|---|---|
| `CHROMA_STORAGE_DIR` | `backend/.data/chroma` | ChromaDB persistence |
| `REPO_STORAGE_DIR` | `backend/.data/repos` | Shallow clones |
| `MANIFEST_STORAGE_DIR` | `backend/.data/manifests` | Rendered manifests |
| `CHUNK_SIZE_CHARS` | `1500` | Chunk size for embedding |
| `MAX_FILE_SIZE_BYTES` | `5242880` (5 MB) | Files larger than this are skipped |

---

## How indexing works

1. **Submit** — `POST /api/v1/repos/` validates the GitHub URL, creates a repository row, and schedules a background task.
2. **Clone** — a shallow (`depth=1`) clone is written to `REPO_STORAGE_DIR/<repo-id>`.
3. **Filter** — the tree walk respects `.gitignore` and skips binaries, lockfiles, build directories, minified assets, and files over the size cap. Every skip is counted and reported.
4. **Parse** — tree-sitter extracts classes, functions, modules, and imports where a grammar is available. Unsupported or unparseable files fall back to raw text chunking.
5. **Chunk and embed** — source is split into character-bounded chunks and embedded in batches into a per-repository Chroma collection.
6. **Summarize** — a deterministic manifest is built from the stored file and chunk rows, persisted to PostgreSQL, and rendered to Markdown.
7. **Explore** — diagrams are derived from the manifest; chat retrieves the top matching chunks and, for architecture questions, prepends manifest context.

Repository status moves through `pending → cloning → processing → embedding → completed` (or `failed` with an error message).

A repository is queryable when it has a complete vector index, or when it is a docs/config-only repository with a stored manifest.

---

## Supported languages

Structural parsing is available for:

| Language | Extensions |
|---|---|
| Python | `.py`, `.pyi` |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` |
| TypeScript | `.ts`, `.mts`, `.cts` |
| TSX | `.tsx` |
| Go | `.go` |
| Rust | `.rs` |
| Java | `.java` |
| C | `.c`, `.h` |
| C++ | `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx`, `.ipp` |
| Ruby | `.rb`, `.rake` |
| PHP | `.php`, `.phtml` |

Other text files are still indexed as raw chunks — they are not dropped — but they do not contribute structural symbols.

---

## API reference

All routes are prefixed with `/api/v1` except the health check. Protected routes expect `Authorization: Bearer <access_token>`.

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Returns service status and whether chat is enabled |

### Authentication

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/register` | Create an account |
| `POST` | `/auth/login` | Returns an access token and sets the HttpOnly refresh cookie |
| `POST` | `/auth/refresh` | Exchange the refresh cookie for a new access token |
| `POST` | `/auth/logout` | Clear the refresh cookie |
| `GET` | `/auth/me` | Current user profile |

`/auth/register` accepts JSON (`{"email": "...", "password": "..."}`) with a password of 8–72 characters. `/auth/login` follows the OAuth2 password flow and expects **form data** (`application/x-www-form-urlencoded`) with `username` set to the email and `password` set to the password — not a JSON body. The refresh token is returned only as an HttpOnly cookie, so browser clients must send credentials with the refresh request.

### Repositories

| Method | Path | Description |
|---|---|---|
| `POST` | `/repos/` | Submit a GitHub URL; returns `202` and starts indexing |
| `GET` | `/repos/` | List the current user's repositories |
| `GET` | `/repos/{repo_id}` | Repository status and progress |
| `GET` | `/repos/{repo_id}/structure` | Full manifest payload and Markdown |
| `GET` | `/repos/{repo_id}/structure.md` | Manifest as Markdown (`text/markdown`) |
| `GET` | `/repos/{repo_id}/diagrams` | Mermaid source plus structured graphs |
| `POST` | `/repos/{repo_id}/reprocess` | Re-clone and re-index |
| `DELETE` | `/repos/{repo_id}` | Delete the repository, clone, and vector index |

### Chat

| Method | Path | Description |
|---|---|---|
| `POST` | `/chat/` | Ask a question; returns `session_id` and the answer |
| `POST` | `/chat/stream` | Streaming answer over server-sent events |
| `GET` | `/chat/sessions/{repo_id}` | List chat sessions for a repository |
| `GET` | `/chat/sessions/{session_id}/messages` | Message history for a session |

Interactive documentation is available at `/docs` (Swagger UI) and `/redoc`.

---

## Data and storage

| Store | Contents |
|---|---|
| **PostgreSQL** | `users`, `repositories`, `files`, `chunks`, `chat_sessions`, `chat_messages`, `repo_manifests` |
| `backend/.data/chroma` | One Chroma collection per repository |
| `backend/.data/repos` | Shallow clones of imported repositories |
| `backend/.data/manifests` | Rendered manifest files |

Deleting a repository removes its clone and vector collection; database rows are removed by cascade. The `.data` directory is git-ignored and should be backed by a persistent volume in production.

### Backfilling manifests

Existing repositories can be given manifests without re-cloning or re-embedding:

```bash
cd backend
python -m app.cli.backfill_manifests
python -m app.cli.backfill_manifests --repo owner/name
```

---

## Database migrations

Local development does not require Alembic: `app/main.py` calls `Base.metadata.create_all` on startup, which creates any missing tables.

Alembic is included for managed deployments. The migration chain assumes the core tables already exist, so the baseline flow is:

```bash
cd backend

# 1. Start the app once (or otherwise create the base schema).
# 2. Record the current schema as the Alembic baseline.
alembic stamp head

# 3. Apply future migrations normally.
alembic upgrade head
```

To generate a new migration after changing a model:

```bash
cd backend
alembic revision --autogenerate -m "describe the change"
```

`alembic/env.py` always uses `DATABASE_URL` from the environment/config, never the hardcoded URL in `alembic.ini`.

---

## Tests and linting

### Backend

```bash
cd backend
python -m unittest discover -s tests -v
```

The suite covers parsing, filtering, manifests, diagrams, chat gating, rate limiting, API guards, and embedding behavior. It uses fakes and does not require a database, network access, or API keys. `backend/.env` must exist because the config tests resolve it.

### Frontend

```bash
cd frontend
npm run lint
npm run build
```

`npm run build` runs the TypeScript project build before bundling with Vite.

---

## Security and secrets

- **Never commit `backend/.env`.** It is git-ignored; commit only `.env.example` with placeholders.
- **Enable the local pre-commit guard** once per clone:

  ```bash
  git config core.hooksPath .githooks
  ```

- **CI scans for secrets** on every push and pull request via `.github/workflows/secret-scan.yml` (gitleaks).
- **Rotate keys immediately** if a credential is ever exposed, and remove it from git history — deleting the file in a later commit is not enough.
- **Access tokens** are held by the frontend in `localStorage`; the refresh token is an HttpOnly cookie. Treat the frontend origin as part of your security boundary and keep dependencies patched.
- **Production cookies** are marked `Secure` only when `ENVIRONMENT=production`, so use HTTPS.
- **Client IP headers** must only be trusted when your edge proxy controls them. Do not point `CLIENT_IP_HEADER` at a client-forgeable header such as `x-forwarded-for`.

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `ValidationError` on startup | `DATABASE_URL` or `SECRET_KEY` is missing from `backend/.env` |
| `could not connect to server` | PostgreSQL is not running, or the DSN is wrong |
| Import stays `pending` or becomes `failed` | Check the backend logs and the repository's `error_message`, then use **Reprocess** |
| Chat returns `503` | `CHAT_ENABLED=false`; set it to `true` and restart |
| `GROQ_API_KEY is not set` | Add the key or switch `LLM_PROVIDER`; restart the backend |
| Embedding `429` / quota errors | Reduce `EMBEDDING_BATCH_SIZE`, wait for the quota window, or switch to the local backend |
| CORS error in the browser | `FRONTEND_URL` must exactly match the frontend origin, including port and scheme |
| Repository no longer queryable after changing embedding backend | Vector dimensions changed — reprocess the repository |
| Frontend cannot reach the API | The backend must be on port `8000`; the axios client is configured for `http://localhost:8000/api/v1` |
| `run_dev.ps1` cannot find Python | Create the virtual environment at the repository root (`.venv`), not inside `backend/` |
| Login blocked behind a proxy | Set `CLIENT_IP_HEADER` to the header your edge sets (for example `cf-connecting-ip`) |

---

## Production checklist

- [ ] Set `ENVIRONMENT=production` and serve the app over HTTPS.
- [ ] Set `FRONTEND_URL` to the exact deployed frontend origin.
- [ ] Set `CLIENT_IP_HEADER` to a header your proxy controls (for example `cf-connecting-ip` on Render/Cloudflare).
- [ ] Use a managed PostgreSQL instance and keep `DATABASE_URL` out of source control.
- [ ] Persist `backend/.data` on a volume, or plan to re-index after each deploy.
- [ ] Run a single backend worker, or move the rate-limit store to Redis before scaling out.
- [ ] Decide whether chat should be enabled; `CHAT_ENABLED=false` is a safe starting point while prompts and error handling are hardened.
- [ ] Keep `.env` files out of images and build artifacts.
- [ ] Rotate `SECRET_KEY` and all provider keys on a schedule, and immediately after any suspected exposure.

---

Built with FastAPI, React, tree-sitter, ChromaDB, and LangChain. **RepoLexicon — the lexicon of your codebase.**
