"""Shared pytest fixtures - temp SQLite DB for tests that touch storage."""

import asyncio

import pytest

from app.storage import database


@pytest.fixture
def temp_db_path(tmp_path, monkeypatch):
    """Point app.storage.database at a fresh temp DB file and initialize its schema."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    asyncio.run(database.init_db())
    return db_path


@pytest.fixture
def db_connection(temp_db_path):
    """A single open connection to the temp DB, closed after the test."""
    db = asyncio.run(database.get_connection())
    yield db
    asyncio.run(db.close())
