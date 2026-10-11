from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from .. import config, repository as repo
from ..csrf import clear_csrf_cookie, ensure_csrf_cookie
from ..database import db
from ..deps import get_current_user
from ..models import LoginRequest, RegisterRequest, new_id, now_iso
from ..ratelimit import enforce_rate_limit
from ..security import (create_access_token, create_refresh_token, decode_token,
                        hash_identifier, hash_password, hash_token, new_jti,
                        verify_password)
from ..seed import seed_defaults_for_user

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_ATTEMPTS = 5
LOCK_MINUTES = 15

# Outer brute-force guards, evaluated before the per-account lockout.
LOGIN_RATE_LIMIT = 10
LOGIN_RATE_WINDOW_SECONDS = LOCK_MINUTES * 60
REGISTER_RATE_LIMIT = 10
REGISTER_RATE_WINDOW_SECONDS = 60 * 60


class LogoutRequest(BaseModel):
    all: bool = False


# ------------------------------------------------------------------ helpers
def _cookie_kwargs(max_age: int, httponly: bool = True) -> dict:
    kwargs = {
        "httponly": httponly,
        "secure": config.COOKIE_SECURE,
        "samesite": config.COOKIE_SAMESITE,
        "max_age": max_age,
        "path": "/",
    }
    if config.COOKIE_DOMAIN:
        kwargs["domain"] = config.COOKIE_DOMAIN
    return kwargs


def _as_utc(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value) -> Optional[str]:
    dt = _as_utc(value)
    return dt.isoformat() if dt else None


def _public(user: dict) -> dict:
    return {"id": user["id"], "email": user["email"], "name": user["name"],
            "role": user.get("role", "researcher"), "created_at": user["created_at"]}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_auth_cookies(response: Response, user_id: str, email: str, refresh_token: str) -> None:
    response.set_cookie(
        "access_token", create_access_token(user_id, email),
        **_cookie_kwargs(config.ACCESS_TOKEN_MINUTES * 60))
    response.set_cookie(
        "refresh_token", refresh_token,
        **_cookie_kwargs(config.REFRESH_TOKEN_DAYS * 86400))


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie("access_token", path="/", domain=config.COOKIE_DOMAIN)
    response.delete_cookie("refresh_token", path="/", domain=config.COOKIE_DOMAIN)


async def _issue_session(request: Request, response: Response, user: dict) -> dict:
    """Persist a refresh session and emit the access/refresh/CSRF cookies."""
    user_id = user["id"]
    jti = new_jti()
    refresh_token = create_refresh_token(user_id, jti)
    now = datetime.now(timezone.utc)
    session = {
        "id": new_id(),
        "owner_id": user_id,
        "user_id": user_id,
        "jti": jti,
        "token_hash": hash_token(refresh_token),
        "created_at": now.isoformat(),
        "expires_at": now + timedelta(days=config.REFRESH_TOKEN_DAYS),
        "revoked_at": None,
        "rotated_to": None,
        "user_agent_hash": hash_identifier(request.headers.get("user-agent")),
        "ip_hash": hash_identifier(_client_ip(request)),
    }
    await repo.auth_sessions.insert(session)
    _set_auth_cookies(response, user_id, user["email"], refresh_token)
    ensure_csrf_cookie(response, request)
    return session


async def _revoke_all_for_user(user_id: str) -> None:
    await db.auth_sessions.update_many(
        {"user_id": user_id, "revoked_at": None},
        {"$set": {"revoked_at": datetime.now(timezone.utc).isoformat()}})


# ---------------------------------------------------------------- endpoints
@router.post("/register")
async def register(body: RegisterRequest, request: Request, response: Response):
    email = body.email.lower()
    await enforce_rate_limit(
        "register", _client_ip(request), REGISTER_RATE_LIMIT, REGISTER_RATE_WINDOW_SECONDS)
    if await repo.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="An account with this email already exists")
    uid = new_id()
    user = {"id": uid, "email": email, "password_hash": hash_password(body.password),
            "name": body.name, "role": "researcher", "created_at": now_iso()}
    await repo.users.insert(user)
    await seed_defaults_for_user(uid)
    await _issue_session(request, response, user)
    return _public(user)


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    email = body.email.lower()
    ip = _client_ip(request)
    ident = f"{ip}:{email}"
    await enforce_rate_limit("login", ident, LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW_SECONDS)

    attempt = await db.login_attempts.find_one({"identifier": ident}, {"_id": 0})
    if attempt and attempt.get("count", 0) >= MAX_ATTEMPTS:
        locked_until = attempt.get("locked_until")
        if locked_until and _as_utc(locked_until) and _as_utc(locked_until) > datetime.now(timezone.utc):
            raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in a few minutes.")

    user = await repo.users.find_one({"email": email})
    now = datetime.now(timezone.utc)
    if not user or not verify_password(body.password, user["password_hash"]):
        count = (attempt.get("count", 0) if attempt else 0) + 1
        await db.login_attempts.update_one(
            {"identifier": ident},
            {"$set": {"identifier": ident, "count": count,
                      "locked_until": (now + timedelta(minutes=LOCK_MINUTES)).isoformat(),
                      "expires_at": now + timedelta(minutes=LOCK_MINUTES)}},
            upsert=True)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await db.login_attempts.delete_one({"identifier": ident})
    await _issue_session(request, response, user)
    return _public(user)


@router.post("/logout")
async def logout(request: Request, response: Response,
                 body: Optional[LogoutRequest] = None, all: bool = Query(False)):
    revoke_all = bool(all) or bool(body and body.all)
    payload = decode_token(request.cookies.get("refresh_token") or "")
    user_id = payload.get("sub") if payload and payload.get("type") == "refresh" else None
    jti = payload.get("jti") if payload and payload.get("type") == "refresh" else None

    if user_id and revoke_all:
        await _revoke_all_for_user(user_id)
    elif user_id and jti:
        session = await repo.auth_sessions.find_one({"jti": jti, "user_id": user_id})
        if session and session.get("revoked_at") is None:
            await repo.auth_sessions.update(
                session["id"], user_id,
                {"revoked_at": datetime.now(timezone.utc).isoformat()})
    elif revoke_all:
        # Access token may still identify the user even when refresh is gone.
        access = decode_token(request.cookies.get("access_token") or "")
        if access and access.get("type") == "access":
            await _revoke_all_for_user(access["sub"])

    _clear_auth_cookies(response)
    clear_csrf_cookie(response)
    return {"ok": True}


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    payload = decode_token(token) if token else None
    if not payload or payload.get("type") != "refresh" or not payload.get("jti"):
        _clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = payload.get("sub")
    jti = payload.get("jti")
    session = await repo.auth_sessions.find_one({"jti": jti, "user_id": user_id})

    if not session:
        _clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if session.get("revoked_at") is not None:
        # Reuse of an already-rotated/revoked token: assume compromise and
        # revoke the whole token family for this user.
        await _revoke_all_for_user(user_id)
        _clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh token reuse detected")

    if session.get("token_hash") != hash_token(token):
        await _revoke_all_for_user(user_id)
        _clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    expires_at = _as_utc(session.get("expires_at"))
    if expires_at is None or expires_at <= datetime.now(timezone.utc):
        await repo.auth_sessions.update(
            session["id"], user_id,
            {"revoked_at": datetime.now(timezone.utc).isoformat()})
        _clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh token expired")

    user = await repo.users.get(user_id)
    if not user:
        _clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="User not found")

    new_session = await _issue_session(request, response, user)
    await repo.auth_sessions.update(
        session["id"], user_id,
        {"revoked_at": datetime.now(timezone.utc).isoformat(),
         "rotated_to": new_session["jti"]})
    return _public(user)


@router.get("/sessions")
async def list_sessions(request: Request, user: dict = Depends(get_current_user)):
    payload = decode_token(request.cookies.get("refresh_token") or "")
    current_jti = payload.get("jti") if payload and payload.get("type") == "refresh" else None
    sessions = await repo.auth_sessions.list(
        {"user_id": user["id"], "revoked_at": None},
        sort_field="created_at", descending=True, limit=200)
    return [
        {
            "id": s.get("id"),
            "created_at": s.get("created_at"),
            "expires_at": _iso(s.get("expires_at")),
            "rotated_to": s.get("rotated_to"),
            "current": bool(current_jti and s.get("jti") == current_jti),
        }
        for s in sessions
    ]


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: str, user: dict = Depends(get_current_user)):
    session = await repo.auth_sessions.find_one({"id": session_id, "user_id": user["id"]})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("revoked_at") is None:
        await repo.auth_sessions.update(
            session["id"], user["id"],
            {"revoked_at": datetime.now(timezone.utc).isoformat()})
    return {"ok": True}


@router.get("/csrf")
async def get_csrf(request: Request, response: Response):
    token = ensure_csrf_cookie(response, request)
    return {"csrf_token": token}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return _public(user)
