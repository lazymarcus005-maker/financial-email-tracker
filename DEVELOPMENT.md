# Development

## Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Copy the env template and fill in what you need for the areas you're
touching (Gmail/LINE aren't required just to run the parser test suite):

```bash
cp .env.example .env
```

## Running tests

```bash
pytest                          # full suite
pytest -q                       # quiet
pytest tests/test_kbank_parser.py           # a single file
pytest tests/integration/                    # only the full-pipeline tests
pytest tests/test_performance.py             # throughput / index-usage guards
pytest --cov=app --cov-report=term-missing   # coverage
```

Tests don't touch your real Gmail/LINE/database - `tests/conftest.py`
provides `temp_db_path`/`db_connection` fixtures that point
`app.storage.database` at a fresh temp SQLite file per test, and
`tests/integration/conftest.py` adds `make_message`/`fake_reader` fixtures
that fake out `GmailReader`. LINE/Ollama calls are mocked at the `httpx`
level in the relevant tests - nothing goes over the network.

## Running locally

```bash
# Initialize the DB schema once (also happens automatically on app startup)
python -c "import asyncio; from app.storage.database import init_db; asyncio.run(init_db())"

# Web app + dashboard (also starts the cron scheduler via its FastAPI lifespan)
uvicorn app.web.main:app --reload

# Or run just the scheduler standalone (no web server)
python -m app.ingestion.scheduler

# One-off Gmail OAuth login (opens a browser, writes secrets/token.json)
python -m app.gmail.authorize
```

`config.yaml` controls the cron schedule (`SCHEDULE`, in `TIMEZONE`) and the
Gmail search query (`GMAIL_QUERY`). Any scalar setting can be overridden by
an environment variable of the same name without touching `config.yaml`
(this is how Docker Compose / `.env` take effect - see `app/config.py`).

## Code layout

See the "Project Structure" section in [README.md](README.md). The KBank
parser pipeline (`app/parsers/kbank/`) is the most involved piece:

```
normalize -> detect_section -> extract_fields -> map_fields
          -> detect (type/direction/status) -> validate
```

Each stage has its own unit tests (`tests/test_normalize.py`,
`tests/test_detector.py`, `tests/test_extractor.py`, `tests/test_mapper.py`,
`tests/test_transaction_detector.py`), plus `tests/test_kbank_parser.py` for
the pipeline end-to-end and `tests/integration/` for the pipeline wired up
to Gmail/DB/LINE.

## Adding a new bank parser

1. Implement `app.parsers.base.BaseParser` (`can_handle(sender)` and
   `parse(email_text, subject) -> Transaction | None`).
2. Register it in `app/parsers/registry.py`.
3. Add fixtures/tests mirroring `tests/test_kbank_parser.py` and, ideally, a
   `tests/integration/` case for the full pipeline.

## Docker (local build)

```bash
docker compose build app
docker compose up app
curl http://localhost:8000/health
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for the full Docker Compose deployment
(volumes, secrets, Gmail/LINE setup, monitoring).
