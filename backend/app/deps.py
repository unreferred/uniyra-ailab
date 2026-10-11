"""FastAPI auth dependency. Resolves the current user from an httpOnly cookie
(access token) or Bearer header, and confirms the user still exists."""
from fastapi import HTTPException, Request

from . import repository as repo
from .security import decode_token


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = await repo.users.get(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    user.pop("password_hash", None)
    return user
