# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Dev setup
python -m venv venv
venv\Scripts\activate          # PowerShell on Windows
pip install -r requirements.txt
cp .env.example .env

# Run the web app (also starts scheduler via FastAPI lifespan)
uvicorn app.web.main:app --reload

# Run the scheduler standalone (no web server)
python -m app.ingestion.scheduler

# Initialize DB schema manually (also runs automatically on startup)
python -c "import asyncio; from app.storage.database import init_db; asyncio.run(init_db())"

# Tests
pytest                                              # full suite
pytest tests/test_kbank_parser.py                   # single file
pytest tests/integration/                           # full pipeline tests only
pytest --cov=app --cov-report=term-missing          # with coverage

# Frontend CSS (only needed after editing templates or tailwind-input.css)
npm install
npm run build:css     # one-shot rebuild
npm run watch:css     # watch mode while iterating
```

On a fresh local DB, visit `http://localhost:8000/setup` to create the first admin user, then `/settings` → **Connect Gmail** to create a per-user Gmail OAuth token.

Set `AUTH_SECRET_KEY` in `.env` for any long-running local instance — without it, sessions break on restart.

## Architecture

### Big Picture

**Financial Email Tracker** ingests bank notification emails from Gmail (KBank, Krungsri, SCB, LH Bank), parses them into structured transactions, categorizes them, stores them in SQLite or PostgreSQL, and sends daily summaries via LINE. A FastAPI + Jinja2 + HTMX web dashboard provides per-user data access.

**Per-user isolation**: every DB table has an `owner_user_id` column; all queries filter through it (see `_add_owner_filter` in `app/storage/queries.py`). Gmail OAuth tokens are stored per-user at `secrets/users/{user_id}/gmail-token.json`.

### Ingestion Pipeline

```
Cron / "Sync Now" click
  → app/ingestion/service.py: run_ingestion(query, owner_user_id)
  → app/gmail/client.py: GmailReader.read(query)       # fetch emails from Gmail API
  → app/parsers/registry.py: ParserRegistry.parse()    # route by sender to correct parser
  → app/classification/engine.py: CategoryEngine.categorize()
  → app/ingestion/persistence.py: save_transaction()   # dedup + write to DB
  → app/ingestion/scheduler.py: daily LINE summary at 22:00
```

### Parser System (`app/parsers/`)

Each bank has its own sub-package (`kbank/`, `krungsri/`, `scb/`, `lhbank/`) following the same 6-stage pipeline:

```
normalize → detect_section → extract_fields → map_fields
          → detect_type/direction/status → validate
```

Each stage is its own module with independent unit tests. `app/parsers/base.py` defines `BaseParser` ABC and the canonical `Transaction` dataclass used everywhere downstream.

To add a new bank: implement `BaseParser.can_handle(sender)` and `parse(email_text, subject) -> Transaction | None`, then register in `app/parsers/registry.py`.

### Category Engine (`app/classification/engine.py`)

Five-tier priority (first match wins):
1. Manual override (user-set)
2. History — same counterparty categorized before
3. Rule — counterparty matches a pattern in `counterparty_mapping` table
4. AI — Ollama (optional, best-effort, falls back silently)
5. Uncategorized

### Configuration (`app/config.py`, `config.yaml`)

`config.yaml` uses `{{ env.VAR }}` placeholders. Any scalar value in the YAML can be overridden by a matching environment variable — this is how `.env` / Docker Compose take effect. Critical variables: `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_USER_ID`, `AUTH_SECRET_KEY`, `DATABASE_BACKEND` (`aiosqlite` or `postgres`).

### Web Layer (`app/web/`)

- **`main.py`** — FastAPI app, lifespan (starts scheduler + initializes DB), middleware, router mounting
- **`deps.py`** — all dependency injection (DB connection, current user, category engine)
- **`routes/`** — 8 route modules; HTMX fragments return partial HTML for in-page updates
- **Templates** — Jinja2 server-rendered; `partials/` and `fragments/` are HTMX targets
- **Tailwind CSS** — `tailwind-input.css` defines a shadcn/ui-inspired component layer; `tailwind.css` is compiled and committed

### Database (`app/storage/`)

- **`database.py`** — connection setup, schema creation for both SQLite and PostgreSQL
- **`queries.py`** — ~1200 lines of async query helpers (50+ functions); all user-scoped
- Deduplication is enforced by UNIQUE constraints on `(owner_user_id, transaction_id)` and `(owner_user_id, gmail_message_id)`

### Testing

Tests use `tests/conftest.py` fixtures that redirect `app.storage.database` to a fresh temp SQLite file per test. Gmail, LINE, and Ollama are mocked at the `httpx` level — nothing goes over the network. Route tests create and log in a test admin user before exercising protected endpoints.
