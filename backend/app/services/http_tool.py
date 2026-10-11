"""HTTP executor for custom tools (Custom Tool Platform).

Order of operations for every call: resolve ``{{secret:NAME}}`` placeholders
server-side, then render ``{param}`` templates, then SSRF-validate the final
URL immediately before the outbound request. Redirects are disabled, methods
are allowlisted, and responses are size-capped. Secret values never appear in
returned dicts, snapshots, errors, or traces.
"""
from __future__ import annotations

import copy
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx

from . import tool_credentials
from .. import config, urlguard

ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

RISK_BY_METHOD = {
    "GET": "low",
    "POST": "high",
    "PUT": "high",
    "PATCH": "high",
    "DELETE": "critical",
}

SECRET_HINT_PATTERN = re.compile(r"\{\{")
PARAM_PATTERN = re.compile(r"\{([A-Za-z0-9_\-]+)\}")

FORBIDDEN_HEADERS = {
    "host",
    "content-length",
    "connection",
    "transfer-encoding",
}

_PLACEHOLDER_DUMMY = "x"

_MAX_BODY_BYTES_FALLBACK = 200 * 1024
_TEXT_PREVIEW_CHARS = 200 * 1024


class HttpToolError(ValueError):
    """Raised for HTTP tool contract violations. Carries an HTTP code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def render_template_string(template: str, arguments: Dict[str, Any]) -> str:
    """Render ``{key}`` placeholders with URL-quoted argument values.

    Missing keys raise a safe 400 naming the key (values are never echoed).
    Templates containing ``{{`` (secret syntax) are rejected here; secrets
    must be resolved first via
    :func:`tool_credentials.resolve_secret_template`.
    """
    if not isinstance(template, str):
        raise HttpToolError("Template must be a string", 400)
    if SECRET_HINT_PATTERN.search(template):
        raise HttpToolError(
            "Secret placeholders must be resolved before rendering", 400
        )

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        if not isinstance(arguments, dict) or key not in arguments:
            raise HttpToolError(f"Missing URL parameter '{key}'", 400)
        return quote(str(arguments[key]), safe="")

    return PARAM_PATTERN.sub(_replace, template)


def _dummy_render(template: str) -> str:
    """Substitute dummy values for ``{param}`` placeholders (save-time check)."""
    if not isinstance(template, str):
        return template
    return PARAM_PATTERN.sub(_PLACEHOLDER_DUMMY, template)


def _check_method(method: Any) -> str:
    if not isinstance(method, str) or method.upper() not in ALLOWED_METHODS:
        raise HttpToolError(
            f"Unsupported HTTP method '{method}'", 400
        )
    return method.upper()


def validate_implementation_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and clean an HTTP implementation config. Unknown keys ignored."""
    if not isinstance(cfg, dict):
        raise HttpToolError("implementation_config must be an object", 400)
    if "method" not in cfg:
        raise HttpToolError("implementation_config.method is required", 400)
    method = _check_method(cfg.get("method"))

    url_template = cfg.get("url_template")
    if not isinstance(url_template, str) or not url_template.strip():
        raise HttpToolError("implementation_config.url_template is required", 400)
    url_template = url_template.strip()
    max_url_len = int(getattr(config, "TOOL_HTTP_MAX_URL_LENGTH", 2048))
    if len(url_template) > max_url_len:
        raise HttpToolError("url_template exceeds maximum length", 400)
    # Secret placeholders are resolved server-side before rendering; the
    # save-time shape check below neutralises them with dummies.
    shape = re.sub(
        r"\{\{\s*secret\s*:[^}]+\}\}", _PLACEHOLDER_DUMMY, url_template
    )
    shape = _dummy_render(shape)
    scheme = urlsplit(shape).scheme.lower()
    # A fully-parameterized host (e.g. "{base}/path") has no static scheme or
    # host; the call-time guard still validates the rendered URL every call.
    if scheme and scheme not in ("http", "https"):
        raise HttpToolError("url_template must use http or https", 400)
    static_parts = urlsplit(shape)
    if static_parts.hostname:
        # urlguard allows+preserves query strings; guard the host/path shape here.
        guard_target = urlunsplit((
            static_parts.scheme, static_parts.netloc,
            static_parts.path or "", "", "",
        ))
        try:
            urlguard.validate_provider_url(guard_target)
        except urlguard.UrlValidationError as exc:
            raise HttpToolError(exc.message, 400) from exc

    query_params = cfg.get("query_params") or {}
    if not isinstance(query_params, dict):
        raise HttpToolError("query_params must be an object", 400)
    headers = cfg.get("headers") or {}
    if not isinstance(headers, dict):
        raise HttpToolError("headers must be an object", 400)
    for header_name in headers:
        if str(header_name).lower() in FORBIDDEN_HEADERS:
            raise HttpToolError(f"Header '{header_name}' is not allowed", 400)
    body = cfg.get("body")
    if body is not None and not isinstance(body, dict):
        raise HttpToolError("body must be an object", 400)

    timeout = cfg.get("timeout", int(getattr(config, "TOOL_HTTP_TIMEOUT_SECONDS", 15)))
    try:
        timeout = int(timeout)
    except (TypeError, ValueError) as exc:
        raise HttpToolError("timeout must be an integer number of seconds", 400) from exc
    if not 1 <= timeout <= 60:
        raise HttpToolError("timeout must be between 1 and 60 seconds", 400)

    credential: Optional[dict] = None
    credential_id = cfg.get("credential_id")
    if credential_id is None and isinstance(cfg.get("credential"), dict):
        credential_id = cfg["credential"].get("credential_id")
    if credential_id is not None:
        if not isinstance(credential_id, str) or not credential_id.strip():
            raise HttpToolError("credential_id must be a non-empty string", 400)
        credential = {"credential_id": credential_id}
        placement = cfg.get("credential_placement") or cfg.get("credential") or {}
        if isinstance(placement, dict):
            location = placement.get("location", "header")
            if location not in ("header", "query"):
                raise HttpToolError(
                    "credential placement location must be 'header' or 'query'", 400
                )
            scheme_name = placement.get("scheme", "bearer")
            if scheme_name not in ("bearer", "basic", "none"):
                raise HttpToolError(
                    "credential placement scheme must be bearer, basic or none", 400
                )
            credential["placement"] = {
                "location": location,
                "name": str(placement.get("name") or "Authorization"),
                "scheme": scheme_name,
            }

    response_cfg: Dict[str, Any] = {}
    response = cfg.get("response") or {}
    if not isinstance(response, dict):
        raise HttpToolError("response must be an object", 400)
    if response.get("extract_path") is not None:
        if not isinstance(response["extract_path"], str):
            raise HttpToolError("response.extract_path must be a string", 400)
        response_cfg["extract_path"] = response["extract_path"]
    max_bytes = response.get("max_bytes")
    if max_bytes is not None:
        try:
            max_bytes = int(max_bytes)
        except (TypeError, ValueError) as exc:
            raise HttpToolError("response.max_bytes must be an integer", 400) from exc
        if max_bytes <= 0:
            raise HttpToolError("response.max_bytes must be positive", 400)
        response_cfg["max_bytes"] = max_bytes

    cleaned: Dict[str, Any] = {
        "kind": "http",
        "method": method,
        "url_template": url_template,
        "query_params": {str(k): v for k, v in query_params.items()},
        "headers": {str(k): v for k, v in headers.items()},
        "timeout": timeout,
        "risk_suggestion": RISK_BY_METHOD[method],
    }
    if body is not None:
        cleaned["body"] = body
    if credential is not None:
        cleaned["credential_id"] = credential["credential_id"]
        if "placement" in credential:
            cleaned["credential_placement"] = credential["placement"]
        # Forward the legacy nested form when it was given.
        if isinstance(cfg.get("credential"), dict):
            cleaned["credential"] = {"credential_id": credential["credential_id"]}
    if response_cfg:
        cleaned["response"] = response_cfg
    if isinstance(cfg.get("openapi"), dict):
        cleaned["openapi"] = dict(cfg["openapi"])
    if isinstance(cfg.get("query"), dict):
        cleaned["query"] = dict(cfg["query"])
    return cleaned


async def _resolve_templates(
    node: Any, owner_id: str, project_id: Optional[str]
) -> Tuple[Any, Any]:
    """Recursively resolve secrets then render params in template leaves."""
    # Returns (resolved_node, sanitized_node).
    if isinstance(node, str):
        resolved, sanitized = await tool_credentials.resolve_secret_template(
            node, owner_id, project_id
        )
        return resolved, sanitized
    if isinstance(node, list):
        resolved_items, sanitized_items = [], []
        for item in node:
            r, s = await _resolve_templates(item, owner_id, project_id)
            resolved_items.append(r)
            sanitized_items.append(s)
        return resolved_items, sanitized_items
    if isinstance(node, dict):
        resolved_d, sanitized_d = {}, {}
        for key, val in node.items():
            r, s = await _resolve_templates(val, owner_id, project_id)
            resolved_d[key] = r
            sanitized_d[key] = s
        return resolved_d, sanitized_d
    return node, node


def _render_node(node: Any, arguments: Dict[str, Any]) -> Any:
    if isinstance(node, str):
        if "{" in node:
            return render_template_string(node, arguments)
        return node
    if isinstance(node, list):
        return [_render_node(item, arguments) for item in node]
    if isinstance(node, dict):
        return {key: _render_node(val, arguments) for key, val in node.items()}
    return node


def _extract_path(body: Any, path: str) -> Any:
    current = body
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise HttpToolError(f"Response path '{path}' was not found", 502)
    return current


def _redact_query(url: str, secret_param_names: List[str]) -> str:
    if not secret_param_names:
        return url
    parts = urlsplit(url)
    if not parts.query:
        return url
    names = set(secret_param_names)
    chunks = []
    for chunk in parts.query.split("&"):
        name = chunk.split("=", 1)[0] if chunk else ""
        chunks.append(f"{name}=[secret]" if name in names else chunk)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, "&".join(chunks), parts.fragment)
    )


async def execute_http(
    owner_id: str,
    project_id: Optional[str],
    impl_config: Dict[str, Any],
    arguments: Dict[str, Any],
) -> dict:
    """Execute an HTTP tool call. Never leaks secret values."""
    cfg = validate_implementation_config(impl_config or {})
    method = cfg["method"]
    timeout = cfg.get("timeout", int(getattr(config, "TOOL_HTTP_TIMEOUT_SECONDS", 15)))
    max_bytes = (cfg.get("response") or {}).get(
        "max_bytes", int(getattr(config, "TOOL_HTTP_MAX_RESPONSE_BYTES", 1048576))
    )

    if not isinstance(arguments, dict):
        arguments = {}

    # 1. resolve secrets server-side (URL + query + headers + body).
    raw_url = cfg["url_template"]
    resolved_url_tpl, sanitized_url_tpl = await tool_credentials.resolve_secret_template(
        raw_url, owner_id, project_id
    )
    url_had_secret = resolved_url_tpl != raw_url
    resolved_qp, sanitized_qp = await _resolve_templates(
        cfg.get("query_params") or {}, owner_id, project_id
    )
    resolved_headers, sanitized_headers = await _resolve_templates(
        cfg.get("headers") or {}, owner_id, project_id
    )
    resolved_body_tpl, _ = await _resolve_templates(
        cfg.get("body") or {}, owner_id, project_id
    )

    # 2. render {params}.
    rendered_url = render_template_string(resolved_url_tpl, arguments)
    rendered_qp = _render_node(resolved_qp, arguments)
    rendered_headers = _render_node(resolved_headers, arguments)
    rendered_body = _render_node(resolved_body_tpl, arguments) if cfg.get("body") else None

    # 3. credential placement (id reference only; value resolved server-side).
    credential_id = cfg.get("credential_id")
    secret_query_names: List[str] = [
        str(k) for k, v in (sanitized_qp or {}).items() if "[secret:" in str(v)
    ]
    final_headers: Dict[str, str] = {str(k): str(v) for k, v in (rendered_headers or {}).items()}
    query_pairs: List[Tuple[str, str]] = [
        (str(k), str(v)) for k, v in (rendered_qp or {}).items() if v is not None
    ]
    credential_ref: Optional[dict] = None
    if credential_id:
        placement = cfg.get("credential_placement") or {}
        location = placement.get("location", "header")
        pname = placement.get("name", "Authorization")
        scheme_name = placement.get("scheme", "bearer")
        value = await tool_credentials.resolve_credential_value(
            owner_id, credential_id, expected_project_id=project_id
        )
        credential_ref = {"credential_id": credential_id}
        if location == "query":
            query_pairs.append((str(pname), value))
            secret_query_names.append(str(pname))
        else:
            if scheme_name == "bearer":
                final_headers[str(pname)] = f"Bearer {value}"
            elif scheme_name == "basic":
                final_headers[str(pname)] = f"Basic {value}"
            else:
                final_headers[str(pname)] = value

    for forbidden in FORBIDDEN_HEADERS:
        final_headers.pop(forbidden, None)
        # Case-insensitive sweep for oddly-cased variants.
        for key in [k for k in final_headers if k.lower() == forbidden]:
            final_headers.pop(key, None)

    # 4. assemble URL + SSRF guard immediately before the request.
    base_parts = urlsplit(rendered_url)
    existing_query = base_parts.query
    encoded_extra = urlencode(query_pairs, doseq=True)
    combined_query = "&".join(p for p in (existing_query, encoded_extra) if p)
    final_url = urlunsplit(
        (base_parts.scheme, base_parts.netloc, base_parts.path, combined_query, base_parts.fragment)
    )
    final_parts = urlsplit(final_url)
    guard_target = urlunsplit((
        final_parts.scheme, final_parts.netloc,
        final_parts.path or "", "", "",
    ))
    try:
        # Host/path shape guard: urlguard allows+preserves query strings, so
        # the query rides along verbatim while the host check pins the target.
        await urlguard.async_validate_provider_url(guard_target)
    except urlguard.UrlValidationError as exc:
        raise HttpToolError(exc.message, 400) from exc
    safe_url = final_url

    public_url = _redact_query(safe_url, secret_query_names)
    if url_had_secret:
        # A secret embedded in the URL path rendered to plaintext: redact it
        # segment-wise against the sanitized template shape.
        try:
            shape_rendered = render_template_string(sanitized_url_tpl, arguments)
        except HttpToolError:
            shape_rendered = None
        if shape_rendered:
            shape_parts = urlsplit(shape_rendered)
            pub_parts = urlsplit(public_url)
            pub_segs = pub_parts.path.split("/")
            shp_segs = shape_parts.path.split("/")
            if len(pub_segs) == len(shp_segs):
                pub_segs = [
                    "[secret]" if "[secret:" in h else s
                    for s, h in zip(pub_segs, shp_segs)
                ]
                public_url = urlunsplit((
                    pub_parts.scheme, pub_parts.netloc,
                    "/".join(pub_segs), pub_parts.query, "",
                ))
            elif "[secret:" in shape_parts.path:
                public_url = urlunsplit((
                    pub_parts.scheme, pub_parts.netloc,
                    shape_parts.path, pub_parts.query, "",
                ))

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            if rendered_body is not None:
                response = await client.request(method, safe_url, headers=final_headers, json=rendered_body)
            else:
                response = await client.request(method, safe_url, headers=final_headers)
    except httpx.TimeoutException as exc:
        raise HttpToolError("Upstream request timed out", 502) from exc
    except httpx.ConnectError as exc:
        raise HttpToolError("Upstream request failed (connection error)", 502) from exc
    except httpx.HTTPError as exc:
        raise HttpToolError("Upstream request failed", 502) from exc
    latency_ms = int((time.perf_counter() - start) * 1000)

    content = await response.aread()
    truncated = False
    if len(content) > max_bytes:
        raise HttpToolError("Upstream response exceeded size limit", 502)
    content_type = response.headers.get("content-type", "")
    body: Any
    if "json" in content_type.lower():
        try:
            body = response.json()
        except Exception:
            text = content.decode("utf-8", errors="replace")
            body = text[:_TEXT_PREVIEW_CHARS]
            truncated = len(text) > _TEXT_PREVIEW_CHARS
    else:
        text = content.decode("utf-8", errors="replace")
        if len(text) > _TEXT_PREVIEW_CHARS:
            body = text[:_TEXT_PREVIEW_CHARS]
            truncated = True
        else:
            body = text

    extract_path = (cfg.get("response") or {}).get("extract_path")
    if extract_path:
        body = _extract_path(body, extract_path)

    return {
        "status_code": response.status_code,
        "ok": response.status_code < 400,
        "headers": {"content-type": content_type} if content_type else {},
        "body": body,
        "truncated": truncated,
        "latency_ms": latency_ms,
        "url": public_url,
        "method": method,
        "credential_ref": credential_ref,
    }


def sanitized_call_snapshot(
    impl_config: Dict[str, Any],
    arguments: Dict[str, Any],
    *,
    credential_ref: Optional[dict] = None,
) -> dict:
    """Build a secret-free snapshot of the call for ToolCall metadata."""
    cfg = copy.deepcopy(impl_config or {})
    sanitized = tool_credentials.sanitize_config(cfg)
    url_tpl = str(sanitized.get("url_template") or "")
    try:
        url_preview = render_template_string(
            re.sub(r"\[secret:[^\]]+\]", _PLACEHOLDER_DUMMY, url_tpl),
            {k: _PLACEHOLDER_DUMMY for k in (arguments or {})},
        )
    except HttpToolError:
        url_preview = url_tpl
    headers = sanitized.get("headers") or {}
    return {
        "method": sanitized.get("method"),
        "url_sanitized": url_preview,
        "headers_sanitized": {str(k): ("[secret]" if "[secret:" in str(v) else v) for k, v in headers.items()},
        "credential_ref": credential_ref or (
            {"credential_id": sanitized.get("credential_id")}
            if sanitized.get("credential_id")
            else None
        ),
    }
