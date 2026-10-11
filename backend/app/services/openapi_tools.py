"""OpenAPI import for the Custom Tool Platform.

Parses OpenAPI 3.x specs (JSON or YAML), extracts operations with local
``$ref`` resolution, and converts operations into ``CustomToolCreate``
payloads backed by the HTTP executor.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx
import yaml

from .http_tool import HttpToolError, RISK_BY_METHOD, validate_implementation_config
from .. import config, urlguard

MAX_OPERATIONS = 200
_REF_DEPTH = 5
_DESC_CAP = 2000


class OpenAPIError(ValueError):
    """Raised for OpenAPI import violations. Carries an HTTP code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def parse_spec(spec_text: str) -> dict:
    """Parse raw spec text into a dict; enforces size cap + OpenAPI 3.x."""
    if not isinstance(spec_text, str) or not spec_text.strip():
        raise OpenAPIError("Spec text is required", 422)
    if len(spec_text.encode("utf-8")) > int(
        getattr(config, "OPENAPI_MAX_SPEC_BYTES", 2097152)
    ):
        raise OpenAPIError("Spec exceeds maximum size", 422)
    spec: Any = None
    text = spec_text.strip()
    if text.startswith("{"):
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OpenAPIError("Spec is not valid JSON", 422) from exc
    if spec is None:
        try:
            spec = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise OpenAPIError("Spec is not valid JSON or YAML", 422) from exc
    if not isinstance(spec, dict):
        raise OpenAPIError("Spec must be a JSON/YAML object", 422)
    openapi_version = str(spec.get("openapi") or "")
    if not openapi_version.startswith("3."):
        if "swagger" in spec:
            raise OpenAPIError("Only OpenAPI 3.x is supported (Swagger 2.0 rejected)", 422)
        raise OpenAPIError("Only OpenAPI 3.x is supported", 422)
    return spec


async def fetch_spec_from_url(url: str) -> str:
    """Fetch a spec server-side behind the SSRF guard. No redirects."""
    if not isinstance(url, str) or not url.strip():
        raise OpenAPIError("spec_url is required", 422)
    try:
        safe_url = await urlguard.async_validate_provider_url(url.strip())
    except urlguard.UrlValidationError as exc:
        raise OpenAPIError(exc.message, 400) from exc
    cap = int(getattr(config, "OPENAPI_MAX_SPEC_BYTES", 2097152))
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get(safe_url)
    except httpx.TimeoutException as exc:
        raise OpenAPIError("Spec fetch timed out", 502) from exc
    except httpx.HTTPError as exc:
        raise OpenAPIError("Spec fetch failed", 502) from exc
    ctype = response.headers.get("content-type", "").lower()
    if ctype and not any(
        token in ctype for token in ("json", "yaml", "yml", "text")
    ):
        raise OpenAPIError("Unsupported spec content-type", 422)
    content = await response.aread()
    if len(content) > cap:
        raise OpenAPIError("Spec exceeds maximum size", 422)
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpenAPIError("Spec is not valid UTF-8 text", 422) from exc


def _resolve_ref(node: Any, spec: dict, depth: int = 0) -> Any:
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return {k: _resolve_ref(v, spec, depth) for k, v in node.items()}
    if not ref.startswith("#/") or depth >= _REF_DEPTH:
        # Remote refs are dropped with a note; over-deep refs stay unresolved.
        return {"_unresolved_ref": True} if not ref.startswith("#/") else dict(node)
    current: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return dict(node)
    resolved = _resolve_ref(current, spec, depth + 1)
    if isinstance(resolved, dict):
        merged = dict(resolved)
        for key, val in node.items():
            if key != "$ref":
                merged[key] = _resolve_ref(val, spec, depth + 1)
        return merged
    return resolved


def _sanitize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:_DESC_CAP]


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text or "").strip("_").lower()
    return slug or "tool"


def _host_slug(base_url: str) -> str:
    try:
        host = urlsplit(base_url).hostname or ""
    except Exception:
        host = ""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", host).strip("_").lower()
    return slug or "api"


def _base_url(spec: dict, override: Optional[str] = None) -> Tuple[str, List[str]]:
    if override:
        return override.rstrip("/"), [override.rstrip("/")]
    servers = spec.get("servers") or []
    urls = [str(s.get("url", "")).rstrip("/") for s in servers if isinstance(s, dict) and s.get("url")]
    if urls:
        return urls[0], urls
    return "", []



def extract_operations(
    spec: dict, base_url_override: Optional[str] = None
) -> dict:
    """Extract a capped, ref-resolved operation list from an OpenAPI spec."""
    if not isinstance(spec, dict):
        raise OpenAPIError("Spec must be an object", 422)
    base_url, servers = _base_url(spec, base_url_override)
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        raise OpenAPIError("Spec.paths must be an object", 422)
    operations: List[dict] = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, raw_op in item.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            if not isinstance(raw_op, dict):
                continue
            op = _resolve_ref(raw_op, spec)
            if not isinstance(op, dict):
                continue
            raw_params = op.get("parameters") or []
            parameters: List[dict] = []
            if isinstance(raw_params, list):
                for raw_p in raw_params:
                    param = _resolve_ref(raw_p, spec)
                    if not isinstance(param, dict):
                        continue
                    loc = str(param.get("in") or "").lower()
                    if loc not in ("query", "path", "header"):
                        continue
                    parameters.append({
                        "name": str(param.get("name") or ""),
                        "location": loc,
                        "required": bool(param.get("required", loc == "path")),
                        "schema": param.get("schema") if isinstance(param.get("schema"), dict) else {},
                        "description": _sanitize_text(param.get("description")),
                    })
            request_body_schema: Optional[dict] = None
            raw_body = op.get("requestBody")
            if isinstance(raw_body, dict):
                body = _resolve_ref(raw_body, spec)
                if isinstance(body, dict):
                    content = body.get("content") or {}
                    if isinstance(content, dict):
                        media = content.get("application/json") or next(
                            (v for v in content.values() if isinstance(v, dict)), None
                        )
                        if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                            request_body_schema = _resolve_ref(media["schema"], spec)
            operation_id = str(op.get("operationId") or f"{method.upper()}_{path}")
            security_req = op.get("security") or spec.get("security") or []
            operations.append({
                "operation_id": operation_id,
                "method": method.upper(),
                "path": path,
                "url_template": f"{base_url}{path}" if base_url else path,
                "summary": _sanitize_text(op.get("summary")),
                "description": _sanitize_text(op.get("description")),
                "parameters": parameters,
                "request_body_schema": request_body_schema,
                "security": security_req if isinstance(security_req, list) else [],
            })
            if len(operations) >= MAX_OPERATIONS:
                break
        if len(operations) >= MAX_OPERATIONS:
            break
    info = spec.get("info") or {}
    return {
        "title": _sanitize_text(info.get("title") if isinstance(info, dict) else ""),
        "version": str((info.get("version") if isinstance(info, dict) else "") or ""),
        "servers": servers,
        "base_url": base_url,
        "operation_count": len(operations),
        "operations": operations,
    }


def _schema_for_param(schema: Any, required: bool) -> Dict[str, Any]:
    if isinstance(schema, dict) and schema:
        out = dict(schema)
        out.pop("description", None)
        return out
    return {"type": "string"}


def operation_to_tool_def(
    operation: Dict[str, Any],
    *,
    project_id: Optional[str],
    credential_id: Optional[str] = None,
    name_override: Optional[str] = None,
    risk_override: Optional[str] = None,
    permissions_override: Optional[List[str]] = None,
    description_override: Optional[str] = None,
    display_name_override: Optional[str] = None,
) -> dict:
    """Convert an extracted operation into a CustomToolCreate-compatible dict."""
    method = str(operation.get("method", "GET")).upper()
    path = str(operation.get("path", "/"))
    base = str(operation.get("url_template") or path)
    op_id = str(operation.get("operation_id") or f"{method}_{path}")
    name = _slug(name_override or op_id)
    properties: Dict[str, Any] = {}
    required: List[str] = []
    query_mapping: Dict[str, Any] = {}
    headers_mapping: Dict[str, Any] = {}
    for param in operation.get("parameters") or []:
        pname = str(param.get("name") or "")
        if not pname:
            continue
        loc = param.get("location")
        properties[pname] = _schema_for_param(param.get("schema"), bool(param.get("required")))
        if param.get("required"):
            required.append(pname)
        if loc == "query":
            query_mapping[pname] = f"{{{pname}}}"
        elif loc == "header":
            headers_mapping[pname] = f"{{{pname}}}"
    body_mapping: Dict[str, Any] = {}
    body_schema = operation.get("request_body_schema")
    if isinstance(body_schema, dict) and body_schema.get("type") == "object":
        for prop in (body_schema.get("properties") or {}):
            properties.setdefault(prop, {"type": "string"})
            body_mapping[prop] = f"{{{prop}}}"
        for req in body_schema.get("required") or []:
            if req not in required:
                required.append(req)
    elif isinstance(body_schema, dict) and body_schema:
        properties.setdefault("body", dict(body_schema))
        body_mapping = {"body": "{body}"}

    impl: Dict[str, Any] = {
        "kind": "http",
        "method": method,
        "url_template": base,
        "query_params": query_mapping,
        "headers": headers_mapping,
    }
    if body_mapping:
        impl["body"] = body_mapping
    if credential_id:
        impl["credential_id"] = credential_id
    impl["openapi"] = {"operation_id": op_id, "path": path}
    try:
        cleaned = validate_implementation_config(impl)
    except HttpToolError as exc:
        raise OpenAPIError(
            exc.message, exc.status_code if isinstance(exc.status_code, int) else 400
        ) from exc

    risk = risk_override or RISK_BY_METHOD.get(method, "low")
    host = _host_slug(base if "://" in base else f"http://{base.lstrip('/')}")
    permissions = (
        permissions_override
        if permissions_override is not None
        else [f"http.{host}.{method.lower()}"]
    )
    description = (
        description_override
        if description_override is not None
        else (operation.get("summary") or operation.get("description") or "")
    )
    return {
        "project_id": project_id,
        "name": name,
        "display_name": display_name_override or str(operation.get("summary") or name),
        "description": _sanitize_text(description),
        "enabled": True,
        "tags": ["openapi"],
        "version": {
            "tool_type": "openapi",
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
            "output_schema": {},
            "permissions": list(permissions),
            "risk_level": risk,
            "implementation_config": cleaned,
            "source": "openapi",
        },
    }


async def preview_import(
    spec_text: str, base_url_override: Optional[str] = None
) -> dict:
    """Parse + extract a spec for the selection UI (no persistence)."""
    spec = parse_spec(spec_text)
    extracted = extract_operations(spec, base_url_override)
    return {
        "title": extracted["title"],
        "version": extracted["version"],
        "servers": extracted["servers"],
        "base_url": extracted["base_url"],
        "operation_count": extracted["operation_count"],
        "operations": extracted["operations"],
    }


async def generate_tool_payloads(
    spec_text: str,
    base_url_override: Optional[str],
    selections: List[Any],
) -> List[dict]:
    """Build CustomToolCreate dicts for the selected (included) operations."""
    spec = parse_spec(spec_text)
    extracted = extract_operations(spec, base_url_override)
    by_id = {op["operation_id"]: op for op in extracted["operations"]}
    payloads: List[dict] = []
    for sel in selections or []:
        if isinstance(sel, dict):
            op_id = sel.get("operation_id")
            include = sel.get("include", True)
            extra = sel
        else:
            op_id = getattr(sel, "operation_id", None)
            include = getattr(sel, "include", True)
            extra = {
                "name": getattr(sel, "name", None),
                "display_name": getattr(sel, "display_name", None),
                "description": getattr(sel, "description", None),
                "risk_level": getattr(sel, "risk_level", None),
                "permissions": getattr(sel, "permissions", None),
                "credential_id": getattr(sel, "credential_id", None),
                "implementation_config": getattr(sel, "implementation_config", None),
            }
        if not include:
            continue
        operation = by_id.get(op_id)
        if operation is None:
            raise OpenAPIError(f"Unknown operation '{op_id}'", 400)
        payload = operation_to_tool_def(
            operation,
            project_id=None,  # caller stamps the project
            credential_id=extra.get("credential_id"),
            name_override=extra.get("name"),
            risk_override=extra.get("risk_level"),
            permissions_override=extra.get("permissions"),
            description_override=extra.get("description"),
            display_name_override=extra.get("display_name"),
        )
        impl_extra = extra.get("implementation_config")
        if isinstance(impl_extra, dict) and impl_extra:
            merged = dict(payload["version"]["implementation_config"])
            merged.update(impl_extra)
            try:
                payload["version"]["implementation_config"] = validate_implementation_config(merged)
            except HttpToolError as exc:
                raise OpenAPIError(
                    exc.message, exc.status_code if isinstance(exc.status_code, int) else 400
                ) from exc
        payloads.append(payload)
    return payloads
