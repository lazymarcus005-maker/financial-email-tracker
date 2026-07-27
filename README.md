# Financial Email Tracker

ระบบ track รายการทางการเงินจาก Gmail → SQLite → แบ่งหมวดหมู่ → LINE Summary

## MVP Features

- ✅ Gmail Reader (OAuth2, readonly)
- ✅ KBank Parser (Thai/English)
- ✅ Transaction Detector (16 types)
- ✅ SQLite Storage
- ✅ Deduplication
- ✅ Category Engine (Rule-based + AI optional)
- ✅ Dashboard (FastAPI + Jinja2 + HTMX)
- ✅ Daily LINE Summary (cron)
- ✅ Extensible for new banks

## Tech Stack

- Python 3.12
- FastAPI
- SQLite
- Gmail API (OAuth2)
- Ollama (optional, for categorization)
- LINE Bot API
- Cloudflare Access (auth)

## Quick Start

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Setup secrets
cp secrets/.env.example secrets/.env
# Edit secrets/.env with Gmail credentials, LINE token, etc.

# Init DB
python -m app.storage.database init

# Run dev server
uvicorn app.web.main:app --reload

# Run cron (background)
python -m app.ingestion.scheduler
```

## Project Structure

```
app/
  gmail/              # Gmail API integration
  parsers/            # Parser registry + KBank implementation
  classification/     # Category rules + AI fallback
  storage/            # SQLite schema + queries
  ingestion/          # Cron scheduler + service
  web/                # FastAPI routes + templates

data/                 # SQLite database
secrets/              # Credentials (gitignored)
```

## Status

Version 2.0 - MVP in development
