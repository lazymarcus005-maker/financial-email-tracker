# Web UI Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add transaction-detail raw-email viewing (as a modal), unknown-email raw-email viewing + manual promotion to a transaction with permanent history, a dashboard spend-by-bank pie chart + spend total, and honest Recent-Runs status/retry messaging.

**Architecture:** Additive SQLite columns (`transactions.bank`, three new columns on `unknown_patterns`) applied via a guarded migration step; a shared HTMX-driven `#modal-root` overlay reused by both the transaction-detail and unknown-email views; raw email content is always fetched live from Gmail via the existing `GmailClient`, never persisted; failed emails are resolved in place instead of deleted, so `/unknown` doubles as permanent ingestion history.

**Tech Stack:** FastAPI, Jinja2, HTMX 1.9, aiosqlite, pytest + pytest-asyncio, vanilla JS (no new dependencies).

## Global Constraints

- Python 3.12, existing FastAPI/aiosqlite stack — no new third-party packages.
- No Alembic/migration framework exists; schema changes are additive `ALTER TABLE` statements guarded by `PRAGMA table_info` checks (SQLite has no `ADD COLUMN IF NOT EXISTS`).
- Follow existing HTMX conventions already used in this app (`hx-get`/`hx-post`/`hx-patch`, `hx-target`, `hx-swap="outerHTML"` as the htmx default per `app.js`) — no new JS framework, no fetch-based JSON API calls from templates except where explicitly noted.
- Raw email content is fetched live via `GmailClient.get_message(gmail_message_id)` (same call the existing reparse flow already makes) — never stored in SQLite, and no backfill of historical `bank` values.
- Design spec: `docs/superpowers/specs/2026-07-28-web-ui-enhancements-design.md` — refer back to it for the "why" behind any decision below.

---

### Task 1: Schema migration — `bank` column + `unknown_patterns` history columns

**Files:**
- Modify: `app/storage/database.py`
- Test: `tests/test_database.py` (new)

**Interfaces:**
- Produces: `database._migrate_schema(db: aiosqlite.Connection) -> None`, called from `database.init_db()`. Adds `transactions.bank TEXT`, `unknown_patterns.received_at DATETIME`, `unknown_patterns.resolved_transaction_id INTEGER`, `unknown_patterns.resolved_at DATETIME`, each only if not already present.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_database.py`:

```python
"""Tests for app.storage.database - schema init and additive migrations."""

import pytest

from app.storage import database


@pytest.mark.asyncio
async def test_init_db_creates_new_columns(temp_db_path):
    db = await database.get_connection()
    cursor = await db.execute("PRAGMA table_info(transactions)")
    transaction_columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    cursor = await db.execute("PRAGMA table_info(unknown_patterns)")
    unknown_columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    await db.close()

    assert "bank" in transaction_columns
    assert {"received_at", "resolved_transaction_id", "resolved_at"} <= unknown_columns


@pytest.mark.asyncio
async def test_migrate_schema_is_idempotent(temp_db_path):
    db = await database.get_connection()
    # Must not raise - SQLite errors on ALTER TABLE ADD COLUMN for a column
    # that already exists, so re-running migration on an up-to-date DB has
    # to be a no-op.
    await database._migrate_schema(db)
    await db.commit()
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database.py -v`
Expected: FAIL - `AttributeError: module 'app.storage.database' has no attribute '_migrate_schema'` (and the column-presence assertions fail too, since `init_db` doesn't add these columns yet).

- [ ] **Step 3: Implement the migration**

In `app/storage/database.py`, add the new function and call it from `init_db`:

```python
async def init_db():
    """Initialize database and schema."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(DATABASE_PATH), timeout=SQLITE_TIMEOUT_SECONDS) as db:
        await configure_connection(db)
        await db.executescript(SCHEMA_SQL)
        await _migrate_schema(db)
        await db.commit()
        logger.info(f"Database initialized: {DATABASE_PATH}")


async def _migrate_schema(db: aiosqlite.Connection) -> None:
    """Add columns introduced after the initial schema, if not already present.

    SQLite has no `ADD COLUMN IF NOT EXISTS`, so each addition is guarded by
    checking `PRAGMA table_info` first.
    """
    cursor = await db.execute("PRAGMA table_info(transactions)")
    transaction_columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    if "bank" not in transaction_columns:
        await db.execute("ALTER TABLE transactions ADD COLUMN bank TEXT")

    cursor = await db.execute("PRAGMA table_info(unknown_patterns)")
    unknown_columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    if "received_at" not in unknown_columns:
        await db.execute("ALTER TABLE unknown_patterns ADD COLUMN received_at DATETIME")
    if "resolved_transaction_id" not in unknown_columns:
        await db.execute("ALTER TABLE unknown_patterns ADD COLUMN resolved_transaction_id INTEGER")
    if "resolved_at" not in unknown_columns:
        await db.execute("ALTER TABLE unknown_patterns ADD COLUMN resolved_at DATETIME")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/storage/database.py tests/test_database.py
git commit -m "feat(db): add bank column and unknown_patterns history columns"
```

---

### Task 2: `ParserRegistry.identify_bank`

**Files:**
- Modify: `app/parsers/registry.py`
- Test: `tests/test_registry.py` (new)

**Interfaces:**
- Produces: `ParserRegistry.identify_bank(sender: str) -> str | None` — returns `"KBank"` / `"Krungsri"` / `"LH Bank"` / `"SCB"` for a matching sender, `None` otherwise. Used by Tasks 4, 5, 11.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_registry.py`:

```python
"""Tests for app.parsers.registry - bank routing and identification."""

from app.parsers.registry import ParserRegistry


def test_identify_bank_matches_known_senders():
    registry = ParserRegistry()
    assert registry.identify_bank("notify@kasikornbank.com") == "KBank"
    assert registry.identify_bank("admin@krungsri.com") == "Krungsri"
    assert registry.identify_bank("LHBYou@lhbank.co.th") == "LH Bank"
    assert registry.identify_bank("scbeasynet@scb.co.th") == "SCB"


def test_identify_bank_returns_none_for_unmatched_sender():
    registry = ParserRegistry()
    assert registry.identify_bank("someone@example.com") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_registry.py -v`
Expected: FAIL - `AttributeError: 'ParserRegistry' object has no attribute 'identify_bank'`

- [ ] **Step 3: Implement `identify_bank`**

In `app/parsers/registry.py`, add a class-level label map and the method:

```python
class ParserRegistry:
    """Route emails to the appropriate bank parser."""

    BANK_LABELS = {
        "kasikornbank": "KBank",
        "krungsri": "Krungsri",
        "lhbank": "LH Bank",
        "scb": "SCB",
    }

    def __init__(self):
        self._default_parser = KBankParser()
        self.parsers: dict[str, BaseParser] = {
            "kasikornbank": self._default_parser,
            "krungsri": KrungsriParser(),
            "lhbank": LHBankParser(),
            "scb": SCBParser(),
        }

    def get_parser(self, sender: str) -> BaseParser:
        """Select parser based on sender email. Falls back to KBank if no match."""
        sender_lower = sender.lower()

        for bank_key, parser in self.parsers.items():
            if bank_key in sender_lower or parser.can_handle(sender):
                logger.info(f"Parser selected: {parser.__class__.__name__} for {sender}")
                return parser

        logger.warning(f"No parser matched sender {sender!r}, falling back to KBank parser")
        return self._default_parser

    def identify_bank(self, sender: str) -> str | None:
        """Return a display label for the bank matching `sender`, or None.

        Uses the same matching rule as get_parser, but returns None on no
        match rather than falling back to KBank - being routed through the
        KBank parser as a fallback doesn't mean the email is actually from KBank.
        """
        sender_lower = sender.lower()
        for bank_key, parser in self.parsers.items():
            if bank_key in sender_lower or parser.can_handle(sender):
                return self.BANK_LABELS.get(bank_key)
        return None

    def parse(self, email_text: str, sender: str, subject: str = "") -> Transaction | None:
        """Parse email, return Transaction or None if failed."""
        parser = self.get_parser(sender)
        return parser.parse(email_text, subject=subject)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/parsers/registry.py tests/test_registry.py
git commit -m "feat(parsers): add ParserRegistry.identify_bank"
```

---

### Task 3: Persistence layer — bank column, received_at, resolve-in-place, manual promotion

**Files:**
- Modify: `app/ingestion/persistence.py`
- Test: `tests/test_persistence.py` (new)

**Interfaces:**
- Consumes: nothing new (uses `aiosqlite.Connection`, `EmailMessage`, `Transaction` as before).
- Produces:
  - `insert_transaction(db, message, transaction, category, category_source, bank=None) -> int` (now returns the new row's id; previously returned `None`)
  - `insert_unknown(db, message, transaction) -> None` (now also stores `message.received_at`)
  - `resolve_unknown(db, unknown_id: int, transaction_id: int) -> None` (new)
  - `resolve_unknown_by_message(db, gmail_message_id: str, transaction_id: int) -> None` (new; replaces `clear_unknown`)
  - `insert_manual_transaction(db, gmail_message_id, bank, transaction_type, direction, status, occurred_at, amount, category, fee=0.0, available_balance=None, counterparty=None, description=None) -> int` (new)
  - `clear_unknown` is **removed**.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_persistence.py`:

```python
"""Tests for app.ingestion.persistence - DB writes for transactions and unknown patterns."""

from datetime import datetime

import pytest

from app.gmail import EmailMessage
from app.ingestion import persistence
from app.parsers.base import Transaction


def _message(message_id="msg-1", sender="notify@kasikornbank.com"):
    return EmailMessage(
        gmail_message_id=message_id,
        gmail_thread_id=f"thread-{message_id}",
        sender=sender,
        subject="K PLUS: Transfer Successful",
        received_at=datetime(2025, 1, 26, 14, 32),
        body_text="Transaction Date: 26/01/2025\nAmount: 100.00 THB",
    )


def _transaction():
    return Transaction(
        transaction_type="bank_transfer",
        direction="out",
        status="success",
        occurred_at="2025-01-26T14:32",
        amount=100.0,
        parse_status="complete",
        parse_confidence=1.0,
        raw_fields={"Amount": "100.00 THB"},
    )


@pytest.mark.asyncio
async def test_insert_transaction_stores_bank_and_returns_id(db_connection):
    transaction_id = await persistence.insert_transaction(
        db_connection, _message(), _transaction(), "Shopping", "rule", bank="KBank"
    )
    await db_connection.commit()

    assert isinstance(transaction_id, int)
    cursor = await db_connection.execute("SELECT bank FROM transactions WHERE id = ?", (transaction_id,))
    row = await cursor.fetchone()
    await cursor.close()
    assert row["bank"] == "KBank"


@pytest.mark.asyncio
async def test_insert_unknown_stores_received_at(db_connection):
    await persistence.insert_unknown(db_connection, _message(), None)
    await db_connection.commit()

    cursor = await db_connection.execute("SELECT received_at FROM unknown_patterns WHERE gmail_message_id = ?", ("msg-1",))
    row = await cursor.fetchone()
    await cursor.close()
    assert row["received_at"] == "2025-01-26T14:32:00"


@pytest.mark.asyncio
async def test_resolve_unknown_marks_resolved_without_deleting(db_connection):
    await persistence.insert_unknown(db_connection, _message(), None)
    await db_connection.commit()
    cursor = await db_connection.execute("SELECT id FROM unknown_patterns WHERE gmail_message_id = ?", ("msg-1",))
    unknown_id = (await cursor.fetchone())["id"]
    await cursor.close()

    await persistence.resolve_unknown(db_connection, unknown_id, 42)
    await db_connection.commit()

    cursor = await db_connection.execute(
        "SELECT status, resolved_transaction_id, resolved_at FROM unknown_patterns WHERE id = ?", (unknown_id,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row["status"] == "resolved"
    assert row["resolved_transaction_id"] == 42
    assert row["resolved_at"] is not None


@pytest.mark.asyncio
async def test_resolve_unknown_by_message_only_updates_pending_rows(db_connection):
    await persistence.insert_unknown(db_connection, _message(), None)
    await db_connection.commit()

    await persistence.resolve_unknown_by_message(db_connection, "msg-1", 42)
    await db_connection.commit()

    cursor = await db_connection.execute(
        "SELECT status, resolved_transaction_id FROM unknown_patterns WHERE gmail_message_id = ?", ("msg-1",)
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row["status"] == "resolved"
    assert row["resolved_transaction_id"] == 42


@pytest.mark.asyncio
async def test_insert_manual_transaction_creates_transaction_with_manual_source(db_connection):
    transaction_id = await persistence.insert_manual_transaction(
        db_connection,
        gmail_message_id="msg-unknown-1",
        bank="SCB",
        transaction_type="bank_transfer",
        direction="out",
        status="success",
        occurred_at="2026-07-27T10:00:00",
        amount=250.0,
        category="Shopping",
        fee=1.5,
        available_balance=1000.0,
        counterparty="Shopee",
        description="Manual entry",
    )
    await db_connection.commit()

    cursor = await db_connection.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,))
    row = await cursor.fetchone()
    await cursor.close()
    assert row["category_source"] == "manual"
    assert row["bank"] == "SCB"
    assert row["amount"] == 250.0
    assert row["gmail_message_id"] == "msg-unknown-1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_persistence.py -v`
Expected: FAIL - `TypeError: insert_transaction() got an unexpected keyword argument 'bank'`, `AttributeError: module 'app.ingestion.persistence' has no attribute 'resolve_unknown'`, etc.

- [ ] **Step 3: Implement the persistence changes**

In `app/ingestion/persistence.py`, replace `insert_transaction`, `insert_unknown`, and `clear_unknown` with:

```python
async def insert_transaction(
    db, message: EmailMessage, transaction: Transaction, category: str, category_source: str,
    bank: str | None = None,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO transactions (
            transaction_id, transaction_type, direction, status, occurred_at, amount, fee,
            available_balance, counterparty, description, category, category_source,
            bank, parser_version, parse_status, parse_confidence, warnings_json,
            raw_fields_json, gmail_message_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction.transaction_id,
            transaction.transaction_type,
            transaction.direction,
            transaction.status,
            transaction.occurred_at,
            transaction.amount,
            transaction.fee,
            transaction.available_balance,
            transaction.counterparty,
            transaction.description,
            category,
            category_source,
            bank,
            "1.0",
            transaction.parse_status,
            transaction.parse_confidence,
            json.dumps(transaction.parse_warnings, ensure_ascii=False),
            json.dumps(transaction.raw_fields, ensure_ascii=False),
            message.gmail_message_id,
        ),
    )
    logger.info(f"Inserted transaction for message {message.gmail_message_id}")
    return cursor.lastrowid


async def insert_unknown(db, message: EmailMessage, transaction: Transaction | None) -> None:
    raw_fields = transaction.raw_fields if transaction else {}
    amount = transaction.amount if transaction else None
    warnings = transaction.parse_warnings if transaction else []
    transaction_code = extract_transaction_code(raw_fields)

    await db.execute(
        """
        INSERT OR IGNORE INTO unknown_patterns (
            subject, sender, transaction_code, amount, warnings_json,
            raw_fields_json, parser_version, gmail_message_id, received_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message.subject,
            message.sender,
            transaction_code,
            amount,
            json.dumps(warnings, ensure_ascii=False),
            json.dumps(raw_fields, ensure_ascii=False),
            "1.0",
            message.gmail_message_id,
            message.received_at.isoformat(),
        ),
    )
    logger.warning(f"Could not parse message {message.gmail_message_id} ({message.subject!r}); logged as unknown")


async def resolve_unknown(db, unknown_id: int, transaction_id: int) -> None:
    """Mark an unknown-pattern row resolved, linking it to the transaction it became.

    Never deletes the row - resolved rows stay as permanent ingestion history.
    """
    await db.execute(
        """
        UPDATE unknown_patterns
        SET status = 'resolved', resolved_transaction_id = ?, resolved_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (transaction_id, unknown_id),
    )


async def resolve_unknown_by_message(db, gmail_message_id: str, transaction_id: int) -> None:
    """Same as resolve_unknown, but looked up by gmail_message_id (used by the main
    ingestion loop, which doesn't have the unknown_patterns row's id at hand).
    No-op if no pending row matches.
    """
    await db.execute(
        """
        UPDATE unknown_patterns
        SET status = 'resolved', resolved_transaction_id = ?, resolved_at = CURRENT_TIMESTAMP
        WHERE gmail_message_id = ? AND status = 'pending'
        """,
        (transaction_id, gmail_message_id),
    )


async def insert_manual_transaction(
    db,
    gmail_message_id: str,
    bank: str | None,
    transaction_type: str,
    direction: str,
    status: str,
    occurred_at: str,
    amount: float,
    category: str,
    fee: float = 0.0,
    available_balance: float | None = None,
    counterparty: str | None = None,
    description: str | None = None,
) -> int:
    """Insert a transaction created by manually promoting an unknown-pattern row."""
    cursor = await db.execute(
        """
        INSERT INTO transactions (
            transaction_type, direction, status, occurred_at, amount, fee,
            available_balance, counterparty, description, category, category_source,
            bank, parser_version, parse_status, parse_confidence, warnings_json,
            raw_fields_json, gmail_message_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, '1.0', 'complete', 1.0, '[]', '{}', ?)
        """,
        (
            transaction_type, direction, status, occurred_at, amount, fee,
            available_balance, counterparty, description, category,
            bank, gmail_message_id,
        ),
    )
    logger.info(f"Manually promoted unknown pattern to transaction for message {gmail_message_id}")
    return cursor.lastrowid
```

Remove the old `clear_unknown` function entirely (it's replaced by `resolve_unknown_by_message`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_persistence.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/persistence.py tests/test_persistence.py
git commit -m "feat(ingestion): resolve unknown patterns in place instead of deleting, add manual promotion"
```

---

### Task 4: Wire `bank` + resolve-in-place into the main ingestion loop

**Files:**
- Modify: `app/ingestion/service.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Consumes: `persistence.insert_transaction(..., bank=...)` (Task 3), `persistence.resolve_unknown_by_message` (Task 3), `registry.identify_bank(sender)` (Task 2).

- [ ] **Step 1: Update `run_ingestion` to compute and pass `bank`, and resolve instead of clear**

In `app/ingestion/service.py`, replace the insert/clear block inside the `for message in messages:` loop:

```python
                category, category_source = await engine.categorize(
                    db, persistence.transaction_to_dict(transaction)
                )
                bank = registry.identify_bank(message.sender)
                transaction_id = await persistence.insert_transaction(
                    db, message, transaction, category, category_source, bank=bank
                )
                await persistence.resolve_unknown_by_message(db, message.gmail_message_id, transaction_id)
                await db.commit()
                inserted += 1
```

- [ ] **Step 2: Update the `FakeRegistry` test double to support `identify_bank`**

In `tests/test_service.py`, `run_ingestion` now calls `registry.identify_bank(...)` on every insert path, so the existing `FakeRegistry` test double needs the method. Update it:

```python
class FakeRegistry:
    def __init__(self, transaction_by_sender):
        self._transaction_by_sender = transaction_by_sender

    def parse(self, email_text, sender, subject=""):
        return self._transaction_by_sender.get(sender)

    def identify_bank(self, sender):
        return None
```

- [ ] **Step 3: Fix the existing test that asserted hard deletion**

`test_successful_ingestion_clears_existing_unknown` currently asserts the unknown row is gone after a later successful parse. Under the new resolve-in-place behavior it should still exist, marked resolved. Replace that test:

```python
@pytest.mark.asyncio
async def test_successful_ingestion_resolves_existing_unknown(temp_db):
    message = _make_message("msg-unknown-fixed")
    failing_reader = FakeReader([message])
    failing_registry = FakeRegistry({})

    await run_ingestion("query", reader=failing_reader, registry=failing_registry)

    fixed_reader = FakeReader([message])
    fixed_registry = FakeRegistry({message.sender: _make_transaction()})
    summary = await run_ingestion("query", reader=fixed_reader, registry=fixed_registry)

    assert summary == {"emails_checked": 1, "inserted": 1, "duplicates": 0, "failed": 0}

    db = await database.get_connection()
    cursor = await db.execute(
        "SELECT status, resolved_transaction_id FROM unknown_patterns WHERE gmail_message_id = ?",
        (message.gmail_message_id,),
    )
    unknown_row = await cursor.fetchone()
    cursor = await db.execute("SELECT id FROM transactions WHERE gmail_message_id = ?", (message.gmail_message_id,))
    transaction_row = await cursor.fetchone()
    await db.close()

    assert unknown_row["status"] == "resolved"
    assert unknown_row["resolved_transaction_id"] == transaction_row["id"]
```

- [ ] **Step 4: Run the full service test file**

Run: `pytest tests/test_service.py -v`
Expected: PASS (all tests, including the replaced one)

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/service.py tests/test_service.py
git commit -m "feat(ingestion): record bank on insert and resolve unknown patterns instead of deleting"
```

---

### Task 5: Wire `bank` + resolve-in-place into the reparse flow

**Files:**
- Modify: `app/ingestion/reparse.py`
- Modify: `app/storage/queries.py`
- Test: `tests/test_reparse.py` (new)

**Interfaces:**
- Produces: `queries.get_transaction_id_by_gmail_message_id(db, gmail_message_id: str) -> int | None` (new)
- Consumes: `persistence.resolve_unknown` (Task 3), `registry.identify_bank` (Task 2)

- [ ] **Step 1: Add the lookup query**

In `app/storage/queries.py`, add near the other transaction queries:

```python
async def get_transaction_id_by_gmail_message_id(db: aiosqlite.Connection, gmail_message_id: str) -> int | None:
    cursor = await db.execute("SELECT id FROM transactions WHERE gmail_message_id = ?", (gmail_message_id,))
    row = await cursor.fetchone()
    await cursor.close()
    return row["id"] if row else None
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_reparse.py`:

```python
"""Tests for app.ingestion.reparse - re-running the parser against an existing email."""

from datetime import datetime

import pytest

from app.gmail import EmailMessage
from app.ingestion import persistence, reparse
from app.parsers.base import Transaction
from app.parsers.registry import ParserRegistry


def _message(message_id="msg-unknown-1", sender="notify@kasikornbank.com"):
    return EmailMessage(
        gmail_message_id=message_id,
        gmail_thread_id=f"thread-{message_id}",
        sender=sender,
        subject="K PLUS: Transfer Successful",
        received_at=datetime(2025, 1, 26, 14, 32),
        body_text="Transaction Date: 26/01/2025\nAmount: 100.00 THB",
    )


def _transaction():
    return Transaction(
        transaction_type="bank_transfer",
        direction="out",
        status="success",
        occurred_at="2025-01-26T14:32",
        amount=100.0,
        parse_status="complete",
        parse_confidence=1.0,
        raw_fields={"Amount": "100.00 THB"},
    )


class _FakeGmailClient:
    def __init__(self, message):
        self._message = message

    def get_message(self, message_id):
        return self._message


class _FakeRegistry:
    def __init__(self, transaction):
        self._transaction = transaction

    def parse(self, email_text, sender, subject=""):
        return self._transaction

    def identify_bank(self, sender):
        return "KBank"


class _FakeEngine:
    async def categorize(self, db, transaction_dict, manual_override=None):
        return "Uncategorized", "uncategorized"


@pytest.mark.asyncio
async def test_reparse_unknown_resolves_instead_of_deleting(db_connection):
    message = _message()
    await persistence.insert_unknown(db_connection, message, None)
    await db_connection.commit()
    cursor = await db_connection.execute(
        "SELECT id FROM unknown_patterns WHERE gmail_message_id = ?", (message.gmail_message_id,)
    )
    unknown_id = (await cursor.fetchone())["id"]
    await cursor.close()

    result = await reparse.reparse_unknown(
        db_connection,
        unknown_id,
        gmail_client=_FakeGmailClient(message),
        registry=_FakeRegistry(_transaction()),
        engine=_FakeEngine(),
    )

    assert result["status"] == "parsed"

    cursor = await db_connection.execute(
        "SELECT status, resolved_transaction_id, bank FROM unknown_patterns u "
        "JOIN transactions t ON t.id = u.resolved_transaction_id WHERE u.id = ?",
        (unknown_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row["status"] == "resolved"
    assert row["resolved_transaction_id"] is not None
    assert row["bank"] == "KBank"

    cursor = await db_connection.execute("SELECT COUNT(*) AS n FROM unknown_patterns WHERE id = ?", (unknown_id,))
    still_exists = (await cursor.fetchone())["n"]
    await cursor.close()
    assert still_exists == 1


@pytest.mark.asyncio
async def test_reparse_transaction_updates_bank(db_connection):
    message = _message(message_id="msg-existing")
    transaction_id = await persistence.insert_transaction(
        db_connection, message, _transaction(), "Uncategorized", "uncategorized", bank=None
    )
    await db_connection.commit()

    result = await reparse.reparse_transaction(
        db_connection,
        transaction_id,
        gmail_client=_FakeGmailClient(message),
        registry=_FakeRegistry(_transaction()),
        engine=_FakeEngine(),
    )

    assert result["status"] == "parsed"
    cursor = await db_connection.execute("SELECT bank FROM transactions WHERE id = ?", (transaction_id,))
    row = await cursor.fetchone()
    await cursor.close()
    assert row["bank"] == "KBank"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_reparse.py -v`
Expected: FAIL (bank stays NULL, unknown_patterns row gets deleted instead of resolved)

- [ ] **Step 4: Implement the reparse.py changes**

In `app/ingestion/reparse.py`, update `reparse_transaction`'s UPDATE statement to also set `bank`:

```python
    manual_override = row["category"] if row["category_source"] == "manual" else None
    category, category_source = await engine.categorize(
        db, persistence.transaction_to_dict(transaction), manual_override=manual_override
    )

    await db.execute(
        """
        UPDATE transactions SET
            transaction_type = ?, direction = ?, status = ?, occurred_at = ?, amount = ?, fee = ?,
            available_balance = ?, counterparty = ?, description = ?, category = ?, category_source = ?,
            bank = ?, parse_status = ?, parse_confidence = ?, warnings_json = ?, raw_fields_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            transaction.transaction_type,
            transaction.direction,
            transaction.status,
            transaction.occurred_at,
            transaction.amount,
            transaction.fee,
            transaction.available_balance,
            transaction.counterparty,
            transaction.description,
            category,
            category_source,
            registry.identify_bank(message.sender),
            transaction.parse_status,
            transaction.parse_confidence,
            json.dumps(transaction.parse_warnings, ensure_ascii=False),
            json.dumps(transaction.raw_fields, ensure_ascii=False),
            transaction_id,
        ),
    )
    await db.commit()
    logger.info(f"Reparsed transaction {transaction_id} successfully")
    return {"status": "parsed", "transaction_id": transaction_id}
```

Update `reparse_unknown`'s success tail (everything from the `already_ingested` check onward) to resolve instead of delete:

```python
    if await persistence.already_ingested(db, row["gmail_message_id"]):
        logger.info(f"Reparse of unknown pattern {unknown_id} now succeeds but transaction already exists")
        transaction_id = await queries.get_transaction_id_by_gmail_message_id(db, row["gmail_message_id"])
    else:
        bank = registry.identify_bank(message.sender)
        category, category_source = await engine.categorize(db, persistence.transaction_to_dict(transaction))
        transaction_id = await persistence.insert_transaction(db, message, transaction, category, category_source, bank=bank)

    await persistence.resolve_unknown(db, unknown_id, transaction_id)
    await db.commit()
    logger.info(f"Reparsed unknown pattern {unknown_id} -> transaction {transaction_id}")
    return {"status": "parsed", "transaction_id": transaction_id}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_reparse.py -v`
Expected: PASS

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `pytest tests/ -v`
Expected: PASS (in particular `tests/test_routes.py`'s reparse tests, which monkeypatch `reparse_transaction`/`reparse_unknown` wholesale and are unaffected by internal changes)

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/reparse.py app/storage/queries.py tests/test_reparse.py
git commit -m "feat(ingestion): record bank and resolve-in-place on reparse"
```

---

### Task 6: Dashboard query — spend by bank + pie chart segment math

**Files:**
- Modify: `app/storage/queries.py`
- Test: `tests/test_queries.py` (new)

**Interfaces:**
- Produces:
  - `queries.get_expense_by_bank(db, days: int = 7) -> list[dict]` — each dict has `bank: str`, `total: float`, ordered by `total` descending, `NULL` bank grouped as `"Unknown"`.
  - `queries.BANK_COLORS: dict[str, str]` — fixed categorical hex per known bank label plus `"Unknown"`.
  - `queries.build_pie_segments(rows: list[dict]) -> list[dict]` — each dict has `bank`, `total`, `pct`, `color`, `dasharray`, `dashoffset` (SVG donut stroke values, out of a circumference of 100).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_queries.py`:

```python
"""Tests for app.storage.queries - dashboard aggregation helpers."""

from datetime import date

import pytest

from app.storage import queries


@pytest.mark.asyncio
async def test_get_expense_by_bank_groups_and_sums(db_connection):
    today = date.today().isoformat()
    await db_connection.execute(
        """
        INSERT INTO transactions (
            transaction_type, direction, status, occurred_at, amount, category_source,
            parse_status, bank, gmail_message_id
        ) VALUES
            ('bank_transfer', 'out', 'success', ?, 100.0, 'rule', 'complete', 'KBank', 'm1'),
            ('bank_transfer', 'out', 'success', ?, 50.0, 'rule', 'complete', 'KBank', 'm2'),
            ('bank_transfer', 'out', 'success', ?, 75.0, 'rule', 'complete', 'SCB', 'm3'),
            ('bank_transfer', 'out', 'success', ?, 25.0, 'rule', 'complete', NULL, 'm4'),
            ('bank_transfer', 'in', 'success', ?, 1000.0, 'rule', 'complete', 'KBank', 'm5')
        """,
        (today, today, today, today, today),
    )
    await db_connection.commit()

    rows = await queries.get_expense_by_bank(db_connection, days=7)

    by_bank = {r["bank"]: r["total"] for r in rows}
    assert by_bank == {"KBank": 150.0, "SCB": 75.0, "Unknown": 25.0}


def test_build_pie_segments_percentages_sum_to_total():
    rows = [{"bank": "KBank", "total": 150.0}, {"bank": "SCB", "total": 50.0}]
    segments = queries.build_pie_segments(rows)

    assert [s["bank"] for s in segments] == ["KBank", "SCB"]
    assert segments[0]["pct"] == pytest.approx(75.0)
    assert segments[1]["pct"] == pytest.approx(25.0)
    assert segments[0]["dashoffset"] == "-0.0000"
    assert segments[0]["color"] == queries.BANK_COLORS["KBank"]


def test_build_pie_segments_handles_zero_total():
    assert queries.build_pie_segments([]) == []
    segments = queries.build_pie_segments([{"bank": "KBank", "total": 0.0}])
    assert segments[0]["pct"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -v`
Expected: FAIL - `AttributeError: module 'app.storage.queries' has no attribute 'get_expense_by_bank'`

- [ ] **Step 3: Implement `get_expense_by_bank` and `build_pie_segments`**

In `app/storage/queries.py`, add near `get_expense_by_day`:

```python
async def get_expense_by_bank(db: aiosqlite.Connection, days: int = 7) -> list[dict]:
    """Return total 'out' spend per bank over the last `days`, NULL bank as 'Unknown'."""
    days = days if days in (7, 14, 30) else 7
    end_day = date.today()
    start_day = end_day - timedelta(days=days - 1)

    cursor = await db.execute(
        """
        SELECT COALESCE(bank, 'Unknown') AS bank, COALESCE(SUM(amount), 0) AS total
        FROM transactions
        WHERE direction = 'out'
          AND parse_status != 'ignored'
          AND date(occurred_at) BETWEEN ? AND ?
        GROUP BY COALESCE(bank, 'Unknown')
        ORDER BY total DESC
        """,
        (start_day.isoformat(), end_day.isoformat()),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [{"bank": r["bank"], "total": float(r["total"] or 0)} for r in rows]


BANK_COLORS = {
    "KBank": "#6366F1",
    "Krungsri": "#14B8A6",
    "LH Bank": "#8B5CF6",
    "SCB": "#0EA5E9",
    "Unknown": "#A3A3A3",
}
_DONUT_CIRCUMFERENCE = 100.0  # SVG circle radius is chosen (15.9155) so its circumference is ~100


def build_pie_segments(rows: list[dict]) -> list[dict]:
    """Turn get_expense_by_bank rows into SVG donut stroke-dasharray/dashoffset segments."""
    total = sum(r["total"] for r in rows)
    segments = []
    offset = 0.0
    for r in rows:
        pct = (r["total"] / total * 100) if total else 0.0
        segments.append({
            "bank": r["bank"],
            "total": r["total"],
            "pct": pct,
            "color": BANK_COLORS.get(r["bank"], "#A3A3A3"),
            "dasharray": f"{pct:.4f} {_DONUT_CIRCUMFERENCE - pct:.4f}",
            "dashoffset": f"{-offset:.4f}",
        })
        offset += pct
    return segments
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -v`
Expected: PASS

- [ ] **Step 5: Validate the pie chart palette for colorblind-safety**

Invoke the `dataviz` skill (`Skill` tool, `skill: "dataviz"`) to locate `scripts/validate_palette.js` relative to its base directory, then run:

```bash
node "<dataviz-skill-base-dir>/scripts/validate_palette.js" "#6366F1,#14B8A6,#8B5CF6,#0EA5E9,#A3A3A3" --mode light
```

If any check FAILs, adjust the failing hex value(s) in `BANK_COLORS` (keep the rest fixed) and re-run until all pass, then re-run `pytest tests/test_queries.py -v` to confirm `test_build_pie_segments_percentages_sum_to_total`'s hardcoded `queries.BANK_COLORS["KBank"]` reference still matches (it reads the constant directly, so no test edit is needed even if the hex changes).

- [ ] **Step 6: Commit**

```bash
git add app/storage/queries.py tests/test_queries.py
git commit -m "feat(dashboard): add expense-by-bank query and pie chart segment math"
```

---

### Task 7: Next scheduled sync time

**Files:**
- Modify: `app/ingestion/scheduler.py`
- Modify: `tests/test_scheduler.py`

**Interfaces:**
- Produces: `scheduler.next_scheduled_run(settings: Settings, now: datetime | None = None) -> datetime | None` — the next entry in `settings.SCHEDULE` strictly after `now` (defaults to current time) in `settings.TIMEZONE`, wrapping to tomorrow's first entry if none remain today; `None` if `SCHEDULE` is empty.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scheduler.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo


def test_next_scheduled_run_returns_next_slot_today():
    settings = _settings()
    now = datetime(2026, 7, 28, 6, 0, tzinfo=ZoneInfo("Asia/Bangkok"))
    result = scheduler.next_scheduled_run(settings, now=now)
    assert result == datetime(2026, 7, 28, 10, 0, tzinfo=ZoneInfo("Asia/Bangkok"))


def test_next_scheduled_run_wraps_to_tomorrow():
    settings = _settings()
    now = datetime(2026, 7, 28, 23, 0, tzinfo=ZoneInfo("Asia/Bangkok"))
    result = scheduler.next_scheduled_run(settings, now=now)
    assert result == datetime(2026, 7, 29, 5, 0, tzinfo=ZoneInfo("Asia/Bangkok"))


def test_next_scheduled_run_returns_none_for_empty_schedule():
    settings = _settings(SCHEDULE=[])
    assert scheduler.next_scheduled_run(settings) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scheduler.py -v`
Expected: FAIL - `AttributeError: module 'app.ingestion.scheduler' has no attribute 'next_scheduled_run'`

- [ ] **Step 3: Implement `next_scheduled_run`**

In `app/ingestion/scheduler.py`, add the import and function:

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
```

(add alongside the existing `import asyncio` / `import logging` block)

```python
def next_scheduled_run(settings: Settings, now: datetime | None = None) -> datetime | None:
    """The next SCHEDULE slot strictly after `now`, wrapping to tomorrow if needed."""
    if not settings.SCHEDULE:
        return None

    tz = ZoneInfo(settings.TIMEZONE)
    now = now.astimezone(tz) if now else datetime.now(tz)
    today = now.date()

    todays_slots = []
    for time_str in settings.SCHEDULE:
        hour, minute = (int(part) for part in time_str.split(":"))
        todays_slots.append(datetime(today.year, today.month, today.day, hour, minute, tzinfo=tz))

    upcoming_today = [slot for slot in todays_slots if slot > now]
    if upcoming_today:
        return min(upcoming_today)

    tomorrow = today + timedelta(days=1)
    hour, minute = (int(part) for part in settings.SCHEDULE[0].split(":"))
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=tz)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): add next_scheduled_run helper"
```

---

### Task 8: Dashboard route + template — pie chart, spend total, run descriptions, retry wording

**Files:**
- Modify: `app/web/routes/dashboard.py`
- Modify: `app/web/templates/dashboard.html`

**Interfaces:**
- Consumes: `queries.get_expense_by_bank`, `queries.build_pie_segments` (Task 6), `scheduler.next_scheduled_run` (Task 7).

- [ ] **Step 1: Update the dashboard route**

Replace `app/web/routes/dashboard.py`:

```python
"""Dashboard route - renders the stats overview page."""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, Query, Request

from app.config import Settings, get_settings
from app.ingestion.scheduler import next_scheduled_run
from app.storage import queries
from app.web.deps import get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


@router.get("/")
async def dashboard(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    days: int = Query(7),
    settings: Settings = Depends(get_settings),
):
    days = days if days in (7, 14, 30) else 7
    stats = await queries.get_dashboard_stats(db)
    expense_days = await queries.get_expense_by_day(db, days=days)
    max_expense = max((item["total"] for item in expense_days), default=0)
    total_expense = sum(item["total"] for item in expense_days)
    expense_by_bank = await queries.get_expense_by_bank(db, days=days)
    pie_segments = queries.build_pie_segments(expense_by_bank)
    runs, _ = await queries.list_runs(db, page=1, page_size=5)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "expense_days": expense_days,
            "expense_window_days": days,
            "max_expense": max_expense,
            "total_expense": total_expense,
            "pie_segments": pie_segments,
            "runs": runs,
            "next_sync": next_scheduled_run(settings),
        },
    )
```

- [ ] **Step 2: Replace the Expense/Recent-Runs row with a four-column Expense/Pie/Recent-Runs row**

In `app/web/templates/dashboard.html`, replace the entire block starting at `<div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-8">` and ending at its matching closing `</div>` (immediately before the `<style>` block) with:

```html
<div class="grid grid-cols-1 lg:grid-cols-4 gap-4 mb-8">
    <div class="card lg:col-span-2">
        <div class="flex items-center justify-between gap-3 mb-1">
            <h2 class="text-sm font-medium text-neutral-900">Expense</h2>
            <div class="flex rounded-lg border border-neutral-300 overflow-hidden text-sm">
                {% for option in [7, 14, 30] %}
                <a
                    href="/?days={{ option }}"
                    class="px-3 py-1.5 transition-colors duration-150 {% if expense_window_days == option %}bg-neutral-900 text-white{% else %}text-neutral-700 hover:bg-neutral-100{% endif %}"
                >{{ option }}</a>
                {% endfor %}
            </div>
        </div>
        <div class="text-xs text-neutral-500 mb-4">Total: ฿{{ '{:,.2f}'.format(total_expense) }} over last {{ expense_window_days }} days</div>

        <div class="h-64 flex items-end gap-2 border-b border-neutral-200">
            {% for item in expense_days %}
            {% set pct = (item.total / max_expense * 100) if max_expense else 0 %}
            <div class="flex-1 min-w-0 h-full flex flex-col justify-end items-center gap-2">
                <div class="text-[11px] text-neutral-500 whitespace-nowrap tabular-nums">฿{{ '{:,.0f}'.format(item.total) }}</div>
                <div
                    class="w-full max-w-10 rounded-t-md bg-red-500 hover:bg-red-600 transition-colors duration-150 motion-safe:animate-[growUp_0.4s_ease-out_both]"
                    style="height: {{ pct if pct > 3 else (3 if item.total > 0 else 0) }}%;"
                    title="{{ item.day }}: ฿{{ '{:,.2f}'.format(item.total) }}"
                ></div>
            </div>
            {% endfor %}
        </div>
        <div class="mt-2 flex gap-2 text-[11px] text-neutral-500">
            {% for item in expense_days %}
            <div class="flex-1 min-w-0 text-center truncate">{{ item.day[5:] }}</div>
            {% endfor %}
        </div>
    </div>

    <div class="card">
        <h2 class="text-sm font-medium text-neutral-900 mb-3">Spend by Bank</h2>
        {% if pie_segments %}
        <div class="flex flex-col items-center gap-4">
            <svg viewBox="0 0 36 36" class="w-32 h-32 -rotate-90">
                <circle cx="18" cy="18" r="15.9155" fill="none" stroke="#e5e5e5" stroke-width="4"></circle>
                {% for seg in pie_segments %}
                <circle
                    cx="18" cy="18" r="15.9155" fill="none" stroke="{{ seg.color }}" stroke-width="4"
                    stroke-dasharray="{{ seg.dasharray }}" stroke-dashoffset="{{ seg.dashoffset }}"
                >
                    <title>{{ seg.bank }}: ฿{{ '{:,.2f}'.format(seg.total) }} ({{ '%.1f'|format(seg.pct) }}%)</title>
                </circle>
                {% endfor %}
            </svg>
            <ul class="w-full space-y-1.5 text-xs">
                {% for seg in pie_segments %}
                <li class="flex items-center justify-between gap-2">
                    <span class="flex items-center gap-1.5 min-w-0">
                        <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background-color: {{ seg.color }}"></span>
                        <span class="truncate text-neutral-700">{{ seg.bank }}</span>
                    </span>
                    <span class="text-neutral-500 tabular-nums shrink-0">฿{{ '{:,.0f}'.format(seg.total) }} ({{ '%.0f'|format(seg.pct) }}%)</span>
                </li>
                {% endfor %}
            </ul>
        </div>
        {% else %}
        <p class="text-sm text-neutral-500">No expense data yet.</p>
        {% endif %}
    </div>

    <div class="card">
        <h2 class="text-sm font-medium text-neutral-900 mb-3">Recent Runs</h2>
        {% if runs %}
        <div class="space-y-3">
            {% for run in runs %}
            <div class="text-sm border-b border-neutral-100 pb-2 last:border-0 last:pb-0">
                <div class="flex items-center justify-between gap-2">
                    <span class="text-neutral-500 truncate">{{ run.run_at | thai_date if run.run_at else '' }}</span>
                    {% if run.failed and run.failed > 0 %}
                    <button
                        class="px-1.5 py-0.5 rounded border border-red-300 text-red-600 text-xs hover:bg-red-50 transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-red-400 shrink-0"
                        hx-post="/api/ingestion/retry/{{ run.id }}"
                        hx-swap="none"
                        hx-trigger="click"
                        title="Retry failed items"
                    >Retry</button>
                    {% endif %}
                </div>
                {% if run.failed and run.failed > 0 %}
                <div class="text-amber-700 text-xs mt-1">⚠️ Completed with {{ run.failed }} failure{{ 's' if run.failed != 1 else '' }} — {{ run.emails_checked or 0 }} scanned, {{ run.inserted or 0 }} saved</div>
                <div class="text-neutral-400 text-xs">No automatic retry — click Retry to reprocess</div>
                {% else %}
                <div class="text-green-700 text-xs mt-1">✅ {{ run.emails_checked or 0 }} scanned, {{ run.inserted or 0 }} saved{% if run.duplicates %}, {{ run.duplicates }} duplicate{{ 's' if run.duplicates != 1 else '' }}{% endif %}</div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
        {% else %}
        <p class="text-sm text-neutral-500">No runs yet.</p>
        {% endif %}
        {% if next_sync %}
        <div class="text-xs text-neutral-400 mt-3 pt-3 border-t border-neutral-100">Next scheduled sync: {{ next_sync.strftime('%H:%M') }}</div>
        {% endif %}
    </div>
</div>
```

- [ ] **Step 3: Verify the page loads**

Run: `pytest tests/test_routes.py::test_dashboard_page_loads -v`
Expected: PASS

- [ ] **Step 4: Manual visual check**

Use the `run` skill to start the app and open `/` in a browser. Confirm: the pie chart renders with a legend, the total-spend line shows next to the day toggle, Recent Runs shows plain-language status text, and (if any run has failures) the "No automatic retry" line and Retry button both appear. Check every check from the `dataviz` skill's step 7 (label collisions, geometry, overflow) by eye.

- [ ] **Step 5: Commit**

```bash
git add app/web/routes/dashboard.py app/web/templates/dashboard.html
git commit -m "feat(dashboard): add spend-by-bank pie chart, expense total, run status descriptions"
```

---

### Task 9: Shared modal infrastructure

**Files:**
- Modify: `app/web/templates/base.html`
- Modify: `app/web/static/app.js`

**Interfaces:**
- Produces: `#modal-root` container in every page; `closeModal()` global JS function that empties it. Consumed by Tasks 10 and 11.

- [ ] **Step 1: Add the modal root container to base.html**

In `app/web/templates/base.html`, add the container right after the toast container:

```html
    <div id="toast-container" aria-live="polite" class="fixed top-4 right-4 z-50 flex flex-col gap-2"></div>

    <div id="modal-root"></div>

    <main class="max-w-6xl mx-auto px-4 py-8">
        {% block content %}{% endblock %}
    </main>
```

- [ ] **Step 2: Add `closeModal` and the Escape-key handler to app.js**

Replace `app/web/static/app.js`:

```js
// HTMX configuration for the Financial Email Tracker web UI.

if (window.htmx) {
    htmx.config.defaultSwapStyle = "outerHTML";
}

function closeModal() {
    var root = document.getElementById("modal-root");
    if (root) root.innerHTML = "";
}

document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeModal();
});
```

- [ ] **Step 3: Manual check**

There's no automated test for this (pure markup/JS scaffolding with nothing to assert against yet - it's exercised by Tasks 10/11). Confirm via `pytest tests/test_routes.py -v` that nothing broke (every page still extends `base.html` and renders 200).

- [ ] **Step 4: Commit**

```bash
git add app/web/templates/base.html app/web/static/app.js
git commit -m "feat(web): add shared modal-root overlay and closeModal helper"
```

---

### Task 10: Transaction detail — modal, raw email, and the apiCall bugfix

**Files:**
- Create: `app/web/templates/partials/transaction_detail_body.html`
- Create: `app/web/templates/partials/transaction_detail_modal.html`
- Create: `app/web/templates/partials/raw_email.html`
- Modify: `app/web/templates/transaction_detail.html`
- Modify: `app/web/templates/transactions.html`
- Modify: `app/web/routes/transactions.py`
- Modify: `tests/test_routes.py`

**Interfaces:**
- Produces: `GET /transactions/{id}/modal` (page route), `GET /api/transactions/{id}/raw-email` (API route, renders `partials/raw_email.html` with `email` dict or `error` string).

**Note on the existing bug:** `transaction_detail.html` currently has its own hardcoded Actions card and an inline `<script>` block calling `apiCall(...)`, which is never defined anywhere - those buttons are dead. `app/web/templates/partials/transaction_actions.html` already exists, is already used by the reparse API route's HTMX response, and already implements the same four actions correctly via `hx-patch`/`hx-post`/`hx-delete`. This task deletes the broken duplicate and reuses that partial instead - no new JS function needed.

- [ ] **Step 1: Extract the shared body partial**

Create `app/web/templates/partials/transaction_detail_body.html` with the content that used to live directly in `transaction_detail.html`, minus the broken Actions card/script, plus the new raw-email section:

```html
{% if t.warnings %}
<div class="mb-6 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
    <div class="text-sm font-medium text-amber-800 mb-1">Warnings</div>
    <ul class="list-disc list-inside text-sm text-amber-700">
        {% for w in t.warnings %}
        <li>{{ w }}</li>
        {% endfor %}
    </ul>
</div>
{% endif %}

<div class="card mb-6">
    <dl class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-4">
        <div>
            <dt class="text-xs text-neutral-500">Transaction Type</dt>
            <dd class="text-sm text-neutral-900 mt-0.5">{{ t.transaction_type or '-' }}</dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Direction</dt>
            <dd class="text-sm text-neutral-900 mt-0.5">{{ t.direction }}</dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Status</dt>
            <dd class="text-sm text-neutral-900 mt-0.5">{{ t.status }}</dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Occurred At</dt>
            <dd class="text-sm text-neutral-900 mt-0.5">{{ t.occurred_at | thai_date if t.occurred_at else '-' }}</dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Amount</dt>
            <dd class="text-sm mt-0.5 font-medium tabular-nums {% if t.direction == 'in' %}text-green-600{% elif t.direction == 'out' %}text-red-600{% else %}text-neutral-900{% endif %}">฿{{ '{:,.2f}'.format(t.amount) }}</dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Fee</dt>
            <dd class="text-sm text-neutral-900 mt-0.5 tabular-nums">{% if t.fee is not none %}฿{{ '{:,.2f}'.format(t.fee) }}{% else %}-{% endif %}</dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Available Balance</dt>
            <dd class="text-sm text-neutral-900 mt-0.5 tabular-nums">{% if t.available_balance is not none %}฿{{ '{:,.2f}'.format(t.available_balance) }}{% else %}-{% endif %}</dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Bank</dt>
            <dd class="text-sm text-neutral-900 mt-0.5">{{ t.bank or '-' }}</dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Counterparty</dt>
            <dd class="text-sm text-neutral-900 mt-0.5">{% if t.counterparty and t.counterparty != '-' %}<span lang="th" class="font-thai">{{ t.counterparty }}</span>{% else %}-{% endif %}</dd>
        </div>
        <div class="sm:col-span-2">
            <dt class="text-xs text-neutral-500">Description</dt>
            <dd class="text-sm text-neutral-900 mt-0.5">{% if t.description and t.description != '-' %}<span lang="th" class="font-thai">{{ t.description }}</span>{% else %}-{% endif %}</dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Category</dt>
            <dd class="text-sm mt-0.5">
                <span
                    class="badge {% if t.category and t.category != 'Uncategorized' %}badge-income{% else %}badge-uncategorized{% endif %} cursor-pointer hover:opacity-80 transition-opacity duration-150"
                    hx-get="/transactions/{{ t.id }}/edit-category"
                    hx-target="closest dd"
                    hx-swap="innerHTML"
                    hx-trigger="click"
                    title="Click to edit category"
                >{{ t.category or 'Uncategorized' }}</span>
            </dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Category Source</dt>
            <dd class="text-sm text-neutral-900 mt-0.5">{{ t.category_source or '-' }}</dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Parse Status</dt>
            <dd class="text-sm text-neutral-900 mt-0.5">{{ t.parse_status }}</dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Parse Confidence</dt>
            <dd class="text-sm text-neutral-900 mt-0.5 tabular-nums">{{ t.parse_confidence if t.parse_confidence is not none else '-' }}</dd>
        </div>
        <div>
            <dt class="text-xs text-neutral-500">Parser Version</dt>
            <dd class="text-sm text-neutral-900 mt-0.5">{{ t.parser_version or '-' }}</dd>
        </div>
    </dl>
</div>

{% include "partials/transaction_actions.html" %}

<div class="card mb-6">
    <div class="flex items-center justify-between mb-3">
        <h2 class="text-sm font-medium text-neutral-900">Raw Email</h2>
        <button
            type="button"
            class="px-3 py-1.5 rounded-lg border border-neutral-300 text-xs font-medium hover:bg-neutral-100 transition-colors duration-150"
            hx-get="/api/transactions/{{ t.id }}/raw-email"
            hx-target="#raw-email-{{ t.id }}"
            hx-swap="innerHTML"
        >View Raw Email</button>
    </div>
    <div id="raw-email-{{ t.id }}" class="text-sm text-neutral-500">Click "View Raw Email" to load the original message from Gmail.</div>
</div>

<div class="card">
    <h2 class="text-sm font-medium text-neutral-900 mb-3">Raw Fields</h2>
    {% if t.raw_fields %}
    <table class="min-w-full text-sm zebra-table">
        <tbody>
            {% for key, value in t.raw_fields.items() %}
            <tr class="border-b border-neutral-100">
                <td class="px-3 py-2 font-medium text-neutral-500 whitespace-nowrap">{{ key }}</td>
                <td class="px-3 py-2 text-neutral-900 break-all">{% if value %}<span lang="th" class="font-thai">{{ value }}</span>{% else %}-{% endif %}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    {% else %}
    <p class="text-sm text-neutral-500">No raw fields.</p>
    {% endif %}
</div>

<datalist id="categories">
    {% for cat in categories %}
    <option value="{{ cat }}">
    {% endfor %}
</datalist>
```

- [ ] **Step 2: Slim down the full-page template to use the shared partial**

Replace `app/web/templates/transaction_detail.html`:

```html
{% extends "base.html" %}

{% block title %}Transaction #{{ t.id }} - Financial Email Tracker{% endblock %}

{% block content %}
<a href="/transactions" class="text-sm text-neutral-500 hover:text-neutral-900 transition-colors duration-150">&larr; Back to Transactions</a>

<h1 class="text-xl font-semibold text-neutral-900 mt-2 mb-6">Transaction #{{ t.id }}</h1>

{% include "partials/transaction_detail_body.html" %}
{% endblock %}
```

- [ ] **Step 3: Create the modal wrapper**

Create `app/web/templates/partials/transaction_detail_modal.html`:

```html
<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onclick="if (event.target === this) closeModal()">
    <div class="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto p-6 relative">
        <button type="button" class="absolute top-3 right-3 text-neutral-400 hover:text-neutral-700 text-xl leading-none" onclick="closeModal()" aria-label="Close">&times;</button>
        <h2 class="text-lg font-semibold text-neutral-900 mb-4">Transaction #{{ t.id }}</h2>
        {% include "partials/transaction_detail_body.html" %}
    </div>
</div>
```

- [ ] **Step 4: Create the raw-email fragment**

Create `app/web/templates/partials/raw_email.html`:

```html
{% if error %}
<p class="text-sm text-red-600">{{ error }}</p>
{% else %}
<dl class="text-xs text-neutral-500 mb-3 space-y-1">
    <div><span class="font-medium">From:</span> {{ email.sender }}</div>
    <div><span class="font-medium">Subject:</span> {{ email.subject }}</div>
    <div><span class="font-medium">Received:</span> {{ email.received_at }}</div>
</dl>
<pre class="text-xs whitespace-pre-wrap break-words bg-neutral-50 border border-neutral-200 rounded-lg p-3 max-h-96 overflow-y-auto font-mono">{{ email.body_text }}</pre>
{% endif %}
```

- [ ] **Step 5: Add the modal and raw-email routes**

In `app/web/routes/transactions.py`, add (near the other `page_router` routes):

```python
@page_router.get("/transactions/{transaction_id}/modal")
async def transaction_detail_modal(request: Request, transaction_id: int, db: aiosqlite.Connection = Depends(get_db)):
    transaction = await queries.get_transaction(db, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    categories = await queries.list_categories(db)
    return templates.TemplateResponse(request, "partials/transaction_detail_modal.html", {"t": transaction, "categories": categories})
```

And in the `router` (API) section:

```python
@router.get("/transactions/{transaction_id}/raw-email")
async def get_transaction_raw_email(
    transaction_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    gmail_client: GmailClient = Depends(get_gmail_client),
):
    transaction = await queries.get_transaction(db, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    try:
        message = gmail_client.get_message(transaction["gmail_message_id"])
        email = {"sender": message.sender, "subject": message.subject, "received_at": message.received_at, "body_text": message.body_text}
        error = None
    except Exception as e:
        logger.warning(f"Failed to fetch raw email for transaction {transaction_id}: {e}")
        email = None
        error = "Could not load the original email. It may have been deleted, or Gmail access failed."
    return templates.TemplateResponse(request, "partials/raw_email.html", {"email": email, "error": error})
```

- [ ] **Step 6: Change the transactions list row to open the modal**

In `app/web/templates/transactions.html`, replace the Status cell's anchor:

```html
                    <td class="px-4 py-3 relative">
                        {{ item.parse_status }}
                        <button
                            type="button"
                            class="absolute inset-0 w-full h-full cursor-pointer"
                            aria-label="View transaction {{ item.id }}"
                            hx-get="/transactions/{{ item.id }}/modal"
                            hx-target="#modal-root"
                            hx-swap="innerHTML"
                        ></button>
                    </td>
```

- [ ] **Step 7: Write route tests**

In `tests/test_routes.py`, add near the other transaction tests:

```python
@pytest.mark.asyncio
async def test_transaction_detail_modal_route(client, db_connection):
    tx_id = await _insert_transaction(db_connection)
    resp = client.get(f"/transactions/{tx_id}/modal")
    assert resp.status_code == 200
    assert "Transaction #" in resp.text


def test_transaction_detail_modal_404_for_missing(client):
    resp = client.get("/transactions/999999/modal")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_transaction_raw_email_renders_fragment(client, db_connection, monkeypatch):
    tx_id = await _insert_transaction(db_connection)

    class FakeMessage:
        sender = "notify@kasikornbank.com"
        subject = "K PLUS: Transfer Successful"
        received_at = "2026-07-27 10:00:00"
        body_text = "Transaction Date: 27/07/2026\nAmount: 100.00 THB"

    class FakeGmailClient:
        def get_message(self, message_id):
            return FakeMessage()

    app.dependency_overrides[deps.get_gmail_client] = lambda: FakeGmailClient()
    resp = client.get(f"/api/transactions/{tx_id}/raw-email")
    assert resp.status_code == 200
    assert "Transaction Date: 27/07/2026" in resp.text


@pytest.mark.asyncio
async def test_transaction_raw_email_shows_error_on_gmail_failure(client, db_connection):
    tx_id = await _insert_transaction(db_connection)

    class FailingGmailClient:
        def get_message(self, message_id):
            raise RuntimeError("Gmail unreachable")

    app.dependency_overrides[deps.get_gmail_client] = lambda: FailingGmailClient()
    resp = client.get(f"/api/transactions/{tx_id}/raw-email")
    assert resp.status_code == 200
    assert "Could not load the original email" in resp.text
```

Add `from app.web.main import app` and `from app.web import deps` are already imported at the top of `tests/test_routes.py` - no new imports needed for these tests.

- [ ] **Step 8: Run the tests**

Run: `pytest tests/test_routes.py -v`
Expected: PASS

- [ ] **Step 9: Manual check**

Use the `run` skill to start the app, open `/transactions`, click a row, confirm the modal opens (not a page navigation), the category badge/Ignore/Reparse/Delete buttons all work (this is the bugfix - they were previously broken), "View Raw Email" loads the original message, and Escape/backdrop-click closes the modal. Also confirm `/transactions/{id}` still works as a direct link.

- [ ] **Step 10: Commit**

```bash
git add app/web/templates/partials/transaction_detail_body.html \
        app/web/templates/partials/transaction_detail_modal.html \
        app/web/templates/partials/raw_email.html \
        app/web/templates/transaction_detail.html \
        app/web/templates/transactions.html \
        app/web/routes/transactions.py \
        tests/test_routes.py
git commit -m "feat(transactions): modal detail view with raw email, fix dead action buttons"
```

---

### Task 11: Unknown page — raw email popup, promote to transaction, resolved history

**Files:**
- Create: `app/web/templates/partials/unknown_detail_modal.html`
- Create: `app/web/templates/partials/unknown_promoted.html`
- Modify: `app/web/templates/partials/unknown_row.html`
- Modify: `app/web/templates/unknown.html`
- Modify: `app/web/routes/unknown.py`
- Modify: `tests/test_routes.py`

**Interfaces:**
- Produces: `GET /unknown/{id}/modal` (page route), `GET /api/unknown/{id}/raw-email` (reuses `partials/raw_email.html`), `POST /api/unknown/{id}/promote` (form-encoded; 404 if missing, 409 if not pending, 422 on missing required fields via FastAPI's default validation).

- [ ] **Step 1: Create the detail modal**

Create `app/web/templates/partials/unknown_detail_modal.html`:

```html
<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onclick="if (event.target === this) closeModal()">
    <div class="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto p-6 relative">
        <button type="button" class="absolute top-3 right-3 text-neutral-400 hover:text-neutral-700 text-xl leading-none" onclick="closeModal()" aria-label="Close">&times;</button>
        <h2 class="text-lg font-semibold text-neutral-900 mb-1">Unknown Email #{{ item.id }}</h2>
        <p class="text-sm text-neutral-500 mb-4">{{ item.subject or '-' }}</p>

        {% if item.warnings %}
        <div class="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
            <div class="text-sm font-medium text-amber-800 mb-1">Warnings</div>
            <ul class="list-disc list-inside text-sm text-amber-700">
                {% for w in item.warnings %}
                <li>{{ w }}</li>
                {% endfor %}
            </ul>
        </div>
        {% endif %}

        <div class="card mb-4">
            <h3 class="text-sm font-medium text-neutral-900 mb-3">Extracted Fields</h3>
            {% if item.raw_fields %}
            <table class="min-w-full text-sm zebra-table">
                <tbody>
                    {% for key, value in item.raw_fields.items() %}
                    <tr class="border-b border-neutral-100">
                        <td class="px-3 py-2 font-medium text-neutral-500 whitespace-nowrap">{{ key }}</td>
                        <td class="px-3 py-2 text-neutral-900 break-all">{% if value %}<span lang="th" class="font-thai">{{ value }}</span>{% else %}-{% endif %}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p class="text-sm text-neutral-500">No fields extracted.</p>
            {% endif %}
        </div>

        <div class="card mb-4">
            <div class="flex items-center justify-between mb-3">
                <h3 class="text-sm font-medium text-neutral-900">Raw Email</h3>
                <button
                    type="button"
                    class="px-3 py-1.5 rounded-lg border border-neutral-300 text-xs font-medium hover:bg-neutral-100 transition-colors duration-150"
                    hx-get="/api/unknown/{{ item.id }}/raw-email"
                    hx-target="#raw-email-unknown-{{ item.id }}"
                    hx-swap="innerHTML"
                >View Raw Email</button>
            </div>
            <div id="raw-email-unknown-{{ item.id }}" class="text-sm text-neutral-500">Click "View Raw Email" to load the original message from Gmail.</div>
        </div>

        {% if item.status == 'resolved' %}
        <div class="card">
            <p class="text-sm text-neutral-700">Resolved &rarr; <a href="/transactions/{{ item.resolved_transaction_id }}" class="text-neutral-900 underline">Transaction #{{ item.resolved_transaction_id }}</a></p>
        </div>
        {% elif item.status == 'ignored' %}
        <div class="card">
            <p class="text-sm text-neutral-500">This email has been ignored.</p>
        </div>
        {% else %}
        <div id="promote-form-{{ item.id }}">
            {% include "partials/unknown_promote_form.html" %}
        </div>
        {% endif %}
    </div>
</div>
```

- [ ] **Step 2: Create the promote form as its own includable partial**

Create `app/web/templates/partials/unknown_promote_form.html` (split out so the promote endpoint's HTMX response can swap `#promote-form-{id}` for a success message on submit, and so the form can be re-included unchanged from the modal):

```html
<div class="card">
    <h3 class="text-sm font-medium text-neutral-900 mb-3">Categorize as Transaction</h3>
    <form
        hx-post="/api/unknown/{{ item.id }}/promote"
        hx-target="#promote-form-{{ item.id }}"
        hx-swap="innerHTML"
        class="grid grid-cols-1 sm:grid-cols-2 gap-3"
    >
        <div>
            <label class="block text-xs text-neutral-500 mb-1">Transaction Type</label>
            <input type="text" name="transaction_type" required class="input" list="types-{{ item.id }}">
        </div>
        <div>
            <label class="block text-xs text-neutral-500 mb-1">Direction</label>
            <select name="direction" required class="input">
                <option value="out">Out</option>
                <option value="in">In</option>
                <option value="internal">Internal</option>
                <option value="unknown">Unknown</option>
            </select>
        </div>
        <div>
            <label class="block text-xs text-neutral-500 mb-1">Status</label>
            <select name="status" required class="input">
                <option value="success">Success</option>
                <option value="failed">Failed</option>
                <option value="pending">Pending</option>
                <option value="cancelled">Cancelled</option>
                <option value="unknown">Unknown</option>
            </select>
        </div>
        <div>
            <label class="block text-xs text-neutral-500 mb-1">Occurred At</label>
            <input type="datetime-local" name="occurred_at" required value="{{ (item.received_at or item.created_at or '')[:16] }}" class="input">
        </div>
        <div>
            <label class="block text-xs text-neutral-500 mb-1">Amount</label>
            <input type="number" step="0.01" name="amount" required value="{{ item.amount if item.amount is not none else '' }}" class="input">
        </div>
        <div>
            <label class="block text-xs text-neutral-500 mb-1">Category</label>
            <input type="text" name="category" required class="input" list="categories-{{ item.id }}">
        </div>
        <div>
            <label class="block text-xs text-neutral-500 mb-1">Fee</label>
            <input type="number" step="0.01" name="fee" value="0" class="input">
        </div>
        <div>
            <label class="block text-xs text-neutral-500 mb-1">Available Balance</label>
            <input type="number" step="0.01" name="available_balance" class="input">
        </div>
        <div>
            <label class="block text-xs text-neutral-500 mb-1">Counterparty</label>
            <input type="text" name="counterparty" class="input">
        </div>
        <div>
            <label class="block text-xs text-neutral-500 mb-1">Description</label>
            <input type="text" name="description" class="input">
        </div>
        <div class="sm:col-span-2">
            <button type="submit" class="px-4 py-2 rounded-lg bg-neutral-900 text-white text-sm font-medium hover:bg-neutral-700 transition-colors duration-150">Create Transaction</button>
        </div>
    </form>
    <datalist id="types-{{ item.id }}">
        {% for t in types %}
        <option value="{{ t }}">
        {% endfor %}
    </datalist>
    <datalist id="categories-{{ item.id }}">
        {% for cat in categories %}
        <option value="{{ cat }}">
        {% endfor %}
    </datalist>
</div>
```

(Update `unknown_detail_modal.html`'s `{% else %}` branch from Step 1 to `{% include "partials/unknown_promote_form.html" %}` inside `<div id="promote-form-{{ item.id }}">` - already written that way above.)

- [ ] **Step 3: Create the post-promotion success fragment**

Create `app/web/templates/partials/unknown_promoted.html`:

```html
<p class="text-sm text-green-700">Promoted to <a href="/transactions/{{ item.resolved_transaction_id }}" class="underline font-medium">Transaction #{{ item.resolved_transaction_id }}</a></p>
```

- [ ] **Step 4: Add the modal, raw-email, and promote routes**

In `app/web/routes/unknown.py`, add the imports and routes:

```python
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request

from app.parsers.registry import ParserRegistry
from app.ingestion import persistence
```

(merge with existing imports rather than duplicating; `ParserRegistry` and `persistence` are new)

```python
@router.get("/unknown/{unknown_id}/raw-email")
async def get_unknown_raw_email(
    unknown_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    gmail_client: GmailClient = Depends(get_gmail_client),
):
    row = await queries.get_unknown(db, unknown_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    try:
        message = gmail_client.get_message(row["gmail_message_id"])
        email = {"sender": message.sender, "subject": message.subject, "received_at": message.received_at, "body_text": message.body_text}
        error = None
    except Exception as e:
        logger.warning(f"Failed to fetch raw email for unknown pattern {unknown_id}: {e}")
        email = None
        error = "Could not load the original email. It may have been deleted, or Gmail access failed."
    return templates.TemplateResponse(request, "partials/raw_email.html", {"email": email, "error": error})


@router.post("/unknown/{unknown_id}/promote")
async def promote_unknown(
    unknown_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    registry: ParserRegistry = Depends(get_parser_registry),
    transaction_type: str = Form(...),
    direction: str = Form(...),
    status: str = Form(...),
    occurred_at: str = Form(...),
    amount: float = Form(...),
    category: str = Form(...),
    fee: float = Form(0.0),
    available_balance: float | None = Form(None),
    counterparty: str | None = Form(None),
    description: str | None = Form(None),
):
    row = await queries.get_unknown(db, unknown_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Cannot promote a {row['status']} record")

    bank = registry.identify_bank(row["sender"]) if row["sender"] else None
    transaction_id = await persistence.insert_manual_transaction(
        db,
        gmail_message_id=row["gmail_message_id"],
        bank=bank,
        transaction_type=transaction_type,
        direction=direction,
        status=status,
        occurred_at=occurred_at,
        amount=amount,
        category=category,
        fee=fee,
        available_balance=available_balance,
        counterparty=counterparty,
        description=description,
    )
    await db.commit()
    await persistence.resolve_unknown(db, unknown_id, transaction_id)
    await db.commit()

    item = await queries.get_unknown(db, unknown_id)
    return templates.TemplateResponse(request, "partials/unknown_promoted.html", {"item": item})
```

And in `page_router`:

```python
@page_router.get("/unknown/{unknown_id}/modal")
async def unknown_detail_modal(request: Request, unknown_id: int, db: aiosqlite.Connection = Depends(get_db)):
    item = await queries.get_unknown(db, unknown_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    categories = await queries.list_categories(db)
    types = await queries.list_transaction_types(db)
    return templates.TemplateResponse(
        request, "partials/unknown_detail_modal.html", {"item": item, "categories": categories, "types": types}
    )
```

- [ ] **Step 5: Update the row partial with a View button and resolved-state display**

Replace `app/web/templates/partials/unknown_row.html`:

```html
<tr id="unknown-row-{{ item.id }}" class="border-b border-neutral-100">
    <td class="px-4 py-3 whitespace-nowrap">{{ item.created_at }}</td>
    <td class="px-4 py-3">{{ item.subject or '-' }}</td>
    <td class="px-4 py-3">{{ item.sender or '-' }}</td>
    <td class="px-4 py-3">{{ item.transaction_code or '-' }}</td>
    <td class="px-4 py-3 text-right">{% if item.amount is not none %}฿{{ '{:,.2f}'.format(item.amount) }}{% else %}-{% endif %}</td>
    <td class="px-4 py-3">
        <span class="badge {% if item.status == 'pending' %}badge-warning{% elif item.status == 'resolved' %}badge-income{% else %}badge-neutral{% endif %}">{{ item.status }}</span>
        {% if item.status == 'resolved' and item.resolved_transaction_id %}
        <a href="/transactions/{{ item.resolved_transaction_id }}" class="block text-xs text-neutral-500 underline mt-0.5">Txn #{{ item.resolved_transaction_id }}</a>
        {% endif %}
    </td>
    <td class="px-4 py-3 text-xs text-neutral-500">
        {% if item.warnings %}
            {{ item.warnings|length }} warning(s): {{ item.warnings|join(', ') }}
        {% else %}
            -
        {% endif %}
    </td>
    <td class="px-4 py-3 whitespace-nowrap">
        <button
            class="px-2 py-1 rounded-md border border-neutral-300 text-xs hover:bg-neutral-100"
            hx-get="/unknown/{{ item.id }}/modal"
            hx-target="#modal-root"
            hx-swap="innerHTML"
        >View</button>
        {% if item.status == 'pending' %}
        <button
            class="px-2 py-1 rounded-md border border-neutral-300 text-xs hover:bg-neutral-100"
            hx-post="/api/unknown/{{ item.id }}/ignore"
            hx-target="#unknown-row-{{ item.id }}"
            hx-swap="outerHTML"
            hx-disabled-elt="this"
        >Ignore</button>
        <button
            class="px-2 py-1 rounded-md border border-neutral-300 text-xs hover:bg-neutral-100"
            hx-post="/api/unknown/{{ item.id }}/reparse"
            hx-target="#unknown-row-{{ item.id }}"
            hx-swap="outerHTML"
            hx-disabled-elt="this"
        >Reparse</button>
        {% endif %}
        <button
            class="px-2 py-1 rounded-md border border-red-300 text-xs text-red-700 hover:bg-red-50"
            hx-delete="/api/unknown/{{ item.id }}"
            hx-target="#unknown-row-{{ item.id }}"
            hx-swap="outerHTML"
            hx-confirm="Delete this unknown email record?"
            hx-disabled-elt="this"
        >Delete</button>
    </td>
</tr>
```

- [ ] **Step 6: Add "Resolved" to the status filter**

In `app/web/templates/unknown.html`, update the filter `<select>`:

```html
        <select id="filter-status" name="status" class="input">
            <option value="" {% if filters.status == '' %}selected{% endif %}>Any</option>
            <option value="pending" {% if filters.status == 'pending' %}selected{% endif %}>Pending</option>
            <option value="resolved" {% if filters.status == 'resolved' %}selected{% endif %}>Resolved</option>
            <option value="ignored" {% if filters.status == 'ignored' %}selected{% endif %}>Ignored</option>
        </select>
```

- [ ] **Step 7: Write route tests**

In `tests/test_routes.py`, add:

```python
@pytest.mark.asyncio
async def test_unknown_detail_modal_route(client, db_connection):
    unknown_id = await _insert_unknown(db_connection)
    resp = client.get(f"/unknown/{unknown_id}/modal")
    assert resp.status_code == 200
    assert "Categorize as Transaction" in resp.text


def test_unknown_detail_modal_404_for_missing(client):
    resp = client.get("/unknown/999999/modal")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_promote_unknown_creates_transaction_and_resolves(client, db_connection):
    unknown_id = await _insert_unknown(db_connection, sender="notify@kasikornbank.com")

    resp = client.post(
        f"/api/unknown/{unknown_id}/promote",
        data={
            "transaction_type": "bank_transfer",
            "direction": "out",
            "status": "success",
            "occurred_at": "2026-07-27T10:00",
            "amount": "250.0",
            "category": "Shopping",
        },
    )
    assert resp.status_code == 200
    assert "Promoted to" in resp.text

    cursor = await db_connection.execute(
        "SELECT status, resolved_transaction_id FROM unknown_patterns WHERE id = ?", (unknown_id,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row["status"] == "resolved"
    assert row["resolved_transaction_id"] is not None

    cursor = await db_connection.execute(
        "SELECT category, category_source, bank FROM transactions WHERE id = ?", (row["resolved_transaction_id"],)
    )
    tx_row = await cursor.fetchone()
    await cursor.close()
    assert tx_row["category"] == "Shopping"
    assert tx_row["category_source"] == "manual"
    assert tx_row["bank"] == "KBank"


@pytest.mark.asyncio
async def test_promote_unknown_missing_required_field_returns_422(client, db_connection):
    unknown_id = await _insert_unknown(db_connection)

    resp = client.post(
        f"/api/unknown/{unknown_id}/promote",
        data={"transaction_type": "bank_transfer", "direction": "out", "status": "success"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_promote_unknown_already_resolved_returns_409(client, db_connection):
    unknown_id = await _insert_unknown(db_connection, status="resolved")

    resp = client.post(
        f"/api/unknown/{unknown_id}/promote",
        data={
            "transaction_type": "bank_transfer",
            "direction": "out",
            "status": "success",
            "occurred_at": "2026-07-27T10:00",
            "amount": "10.0",
            "category": "Shopping",
        },
    )
    assert resp.status_code == 409


def test_promote_unknown_not_found_returns_404(client):
    resp = client.post(
        "/api/unknown/999999/promote",
        data={
            "transaction_type": "bank_transfer",
            "direction": "out",
            "status": "success",
            "occurred_at": "2026-07-27T10:00",
            "amount": "10.0",
            "category": "Shopping",
        },
    )
    assert resp.status_code == 404
```

- [ ] **Step 8: Run the tests**

Run: `pytest tests/test_routes.py -v`
Expected: PASS

- [ ] **Step 9: Manual check**

Use the `run` skill to start the app, open `/unknown`, click "View" on a row, confirm the modal shows extracted fields, loads the raw email on demand, and (for a pending row) shows the promote form; submit it and confirm the row now shows "resolved" with a link to the new transaction, and that filtering by "Resolved" shows it.

- [ ] **Step 10: Commit**

```bash
git add app/web/templates/partials/unknown_detail_modal.html \
        app/web/templates/partials/unknown_promote_form.html \
        app/web/templates/partials/unknown_promoted.html \
        app/web/templates/partials/unknown_row.html \
        app/web/templates/unknown.html \
        app/web/routes/unknown.py \
        tests/test_routes.py
git commit -m "feat(unknown): raw email popup and manual promotion to transaction with permanent history"
```

---

### Task 12: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `pytest tests/ -v`
Expected: PASS. If anything fails, it's most likely one of: a test still asserting the old delete-on-success behavior somewhere not caught above, a `FakeRegistry`/`FakeParser` test double elsewhere missing `identify_bank`, or an `INSERT INTO transactions` test helper missing the new `bank` column (should be fine since it's nullable and not referenced positionally anywhere). Fix any such breakage before proceeding - do not skip or `xfail` a test to make this pass.

- [ ] **Step 2: Full manual smoke test**

Use the `run` skill to start the app and walk through, in order: Dashboard (pie chart + total + run descriptions load), Transactions (open a modal, edit category, view raw email, close via Escape), Unknown (view a row's raw email, promote one to a transaction, confirm it shows under the "Resolved" filter), and confirm `/transactions/{id}` and the rest of the nav (Mappings, Settings) still work.

- [ ] **Step 3: Commit (only if Step 1 required fixes)**

```bash
git add -A
git commit -m "test: fix regressions from web UI enhancements"
```
