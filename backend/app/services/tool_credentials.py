"""Tool credential vault (Custom Tool Platform).

Secrets live only encrypted in ``tool_credentials``. Tool versions reference
credential IDs or ``{{secret:NAME}}`` templates, never values. Every public
view and persisted snapshot carries the sanitized ``[secret:NAME]`` marker;
plaintext is only ever produced server-side at call time.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from .. import security
from ..models import CREDENTIAL_TYPES, new_id, now_iso

#: ``{{secret:NAME}}`` placeholder syntax. Whitespace around the parts is
#: tolerated; NAME is restricted to a safe charset.
SECRET_PATTERN = re.compile(r"\{\{\s*secret\s*:\s*([A-Za-z0-9_.\-]+)\s*\}\}")

SANITIZED_FMT = "[secret:{name}]"

_MAX_VALUE_CHARS = 8 * 1024


class CredentialError(ValueError):
    """Raised for credential contract violations. Carries an HTTP code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _public(doc: dict) -> dict:
    """Build the client-safe credential view. Never includes secret material."""
    view = {
        "id": doc.get("id"),
        "owner_id": doc.get("owner_id"),
        "project_id": doc.get("project_id"),
        "name": doc.get("name"),
        "credential_type": doc.get("credential_type"),
        "has_value": True,
        "metadata": dict(doc.get("metadata") or {}),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }
    assert "encrypted_value" not in view and "value" not in view
    return view


async def create_credential(
    owner_id: str,
    project_id: str,
    name: str,
    credential_type: str,
    value: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> dict:
    """Encrypt and store a new credential. Returns the public view."""
    project = await repo.projects.get(project_id, owner_id)
    if project is None:
        raise CredentialError("Project not found", 404)
    if credential_type not in CREDENTIAL_TYPES:
        raise CredentialError(
            f"Unsupported credential type '{credential_type}'", 400
        )
    if not isinstance(value, str) or not value:
        raise CredentialError("Credential value is required", 400)
    if len(value) > _MAX_VALUE_CHARS:
        raise CredentialError("Credential value exceeds 8KB", 400)
    existing = await repo.tool_credentials.find_one(
        {"owner_id": owner_id, "project_id": project_id, "name": name}
    )
    if existing is not None:
        raise CredentialError(f"Credential '{name}' already exists", 409)
    now = now_iso()
    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "name": name,
        "credential_type": credential_type,
        "encrypted_value": security.encrypt_secret(value),
        "metadata": dict(metadata or {}),
        "created_at": now,
        "updated_at": now,
    }
    await repo.tool_credentials.insert(doc)
    return _public(doc)


async def list_credentials(owner_id: str, project_id: str) -> List[dict]:
    """List public credential views for a project."""
    docs = await repo.tool_credentials.list(
        {"owner_id": owner_id, "project_id": project_id}, limit=500
    )
    return [_public(doc) for doc in docs]


async def get_credential(owner_id: str, cred_id: str) -> Optional[dict]:
    """Return the public view, or None when absent / not owned."""
    doc = await repo.tool_credentials.get(cred_id, owner_id)
    if doc is None:
        return None
    return _public(doc)


async def get_credential_doc(owner_id: str, cred_id: str) -> Optional[dict]:
    """Return the raw internal document (includes ``encrypted_value``)."""
    return await repo.tool_credentials.get(cred_id, owner_id)


async def update_credential(
    owner_id: str, cred_id: str, patch: Dict[str, Any]
) -> dict:
    """Update type/value/metadata. Re-encrypts when the value changes."""
    doc = await repo.tool_credentials.get(cred_id, owner_id)
    if doc is None:
        raise CredentialError("Credential not found", 404)
    update: Dict[str, Any] = {}
    if "credential_type" in patch and patch["credential_type"] is not None:
        if patch["credential_type"] not in CREDENTIAL_TYPES:
            raise CredentialError(
                f"Unsupported credential type '{patch['credential_type']}'", 400
            )
        update["credential_type"] = patch["credential_type"]
    if "value" in patch and patch["value"] is not None:
        value = patch["value"]
        if not isinstance(value, str) or not value:
            raise CredentialError("Credential value is required", 400)
        if len(value) > _MAX_VALUE_CHARS:
            raise CredentialError("Credential value exceeds 8KB", 400)
        update["encrypted_value"] = security.encrypt_secret(value)
    if "metadata" in patch and patch["metadata"] is not None:
        if not isinstance(patch["metadata"], dict):
            raise CredentialError("metadata must be an object", 400)
        update["metadata"] = dict(patch["metadata"])
    if not update:
        return _public(doc)
    update["updated_at"] = now_iso()
    updated = await repo.tool_credentials.update(cred_id, owner_id, update)
    return _public(updated or {**doc, **update})


def _walk_config_refs(config: Any) -> Tuple[List[str], List[str]]:
    """Collect (credential_ids, secret_names) referenced anywhere in config."""
    ids: List[str] = []
    names: List[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                if (
                    isinstance(key, str)
                    and key.endswith("credential_id")
                    and isinstance(val, str)
                ):
                    ids.append(val)
                visit(val)
        elif isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, str):
            for match in SECRET_PATTERN.finditer(node):
                names.append(match.group(1))

    visit(config)
    return ids, names


async def find_referencing_versions(
    owner_id: str, credential_id: str
) -> List[dict]:
    """Find tool versions referencing a credential by id or by secret name.

    Name matches only count within the same project: the referencing tool's
    ``project_id`` must equal the credential's ``project_id``.
    """
    cred = await repo.tool_credentials.get(credential_id, owner_id)
    if cred is None:
        raise CredentialError("Credential not found", 404)
    cred_name = cred.get("name")
    cred_project = cred.get("project_id")
    versions = await repo.tool_versions.list({"owner_id": owner_id}, limit=5000)
    tool_cache: Dict[str, Optional[dict]] = {}
    refs: List[dict] = []
    for version in versions:
        config = version.get("implementation_config") or {}
        ids, names = _walk_config_refs(config)
        if credential_id in ids:
            refs.append(
                {"tool_id": version.get("tool_id"), "version": version.get("version")}
            )
            continue
        if cred_name in names:
            tool_id = version.get("tool_id")
            if tool_id not in tool_cache:
                try:
                    tool_cache[tool_id] = await repo.tools.get(tool_id, owner_id)
                except Exception:
                    tool_cache[tool_id] = None
            tool = tool_cache.get(tool_id)
            if tool is not None and tool.get("project_id") == cred_project:
                refs.append({"tool_id": tool_id, "version": version.get("version")})
    return refs


async def delete_credential(owner_id: str, cred_id: str) -> dict:
    """Delete a credential; refuses with 409 while versions reference it."""
    doc = await repo.tool_credentials.get(cred_id, owner_id)
    if doc is None:
        raise CredentialError("Credential not found", 404)
    refs = await find_referencing_versions(owner_id, cred_id)
    if refs:
        raise CredentialError(
            "Credential is referenced by tool versions and cannot be deleted", 409
        )
    await repo.tool_credentials.delete(cred_id, owner_id)
    return {"ok": True}


async def resolve_credential_value(
    owner_id: str, credential_id: str, expected_project_id: Optional[str] = None
) -> str:
    """Decrypt a credential value. Server-side only; never expose to clients."""
    doc = await repo.tool_credentials.get(credential_id, owner_id)
    if doc is None:
        raise CredentialError("Credential not found", 404)
    if (
        expected_project_id is not None
        and doc.get("project_id") != expected_project_id
    ):
        raise CredentialError("Credential does not belong to this project", 403)
    try:
        return security.decrypt_secret(doc["encrypted_value"])
    except Exception as exc:
        raise CredentialError("Stored credential is unreadable", 500) from exc


async def resolve_secret_template(
    value: str, owner_id: str, project_id: Optional[str]
) -> Tuple[str, str]:
    """Replace ``{{secret:NAME}}`` placeholders with decrypted values.

    Returns ``(resolved, sanitized)`` where the sanitized string carries
    ``[secret:NAME]`` markers. Unknown names raise 400 without proceeding,
    so a call can never go out with an unreplaced placeholder.
    """
    if not isinstance(value, str) or "{{" not in value:
        return value, value
    names: List[str] = []
    for match in SECRET_PATTERN.finditer(value):
        if match.group(1) not in names:
            names.append(match.group(1))
    if not names:
        return value, value
    plaintexts: Dict[str, str] = {}
    for name in names:
        cred = await repo.tool_credentials.find_one(
            {"owner_id": owner_id, "project_id": project_id, "name": name}
        )
        if cred is None:
            raise CredentialError(f"Unknown credential '{name}'", 400)
        try:
            plaintexts[name] = security.decrypt_secret(cred["encrypted_value"])
        except Exception as exc:
            raise CredentialError(
                f"Stored credential '{name}' is unreadable", 500
            ) from exc

    def _resolved(match: re.Match) -> str:
        return plaintexts[match.group(1)]

    def _sanitized(match: re.Match) -> str:
        return SANITIZED_FMT.format(name=match.group(1))

    return SECRET_PATTERN.sub(_resolved, value), SECRET_PATTERN.sub(_sanitized, value)


def sanitize_config(config: Any) -> Any:
    """Copy a config with secrets scrubbed: ``{{secret:*}}`` -> ``[secret:*]``.

    Any ``*credential_id`` holding a mapping is reduced to the id string only,
    and stored ``encrypted_value`` keys are dropped.
    """
    if isinstance(config, str):
        return SECRET_PATTERN.sub(
            lambda m: SANITIZED_FMT.format(name=m.group(1)), config
        )
    if isinstance(config, list):
        return [sanitize_config(item) for item in config]
    if isinstance(config, dict):
        out: Dict[str, Any] = {}
        for key, val in config.items():
            if key == "encrypted_value":
                continue
            if (
                isinstance(key, str)
                and key.endswith("credential_id")
                and isinstance(val, dict)
            ):
                out[key] = val.get("id")
                continue
            out[key] = sanitize_config(val)
        return out
    return copy.deepcopy(config)
