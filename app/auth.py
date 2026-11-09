# app/auth.py
"""Editor authentication. ``AUTH_MODE`` picks one of:

``none``
    No login: everyone gets the editor. For local use only.

``password``
    The app's own login: ``/login`` asks for ``ADMIN_PASSWORD`` and sets a
    signed, expiring editor cookie. Writes need that cookie and a same-origin
    ``Origin``. No reverse proxy auth needed.

``proxy`` (alias ``authelia``)
    A reverse proxy authenticates (Authelia, oauth2-proxy, ... via nginx
    ``auth_request``) before it passes the editor paths (``/login``, the write
    endpoints, ``/analytics/``) and adds ``Remote-User`` plus
    ``X-Proxy-Secret`` (a shared secret only the proxy knows). The public
    pages never go through the proxy auth, so ``GET /login`` turns that
    session into a signed editor cookie that lets those pages show the
    editor. Every write still needs the proxy headers; the cookie alone only
    changes what a page shows.

``auto`` (default, also empty): ``proxy`` if ``PROXY_SECRET`` is set, else
``password`` if ``ADMIN_PASSWORD`` is set, else ``none``.
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

AUTH_MODES = ("none", "password", "proxy")
_ALIASES = {"authelia": "proxy", "off": "none"}


def auth_mode() -> str:
    """The effective mode; raises ValueError for an unknown AUTH_MODE."""
    mode = (settings.auth_mode or "auto").strip().lower()
    mode = _ALIASES.get(mode, mode)
    if mode == "auto":
        if settings.proxy_secret:
            return "proxy"
        if settings.admin_password:
            return "password"
        return "none"
    if mode not in AUTH_MODES:
        raise ValueError(f"AUTH_MODE must be one of auto, {', '.join(AUTH_MODES)}, authelia; got {mode!r}")
    return mode


def is_live_mode() -> bool:
    """Whether the editor is locked (any mode except none)."""
    return auth_mode() != "none"


def proxy_user(request: Request) -> Optional[str]:
    """The user the reverse proxy vouches for (proxy mode only), or None."""
    if auth_mode() != "proxy" or not settings.proxy_secret or not settings.allowed_user:
        return None
    sent = request.headers.get("x-proxy-secret", "")
    if not hmac.compare_digest(sent.encode(), settings.proxy_secret.encode()):
        return None
    user = request.headers.get("remote-user", "")
    if not user or not hmac.compare_digest(user.encode(), settings.allowed_user.encode()):
        return None
    return user


def check_password(password: str) -> bool:
    """Password mode: compare against ADMIN_PASSWORD."""
    if auth_mode() != "password" or not settings.admin_password:
        return False
    return hmac.compare_digest(password.encode(), settings.admin_password.encode())


def _sign(expires: int) -> str:
    if auth_mode() == "password":
        # Changing ADMIN_PASSWORD logs every editor out.
        identity = "password:" + hashlib.sha256(settings.admin_password.encode()).hexdigest()
    else:
        identity = settings.allowed_user
    message = f"editor:{identity}:{expires}".encode()
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


def editor_user(request: Request) -> Optional[str]:
    """Who may write with this request, or None. See require_editor."""
    mode = auth_mode()
    if mode == "none":
        return "dev"
    if mode == "password":
        return "admin" if verify_editor_token(request.cookies.get(EDITOR_COOKIE)) else None
    return proxy_user(request)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        # Browsers send Origin on every POST; other clients still need the
        # credentials checked in editor_user.
        return True
    return urlparse(origin).netloc == request.headers.get("host", "")


def require_editor(request: Request) -> str:
    """Guard for every endpoint that changes something."""
    if not is_live_mode():
        return "dev"
    user = editor_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Cross-origin request refused")
    return user


def cookie_secure(request: Request) -> bool:
    """Secure cookies behind a proxy; in password mode only over HTTPS, so a
    plain-HTTP LAN or localhost install can still log in."""
    if auth_mode() == "proxy":
        return True
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return request.url.scheme == "https" or forwarded == "https"


def safe_redirect_target(target: Optional[str]) -> str:
    """Only same-site relative paths; anything else goes to the home page."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def check_live_config() -> None:
    """Refuse to start with an unknown mode or a half-done auth setup."""
    try:
        mode = auth_mode()
    except ValueError as e:
        raise RuntimeError(str(e)) from None
    if mode == "none":
        return
    problems = []
    if mode == "proxy":
        if not settings.proxy_secret:
            problems.append("PROXY_SECRET is empty")
        if not settings.allowed_user:
            problems.append("ALLOWED_USER is empty")
    if mode == "password" and not settings.admin_password:
        problems.append("ADMIN_PASSWORD is empty")
    if settings.session_secret == DEFAULT_SESSION_SECRET or len(settings.session_secret) < 32:
        problems.append("SESSION_SECRET is the default or shorter than 32 characters")
    if problems:
        raise RuntimeError(f"AUTH_MODE={mode} misconfigured: " + "; ".join(problems))
