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

DEFAULT_CREDENTIALS_PATH = Path("secrets/credentials.json")
USER_TOKEN_ROOT = Path("secrets/users")


def user_token_path(user_id: int, token_root: Path | str | None = None) -> Path:
    return Path(token_root or USER_TOKEN_ROOT) / str(user_id) / "gmail-token.json"


def token_exists(token_path: Path | str) -> bool:
    return Path(token_path).exists()


def get_credentials(
    credentials_path: Path | str = DEFAULT_CREDENTIALS_PATH,
    token_path: Path | str | None = None,
) -> Credentials:
    """Return valid OAuth2 credentials, refreshing or running the auth flow as needed."""
    if token_path is None:
        raise ValueError("Gmail token_path is required; use user_token_path(user_id)")

    credentials_path = Path(credentials_path)
    token_path = Path(token_path)

    creds: Credentials | None = None

    if not token_path.exists():
        raise FileNotFoundError(f"Gmail OAuth token not found at {token_path}. Connect Gmail in Settings.")

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            logger.info("Refreshing expired Gmail OAuth token")
            creds.refresh(Request())
            _save_token(creds, token_path)
            return creds
        except RefreshError:
            logger.warning("Token refresh failed for %s", token_path)
            raise

    raise RuntimeError(f"Gmail OAuth token at {token_path} is invalid. Reconnect Gmail in Settings.")


def _save_token(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    logger.info(f"Saved Gmail OAuth token to {token_path}")


def build_authorization_url(
    redirect_uri: str,
    state: str,
    credentials_path: Path | str = DEFAULT_CREDENTIALS_PATH,
) -> str:
    credentials_path = Path(credentials_path)
    if redirect_uri.startswith(("http://127.0.0.1", "http://localhost")):
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    if not credentials_path.exists():
        raise FileNotFoundError(
            f"Gmail OAuth client credentials not found at {credentials_path}. "
            "Download it from Google Cloud Console and save it there."
        )
    flow = Flow.from_client_secrets_file(str(credentials_path), scopes=SCOPES, redirect_uri=redirect_uri)
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
    credentials_path: Path | str = DEFAULT_CREDENTIALS_PATH,
) -> None:
    credentials_path = Path(credentials_path)
    token_path = Path(token_path)
    if redirect_uri.startswith(("http://127.0.0.1", "http://localhost")):
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow = Flow.from_client_secrets_file(str(credentials_path), scopes=SCOPES, redirect_uri=redirect_uri)
    flow.fetch_token(authorization_response=authorization_response)
    _save_token(flow.credentials, token_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit("Use the web Settings page to connect Gmail per user.")
