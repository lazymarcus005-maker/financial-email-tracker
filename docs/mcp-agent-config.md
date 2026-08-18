# MCP Agent Configuration

`financial-email-tracker` ships a local [MCP](https://modelcontextprotocol.io)
stdio server (`app/mcp/server.py`) that lets an AI agent read your financial
data and, optionally, make a small set of safe changes. It runs as a separate
process from the FastAPI web app - it does not need the web server running,
and it never shares its own process with another user's data.

This covers Phase 1 (read-only tools) and Phase 2 (write tools) from
`ImplementMCP.md`. Phase 3 (remote HTTP MCP with per-user token auth) is not
implemented yet.

## Prerequisites

1. `pip install -r requirements.txt` (installs the `mcp` SDK).
2. A user account already created in the app (see the web `/setup` flow), and
   its numeric user id - that's what `MCP_OWNER_USER_ID` points at. You can
   find it from **Settings** in the web UI or by querying the `users` table.
3. If you plan to use `run_ingestion`, that user must have connected Gmail
   from **Settings -> Connect Gmail** first, same as the web app requires.

## Configuration

Set these in `.env` (or as real environment variables - they override
`config.yaml`, see `app/config.py`):

| Variable | Default | Meaning |
|---|---|---|
| `MCP_ENABLED` | `false` | Gate for `python -m app.mcp.server`; the process refuses to start unless this is `true`. |
| `MCP_OWNER_USER_ID` | unset | The single user id this MCP process acts as. **Required.** The local stdio server is single-user per process - there is no way for the agent to switch users or see another user's data. |
| `MCP_ALLOW_WRITE` | `false` | Enables `update_transaction_category`, `ignore_transaction`, `create_category_mapping`, `run_ingestion`. Read-only tools work regardless of this flag. |
| `MCP_ALLOW_SEND` | `false` | Enables `send_line_daily_summary`, which pushes a real LINE message. Kept separate from `MCP_ALLOW_WRITE` on purpose - it has an external, user-visible side effect that a local agent usually can't ask you to confirm first. |
| `MCP_EXPOSE_RAW_EMAIL` | `false` | When `true`, transaction results include a `raw_fields` object (parsed structured fields, not the raw email body) with account/reference/card/email/phone-shaped values masked to their last 4 characters. When `false` (default), `raw_fields` is omitted entirely. |
| `MCP_API_TOKEN` | unset | Reserved for a future remote/HTTP MCP transport (Phase 3). Unused by the stdio server. |

`DATABASE_PATH`, `GMAIL_QUERY`, `LINE_CHANNEL_ACCESS_TOKEN`,
and `LINE_USER_ID` are shared with the web app - the MCP server reads the same
`config.yaml`/env via `app.config.get_settings()`.

## Running it

```bash
MCP_ENABLED=true MCP_OWNER_USER_ID=1 python -m app.mcp.server
```

The process communicates over stdio, so it's normally not run by hand -
your agent's MCP client launches it as a subprocess.

## Example agent config

Most MCP-capable agents (Claude Desktop, Cursor, etc.) use a JSON block like
this - check your specific agent's docs for the exact file location and key
name:

```json
{
  "mcpServers": {
    "financial-email-tracker": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "/absolute/path/to/financial-email-tracker",
      "env": {
        "MCP_ENABLED": "true",
        "MCP_OWNER_USER_ID": "1",
        "MCP_ALLOW_WRITE": "false",
        "MCP_ALLOW_SEND": "false",
        "DATABASE_PATH": "data/finance.db"
      }
    }
  }
}
```

Start read-only (`MCP_ALLOW_WRITE=false`, the default) until you've seen the
agent behave sensibly with `search_transactions` and `get_dashboard_summary`.
Flip `MCP_ALLOW_WRITE=true` only once you trust the agent to categorize and
ignore transactions on your behalf; leave `MCP_ALLOW_SEND=false` unless you
specifically want the agent to be able to push a real LINE message.

## Tools

### Read-only (Phase 1, always available)

| Tool | Purpose |
|---|---|
| `get_dashboard_summary()` | Today's income/expense totals, transaction/uncategorized/parse-error counts, last sync time. |
| `search_transactions(date_from?, date_to?, category?, transaction_type?, direction?, search?, page?, page_size?)` | Filtered, paginated transaction search. `page_size` is capped at 50 even if a larger value is requested. Sorted `occurred_at DESC`. |
| `get_transaction_detail(transaction_id)` | One transaction by id. Returns `{"error": "not_found", "transaction_id": ...}` (never an exception/stack trace) if it doesn't exist or belongs to another user. |
| `list_unknown_emails(status?, page?, page_size?)` | Emails the parser couldn't understand. Only returns `id`, `subject`, `sender`, `amount`, `warnings`, `received_at`, `status`, `transaction_code` - never the raw email body. |
| `list_category_mappings()` | Current counterparty -> category mapping rules. |
| `get_daily_summary(day?)` | Same aggregation used for the LINE daily summary, returned as both structured `data` and pre-formatted `line_text` - without sending anything. |

### Write tools (Phase 2, require `MCP_ALLOW_WRITE=true`)

| Tool | Purpose |
|---|---|
| `update_transaction_category(transaction_id, category)` | Sets a transaction's category (`category_source="manual"`) and records the counterparty -> category mapping so future transactions from the same counterparty categorize the same way. |
| `ignore_transaction(transaction_id, ignored=true)` | Marks a transaction ignored/unignored (`parse_status`). |
| `create_category_mapping(counterparty, category)` | Creates/updates a counterparty -> category rule. Both fields must be non-empty and <= 200 characters. |
| `run_ingestion(window="default")` | Fetches new Gmail messages and ingests them. `window` is one of `default`, `last_7_days`, `last_30_days` - the agent cannot pass an arbitrary Gmail search query. Requires a connected Gmail token for the owner user; fails with a clear error if one run is already in progress. |

### Send tool (requires the separate `MCP_ALLOW_SEND=true`)

| Tool | Purpose |
|---|---|
| `send_line_daily_summary(day?)` | Sends the daily summary to LINE for real, using the same `LINE_CHANNEL_ACCESS_TOKEN`/`LINE_USER_ID` as the scheduled job. Use `get_daily_summary` first to preview what will be sent. |

All not-found conditions return a structured `{"error": ...}` value rather
than raising, so an agent can branch on the result. Permission and validation
failures (`MCP_ALLOW_WRITE`/`MCP_ALLOW_SEND` off, missing `category`, invalid
`window`, transaction id required) raise `PermissionError` or `ValueError`,
which most MCP clients surface to the agent as a tool error.

## Security notes

- **Owner scope is mandatory and process-wide.** Every tool resolves
  `owner_user_id` from `MCP_OWNER_USER_ID` and passes it through to every
  query - there's no code path that queries across all users. If you need a
  second user's data available to an agent, run a second MCP server process
  with a different `MCP_OWNER_USER_ID` (and a different agent config entry).
- **Raw email body is never returned**, in any phase. `MCP_EXPOSE_RAW_EMAIL`
  only affects `raw_fields` (the parser's extracted structured fields on a
  *transaction*, e.g. reference numbers) and even then masks anything that
  looks like an account/reference/card/email/phone value.
- **Write tools are off by default.** Flip `MCP_ALLOW_WRITE=true` only for
  agents/environments you trust to edit your data unattended.
- **`send_line_daily_summary` is gated separately** (`MCP_ALLOW_SEND`) because
  it's the one tool with a real external effect a human can't easily undo.
  Prefer `get_daily_summary` to preview, and only enable sending for agents
  that show you the preview and get your confirmation before calling it.
- **Every tool call is audit-logged** (tool name, `owner_user_id`, arguments,
  success/failure, duration) via the standard app logger. Argument keys that
  look like `token`/`password`/`secret`/`body`/`raw` are masked to `***`
  before logging - Gmail/LINE tokens and email bodies are never written to
  logs.
- **`run_ingestion` cannot run an arbitrary Gmail query.** The agent only
  chooses a time window (`default`/`last_7_days`/`last_30_days`); the actual
  query is always built from your configured `GMAIL_QUERY`.
