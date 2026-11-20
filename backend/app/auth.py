"""Single shared password with a signed, HTTP-only session cookie."""
import hmac
import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE = "redline_session"
MAX_AGE = 60 * 60 * 24 * 7
PUBLIC_API = {"/api/login", "/api/health"}

_secret = os.environ.get("SESSION_SECRET", "")
if not _secret and os.environ.get("REDLINE_DURABLE_JOBS", "").lower() in {"1", "true"}:
    # Durable deployments can run several replicas; a random per-process secret would sign people out.
    raise RuntimeError("SESSION_SECRET must be set for durable deployments")
_serializer = URLSafeTimedSerializer(_secret or secrets.token_hex(32), salt="redline")

# Sign-in throttling: a shared password must not be guessable at machine speed.
MAX_FAILURES, WINDOW_SECONDS = 5, 300
_failures: dict[str, list[float]] = {}


def login_blocked(client: str, now: float) -> bool:
    recent = [t for t in _failures.get(client, []) if now - t < WINDOW_SECONDS]
    _failures[client] = recent
    return len(recent) >= MAX_FAILURES


def record_failure(client: str, now: float) -> None:
    _failures.setdefault(client, []).append(now)
    if len(_failures) > 10_000:  # bounded memory under a spray of addresses
        _failures.pop(next(iter(_failures)))


def password_ok(candidate: str) -> bool:
    expected = os.environ.get("APP_PASSWORD", "")
    return bool(expected) and hmac.compare_digest(candidate.encode(), expected.encode())


def issue_token() -> str:
    return _serializer.dumps({"ok": True})


def token_valid(token: str | None) -> bool:
    if not token:
        return False
    try:
        return bool(_serializer.loads(token, max_age=MAX_AGE).get("ok"))
    except (BadSignature, SignatureExpired):
        return False


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api") and path not in PUBLIC_API and not token_valid(request.cookies.get(COOKIE)):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return await call_next(request)
