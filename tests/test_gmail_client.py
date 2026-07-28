"""Tests for the Gmail API client wrapper."""

from app.gmail import client as gmail_client


def test_gmail_client_requires_per_user_token_path():
    try:
        gmail_client.GmailClient()
    except ValueError as e:
        assert "per-user token_path" in str(e)
    else:
        raise AssertionError("GmailClient() should require an explicit per-user token path")


class _Execute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _Messages:
    def list(self, **kwargs):
        return _Execute({"messages": [{"id": "msg-1"}]})


class _Users:
    def getProfile(self, **kwargs):
        return _Execute({"emailAddress": "bank-inbox@example.com"})

    def messages(self):
        return _Messages()


class _Service:
    def users(self):
        return _Users()


def test_search_logs_profile_email_and_token_path(monkeypatch, caplog):
    monkeypatch.setattr(gmail_client, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(gmail_client, "build", lambda *args, **kwargs: _Service())

    client = gmail_client.GmailClient(token_path="secrets/users/1/gmail-token.json")

    with caplog.at_level("INFO"):
        assert client.search_message_ids("from:KPLUS@kasikornbank.com") == ["msg-1"]

    assert any("profile=bank-inbox@example.com" in record.message for record in caplog.records)
    assert any("token_path=secrets/users/1/gmail-token.json" in record.message for record in caplog.records)
