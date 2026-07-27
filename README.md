# Financial Email Tracker

ระบบ track รายการทางการเงินจาก Gmail → SQLite → แบ่งหมวดหมู่ → LINE Summary

Reads bank notification emails from Gmail, parses them into structured
transactions, categorizes them, stores everything in SQLite, and pushes a
daily summary to LINE. Ships with a small FastAPI dashboard for browsing
transactions and fixing anything the parser got wrong.

## Features

- Gmail Reader (OAuth2, readonly)
- KBank email parser (Thai + English, bilingual-aware)
- Transaction type/status/direction detection (16+ types)
- SQLite storage with deduplication (by Gmail message id, bank reference
  number, and a type/amount/date/counterparty fingerprint)
- Category engine: manual override → history → rule → AI (optional, via
  Ollama) → uncategorized
- Dashboard (FastAPI + Jinja2 + HTMX)
- Daily LINE summary (cron, Asia/Bangkok by default)
- Structured JSON logging with secret masking and rotation
- Extensible parser registry for adding more banks

## Tech Stack

Python 3.12 · FastAPI · SQLite (aiosqlite) · Gmail API (OAuth2) · APScheduler
· LINE Messaging API · Ollama (optional, for AI categorization)

## Quick Start (local dev)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Gmail OAuth + LINE token, etc - see DEPLOYMENT.md for how to obtain these
cp .env.example .env
# edit .env with your values

# Run the web app (also starts the cron scheduler via its lifespan)
uvicorn app.web.main:app --reload
```

Open http://localhost:8000 for the dashboard. See [DEVELOPMENT.md](DEVELOPMENT.md)
for running tests and more local-dev detail.

## Quick Start (Docker)

```bash
cp .env.example .env
# edit .env, and place credentials.json (Gmail) under ./secrets/

docker compose up --build
```

The app listens on `:8000` and the container reports healthy once
`GET /health` responds. To also run the optional AI categorizer
(local Ollama):

```bash
docker compose --profile ai up --build
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for the full production setup (Gmail
OAuth, LINE bot, volumes, monitoring).

## Configuration

Settings are loaded from `config.yaml`, with `{{ env.VAR }}` placeholders
resolved from the environment, and any real environment variable of the same
name (e.g. from `.env` / `docker-compose.yml`) overriding a scalar value
directly. See `.env.example` for the full list of variables:

| Variable | Purpose |
|---|---|
| `DATABASE_PATH` | Path to the SQLite file |
| `GMAIL_CREDENTIALS_PATH` / `GMAIL_TOKEN_PATH` | Gmail OAuth2 client + token |
| `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_USER_ID` | LINE Messaging API push target |
| `AI_ENABLED` / `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | Optional AI-assisted categorization |
| `TIMEZONE` | Cron schedule timezone (default `Asia/Bangkok`) |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO`/`DEBUG`/... and `json`/`text` |

Cron times and the Gmail search query live in `config.yaml` (`SCHEDULE`,
`GMAIL_QUERY`).

## API Endpoints

All JSON endpoints are under `/api`; the same paths without `/api` render the
matching HTML page.

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Dashboard (income/expense today, uncategorized count, last sync) |
| `GET` | `/health` | Health check (`{"status": "ok", "version": ...}`) |
| `GET` | `/api/transactions` | Paginated/filtered transaction list (date range, category, type, direction, search) |
| `GET` | `/api/transactions/{id}` | Transaction detail |
| `PATCH` | `/api/transactions/{id}` | Manual category override and/or ignore toggle |
| `POST` | `/api/reparse/{id}` | Re-fetch the source email and re-run the parser |
| `GET` | `/api/unknown` | Emails that failed to parse |
| `POST` | `/api/unknown/{id}/ignore` | Mark an unparseable email as ignored |
| `POST` | `/api/unknown/{id}/reparse` | Retry parsing a single unparseable email |
| `GET` / `POST` / `PATCH` / `DELETE` | `/api/mappings` | Counterparty → category rules (CRUD) |
| `GET` | `/api/runs` | Ingestion run history |
| `POST` | `/api/ingestion/run` | Trigger an ingestion pass immediately |
| `POST` | `/api/ingestion/retry/{run_id}` | Retry all currently-pending unparseable emails |
| `GET` | `/api/settings` | Read-only, non-secret settings snapshot |

## Sample KBank Email

The parser expects a `Label : Value` block (English or Thai, or both):

```
Transfer Successful

Transaction Date : 26/01/2025
Transaction Time : 14:32
Amount : 1,500.00 THB
Fee : 0.00 THB
Available Balance : 25,430.50 THB
Reference No : 202501261432001234
Status : Success
```

or the Thai equivalent:

```
รายการโอนเงินสำเร็จ

วันที่ทำรายการ : 26 ม.ค. 2568
เวลาทำรายการ : 14:32
จำนวนเงิน : 1,500.00 บาท
ค่าธรรมเนียม : 0.00 บาท
ยอดเงินคงเหลือ : 25,430.50 บาท
หมายเลขอ้างอิง : 202501261432001234
สถานะ : สำเร็จ
```

Both parse into the same canonical `Transaction`. See `app/parsers/kbank/`
for the full pipeline (normalize → detect language section → extract fields
→ map to canonical fields → classify type/direction/status → validate), and
`tests/integration/` for worked examples including edge cases (missing
fields, malformed amounts, unknown transaction types, encoding cleanup).

## Project Structure

```
app/
  gmail/              # Gmail API integration (OAuth, search, fetch)
  parsers/            # Parser registry + KBank implementation
  classification/     # Category engine: history, rules, AI fallback
  storage/             # SQLite schema + queries
  ingestion/           # Ingestion service + cron scheduler
  integrations/        # LINE Messaging API
  web/                  # FastAPI routes + templates
  logging_config.py    # JSON logging, rotation, secret masking

tests/
  integration/         # Full-pipeline tests (Gmail -> DB -> LINE)
  test_performance.py  # Parse throughput + index-usage regression guards

data/                  # SQLite database (gitignored)
secrets/                # Gmail credentials.json / token.json (gitignored)
```

## Troubleshooting

- **`GET /health` fails / container unhealthy** - check `docker compose logs
  app`; most often a missing/invalid `config.yaml` or a bad `DATABASE_PATH`
  the app can't create.
- **No transactions after ingestion runs** - check `/api/unknown` for emails
  that failed to parse, and their `warnings`. A KBank email with an unusual
  wording can end up with `parse_status: partial` (still inserted) or
  `failed` (logged to `unknown_patterns`, not inserted).
- **Gmail 401 / re-auth loop** - delete `secrets/token.json` and re-run
  `python -m app.gmail.authorize` (see DEPLOYMENT.md); the refresh token may
  have been revoked.
- **LINE summary never arrives** - `GET /api/settings` shows
  `line_configured`; if `false`, `LINE_CHANNEL_ACCESS_TOKEN`/`LINE_USER_ID`
  aren't set. Check the `daily_summary_sent` / `ingestion_error` events in
  `logs/app.log`.
- **AI categorization silently not applied** - it's best-effort: any Ollama
  failure (timeout, unreachable, unrecognized response) falls back to
  `Uncategorized` rather than raising. Check `OLLAMA_BASE_URL` is reachable
  from the container (`http://ollama:11434` when using the `ai` compose
  profile, not `localhost`).
- **Secrets in logs** - logging redacts `Bearer ...` tokens and any field
  whose name looks like a secret (`token`, `credential`, `password`,
  `secret`, `authorization`). If you add a new integration, name its secret
  fields accordingly rather than logging them directly.

## Status

Version 2.0 - Phase 3 (Docker, integration tests, edge cases, production
readiness) complete.
