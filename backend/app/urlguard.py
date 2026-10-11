"""SSRF guard for user-supplied provider base URLs.

Provider URLs are attacker-influenced configuration: a researcher (or a stolen
token) can point an OpenAI-compatible provider at ``http://169.254.169.254/`` in
an attempt to make the backend fetch cloud metadata. This module validates such
URLs before they are stored *and* immediately before every outbound request.

Design notes:
  * Only ``http`` and ``https`` are accepted.
  * Embedded credentials (``user:pass@host``) are rejected so secrets never live
    in a URL that could be logged or echoed back.
  * Private, loopback, link-local, unspecified, multicast, reserved and cloud
    metadata addresses are rejected by default.
  * Hostnames are resolved (A/AAAA) and *every* resolved address must be public.
  * A narrow local-development override (``PROVIDER_ALLOW_LOCAL_URLS``) permits
    loopback/private targets for local gateways such as Ollama/LiteLLM, but it
    still rejects cloud metadata. An explicit ``PROVIDER_URL_ALLOWLIST`` can
    permit specific host[:port] entries, including metadata, on purpose.
  * ``SAFE_REDIRECTS`` documents the required HTTP-client behavior: redirects
    must be disabled (see :func:`is_cross_host_redirect`).

The module is dependency-free apart from the standard library and the existing
``httpx`` dependency (used only for exception alignment by callers).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from . import config

#: Redirects must never be followed by provider HTTP clients. Anything that
#: wants to follow a redirect must first re-validate the target host with this
#: module.
SAFE_REDIRECTS = False

#: Egress trust boundaries.
#:
#: ``LOCAL_EGRESS``  -- OmniLab itself opens the outbound connection. The target
#:                    host is resolved locally and every resolved address is
#:                    SSRF-checked. Used by Standard HTTP Fetch.
#: ``REMOTE_EGRESS`` -- a remote provider (TinyFish) opens the connection.
#:                    OmniLab performs only syntactic/policy validation of the
#:                    target and MUST NOT resolve ordinary public hostnames
#:                    locally: a hostname our resolver cannot see is not a
#:                    reason to withhold it from the remote provider.
LOCAL_EGRESS = "LOCAL"
REMOTE_EGRESS = "REMOTE"

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_CLOUD_METADATA_IPS = frozenset({"169.254.169.254", "100.100.100.200", "fd00:ec2::254"})


class UrlValidationError(Exception):
    """Raised when a provider URL fails SSRF validation.

    ``message`` is always safe to return to the client; it never echoes
    embedded credentials or the full offending URL.
    """

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class _UrlParts:
    normalized: str
    host: str
    port: Optional[int]
    allowlist_hit: bool
    literal_ip: Optional[str]
    is_localhost_name: bool


def _allow_local_enabled(allow_local: Optional[bool]) -> bool:
    if allow_local is not None:
        return bool(allow_local)
    return bool(getattr(config, "PROVIDER_ALLOW_LOCAL_URLS", False))


def _allow_private_enabled() -> bool:
    return bool(getattr(config, "PROVIDER_URL_ALLOW_PRIVATE", False))


def _allowlist_entries() -> List[str]:
    entries = getattr(config, "PROVIDER_URL_ALLOWLIST", None) or []
    return [str(entry).strip() for entry in entries if str(entry).strip()]


def _parse_allowlist_entry(entry: str) -> Optional[Tuple[str, Optional[int]]]:
    value = entry.strip()
    if not value:
        return None
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return value[1:].lower(), None
        host = value[1:end].lower()
        rest = value[end + 1:]
        if rest.startswith(":") and rest[1:].isdigit():
            return host, int(rest[1:])
        return host, None
    if value.count(":") == 1:
        host, port_text = value.split(":", 1)
        if port_text.isdigit():
            return host.lower(), int(port_text)
    return value.lower(), None


def _host_in_allowlist(host: str, port: Optional[int]) -> bool:
    if not host:
        return False
    normalized_host = host.lower().rstrip(".")
    for entry in _allowlist_entries():
        parsed = _parse_allowlist_entry(entry)
        if parsed is None:
            continue
        entry_host, entry_port = parsed
        if entry_host != normalized_host:
            continue
        if entry_port is None:
            return True
        if port is not None and entry_port == port:
            return True
    return False


def _kind_message(kind: str) -> str:
    return {
        "loopback": "Provider URL must not target localhost or loopback addresses",
        "private": "Provider URL must not target a private network address",
        "link_local": "Provider URL must not target a link-local address",
        "unspecified": "Provider URL must not target an unspecified address",
        "multicast": "Provider URL must not target a multicast address",
        "reserved": "Provider URL must not target a reserved address",
        "metadata": "Provider URL must not target a cloud metadata address",
    }.get(kind, "Provider URL must not target a non-public address")


def _classify_ip(ip: ipaddress._BaseAddress) -> str:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if str(ip) in _CLOUD_METADATA_IPS:
        return "metadata"
    if ip.is_loopback:
        return "loopback"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved:
        return "reserved"
    if ip.is_private:
        return "private"
    return "public"


def _check_addresses(
    addresses: Iterable[str],
    *,
    allow_local: bool,
    allow_private: bool,
    allowlist_hit: bool,
) -> None:
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UrlValidationError(
                "Provider URL resolved to an invalid address",
                code="DNS_RESOLUTION_FAILED",
            ) from exc
        kind = _classify_ip(ip)
        if kind == "public":
            continue
        if allowlist_hit:
            continue
        if kind == "metadata":
            raise UrlValidationError(_kind_message("metadata"), code="SSRF_BLOCKED")
        if kind == "loopback":
            if allow_local:
                continue
            raise UrlValidationError(_kind_message("loopback"), code="SSRF_BLOCKED")
        if kind in ("private", "link_local", "unspecified"):
            if allow_local or (allow_private and kind == "private"):
                continue
            raise UrlValidationError(_kind_message(kind), code="SSRF_BLOCKED")
        # multicast / reserved (and any future non-public kind)
        raise UrlValidationError(_kind_message(kind), code="SSRF_BLOCKED")


def _inspect(url: str, allow_local: Optional[bool]) -> _UrlParts:
    if not isinstance(url, str) or not url.strip():
        raise UrlValidationError("Provider URL is required", code="INVALID_URL")

    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UrlValidationError("Provider URL must use http or https", code="INVALID_URL")

    if parsed.username is not None or parsed.password is not None:
        raise UrlValidationError(
            "Provider URL must not include embedded credentials", code="INVALID_URL"
        )

    host = parsed.hostname
    if not host:
        raise UrlValidationError("Provider URL must include a host", code="INVALID_URL")

    try:
        port = parsed.port
    except ValueError as exc:
        raise UrlValidationError("Provider URL contains an invalid port", code="INVALID_URL") from exc

    # Query strings are allowed for general Web Fetch use. Only the host/IP is
    # subject to SSRF checks; a query such as ``?url=http://127.0.0.1`` must not
    # itself cause a block. Fragments are never sent outbound, so strip them.

    host_normalized = host.lower().rstrip(".")
    if not host_normalized:
        raise UrlValidationError("Provider URL must include a host", code="INVALID_URL")

    allowlist_hit = _host_in_allowlist(host_normalized, port)
    literal_ip: Optional[str] = None
    try:
        ipaddress.ip_address(host_normalized)
        literal_ip = host_normalized
    except ValueError:
        literal_ip = None
    is_localhost_name = host_normalized == "localhost" or host_normalized.endswith(".localhost")

    netloc = f"[{host_normalized}]" if ":" in host_normalized else host_normalized
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = parsed.path or ""
    if path == "/":
        path = ""
    path = path.rstrip("/")
    query = parsed.query or ""
    normalized = f"{scheme}://{netloc}{path}" + (f"?{query}" if query else "")

    return _UrlParts(
        normalized=normalized,
        host=host_normalized,
        port=port,
        allowlist_hit=allowlist_hit,
        literal_ip=literal_ip,
        is_localhost_name=is_localhost_name,
    )


def _resolve_host_sync(host: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as exc:
        raise UrlValidationError(
            "Provider URL host could not be resolved", code="DNS_RESOLUTION_FAILED"
        ) from exc
    addresses: List[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr and sockaddr[0]:
            addresses.append(str(sockaddr[0]))
    if not addresses:
        raise UrlValidationError(
            "Provider URL host could not be resolved", code="DNS_RESOLUTION_FAILED"
        )
    return addresses


def _safe_diagnostic_hostname(host: Any) -> str:
    """Extract a bare hostname without credentials or URL decoration.

    Plain hostnames are returned verbatim (``{"hostname": host}``); only
    URL/credential-decorated inputs are sanitized so diagnostics never leak
    secrets.
    """
    raw = host if isinstance(host, str) else str(host)
    if "://" not in raw and "@" not in raw:
        return raw
    value = raw.strip()
    if "://" in value:
        try:
            parsed = urlsplit(value)
            if parsed.hostname:
                return parsed.hostname.lower().rstrip(".")
        except ValueError:
            pass
    if "@" in value:
        value = value.rsplit("@", 1)[-1]
    if value.startswith("[") and "]" in value:
        value = value[1:value.find("]")]
    return value.lower().rstrip(".") or raw.strip()


def resolve_host_diagnostics(host: str) -> Dict[str, Any]:
    """Return safe DNS diagnostics for ``host`` (never raises).

    The result contains no credentials, only the hostname, resolved
    addresses, address families, and a validation marker (``"ok"`` or an
    error code such as ``"DNS_RESOLUTION_FAILED"`` / ``"SSRF_BLOCKED"``).
    """
    hostname = _safe_diagnostic_hostname(host)
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        message = "Provider URL host could not be resolved"
        return {
            "hostname": hostname,
            "addresses": [],
            "address_count": 0,
            "families": [],
            "validation": "DNS_RESOLUTION_FAILED",
            "error": message,
        }
    except Exception as exc:  # defensive: never raise
        return {
            "hostname": hostname,
            "addresses": [],
            "address_count": 0,
            "families": [],
            "validation": "DNS_RESOLUTION_FAILED",
            "error": "Provider URL host could not be resolved",
        }
    try:
        addresses: List[str] = []
        for info in infos:
            sockaddr = info[4]
            if sockaddr and sockaddr[0]:
                addresses.append(str(sockaddr[0]))
        if not addresses:
            return {
                "hostname": hostname,
                "addresses": [],
                "address_count": 0,
                "families": [],
                "validation": "DNS_RESOLUTION_FAILED",
                "error": "Provider URL host could not be resolved",
            }
        families: List[str] = []
        seen = set()
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            fam = "IPv6" if isinstance(ip, ipaddress.IPv6Address) else "IPv4"
            if fam not in seen:
                seen.add(fam)
                families.append(fam)
        return {
            "hostname": hostname,
            "addresses": addresses,
            "address_count": len(addresses),
            "families": families,
            "validation": "ok",
            "error": None,
        }
    except Exception as exc:  # defensive: never raise
        return {
            "hostname": hostname,
            "addresses": [],
            "address_count": 0,
            "families": [],
            "validation": "DNS_RESOLUTION_FAILED",
            "error": "Provider URL host could not be resolved",
        }


def _validate_parts(parts: _UrlParts, allow_local: Optional[bool]) -> str:
    if parts.allowlist_hit:
        return parts.normalized

    allow_local_flag = _allow_local_enabled(allow_local)
    allow_private_flag = _allow_private_enabled()

    if parts.literal_ip is not None:
        _check_addresses(
            [parts.literal_ip],
            allow_local=allow_local_flag,
            allow_private=allow_private_flag,
            allowlist_hit=parts.allowlist_hit,
        )
        return parts.normalized

    if parts.is_localhost_name:
        if allow_local_flag:
            return parts.normalized
        raise UrlValidationError(_kind_message("loopback"), code="SSRF_BLOCKED")

    try:
        addresses = _resolve_host_sync(parts.host)
    except (socket.gaierror, OSError) as exc:
        raise UrlValidationError(
            "Provider URL host could not be resolved", code="DNS_RESOLUTION_FAILED"
        ) from exc
    _check_addresses(
        addresses,
        allow_local=allow_local_flag,
        allow_private=allow_private_flag,
        allowlist_hit=parts.allowlist_hit,
    )
    return parts.normalized


def validate_provider_url(url: str, *, allow_local: Optional[bool] = None) -> str:
    """Validate and normalize a provider base URL (raises ``UrlValidationError``)."""
    parts = _inspect(url, allow_local)
    return _validate_parts(parts, allow_local)


async def async_validate_provider_url(url: str, *, allow_local: Optional[bool] = None) -> str:
    """Async variant that performs DNS resolution off the event loop.

    Use immediately before each outbound request to shrink the DNS-rebinding
    window (see module docstring; a residual TOCTOU window remains between this
    check and the HTTP client's own resolution).
    """
    parts = _inspect(url, allow_local)
    if parts.allowlist_hit:
        return parts.normalized

    allow_local_flag = _allow_local_enabled(allow_local)
    allow_private_flag = _allow_private_enabled()

    if parts.literal_ip is not None:
        _check_addresses(
            [parts.literal_ip],
            allow_local=allow_local_flag,
            allow_private=allow_private_flag,
            allowlist_hit=parts.allowlist_hit,
        )
        return parts.normalized

    if parts.is_localhost_name:
        if allow_local_flag:
            return parts.normalized
        raise UrlValidationError(_kind_message("loopback"), code="SSRF_BLOCKED")

    try:
        addresses = await asyncio.to_thread(_resolve_host_sync, parts.host)
    except (socket.gaierror, OSError) as exc:
        raise UrlValidationError(
            "Provider URL host could not be resolved", code="DNS_RESOLUTION_FAILED"
        ) from exc
    _check_addresses(
        addresses,
        allow_local=allow_local_flag,
        allow_private=allow_private_flag,
        allowlist_hit=parts.allowlist_hit,
    )
    return parts.normalized


def validate_remote_target_url(url: str, *, allow_local: Optional[bool] = None) -> str:
    """Validate a target URL that a REMOTE provider will fetch.

    This is the REMOTE-egress sibling of :func:`validate_provider_url`. It uses
    the same :func:`_inspect` syntax/policy pass (http/https only, no embedded
    credentials, host required, valid port, fragment stripped, query preserved)
    but performs **no local DNS resolution** for ordinary public hostnames: the
    trust boundary is the remote provider's egress, not ours.

    Literal IPs and localhost names are still blocked (unless ``allow_local`` /
    the configured allowlist permits them) so OmniLab never hands an obviously
    internal target to a third party. This function never raises
    ``DNS_RESOLUTION_FAILED``; an unresolvable public hostname is accepted and
    returned normalized.
    """
    parts = _inspect(url, allow_local)
    if parts.allowlist_hit:
        return parts.normalized

    allow_local_flag = _allow_local_enabled(allow_local)
    allow_private_flag = _allow_private_enabled()

    if parts.literal_ip is not None:
        _check_addresses(
            [parts.literal_ip],
            allow_local=allow_local_flag,
            allow_private=allow_private_flag,
            allowlist_hit=parts.allowlist_hit,
        )
        return parts.normalized

    if parts.is_localhost_name:
        if allow_local_flag:
            return parts.normalized
        raise UrlValidationError(_kind_message("loopback"), code="SSRF_BLOCKED")

    # Ordinary public hostname: ACCEPT without any local DNS resolution. This is
    # the whole point of the REMOTE trust boundary.
    return parts.normalized


async def async_validate_remote_target_url(
    url: str, *, allow_local: Optional[bool] = None
) -> str:
    """Async variant of :func:`validate_remote_target_url`.

    Remote target validation needs no I/O (no DNS), so this simply delegates to
    the synchronous implementation; it exists so callers can use one async
    validation seam for both LOCAL and REMOTE egress.
    """
    return validate_remote_target_url(url, allow_local=allow_local)


def is_url_allowed(url: str, *, allow_local: Optional[bool] = None) -> bool:
    """Boolean convenience wrapper around :func:`validate_provider_url`."""
    try:
        validate_provider_url(url, allow_local=allow_local)
        return True
    except UrlValidationError:
        return False


def _host_port_key(url: str) -> Tuple[str, int]:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        port = None
    scheme = parsed.scheme.lower()
    if port is None:
        port = 443 if scheme == "https" else 80
    return host, port


def is_cross_host_redirect(original_url: str, redirect_url: str) -> bool:
    """True when a redirect changes host (or effective port)."""
    return _host_port_key(original_url) != _host_port_key(redirect_url)


def validate_redirect(
    original_url: str, redirect_url: str, *, allow_local: Optional[bool] = None
) -> str:
    """Validate a redirect target.

    Same-host and safe public cross-host redirects are allowed; a target is
    only blocked when it fails SSRF validation (the original error code is
    propagated). Use :func:`is_cross_host_redirect` if callers need to detect
    host changes separately.
    """
    try:
        return validate_provider_url(redirect_url, allow_local=allow_local)
    except UrlValidationError as exc:
        # Preserve specific validation codes; only synthesize
        # REDIRECT_BLOCKED when the underlying error has no code.
        if exc.code is None:
            raise UrlValidationError(exc.message, code="REDIRECT_BLOCKED") from exc
        raise
