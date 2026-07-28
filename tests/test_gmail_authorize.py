"""Tests for Gmail OAuth credential loading."""

import pytest

from app.gmail import authorize


def test_get_credentials_requires_explicit_per_user_token_path():
    with pytest.raises(ValueError, match="token_path is required"):
        authorize.get_credentials()


def test_get_credentials_missing_user_token_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="Connect Gmail in Settings"):
        authorize.get_credentials(token_path=tmp_path / "missing-token.json")
