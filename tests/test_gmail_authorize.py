"""Tests for Gmail OAuth credential loading."""

import json

import pytest

from app.gmail import authorize


def test_get_credentials_requires_explicit_per_user_token_path():
    with pytest.raises(ValueError, match="token_path is required"):
        authorize.get_credentials()


def test_get_credentials_missing_user_token_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="Connect Gmail in Settings"):
        authorize.get_credentials(token_path=tmp_path / "missing-token.json")


def test_get_credentials_refreshes_and_persists_expired_token(monkeypatch, tmp_path):
    token_path = tmp_path / "gmail-token.json"
    token_path.write_text("{}")

    class FakeCredentials:
        valid = False
        expired = True
        refresh_token = "refresh-token"

        def refresh(self, request):
            self.valid = True
            self.expired = False

        def to_json(self):
            return json.dumps({"token": "new-access-token"})

    credentials = FakeCredentials()
    monkeypatch.setattr(
        authorize.Credentials,
        "from_authorized_user_file",
        lambda path, scopes: credentials,
    )

    result = authorize.get_credentials(token_path=token_path)

    assert result is credentials
    assert json.loads(token_path.read_text()) == {"token": "new-access-token"}


def test_get_credentials_reports_revoked_refresh_token(monkeypatch, tmp_path):
    token_path = tmp_path / "gmail-token.json"
    token_path.write_text("{}")

    class FakeCredentials:
        valid = False
        expired = True
        refresh_token = "refresh-token"

        def refresh(self, request):
            raise authorize.RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(
        authorize.Credentials,
        "from_authorized_user_file",
        lambda path, scopes: FakeCredentials(),
    )

    with pytest.raises(authorize.GmailReauthorizationRequired, match="Reconnect Gmail"):
        authorize.get_credentials(token_path=token_path)
