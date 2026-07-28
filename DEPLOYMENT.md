# Deployment

## 1. Environment setup

```bash
git clone <this repo> && cd financial-email-tracker
cp .env.example .env
mkdir -p secrets
```

Edit `.env` - at minimum set `AUTH_SECRET_KEY` and `PUBLIC_BASE_URL`; set
`LINE_CHANNEL_ACCESS_TOKEN`/`LINE_USER_ID` (see below) if you want the daily
summary. Gmail OAuth client credentials live in `secrets/`, not `.env`.
`docker-compose.yml` reads `.env` automatically and forwards these as
container environment variables, which override `config.yaml` for any scalar
setting (see `app/config.py`).

Generate a strong auth secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

On first startup, open the app and create the first admin user at `/setup`.
Existing runtime data is assigned to that first admin during migration/setup.

## 2. Gmail OAuth setup

1. In [Google Cloud Console](https://console.cloud.google.com/), create a
   project (or reuse one), enable the **Gmail API**, and create an OAuth
   **client ID**.
2. For the in-app **Connect Gmail** button, use an OAuth client of type
   **Web application** and add the exact authorized redirect URI:
   - Local: `http://localhost:8000/gmail/oauth2/callback`
   - Production: `https://your-domain/gmail/oauth2/callback`
   - This deployment: `https://kplus.mxlabs.cloud/gmail/oauth2/callback`
   - If you use `127.0.0.1` or another port locally, add that exact URI too.
3. Download the client secret JSON and save it as `secrets/credentials.json`.
4. Each user connects Gmail from `/settings`. The app stores a readonly Gmail
   token per user at `secrets/users/{user_id}/gmail-token.json`. Manual
   ingestion and reparse use the logged-in user's token.
5. Optional legacy fallback for scheduled ingestion: you can still create a
   shared token with the CLI flow:
   ```bash
   python -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   python -m app.gmail.authorize
   ```
   This writes `secrets/token.json`. Scheduled ingestion uses the first
   active admin's per-user token when available, then falls back to this
   legacy shared token.
6. The scope requested is `gmail.readonly` - the app never sends, deletes, or
   modifies mail.
7. Files under `secrets/` are picked up by the container via the
   `./secrets:/app/secrets` bind mount in `docker-compose.yml`. `secrets/` is
   gitignored - never commit credentials or tokens.
   The container entrypoint fixes ownership of mounted `data/`, `secrets/`,
   and `logs/` before starting the app so tokens remain readable and
   refreshable after deploy.
8. Set `GMAIL_QUERY` in `config.yaml` to scope which emails are ingested,
   e.g. `from:(KPLUS@kasikornbank.com) newer_than:90d`.

Set the public base URL in `.env` so the app sends the same redirect URI that
Google Cloud expects:

```bash
PUBLIC_BASE_URL=https://kplus.mxlabs.cloud
```

Token refresh is automatic (`app/gmail/authorize.py` refreshes expired tokens
using stored refresh tokens). If refresh fails for a user, open Settings,
disconnect Gmail, and connect again. If the legacy fallback token fails,
delete `secrets/token.json` and repeat the CLI flow.

## 3. LINE Bot setup

1. In the [LINE Developers Console](https://developers.line.biz/console/),
   create a provider and a **Messaging API** channel.
2. Under the channel's **Messaging API** tab, issue a **Channel access
   token** (long-lived) → `LINE_CHANNEL_ACCESS_TOKEN`.
3. Get your own `userId` to push to: add the channel's official account as a
   friend, then either read `userId` from a webhook event, or use "Your user
   ID" shown in the Developers Console's Basic settings if enabled →
   `LINE_USER_ID`.
4. `GET /api/settings` on the running app reports `line_configured: true`
   once both are set correctly.
5. If pushes ever silently stop, check `logs/app.log` for a
   `daily_summary_sent` (success) vs `ingestion_error` with
   `job: "daily_summary"` (failure) event - the send is best-effort and
   never crashes the scheduler.

## 4. Docker Compose deploy

```bash
docker compose up -d --build
```

- App listens on `:8000`, backed by the `finance_data` named volume for
  SQLite and the `./secrets` bind mount for Gmail credentials and user
  tokens.
- `/health`, `/login`, `/setup`, and static assets are public. All runtime
  pages and `/api/*` require login.
- Admins manage users at `/users`. Runtime data is scoped per user; exports,
  imports, mappings, ignored subjects, transaction lists, and dashboard stats
  operate on the logged-in user's data only.
- The container runs as non-root (`appuser`), and Docker reports it healthy
  once `curl -f http://localhost:8000/health` succeeds (30s interval, 3
  retries, 5s start period - see `Dockerfile`/`docker-compose.yml`).
- Optional AI-assisted categorization via a local Ollama model:
  ```bash
  docker compose --profile ai up -d --build
  # then pull the model once:
  docker compose exec ollama ollama pull qwen3:1.7b
  ```
  and set `AI_ENABLED=true` in `.env`. AI categorization is best-effort -
  any failure (unreachable, timeout, bad response) falls back to the
  rule-based/uncategorized path rather than failing ingestion.

To update:

```bash
git pull
docker compose up -d --build
```

The SQLite file lives in the `finance_data` volume and survives rebuilds;
back it up with `docker run --rm -v financial-email-tracker_finance_data:/data -v $PWD:/backup alpine tar czf /backup/finance-backup.tgz -C /data .` (adjust the volume name to match `docker volume ls`).

## 5. Monitoring

- **Health**: `GET /health` (also what the container healthcheck polls).
- **Ingestion history**: `GET /api/runs` (or the dashboard at `/`) - counts
  of emails checked/inserted/duplicates/failed per cron run, plus
  `last_sync`, scoped to the logged-in user.
- **Parse failures**: `GET /api/unknown` - emails that failed to parse, with
  warnings and raw fields, so you can extend `app/parsers/kbank/aliases.py`
  or a new bank's parser without losing data (nothing is discarded, just
  logged as `unknown`).
- **Logs**: structured JSON to stdout (`docker compose logs -f app`) and to
  `logs/app.log`, rotated at 5MB × 10 files. Key events: `email_parsed`,
  `duplicate`, `categorized`, `cron_start`, `cron_finish`,
  `thai_section_not_found`, `daily_summary_sent`, `ingestion_error`. Set
  `LOG_FORMAT=text` for human-readable local debugging;  `json` (default) is
  meant for log aggregators.
- **Secrets in logs**: bearer tokens and any field named like a secret
  (`token`, `credential`, `password`, `secret`, `authorization`) are
  redacted before being written - verified by `tests/test_logging_config.py`
  and `tests/integration/test_line_sending.py`.
