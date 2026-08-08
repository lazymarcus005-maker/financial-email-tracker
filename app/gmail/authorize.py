"""Gmail OAuth2 authorization - obtain and refresh per-user credentials."""

import logging
import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

USER_TOKEN_ROOT = Path("secrets/users")


class GmailReauthorizationRequired(RuntimeError):
    """Raised when Google will not accept the stored refresh token anymore."""


def build_client_config(client_id: str, client_secret: str) -> dict:
    """Build the OAuth client config dict from env-provided secrets.

    Replaces the on-disk credentials.json - only client_id/client_secret are
    secret; the endpoints below are fixed Google constants.
    """
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        }
    }


def _require_client(client_id: str | None, client_secret: str | None) -> None:
    if not client_id or not client_secret:
        raise ValueError(
            "Gmail OAuth client is not configured. Set GMAIL_CLIENT_ID and "
            "GMAIL_CLIENT_SECRET in the environment (.env)."
        )


def user_token_path(user_id: int, token_root: Path | str | None = None) -> Path:
    return Path(token_root or USER_TOKEN_ROOT) / str(user_id) / "gmail-token.json"


def token_exists(token_path: Path | str) -> bool:
    return Path(token_path).exists()


def get_credentials(
    token_path: Path | str | None = None,
) -> Credentials:
    """Return valid OAuth2 credentials, refreshing the stored token as needed.

    The per-user token file already embeds client_id/client_secret/refresh_token,
    so no client-secret input is needed here to refresh.
    """
    if token_path is None:
        raise ValueError("Gmail token_path is required; use user_token_path(user_id)")

    token_path = Path(token_path)

    creds: Credentials | None = None

    if not token_path.exists():
        raise FileNotFoundError(f"Gmail OAuth token not found at {token_path}. Connect Gmail in Settings.")

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        return refresh_credentials(creds, token_path)

    raise GmailReauthorizationRequired(
        f"Gmail OAuth token at {token_path} is invalid. Reconnect Gmail in Settings."
    )


def refresh_credentials(creds: Credentials, token_path: Path | str) -> Credentials:
    """Refresh credentials and persist the new access token.

    Access tokens expire regularly and can be refreshed without user input. A
    ``RefreshError`` with ``invalid_grant`` means the refresh token itself was
    revoked or expired; Google does not provide a silent recovery path for
    that case, so callers can send the user through OAuth again.
    """
    token_path = Path(token_path)
    try:
        logger.info("Refreshing expired Gmail OAuth token")
        creds.refresh(Request())
        _save_token(creds, token_path)
        return creds
    except RefreshError as exc:
        logger.warning("Gmail OAuth token refresh failed for %s: %s", token_path, exc)
        raise GmailReauthorizationRequired(
            "Gmail authorization has expired or was revoked. Reconnect Gmail in Settings."
        ) from exc


def _save_token(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    logger.info(f"Saved Gmail OAuth token to {token_path}")


def build_authorization_url(
    redirect_uri: str,
    state: str,
    client_id: str,
    client_secret: str,
) -> str:
    _require_client(client_id, client_secret)
    if redirect_uri.startswith(("http://127.0.0.1", "http://localhost")):
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow = Flow.from_client_config(
        build_client_config(client_id, client_secret), scopes=SCOPES, redirect_uri=redirect_uri
    )
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return authorization_url


def exchange_authorization_response(
    redirect_uri: str,
    authorization_response: str,
    token_path: Path | str,
    client_id: str,
    client_secret: str,
) -> None:
    _require_client(client_id, client_secret)
    token_path = Path(token_path)
    if redirect_uri.startswith(("http://127.0.0.1", "http://localhost")):
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow = Flow.from_client_config(
        build_client_config(client_id, client_secret), scopes=SCOPES, redirect_uri=redirect_uri
    )
    flow.fetch_token(authorization_response=authorization_response)
    _save_token(flow.credentials, token_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit("Use the web Settings page to connect Gmail per user.")
