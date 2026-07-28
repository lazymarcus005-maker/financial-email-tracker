# Web UI Enhancements — Design

Date: 2026-07-28

## Overview

Six related improvements to the dashboard/web UI, requested together:

1. Transaction detail should be viewable inline with the original raw email.
2. Unknown (unparseable) emails should show a raw-email popup and let the user
   manually classify one into a real transaction.
3. Dashboard: pie chart of spending by bank.
4. Dashboard: total spend figure alongside the existing expense bar chart.
5. Recent Runs: plain-language success/failure description and honest retry
   semantics.
6. A permanent, browsable history of parser failures (including ones later
   resolved), for future pattern-mining and re-ingestion.

Items 2 and 6 turned out to be the same underlying change (the `unknown_patterns`
table becoming a permanent, non-deleted history) and are designed together.
Items 3 and 4 both live on the dashboard and share a data window control.

## Context found while investigating

- `/transactions/{id}` already exists as a full detail page (fields, inline
  category editor, reparse/delete), linked from every row in the transactions
  table. It was missing the original raw email body and a modal presentation.
- Its action buttons (Save Category / Ignore / Reparse / Delete) call
  `apiCall(...)`, a function that is referenced in `transaction_detail.html`
  but **never defined anywhere in the codebase** — these buttons currently
  throw and do nothing. Fixed as part of this work since we're touching this
  page anyway.
- `unknown_patterns` rows store only the fields the parser managed to extract
  (`raw_fields_json`), not the original email body — and are hard-deleted the
  moment a reparse succeeds (`reparse_unknown`, `persistence.clear_unknown`).
  Neither the raw-email view nor a permanent history works without a schema
  change.
- Transactions don't record which bank they came from — only a generic
  `parser_version`. The parser *is* selected per-bank
  (`ParserRegistry.get_parser`), but that fact isn't persisted.
- There is no automatic retry of failed emails. Scheduled ingestion runs only
  process messages not yet in `transactions`; a message already logged in
  `unknown_patterns` gets re-skipped via `INSERT OR IGNORE` on its unique
  `gmail_message_id`. The only ways a failure gets resolved are a manual
  "Reparse"/"Retry" click, or the new manual promotion flow below.

## Decisions (confirmed with user)

- Transaction detail: raw email added, presented as a **modal** launched from
  the transactions list; the standalone `/transactions/{id}` page stays for
  direct links/bookmarks and renders the same shared partial.
- Unknown-page manual categorization **promotes the row to a real transaction**
  (not just a label on the failed record).
- Bank pie chart is **new-data-only** — no backfill of historical transactions
  via re-fetching Gmail. Old rows show under an "Unknown" bucket.
- Resolved failures are **kept permanently** (marked `resolved`, never
  deleted) so they double as the requested ingestion history.
- Pie chart and bar chart **share one day-range toggle** (7/14/30).
- Recent Runs is **explicit** about the lack of automatic retry, and shows the
  next scheduled sync time separately for context.

## Data model changes

All additive, applied via a small guarded migration step in
`app/storage/database.py` (check `PRAGMA table_info(...)` before
`ALTER TABLE ... ADD COLUMN`, since `CREATE TABLE IF NOT EXISTS` doesn't touch
existing tables and SQLite has no `ADD COLUMN IF NOT EXISTS`).

```sql
ALTER TABLE transactions ADD COLUMN bank TEXT;

ALTER TABLE unknown_patterns ADD COLUMN received_at DATETIME;
ALTER TABLE unknown_patterns ADD COLUMN resolved_transaction_id INTEGER;
ALTER TABLE unknown_patterns ADD COLUMN resolved_at DATETIME;
```

`unknown_patterns.status` gains a third value used going forward: `resolved`
(alongside existing `pending` / `ignored`). No CHECK constraint exists today,
so this is purely a convention change plus the two new columns above.

## Feature 1 — Transaction detail: modal + raw email

- Extract the current content of `transaction_detail.html` (fields, category
  editor, actions, extracted raw fields) into a partial,
  `partials/transaction_detail_body.html`, parameterized the same way
  (`t`, `categories`).
- Add a `#modal-root` container to `base.html`: a fixed, centered overlay with
  a backdrop, closable via backdrop click / Escape / a close button. Reusable
  by Feature 2 as well.
- `transactions.html`: row click becomes `hx-get="/transactions/{id}/modal"
  hx-target="#modal-root" hx-swap="innerHTML"` instead of an `<a href>`. New
  route `GET /transactions/{id}/modal` renders `transaction_detail_body.html`
  wrapped in the modal chrome.
- `/transactions/{id}` (full page) keeps working, rendering
  `transaction_detail_body.html` inside the normal page shell — same partial,
  two shells.
- New button "View Raw Email" inside the body partial →
  `GET /api/transactions/{id}/raw-email`. Handler fetches
  `gmail_client.get_message(gmail_message_id)` (same call `reparse` already
  makes) and returns an HTML fragment: sender, subject, received date, and the
  plain-text body in a scrollable monospace `<pre>`. On fetch failure (message
  deleted, API error), return a fragment with an inline error message rather
  than a 500.
- Fix: add `apiCall(url, method, body)` to `app.js` — `fetch` wrapper that
  sets `Content-Type: application/json`, JSON-encodes `body` when present,
  and throws (with a toast) on a non-2xx response. Existing
  `saveCategory`/`toggleIgnore`/`reparseTransaction`/`deleteTransaction`
  inline scripts start working as originally written.

## Feature 2 & 6 — Unknown page: raw email popup, promote to transaction, permanent history

- Each row in `unknown.html` gets a "View" button opening the same modal
  chrome, via `GET /unknown/{id}/modal`: subject, sender, warnings, extracted
  `raw_fields`, and a "View Raw Email" button using the same
  `/api/unknown/{id}/raw-email` live-fetch pattern as Feature 1.
- The modal includes a "Categorize as Transaction" form:
  - `transaction_type` — text input + datalist of `queries.list_transaction_types`
  - `direction` — select: in / out / internal / unknown
  - `status` — select: success / failed / pending / cancelled / unknown (default success)
  - `occurred_at` — datetime-local, prefilled from the new `received_at` column
    (falls back to `created_at` if `received_at` is null, e.g. rows created
    before this migration)
  - `amount` — number, prefilled from `unknown_patterns.amount` if present
  - `fee`, `available_balance`, `counterparty`, `description` — optional text
  - `category` — text input + datalist of `queries.list_categories` (required)
- `POST /api/unknown/{id}/promote`: validates required fields (400 with field
  errors if missing), identifies `bank` via `registry.identify_bank(row.sender)`,
  inserts a new `transactions` row (`category_source="manual"`,
  `parse_status="complete"`, `parser_version` = current), then updates the
  `unknown_patterns` row: `status="resolved"`, `resolved_transaction_id=<new id>`,
  `resolved_at=CURRENT_TIMESTAMP`. The row is **not deleted**.
- `reparse_unknown` (existing parser-fix path) changes analogously: on success,
  instead of `DELETE FROM unknown_patterns`, it sets the same three
  `resolved_*` fields, linking to the transaction `persistence.insert_transaction`
  just created.
- `persistence.clear_unknown` (called from the main ingestion loop when an
  email that previously failed now parses on a later run) takes the new
  transaction id and does the same resolve-in-place update instead of a
  hard delete.
- `insert_unknown` additionally stores `message.received_at` into the new
  `received_at` column.
- `unknown.html`'s status filter gains a `Resolved` option. Resolved rows
  render with a link to `/transactions/{resolved_transaction_id}` (the
  standalone detail page), giving the browsable "ingestion history" requested
  in item 6 without a separate page.
- Dashboard's `unknown_parser` stat and the retry-pending logic are unaffected
  since both already filter on `status = 'pending'`.

## Feature 3 & 4 — Dashboard: spending-by-bank pie chart + bar chart total

- `ParserRegistry.identify_bank(sender: str) -> str | None`: reuses the same
  `bank_key in sender_lower or parser.can_handle(sender)` matching loop as
  `get_parser`, returning a display label (`"KBank"`, `"Krungsri"`,
  `"LH Bank"`, `"SCB"`) or `None` when nothing matches — independent of the
  fact that `get_parser` itself falls back to KBank's parser for unmatched
  senders. `None` is honest here: being parsed *by* the KBank parser doesn't
  mean the email *is* from KBank.
- Wired into `bank = registry.identify_bank(message.sender)` /
  `registry.identify_bank(row["sender"])` at the three transaction-creation
  sites: `persistence.insert_transaction` (new ingestion), `reparse_transaction`
  (reparse), and the new promote endpoint (Feature 2).
- `queries.get_expense_by_bank(db, days) -> list[dict]`: sums `amount` where
  `direction = 'out' AND parse_status != 'ignored'`, grouped by
  `COALESCE(bank, 'Unknown')`, over the same `days` window already used by
  `get_expense_by_day` — driven by the dashboard's existing 7/14/30 toggle
  (one control feeds both charts, per the confirmed decision).
- Rendered as an SVG donut following the dataviz skill's procedure:
  - Fixed categorical hue order, one slot per known bank plus "Unknown",
    validated for CVD-safety at implementation time
    (`scripts/validate_palette.js`) rather than eyeballed.
  - A legend is always shown (more than one series): bank name, ฿ total, %.
  - Selective direct labels (e.g. on slices above a small threshold), not a
    number crammed onto every sliver.
  - No dual-axis or 3D tricks; this is a single proportion-of-whole read.
- Bar chart card: add "Total: ฿X,XXX.XX over last N days" next to the
  existing day-toggle, computed as `sum(item.total for item in expense_days)`
  — no new query, this is a fold over data the route already fetches.

## Feature 5 — Recent Runs: description + honest retry semantics

- Each run in the Recent Runs card gets a plain-language line built from
  existing fields, e.g.:
  - `✅ 12 scanned, 5 saved` (no failures)
  - `⚠️ Completed with 3 failures — 12 scanned, 5 saved, 3 failed`
- On runs with failures, add: **"No automatic retry — click Retry to
  reprocess"** next to the existing Retry button, since scheduled runs do not
  revisit already-logged failures (see Context above).
- Separately (not tied to any specific run), show the next scheduled sync
  time, computed from `config.yaml`'s `SCHEDULE` list and `TIMEZONE` — the
  next entry in `SCHEDULE` strictly after "now" in that timezone, wrapping to
  tomorrow's first entry if none remain today.

## Error handling

- Raw-email fetch (`/api/transactions/{id}/raw-email`,
  `/api/unknown/{id}/raw-email`): Gmail API errors or a missing message
  return a fragment with an inline error, not a 500 — the modal shouldn't
  break because an old email was deleted or a token expired.
- `POST /api/unknown/{id}/promote`: missing required fields → 400 with a
  per-field error message rendered back into the form (HTMX pattern
  consistent with existing inline-edit validation in this app); already-
  resolved or already-ignored rows are rejected with a 409 rather than
  silently double-promoting.
- Migration step: `ALTER TABLE` guarded by a `PRAGMA table_info` check so
  re-running `init_db()` against an already-migrated database is a no-op, not
  an error.

## Testing

- `tests/test_registry.py` (new, or added to an existing parser test file):
  `identify_bank` returns the right label per known sender and `None` for an
  unmatched one.
- `tests/test_service.py` / `tests/test_reparse.py`: resolve-in-place
  behavior — a successful reparse or ingestion-loop resolution updates
  `resolved_transaction_id`/`resolved_at`/`status` instead of deleting the
  row; assert the row still exists afterward.
- New tests for `POST /api/unknown/{id}/promote`: happy path creates a
  transaction and resolves the source row; missing required fields returns
  400; promoting an already-resolved row returns 409.
- `queries.get_expense_by_bank`: grouping/window correctness, including the
  `NULL -> "Unknown"` bucket.
- No browser/e2e test infrastructure exists in this repo today; the
  modal/JS/chart pieces are verified manually (via the `run` skill) once
  implemented rather than through new automated tests.

## Out of scope

- Backfilling `bank` for historical transactions (would require re-fetching
  every old email from Gmail).
- A dedicated "Ingestion History" page — folded into `/unknown`'s existing
  status filter instead.
- Any change to the actual retry *mechanism* (e.g. adding real automatic
  retry) — only the UI's description of current behavior changes.
