"""Password hashing, JWT tokens, and symmetric encryption for provider secrets.

Kept free of any domain / persistence concerns so it can be reused unchanged
after the project is exported to VS Code.
"""
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
import jwt
from cryptography.fernet import Fernet

from . import config

_fernet = Fernet(config.ENCRYPTION_KEY.encode())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def new_jti() -> str:
    """Opaque per-token identifier used to bind a refresh token to a session."""
    return uuid.uuid4().hex


def hash_token(token: str) -> str:
    """Stable, non-reversible digest for at-rest token storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_identifier(value: Optional[str]) -> Optional[str]:
    """Hash a request attribute (IP / user-agent) before storage."""
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_access_token(user_id: str, email: str, jti: Optional[str] = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=config.ACCESS_TOKEN_MINUTES),
    }
    if jti:
        payload["jti"] = jti
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def create_refresh_token(user_id: str, jti: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "jti": jti,
        "iat": now,
        "exp": now + timedelta(days=config.REFRESH_TOKEN_DAYS),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def encrypt_secret(value: str) -> str:
    return _fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    return _fernet.decrypt(token.encode("utf-8")).decode("utf-8")
