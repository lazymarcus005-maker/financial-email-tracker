"""Authentication helpers for password hashing and signed session cookies."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.config import Settings, get_settings
from app.storage import queries
from app.storage.database import get_connection

PASSWORD_ITERATIONS = 260_000
_FALLBACK_AUTH_SECRET = secrets.token_urlsafe(32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = password_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _auth_secret(settings: Settings | None = None) -> bytes:
    settings = settings or get_settings()
    secret = settings.AUTH_SECRET_KEY
    if not secret:
        secret = _FALLBACK_AUTH_SECRET
    return secret.encode("utf-8")


def _b64_json(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64_json(value: str) -> dict:
    padded = value + "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))


def create_session_token(user_id: int, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    payload = {"uid": user_id, "exp": int(time.time()) + settings.AUTH_SESSION_TTL_SECONDS}
    body = _b64_json(payload)
    signature = hmac.new(_auth_secret(settings), body.encode("ascii"), hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{body}.{sig}"


def read_session_token(token: str | None, settings: Settings | None = None) -> int | None:
    if not token or "." not in token:
        return None
    settings = settings or get_settings()
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(_auth_secret(settings), body.encode("ascii"), hashlib.sha256).digest()
    padded_sig = sig + "=" * (-len(sig) % 4)
    try:
        actual = base64.urlsafe_b64decode(padded_sig.encode("ascii"))
        payload = _unb64_json(body)
    except (ValueError, json.JSONDecodeError):
        return None
    if not hmac.compare_digest(actual, expected):
        return None
    try:
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return int(payload["uid"])
    except (KeyError, TypeError, ValueError):
        return None


def set_login_cookie(response: Response, user_id: int) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.AUTH_COOKIE_NAME,
        create_session_token(user_id, settings),
        max_age=settings.AUTH_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )


def clear_login_cookie(response: Response) -> None:
    response.delete_cookie(get_settings().AUTH_COOKIE_NAME)


async def load_user_from_request(request: Request) -> dict | None:
    settings = get_settings()
    user_id = read_session_token(request.cookies.get(settings.AUTH_COOKIE_NAME), settings)
    if user_id is None:
        return None
    db = await get_connection()
    try:
        user = await queries.get_user(db, user_id)
    finally:
        await db.close()
    if not user or not user["is_active"]:
        return None
    user.pop("password_hash", None)
    return user


def is_public_path(path: str) -> bool:
    """Paths that bypass session auth and the setup redirect.

    `/mcp` and `/sse` are only public when the MCP server is actually mounted
    (i.e. `MCP_ENABLED=true`) - otherwise they are not real routes and the
    whitelist would silently bypass auth for non-existent URLs while lying in
    the config (MCP_ENABLED=false but the URL is treated as public).
    """
    if (
        path == "/health"
        or path == "/login"
        or path == "/setup"
        or path.startswith("/static/")
    ):
        return True
    if path.startswith("/mcp") or path.startswith("/sse"):
        return get_settings().MCP_ENABLED
    return False


def unauthenticated_response(request: Request) -> Response:
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    next_url = quote(str(request.url.path) + (f"?{request.url.query}" if request.url.query else ""))
    return RedirectResponse(f"/login?next={next_url}", status_code=303)


def require_admin(request: Request) -> dict:
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
