# SMS Pipeline — Systematic Mapping Study Platform

![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-20-green?logo=node.js&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)

A production-grade, full-stack SaaS application for automating **Systematic Mapping Studies (SMS)** in software security research. Collects, deduplicates, filters, and enriches academic papers from **9 data sources** — ACM, IEEE, Springer, Elsevier, Wiley, Taylor & Francis, MDPI, SAGE, and a broad cross-publisher search — all via free APIs (OpenAlex + CrossRef). Powered by AI relevance filtering and real-time pipeline progress.

---

## Screenshots

> **TODO:** Add screenshots or a demo GIF here showing the Dashboard, Project Detail page, and pipeline progress.

<!--
Suggested screenshots:
1. Dashboard — stat cards and recent projects overview
2. New Project — form with topic, year range, and source selection
3. Project Detail — pipeline controls, real-time progress bar, papers table
4. Export — XLSX/CSV download dialog
-->

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Environment Setup](#environment-setup)
  - [Running with Docker](#running-with-docker)
  - [Local Development](#local-development)
- [Configuration](#configuration)
- [Pipeline Steps](#pipeline-steps)
  - [1. Collection](#1-collection)
  - [2. Deduplication](#2-deduplication)
  - [3. AI Filtering](#3-ai-filtering)
  - [4. PDF Link Discovery](#4-pdf-link-discovery)
- [API Reference](#api-reference)
  - [Authentication](#authentication)
  - [Projects](#projects)
  - [Papers](#papers)
  - [Jobs](#jobs)
  - [Exports](#exports)
  - [WebSocket](#websocket)
- [Frontend](#frontend)
- [Database Schema](#database-schema)
- [Background Tasks](#background-tasks)
- [Testing](#testing)
- [Security](#security)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Contributing](#contributing)

---

## Architecture Overview

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│   Frontend   │────▶│    Nginx     │────▶│  FastAPI Backend  │
│  React + TS  │     │  (reverse    │     │  (uvicorn ASGI)   │
│  Vite + TW   │◀────│   proxy)     │◀────│                   │
└──────────────┘     └──────────────┘     └────────┬─────────┘
                                                   │
                            ┌──────────────────────┼──────────────────────┐
                            ▼                      ▼                      ▼
                     ┌─────────────┐       ┌──────────────┐      ┌──────────────┐
                     │  PostgreSQL │       │    Redis      │      │    Celery     │
                     │  (primary   │       │  (broker +    │      │   Workers     │
                     │   database) │       │   pub/sub)    │      │  (API calls + │
                     └─────────────┘       └──────────────┘      │  processing)  │
                                                                  └──────────────┘
```

- **Frontend** → React 18 SPA served by Nginx, proxies API/WebSocket to backend
- **Backend** → FastAPI with async SQLAlchemy, JWT auth, rate limiting
- **Workers** → Celery processes executing API-based collection and data processing in the background
- **Database** → PostgreSQL 16 storing users, projects, papers, and jobs
- **Broker** → Redis 7 for Celery task queuing, WebSocket pub/sub, and caching
- **Real-time** → WebSocket endpoint delivers live job progress to the browser

---

## Tech Stack

### Backend
| Component | Technology |
|-----------|-----------|
| Web Framework | FastAPI 0.115 (async ASGI) |
| ORM | SQLAlchemy 2.0 (async via asyncpg) |
| Migrations | Alembic 1.14 |
| Task Queue | Celery 5.4 + Redis broker |
| Auth | JWT (python-jose) + bcrypt (passlib) |
| AI Filtering | Groq API (llama-3.3-70b-versatile) |
| Paper Collection | OpenAlex API (primary) + CrossRef API (fallback) |
| Data Processing | pandas, scikit-learn (TF-IDF dedup) |
| Rate Limiting | slowapi |
| Logging | structlog (JSON in prod, pretty in dev) |

### Frontend
| Component | Technology |
|-----------|-----------|
| Framework | React 18 + TypeScript |
| Build Tool | Vite 6 |
| Styling | TailwindCSS 3.4 |
| Routing | React Router v6 |
| State | Zustand 5 (auth persistence) |
| Server State | TanStack Query 5 |
| HTTP Client | Axios (JWT interceptor + auto-refresh) |
| Charts | Recharts 2 |
| Icons | Lucide React |
| Notifications | react-hot-toast |

### Infrastructure
| Component | Technology |
|-----------|-----------|
| Containerization | Docker + Docker Compose |
| Reverse Proxy | Nginx 1.25 |
| Database | PostgreSQL 16 |
| Message Broker | Redis 7 |

---

## Project Structure

```
sms_pipeline/
├── docker-compose.yml          # Full stack orchestration (6 services)
├── .env.example                # Environment variable template
├── .dockerignore
│
├── backend/
│   ├── Dockerfile              # Python 3.12 application image
│   ├── requirements.txt        # Pinned Python dependencies
│   ├── alembic.ini             # Alembic migration config
│   ├── alembic/
│   │   ├── env.py              # Migration environment (auto-import models)
│   │   └── versions/
│   │       └── 001_initial_schema.py
│   └── app/
│       ├── main.py             # FastAPI application factory
│       ├── config.py           # Pydantic Settings (env-based config)
│       ├── database.py         # Async SQLAlchemy engine + session
│       ├── api/                # Route handlers
│       │   ├── deps.py         # Shared dependencies (auth, pagination)
│       │   ├── auth.py         # POST /register, /login, /refresh, GET /me
│       │   ├── projects.py     # CRUD for projects
│       │   ├── papers.py       # List/filter/update papers + stats
│       │   ├── jobs.py         # Launch/cancel pipeline jobs
│       │   └── exports.py      # XLSX/CSV download endpoint
│       ├── core/
│       │   ├── security.py     # JWT creation/validation, password hashing
│       │   ├── exceptions.py   # Typed HTTP exceptions
│       │   └── logging.py      # structlog configuration
│       ├── models/             # SQLAlchemy ORM models
│       │   ├── user.py         # Users (email, hashed password, roles)
│       │   ├── project.py      # Projects + SearchStrings
│       │   ├── paper.py        # Papers (title, DOI, relevance, PDF link)
│       │   └── job.py          # Pipeline jobs (type, status, progress)
│       ├── schemas/            # Pydantic v2 request/response schemas
│       │   ├── auth.py
│       │   ├── project.py
│       │   ├── paper.py
│       │   └── job.py
│       ├── scrapers/           # API-based paper collectors
│       │   ├── base.py         # Shared utilities (driver compat layer)
│       │   ├── api_base.py     # Generic OpenAlex + CrossRef base class
│       │   ├── acm.py          # ACM Digital Library (DOI prefix 10.1145)
│       │   ├── ieee.py         # IEEE Xplore (DOI prefix 10.1109)
│       │   ├── springer.py     # Springer (DOI prefix 10.1007)
│       │   ├── elsevier.py     # Elsevier / ScienceDirect (DOI prefix 10.1016)
│       │   ├── wiley.py        # Wiley (DOI prefix 10.1002)
│       │   ├── taylor_francis.py # Taylor & Francis (DOI prefix 10.1080)
│       │   ├── mdpi.py         # MDPI (DOI prefix 10.3390)
│       │   ├── sage.py         # SAGE Publications (DOI prefix 10.1177)
│       │   └── scopus.py       # Broad cross-publisher search (all OpenAlex)
│       ├── services/           # Business logic layer
│       │   ├── collection.py   # Orchestrates API collectors → DB
│       │   ├── deduplication.py# DOI exact + TF-IDF cosine similarity
│       │   ├── filtering.py    # Manual relevance classification
│       │   ├── automated_exclusion.py # Groq API automated exclusion
│       │   └── pdf_finder.py   # OpenAlex → arXiv → S2 → BASE lookup
│       ├── tasks/              # Celery task definitions
│       │   ├── celery_app.py   # Celery factory + beat schedule
│       │   └── pipeline.py     # Task wrappers + Redis progress pub/sub
│       └── websocket/
│           └── progress.py     # WS endpoint for real-time progress
│
├── frontend/
│   ├── Dockerfile              # Node 20 build → Nginx production
│   ├── nginx.conf              # SPA routing + API/WS proxy
│   ├── package.json            # Dependencies (React, Vite, TailwindCSS, etc.)
│   ├── vite.config.ts          # Dev proxy + build chunk splitting
│   ├── tailwind.config.js      # Brand color palette, fonts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx            # React root + QueryClient + Router
│       ├── App.tsx             # Route definitions (public + protected)
│       ├── index.css           # Tailwind imports + component classes
│       ├── api/
│       │   ├── client.ts       # Axios instance + JWT interceptor + refresh
│       │   └── endpoints.ts    # Typed API functions (auth, projects, papers, …)
│       ├── stores/
│       │   └── auth.ts         # Zustand auth store (persisted to localStorage)
│       ├── types/
│       │   └── index.ts        # Shared TypeScript interfaces
│       ├── hooks/
│       │   └── useWebSocket.ts # WebSocket hook for pipeline progress
│       ├── components/
│       │   ├── Layout.tsx      # Sidebar + header shell
│       │   ├── ProtectedRoute.tsx # Auth guard wrapper
│       │   └── ui.tsx          # Reusable UI primitives (Spinner, StatCard, etc.)
│       └── pages/
│           ├── Login.tsx
│           ├── Register.tsx
│           ├── Dashboard.tsx   # Stats overview + recent projects
│           ├── Projects.tsx    # Project grid with delete
│           ├── NewProject.tsx  # Create form (topic, years, sources)
│           └── ProjectDetail.tsx # Pipeline controls, papers table, jobs
│
├── config.py                   # (original CLI config — preserved)
├── main.py                     # (original CLI entry — preserved)
├── scrapers/                   # (original scraper code — preserved)
└── steps/                      # (original pipeline steps — preserved)
```

---

## Getting Started

### Prerequisites

- **Docker** ≥ 24.0 and **Docker Compose** ≥ 2.20
- (Optional for local dev) **Python** ≥ 3.10, **Node.js** ≥ 20

### Environment Setup

1. Clone the repository and copy the environment template:

   ```bash
   git clone <repository-url>
   cd sms_pipeline
   cp .env.example .env
   ```

2. Edit `.env` and fill in the required values:

   ```dotenv
   # REQUIRED — generate a strong random string
   SECRET_KEY=your-secret-key-change-me

   # REQUIRED — get from https://console.groq.com/
   GROQ_API_KEYS=gsk_key1,gsk_key2,...

   # Optional — adjust as needed
   DATABASE_URL=postgresql+asyncpg://sms:sms_password@postgres:5432/sms_pipeline
   REDIS_URL=redis://redis:6379/0
   ```

### Running with Docker

```bash
# Build and start all services
docker-compose up --build -d

# Check logs
docker-compose logs -f backend
docker-compose logs -f celery_worker

# Run database migrations
docker-compose exec backend alembic upgrade head

# Open the app
# http://localhost (frontend via Nginx)
# http://localhost:8000/docs (FastAPI Swagger UI — direct backend)
```

**Services launched:**

| Service | Port | Description |
|---------|------|-------------|
| `frontend` | 80 | Nginx serving React SPA + API proxy |
| `backend` | 8000 | FastAPI (uvicorn) |
| `postgres` | 5432 | PostgreSQL 16 |
| `redis` | 6379 | Redis 7 |
| `celery_worker` | — | Background task processor |
| `celery_beat` | — | Periodic task scheduler |

### Local Development

#### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # macOS/Linux

pip install -r requirements.txt

# Start FastAPI dev server (requires PostgreSQL + Redis running)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Start Celery worker (separate terminal)
celery -A app.tasks.celery_app:celery_app worker -l info -Q default,scraping

# Run migrations
alembic upgrade head
```

#### Frontend

```bash
cd frontend
npm install
npm run dev    # Vite dev server on http://localhost:5173
```

---

## Configuration

All configuration is via environment variables (`.env` file). Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | `SMS Pipeline` | Application display name |
| `ENVIRONMENT` | `development` | `development` / `production` |
| `DEBUG` | `true` | Enable debug mode |
| `SECRET_KEY` | — | **Required.** JWT signing key |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Async PostgreSQL connection |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://...` | Sync connection (Celery workers) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | Celery broker URL |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/2` | Celery result backend URL |
| `GROQ_API_KEYS` | — | **Required.** Comma-separated Groq API keys for automated exclusion |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | JWT access token lifetime (24 h) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | JWT refresh token lifetime |
| `API_REQUEST_DELAY` | `1` | Delay between API requests to OpenAlex/CrossRef (seconds) |
| `API_MAX_RETRIES` | `3` | Max retries per API request on failure |
| `TITLE_SIM_THRESHOLD` | `0.92` | TF-IDF cosine similarity threshold for dedup |
| `CORS_ORIGINS` | `http://localhost:3000,...` | Allowed CORS origins (comma-separated or JSON array) |

---

## Pipeline Steps

Each step can be run individually or as a full pipeline from the UI.

### 1. Collection

Collects academic papers from selected sources via **free academic APIs** — no web scraping, no browser automation, no CAPTCHAs.

Each source uses a two-tier strategy:
1. **OpenAlex API** (primary) — Free, open scholarly metadata covering 250M+ works. Filters by publisher and year range. Cursor-based pagination, polite pool via `mailto` parameter.
2. **CrossRef API** (fallback) — If OpenAlex returns no results, falls back to CrossRef filtered by DOI prefix (e.g., `10.1145` for ACM) and publication date range.

**Supported sources (9):**

| Source | OpenAlex Publisher Filter | CrossRef DOI Prefix |
|--------|--------------------------|---------------------|
| ACM Digital Library | `ACM` | `10.1145` |
| IEEE Xplore | `IEEE` | `10.1109` |
| Springer | `Springer` | `10.1007` |
| Elsevier / ScienceDirect | `Elsevier` | `10.1016` |
| Wiley | `Wiley` | `10.1002` |
| Taylor & Francis | `Taylor & Francis` | `10.1080` |
| MDPI | `MDPI` | `10.3390` |
| SAGE Publications | `SAGE` | `10.1177` |
| Scopus (Broad) | *(no filter — all publishers)* | *(all prefixes)* |

Papers are stored with: title, authors, year, publisher, DOI, abstract, link, and source metadata.

### 2. Deduplication

Two-pass deduplication strategy:

1. **DOI exact match** — Papers sharing the same non-empty DOI are grouped
2. **Title similarity** — TF-IDF vectorization + cosine similarity (threshold ≥ 0.92) catches near-duplicate titles across sources

Duplicates are flagged with `is_duplicate = true` and linked to the original via `duplicate_of_id`.

### 3. Automated Exclusion

Uses the Groq API (llama-3.3-70b-versatile) to classify papers as **INCLUDE** or **EXCLUDE** based on relevance to software security:

- Papers are processed in parallel using up to 5 Groq API keys
- The model evaluates paper titles against a strict software-security-only prompt
- Ambiguous papers default to EXCLUDE (high precision)
- Failed API calls default to INCLUDE (conservative approach)
- Progress is reported in real-time via WebSocket

### 4. PDF Link Discovery

Four-source cascade to find open-access PDF URLs:

1. **OpenAlex** — DOI lookup for open-access PDFs
2. **arXiv** — Title search for preprint PDFs
3. **Semantic Scholar** — DOI/title lookup for open-access links
4. **BASE (Bielefeld)** — Broad academic search engine fallback

Each candidate URL is validated by checking for the PDF magic bytes (`%PDF`). Progress checkpoints are saved every 50 papers.

---

## API Reference

All endpoints are prefixed with `/api/v1`. Authentication uses Bearer JWT tokens.

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/auth/register` | Create account → returns tokens |
| `POST` | `/auth/login` | Email/password login → returns tokens |
| `POST` | `/auth/refresh` | Refresh access token |
| `GET` | `/auth/me` | Get current user profile |

**Request body (register):**
```json
{
  "email": "user@example.com",
  "password": "securepassword",
  "full_name": "John Doe"
}
```

**Response (tokens):**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer"
}
```

### Projects

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/projects` | List projects (paginated) |
| `POST` | `/projects` | Create project |
| `GET` | `/projects/{project_id}` | Get project details |
| `PATCH` | `/projects/{project_id}` | Update project |
| `DELETE` | `/projects/{project_id}` | Delete project + all data |

**Create project body:**
```json
{
  "name": "Vulnerability Detection SMS",
  "description": "Mapping study on software vulnerability detection",
  "topic": "detection",
  "year_start": 2000,
  "year_end": 2026,
  "sources": "acm,ieee,springer,elsevier,wiley"
}
```

Topics `detection` and `remediation` use pre-configured search strings. Use `"topic": "custom"` with `custom_search_strings` to define your own.

### Papers

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/projects/{project_id}/papers` | List papers (paginated + filters) |
| `GET` | `/projects/{project_id}/papers/stats` | Aggregate statistics |
| `GET` | `/projects/{project_id}/papers/{paper_id}` | Get paper details |
| `PATCH` | `/projects/{project_id}/papers/{paper_id}` | Update relevance/PDF link |

**Query filters for paper listing:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `source` | `string` | Filter by source (`acm`, `ieee`, `springer`, `elsevier`, `wiley`, `taylor_francis`, `mdpi`, `sage`, `scopus`) |
| `year` | `integer` | Filter by publication year |
| `relevance` | `string` | `pending`, `keep`, `remove` |
| `is_duplicate` | `boolean` | Filter duplicates |
| `has_pdf` | `boolean` | Filter by PDF availability |
| `search` | `string` | Search in title/authors |

### Jobs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/projects/{project_id}/jobs` | List jobs (paginated) |
| `POST` | `/projects/{project_id}/jobs` | Launch pipeline step |
| `GET` | `/projects/{project_id}/jobs/{job_id}` | Get job details |
| `POST` | `/projects/{project_id}/jobs/{job_id}/cancel` | Cancel running job |

**Launch job body:**
```json
{
  "job_type": "collect"
}
```

Valid job types: `collect`, `deduplicate`, `filter`, `find_links`, `full_pipeline`.

### Exports

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/projects/{project_id}/export` | Download papers as XLSX or CSV |

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `format` | `xlsx` | `xlsx` or `csv` |
| `include_duplicates` | `false` | Include duplicate papers |
| `relevance_filter` | — | `keep`, `remove`, or omit for all |

### WebSocket

```
ws://localhost/ws/projects/{project_id}/progress
```

Connects to a real-time progress stream for a project. Messages are JSON:

```json
{
  "job_id": "uuid",
  "project_id": "uuid",
  "progress": 45.5,
  "message": "Collecting from IEEE: page 3/12",
  "status": "running"
}
```

The connection supports `ping`/`pong` keepalive. Progress updates are delivered via Redis pub/sub.

---

## Frontend

The React frontend provides:

- **Login / Register** — Email + password authentication
- **Dashboard** — Overview with stat cards (total projects, papers, completed, failed) and recent projects
- **Projects List** — Card grid with status badges, stats, and delete
- **New Project** — Form with topic selection (detection/remediation/custom), year range, source checkboxes (9 sources), and custom search string editor
- **Project Detail** — Pipeline control panel, real-time progress bar via WebSocket, filterable/searchable papers table with pagination, XLSX/CSV export, jobs history, and stats breakdown by source/relevance

### Design System

The CSS uses Tailwind with a custom component layer:

- `.btn-primary`, `.btn-secondary`, `.btn-danger`, `.btn-ghost` — Button variants
- `.btn-sm`, `.btn-lg` — Size modifiers
- `.input`, `.label` — Form controls
- `.card` — Container with border + shadow
- `.badge-blue`, `.badge-green`, `.badge-red`, `.badge-yellow`, `.badge-gray` — Status badges

---

## Database Schema

Five core tables with UUID primary keys:

### `users`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `email` | VARCHAR(320) | Unique, indexed |
| `hashed_password` | VARCHAR(1024) | bcrypt hash |
| `full_name` | VARCHAR(256) | |
| `is_active` | BOOLEAN | Default: true |
| `is_superuser` | BOOLEAN | Default: false |
| `created_at` / `updated_at` | TIMESTAMPTZ | Auto-set |

### `projects`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `owner_id` | UUID → users | Cascade delete, indexed |
| `name` | VARCHAR(512) | |
| `description` | TEXT | Default: empty |
| `topic` | ENUM | detection / remediation / custom |
| `status` | ENUM | draft → collecting → … → completed |
| `year_start` / `year_end` | INT | Search range (default: 2000–2026) |
| `sources` | VARCHAR(512) | Comma-separated (acm,ieee,springer,elsevier,wiley,...) |
| `total_collected` / `total_after_dedup` / `total_after_filter` / `total_with_pdf` | INT | Aggregate counters |
| `created_at` / `updated_at` | TIMESTAMPTZ | Auto-set |

### `search_strings`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `project_id` | UUID → projects | Cascade delete, indexed |
| `label` | VARCHAR(64) | String identifier (e.g., DET-1, REM-2) |
| `query` | TEXT | Full search query text |
| `created_at` | TIMESTAMPTZ | Auto-set |

### `papers`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `project_id` | UUID → projects | Cascade delete, indexed |
| `title` | TEXT | |
| `authors` | TEXT | Default: empty |
| `year` | INT | Nullable |
| `publisher` | VARCHAR(256) | |
| `doi` | VARCHAR(512) | Indexed |
| `link` | TEXT | Original publisher URL |
| `abstract` | TEXT | |
| `source` | VARCHAR(64) | acm / ieee / springer / elsevier / wiley / taylor_francis / mdpi / sage / scopus |
| `search_string_label` | VARCHAR(64) | Which search string found it |
| `is_duplicate` | BOOLEAN | |
| `duplicate_of_id` | UUID → papers (self) | SET NULL on delete |
| `relevance` | ENUM | pending / keep / remove |
| `pdf_link` | TEXT | Open-access URL |
| `pdf_source` | VARCHAR(64) | openalex / arxiv / s2 / base |
| `created_at` / `updated_at` | TIMESTAMPTZ | Auto-set |

Composite indexes: `(project_id, doi)`, `(project_id, source)`

### `jobs`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `project_id` | UUID → projects | Cascade delete |
| `job_type` | ENUM | collect / deduplicate / filter / find_links / full_pipeline |
| `status` | ENUM | pending / running / completed / failed / cancelled |
| `progress` | FLOAT | 0–100 |
| `celery_task_id` | VARCHAR | For cancellation |
| `config` / `result` | JSON | Step-specific data |

---

## Background Tasks

### Celery Queues

| Queue | Purpose |
|-------|---------|
| `default` | General tasks (dedup, filtering, PDF finding) |
| `scraping` | Collection tasks (API-based paper retrieval) |

### Periodic Tasks (Celery Beat)

| Task | Schedule | Description |
|------|----------|-------------|
| `cleanup_stale_jobs` | Every 30 min | Marks jobs stuck > 6 hours as failed |

### Task Reliability

- `acks_late = True` — Tasks acknowledged only after completion
- `prefetch_multiplier = 1` — One task per worker at a time
- `task_reject_on_worker_lost = True` — Re-queue on worker crash
- Progress stored in Redis (survives WebSocket reconnects)

---

## Testing

### End-to-End Integration Tests

The project includes an end-to-end test suite that simulates a complete user journey through the API. Tests run against an **in-memory SQLite database** — no PostgreSQL or Redis required.

```bash
cd backend
python test_e2e.py
```

The test covers:

| Area | What's tested |
|------|---------------|
| **Auth** | Register, login, token refresh, profile retrieval |
| **Projects** | Create, list, get by ID, update, search strings |
| **Pipeline** | Job launch (collect, dedup, filter, find_links), status polling |
| **Papers** | List with filters, pagination, stats endpoint |
| **Export** | XLSX and CSV downloads |
| **Edge cases** | Duplicate registration, invalid credentials, missing resources (404) |

### Smoke Tests

Quick import verification (useful for CI):

```bash
cd backend
python -c "from app.main import app; print('Backend OK')"
```

```bash
cd frontend
npm run build   # TypeScript + Vite build succeeds with zero errors
```

### Adding Tests

New tests should follow the existing pattern in `test_e2e.py`:
- Use the `httpx.AsyncClient` with the FastAPI `app` directly
- Tests are self-contained (create their own users/projects)
- No external services required

---

## Security

Security measures built into the platform:

### Authentication & Authorization

| Mechanism | Implementation |
|-----------|----------------|
| Password hashing | bcrypt via `passlib` (auto-upgrade on deprecated schemes) |
| JWT tokens | HS256-signed access + refresh tokens (`python-jose`) |
| Token expiry | Access: 24 hours, Refresh: 30 days (configurable) |
| Auth guard | All API endpoints (except register/login) require valid Bearer token |
| User isolation | Projects, papers, and jobs are scoped to the authenticated user |

### API Protection

| Mechanism | Implementation |
|-----------|----------------|
| Rate limiting | `slowapi` — per-IP rate limits on all endpoints |
| CORS | Configurable allowed origins (strict in production) |
| Request IDs | Every response includes `X-Request-ID` for traceability |
| Input validation | Pydantic v2 schemas validate all request bodies and query parameters |
| Global error handler | Unhandled exceptions return generic 500 — stack traces only in logs |

### Infrastructure

| Mechanism | Implementation |
|-----------|----------------|
| Secrets management | All secrets via environment variables (never committed) |
| SQL injection | Prevented by SQLAlchemy ORM (parameterized queries) |
| WebSocket auth | Connection validated with project ownership check |
| Structured logging | `structlog` with JSON output in production (no sensitive data logged) |

### Production Hardening Checklist

- [ ] Set a cryptographically strong `SECRET_KEY` (≥ 64 random hex chars)
- [ ] Set `ENVIRONMENT=production` and `DEBUG=false`
- [ ] Restrict `CORS_ORIGINS` to your exact domain
- [ ] Enable HTTPS/TLS via reverse proxy (Cloudflare, Traefik, or certbot)
- [ ] Rotate JWT signing key periodically
- [ ] Set up database connection pooling limits
- [ ] Enable PostgreSQL SSL connections

---

## Deployment

### Production Checklist

1. **Set strong secrets** in `.env`:
   ```dotenv
   SECRET_KEY=<64-char-random-hex>
   ENVIRONMENT=production
   DEBUG=false
   ```

2. **Configure CORS** for your domain:
   ```dotenv
   CORS_ORIGINS=https://yourdomain.com
   ```

3. **Set up SSL/TLS** — Use a reverse proxy (Cloudflare, Traefik, or certbot with Nginx)

4. **Scale workers** based on load:
   ```bash
   docker-compose up --scale celery_worker=3 -d
   ```

5. **Database backups**:
   ```bash
   docker-compose exec postgres pg_dump -U sms sms_pipeline > backup.sql
   ```

6. **Monitor** logs:
   ```bash
   docker-compose logs -f --tail=100
   ```

### Resource Requirements

| Component | Min RAM | Recommended |
|-----------|---------|-------------|
| Backend | 256 MB | 512 MB |
| Celery Worker | 256 MB | 512 MB |
| PostgreSQL | 256 MB | 512 MB |
| Redis | 64 MB | 128 MB |
| Frontend (Nginx) | 32 MB | 64 MB |

---

## Troubleshooting

### Common Issues

**Collection returns no results for a source**
- OpenAlex publisher name filters are case-sensitive — verify the publisher string matches OpenAlex's data
- Try the Scopus (Broad) source which searches across all publishers
- Check your search string syntax — overly specific queries may yield zero results
- CrossRef fallback will activate automatically if OpenAlex finds nothing

**API rate limiting (429 errors)**
- The scrapers automatically handle 429 responses with exponential backoff
- OpenAlex polite pool requires a `mailto` parameter (already configured)
- For very large collections, increase `API_REQUEST_DELAY` in `.env`

**Celery worker not picking up tasks**
```bash
docker-compose logs celery_worker
# Ensure Redis is running:
docker-compose exec redis redis-cli ping
```

**Database migration errors**
```bash
# Reset and re-run migrations
docker-compose exec backend alembic downgrade base
docker-compose exec backend alembic upgrade head
```

**WebSocket not connecting**
- Ensure Nginx is configured with WebSocket upgrade headers (already done in `nginx.conf`)
- Check browser console for connection errors
- Verify the backend is accessible at the expected host

**Out of memory (Celery worker)**
- Workers are lightweight since they only make API calls (no browser)
- If processing very large datasets, scale horizontally instead of adding more concurrent tasks

---

## Roadmap

### More Data Sources

The current 9 sources cover the most common academic publishers, but there are many more worth integrating:

- **PubMed / MEDLINE** — Biomedical and life sciences literature (free NLM API)
- **arXiv** — Preprint server for CS, physics, math (free API)
- **DBLP** — Computer science bibliography (free XML API)
- **Semantic Scholar** — AI-powered academic search (free API, 200 req/s)
- **NASA ADS** — Astrophysics + CS literature (free API)
- **CORE** — Aggregates open-access papers from repositories worldwide (free API)
- **BASE (Bielefeld)** — Largest academic search engine for open-access content (free API)

### User-Defined Data Sources

A planned feature is to let users **plug in their own data sources** directly from the UI or config, without touching the codebase. The envisioned design:

- **Custom API connector** — User provides a base URL, authentication (API key / OAuth token), and a field-mapping schema (title, DOI, year, abstract…). The platform normalizes the response into the standard paper format.
- **Institutional access** — Users with paid Scopus, Web of Science, or Dimensions API credentials could enter their API key through project settings and query those sources directly.
- **File import** — Upload a BibTeX / RIS / CSV export from any source (e.g., PubMed export, Scopus export) and have it ingested into the pipeline as a virtual "source".
- **Plugin interface** — A documented Python base class (`APIScraperBase`) already exists — power users can implement a subclass and register it without modifying core code.

Contributions toward any of these are very welcome — see [Contributing](#contributing) below.

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Commit with conventional messages: `git commit -m "feat: add new source"`
4. Push and open a Pull Request

Please ensure:
- All backend imports pass (`python -c "from app.main import app"`)
- End-to-end tests pass (`cd backend && python test_e2e.py`)
- Frontend builds cleanly (`npm run build`)
- New environment variables are added to both `.env.example` and the Configuration table in this README

---

> **Disclaimer:** This platform collects metadata from academic publishers via the OpenAlex and CrossRef APIs for academic research purposes. No copyrighted full-text content is downloaded or stored. Users are responsible for ensuring compliance with applicable terms of service and laws.
