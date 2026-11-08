# app/auth.py
"""Editor authentication.

Live mode (``proxy_secret`` set): nginx asks Authelia before it passes the
editor paths (``/login``, the write endpoints, ``/analytics/``) and adds
``Remote-User`` plus ``X-Proxy-Secret`` (docs/identity.md in the breathdance
repo). The public pages never go through Authelia, so ``GET /login`` turns the
Authelia session into a signed editor cookie that lets those pages show the
editor. Every write still needs the Authelia headers; the cookie alone only
changes what a page shows.

Dev mode (no ``proxy_secret``): everything is allowed, as before.
"""

import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from app.settings import settings

EDITOR_COOKIE = "flow_editor"
LEGACY_COOKIE = "flow_session"
DEFAULT_SESSION_SECRET = "change-this-secret-key-in-production"


def is_live_mode() -> bool:
    return bool(settings.proxy_secret)


def proxy_user(request: Request) -> Optional[str]:
    """The Authelia user nginx vouches for, or None."""
    if not settings.proxy_secret or not settings.allowed_user:
        return None
    sent = request.headers.get("x-proxy-secret", "")
    if not hmac.compare_digest(sent.encode(), settings.proxy_secret.encode()):
        return None
    user = request.headers.get("remote-user", "")
    if not user or not hmac.compare_digest(user.encode(), settings.allowed_user.encode()):
        return None
    return user


def _sign(expires: int) -> str:
    message = f"editor:{settings.allowed_user}:{expires}".encode()
    return hmac.new(settings.session_secret.encode(), message, hashlib.sha256).hexdigest()


def create_editor_token(now: Optional[float] = None) -> str:
    expires = int(now if now is not None else time.time()) + settings.editor_session_days * 86400
    return f"{expires}.{_sign(expires)}"


def verify_editor_token(token: Optional[str], now: Optional[float] = None) -> bool:
    if not token or "." not in token:
        return False
    expires_part, signature = token.split(".", 1)
    if not expires_part.isdigit():
        return False
    expires = int(expires_part)
    if expires < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(signature, _sign(expires))


def is_authenticated(request: Request) -> bool:
    """Whether to show the editor. Not enough for writes: see require_editor."""
    if not is_live_mode():
        return True
    if proxy_user(request) is not None:
        return True
    return verify_editor_token(request.cookies.get(EDITOR_COOKIE))


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        # Browsers send Origin on every POST; other clients still need the
        # Authelia headers below, which they cannot get without a session.
        return True
    return urlparse(origin).netloc == request.headers.get("host", "")


def require_editor(request: Request) -> str:
    """Guard for every endpoint that changes something."""
    if not is_live_mode():
        return "dev"
    user = proxy_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Cross-origin request refused")
    return user


def safe_redirect_target(target: Optional[str]) -> str:
    """Only same-site relative paths; anything else goes to the home page."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def check_live_config() -> None:
    """Refuse to start a live server with a half-done auth setup."""
    if not is_live_mode():
        return
    problems = []
    if not settings.allowed_user:
        problems.append("ALLOWED_USER is empty")
    if settings.session_secret == DEFAULT_SESSION_SECRET or len(settings.session_secret) < 32:
        problems.append("SESSION_SECRET is the default or shorter than 32 characters")
    if problems:
        raise RuntimeError("Live mode (PROXY_SECRET set) misconfigured: " + "; ".join(problems))
