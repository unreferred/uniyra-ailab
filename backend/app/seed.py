"""Seeding: admin user and per-user default rubric.

Seeded records are ordinary registry records (editable/removable). They carry
`seeded: true` only for provenance, never for privileged behaviour.
"""
from . import config, repository as repo
from .models import new_id, now_iso
from .security import hash_password


DEFAULT_RUBRIC = {
    "name": "General Response Quality",
    "description": "Balanced rubric for comparing free-form model responses.",
    "dimensions": [
        {"key": "correctness", "label": "Correctness", "description": "Factual and logical accuracy.", "max_score": 10},
        {"key": "completeness", "label": "Completeness", "description": "Covers the full task.", "max_score": 10},
        {"key": "instruction_following", "label": "Instruction Following", "description": "Adheres to constraints.", "max_score": 10},
        {"key": "reasoning", "label": "Reasoning Quality", "description": "Depth and soundness of reasoning.", "max_score": 10},
        {"key": "clarity", "label": "Clarity", "description": "Readability and structure.", "max_score": 10},
    ],
}


async def seed_defaults_for_user(user_id: str) -> None:
    if not await repo.rubrics.find_one({"owner_id": user_id, "seeded": True}):
        await repo.rubrics.insert({
            "id": new_id(), "owner_id": user_id, "seeded": True,
            "created_at": now_iso(), "updated_at": now_iso(), **DEFAULT_RUBRIC,
        })


async def seed_admin() -> None:
    """Create the admin user once, and only when explicitly opted in.

    There is no default credential and no password-reset-on-startup: an
    existing account's password is never rewritten by a boot. Seeding is a
    no-op unless ENABLE_ADMIN_SEED is on and both ADMIN_EMAIL and
    ADMIN_PASSWORD are configured.
    """
    if not config.ENABLE_ADMIN_SEED or not config.ADMIN_EMAIL or not config.ADMIN_PASSWORD:
        return
    existing = await repo.users.find_one({"email": config.ADMIN_EMAIL})
    if existing is not None:
        return
    uid = new_id()
    await repo.users.insert({
        "id": uid, "email": config.ADMIN_EMAIL,
        "password_hash": hash_password(config.ADMIN_PASSWORD),
        "name": "Lab Researcher", "role": "admin", "created_at": now_iso(),
    })
    await seed_defaults_for_user(uid)
