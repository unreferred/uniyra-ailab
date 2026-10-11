"""Mongo-backed fixed-window rate limiter.

Each counter is a single document keyed by a hashed identifier so raw IPs /
emails are never stored. A TTL index on `expires_at` (see database.py) lets
Mongo clean up stale windows automatically.
"""
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import HTTPException

from .database import db

TOO_MANY_REQUESTS = "Too many requests. Please slow down."


def _identifier(scope: str, key: str) -> str:
    return hashlib.sha256(f"{scope}:{key}".encode("utf-8")).hexdigest()


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def enforce_rate_limit(scope: str, key: str, limit: int, window_seconds: int) -> None:
    """Count one hit in `scope`/`key` and raise 429 once `limit` is exceeded."""
    if limit <= 0:
        return
    identifier = _identifier(scope, key)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=window_seconds)

    # Try to advance an existing, still-open window atomically.
    result = await db.rate_limits.update_one(
        {"identifier": identifier, "expires_at": {"$gt": now}},
        {"$inc": {"count": 1}},
    )

    if result.modified_count == 0:
        # No live window: start a fresh one (upsert covers the very first hit).
        await db.rate_limits.update_one(
            {"identifier": identifier},
            {"$set": {
                "identifier": identifier,
                "scope": scope,
                "window_start": now,
                "count": 1,
                "expires_at": expires_at,
            }},
            upsert=True,
        )
        count = 1
    else:
        doc = await db.rate_limits.find_one({"identifier": identifier}, {"_id": 0, "count": 1})
        count = int((doc or {}).get("count", 1))

    if count > limit:
        raise HTTPException(status_code=429, detail=TOO_MANY_REQUESTS)


async def reset_rate_limit(scope: str, key: str) -> None:
    await db.rate_limits.delete_one({"identifier": _identifier(scope, key)})
