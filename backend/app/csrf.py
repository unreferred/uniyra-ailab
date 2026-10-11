"""Double-submit-cookie CSRF protection for cookie-authenticated requests.

The browser keeps an httpOnly session cookie (`access_token` / `refresh_token`)
plus a readable `csrf_token` cookie. State-changing requests must echo the
readable cookie back in the `X-CSRF-Token` header. Because a cross-site attacker
can make the browser *send* cookies but cannot read them, it cannot produce the
matching header.

Bearer-token (non-browser) clients carry no auth cookies and are unaffected.
"""
import secrets
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from . import config

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# No session can exist yet, so there is nothing to protect.
CSRF_EXEMPT_PATHS = {"/api/auth/login", "/api/auth/register"}

# These endpoints establish or restore a session. They must remain usable when
# the CSRF cookie was never issued (e.g. an API tester or a first-ever call);
# when a csrf cookie does exist they are validated like any other request.
CSRF_BOOTSTRAP_PATHS = {"/api/auth/refresh", "/api/auth/logout"}

_AUTH_COOKIE_NAMES = ("access_token", "refresh_token")


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_cookie_max_age() -> int:
    return config.REFRESH_TOKEN_DAYS * 86400


def _csrf_cookie_kwargs() -> dict:
    return {
        "httponly": False,  # must be readable by the frontend JS
        "secure": config.COOKIE_SECURE,
        "samesite": config.COOKIE_SAMESITE,
        "path": "/",
        "max_age": csrf_cookie_max_age(),
    }


def set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(config.CSRF_COOKIE_NAME, token, **_csrf_cookie_kwargs())


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(config.CSRF_COOKIE_NAME, path="/",
                           domain=config.COOKIE_DOMAIN)


def ensure_csrf_cookie(response: Response, request: Optional[Request] = None) -> str:
    """Return the current CSRF token, issuing a new one when absent."""
    existing = request.cookies.get(config.CSRF_COOKIE_NAME) if request is not None else None
    if existing:
        # Re-assert it on the response so the cookie's lifetime slides with the
        # session, but never rotate a token the client is already holding.
        set_csrf_cookie(response, existing)
        return existing
    token = new_csrf_token()
    set_csrf_cookie(response, token)
    return token


def _origin_of(value: str) -> Optional[str]:
    try:
        parsed = urlparse(value)
    except Exception:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _allowed_origins() -> set:
    return {o.rstrip("/") for o in config.CORS_ORIGINS if o and o != "*"}


def _origin_allowed(origin: str) -> bool:
    # Prefer the shared config helper so CORS and CSRF accept the same
    # dev-LAN/Tailscale origins.
    checker = getattr(config, "is_origin_allowed", None)
    if callable(checker):
        try:
            return bool(checker(origin))
        except Exception:
            pass
    return origin.rstrip("/") in _allowed_origins()


def _check_origin(request: Request) -> None:
    source = request.headers.get("origin")
    if not source:
        referer = request.headers.get("referer")
        if referer:
            source = _origin_of(referer)
    if not source:
        return
    if not _origin_allowed(source):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


async def csrf_protect(request: Request) -> None:
    """FastAPI dependency enforcing CSRF for unsafe, cookie-authenticated requests."""
    if request.method.upper() not in UNSAFE_METHODS:
        return

    path = request.url.path
    if path in CSRF_EXEMPT_PATHS:
        return

    cookies = request.cookies
    has_auth_cookie = any(name in cookies for name in _AUTH_COOKIE_NAMES)
    if not has_auth_cookie:
        # Bearer/API clients: not CSRF-susceptible.
        return

    _check_origin(request)

    csrf_cookie = cookies.get(config.CSRF_COOKIE_NAME)
    if not csrf_cookie:
        if path in CSRF_BOOTSTRAP_PATHS:
            # No CSRF token has ever been issued to this browser; allow the
            # bootstrap/cleanup path so a stale session can recover or log out.
            return
        # Fail closed: cookie-auth is in use but no anti-CSRF token exists.
        raise HTTPException(status_code=403, detail="CSRF validation failed")

    header = request.headers.get(config.CSRF_HEADER_NAME)
    if not header or not secrets.compare_digest(header, csrf_cookie):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def csrf_error_response(detail: str = "CSRF validation failed") -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": detail})
