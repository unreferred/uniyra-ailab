"""Sandboxed Python code interpreter (Docker-isolated execution).

Trust boundary
--------------
Model- or user-supplied Python code runs ONLY inside an ephemeral,
non-privileged Docker container. The FastAPI host process NEVER executes
untrusted code: this module contains no ``exec``/``eval``/``compile`` of
untrusted code and never spawns a host Python interpreter (or any other host
subprocess) on untrusted input. The ``docker`` CLI subprocess is the sandbox
boundary: the host only stages input files, spawns ``docker run``, enforces a
wall-clock timeout, truncates captured output, and collects output files.

Container guarantees (see :func:`build_docker_command`):

* ``--network none``: the container has no network access at all.
* ``--read-only`` root filesystem with ``tmpfs`` mounts for ``/workspace``
  and ``/tmp``: nothing persists and nothing outside the workspace is
  writable.
* CPU / RAM / PID limits taken from server configuration
  (``CODE_INTERPRETER_MEMORY_MB`` / ``CODE_INTERPRETER_CPUS`` /
  ``CODE_INTERPRETER_PIDS_LIMIT``), plus a bounded ``tmpfs`` workspace.
* Non-root user (uid/gid ``65532``), ``--cap-drop ALL`` and
  ``--security-opt no-new-privileges=true``; never ``--privileged``.
* No host mounts except two per-execution staging directories: a read-only
  input dir (``/workspace/input``) and a writable output dir
  (``/workspace/output``). No host code/state directories are mounted and the
  Docker socket is never exposed. Staging directories live under a dedicated
  sandbox root (an ``ATTACHMENT_STORAGE_DIR`` sibling, ``sandbox-staging``)
  and every staging path is validated with realpath containment before use.
* No environment secrets: only a minimal benign whitelist
  (``PYTHONDONTWRITEBYTECODE``, ``PYTHONUNBUFFERED``, ``MPLBACKEND``) is
  passed via ``--env``. This module never imports server secrets.
* The untrusted program is streamed to the container's ``python -`` over
  STDIN, never via argv and never via environment variables (avoids
  process-table / environment leakage).

Lifecycle: ephemeral per-execution staging directories are removed after
every run. Collected outputs are copied to a durable per-execution directory
(``data/sandbox-outputs/<execution_id>/``) which persists until the execution
record is deleted (:func:`delete_execution` removes both).
"""

import asyncio
import hashlib
import os
import shutil
import time
import uuid
from asyncio.subprocess import PIPE
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import config
from .. import repository as repo
from ..models import CODE_EXECUTION_STATUSES, new_id, now_iso

TOOL_NAME = "code_interpreter"
CODE_INTERPRETER_TOOL_ID = "tool-code-interpreter-0001"
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "input_file_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["code"],
    "additionalProperties": False,
}
PERMISSIONS = ["code.execute"]
RISK = "high"

# Documented (not probed) sandbox package set, mirrored in the image build.
PACKAGES = ["python", "pandas", "numpy", "matplotlib"]

_CONTAINER_USER = "65532:65532"
_CONTAINER_WORKDIR = "/workspace"
_CONTAINER_INPUT_MOUNT = "/workspace/input"
_CONTAINER_OUTPUT_MOUNT = "/workspace/output"

_OUTPUT_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "csv": "text/csv",
    "json": "application/json",
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
    "htm": "text/html",
    "svg": "image/svg+xml",
}
_ACTIVE_CONTENT_EXTENSIONS = {"html", "htm", "svg"}

_MAX_CODE_PREVIEW_CHARS = 2000
_FULL_CODE_STORE_LIMIT = 100_000
_AVAILABILITY_TTL_SECONDS = 60.0

_availability_cache = {"at": 0, "ok": False, "reason": ""}


class CodeInterpreterError(ValueError):
    """Client-safe code-interpreter failure. Carries an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ------------------------------------------------------------------ paths
def _staging_root() -> Path:
    """Ephemeral per-execution staging root (sibling of attachment storage)."""
    return Path(config.ATTACHMENT_STORAGE_DIR).parent / "sandbox-staging"


def _outputs_root() -> Path:
    """Durable per-execution output root (sibling of attachment storage)."""
    return Path(config.ATTACHMENT_STORAGE_DIR).parent / "sandbox-outputs"


def _ensure_within_root(candidate: Any, root: Path) -> Path:
    """Resolve ``candidate`` and require containment inside ``root``.

    Defeats ``..`` traversal and symlink escape. Raises
    :class:`CodeInterpreterError` (400) on any violation.
    """
    root.mkdir(parents=True, exist_ok=True)
    root_real = os.path.realpath(str(root))
    text = str(candidate or "")
    if "\x00" in text or not text.strip():
        raise CodeInterpreterError(
            "Invalid sandbox path (sandbox policy violation)", status_code=400
        )
    resolved = os.path.realpath(text)
    if resolved != root_real and not resolved.startswith(root_real + os.sep):
        raise CodeInterpreterError(
            "Invalid staging path (sandbox policy violation)", status_code=400
        )
    return Path(resolved)


def _limits_snapshot() -> Dict[str, Any]:
    return {
        "memory_mb": int(config.CODE_INTERPRETER_MEMORY_MB),
        "cpus": float(config.CODE_INTERPRETER_CPUS),
        "pids_limit": int(config.CODE_INTERPRETER_PIDS_LIMIT),
        "tmpfs_mb": int(config.CODE_INTERPRETER_TMPFS_MB),
        "timeout_s": int(config.CODE_INTERPRETER_TIMEOUT_SECONDS),
        "max_output_bytes": int(config.CODE_INTERPRETER_MAX_OUTPUT_BYTES),
        "max_files": int(config.CODE_INTERPRETER_MAX_FILES),
        "max_file_bytes": int(config.CODE_INTERPRETER_MAX_FILE_BYTES),
        "max_code_chars": int(config.CODE_INTERPRETER_MAX_CODE_CHARS),
    }


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    """Coerce ``value`` to an int and clamp it to ``[low, high]``."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return max(int(low), min(number, int(high)))


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    """Coerce ``value`` to a float and clamp it to ``[low, high]``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number != number:  # NaN
        number = float(default)
    return max(float(low), min(number, float(high)))


async def resolve_execution_limits(
    owner_id: str, project_id: Optional[str]
) -> Dict[str, Any]:
    """Effective sandbox limits: server defaults overlaid with tool valves.

    Runtime is defense-in-depth: values are clamped to the protected
    server-side ceilings from ``app.config`` even though validation already
    rejects above-ceiling saves. Only resource limits (timeout / memory /
    cpus / output) are valve-driven; network, read-only, cap-drop,
    no-new-privileges, user and pids policy is never valve-affected. On any
    resolution error the server defaults are returned.
    """
    limits = _limits_snapshot()
    try:
        from . import tool_valves

        resolved = await tool_valves.resolve_tool(
            owner_id,
            {
                "id": CODE_INTERPRETER_TOOL_ID,
                "name": "code_interpreter",
                "tool_type": "code_interpreter",
                "enabled": True,
            },
            project_id,
        )
        values = resolved.get("values") or {}
    except Exception:
        return limits
    limits["timeout_s"] = _clamp_int(
        values.get("timeout_seconds"),
        limits["timeout_s"],
        1,
        int(config.CODE_INTERPRETER_TIMEOUT_SECONDS),
    )
    limits["memory_mb"] = _clamp_int(
        values.get("memory_mb"),
        limits["memory_mb"],
        64,
        int(config.CODE_INTERPRETER_MEMORY_MB),
    )
    limits["cpus"] = _clamp_float(
        values.get("cpus"),
        limits["cpus"],
        0.1,
        float(config.CODE_INTERPRETER_CPUS),
    )
    limits["max_output_bytes"] = _clamp_int(
        values.get("max_output_bytes"),
        limits["max_output_bytes"],
        1024,
        int(config.CODE_INTERPRETER_MAX_OUTPUT_BYTES),
    )
    return limits


def _availability_payload(ok: bool, reason: str) -> Dict[str, Any]:
    return {
        "available": bool(ok),
        "reason": str(reason or ""),
        "image": str(config.CODE_INTERPRETER_IMAGE),
        "runtime": str(config.CODE_INTERPRETER_RUNTIME),
        "limits": _limits_snapshot(),
    }


def _store_availability(ok: bool, reason: str) -> None:
    _availability_cache["at"] = time.monotonic()
    _availability_cache["ok"] = bool(ok)
    _availability_cache["reason"] = str(reason or "")


# ------------------------------------------------------------ availability
_availability_lock: Optional[asyncio.Lock] = None


def _get_availability_lock() -> asyncio.Lock:
    """Lazily-created probe-coalescing lock (single-threaded event loop)."""
    global _availability_lock
    lock = _availability_lock
    if lock is None:
        lock = asyncio.Lock()
        _availability_lock = lock
    return lock


def reset_availability_cache() -> None:
    """Reset the availability snapshot to cold (never probed). Test helper."""
    _availability_cache["at"] = 0
    _availability_cache["ok"] = False
    _availability_cache["reason"] = ""


def _cache_age_seconds() -> float:
    """Age of the cached availability snapshot; corrupt values read as stale."""
    try:
        return time.monotonic() - float(_availability_cache.get("at") or 0)
    except (TypeError, ValueError):
        return _AVAILABILITY_TTL_SECONDS


def _fresh_cache_payload() -> Optional[Dict[str, Any]]:
    """Cached payload when fresh, else None (cold or stale)."""
    if _cache_age_seconds() < _AVAILABILITY_TTL_SECONDS:
        return _availability_payload(
            bool(_availability_cache.get("ok")),
            str(_availability_cache.get("reason") or ""),
        )
    return None


async def _probe_availability() -> Dict[str, Any]:
    """Run one sandbox probe and cache the outcome. Never raises.

    Never leaks daemon output: probe failures map to short, safe ``reason``
    strings. Docker sandbox flags are untouched (see :func:`execute_code`).
    """
    if not bool(config.CODE_INTERPRETER_ENABLED):
        _store_availability(False, "disabled by server configuration")
        return _availability_payload(False, "disabled by server configuration")

    if shutil.which("docker") is None:
        _store_availability(False, "docker runtime not available")
        return _availability_payload(False, "docker runtime not available")

    image = str(config.CODE_INTERPRETER_IMAGE or "").strip()
    if not image:
        _store_availability(False, "sandbox image is not configured")
        return _availability_payload(False, "sandbox image is not configured")

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", image, stdout=PIPE, stderr=PIPE
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=10)
        except (asyncio.TimeoutError, TimeoutError):
            try:
                proc.kill()
            except Exception:
                pass
            _store_availability(False, "sandbox image check timed out")
            return _availability_payload(False, "sandbox image check timed out")
        if proc.returncode == 0:
            _store_availability(True, "")
            return _availability_payload(True, "")
        _store_availability(False, "sandbox image is not available")
        return _availability_payload(False, "sandbox image is not available")
    except (FileNotFoundError, OSError):
        _store_availability(False, "docker runtime not available")
        return _availability_payload(False, "docker runtime not available")
    except Exception:
        _store_availability(False, "sandbox unavailable")
        return _availability_payload(False, "sandbox unavailable")


async def check_availability(force: bool = False) -> Dict[str, Any]:
    """Report whether the Docker sandbox can run code right now.

    Never raises and never leaks daemon output: probe failures map to short,
    safe ``reason`` strings. Results cache for 60 seconds. Concurrent callers
    coalesce onto a single probe via a lazily-created lock: after acquiring
    it the TTL cache is re-checked because another waiter may have warmed it.
    """
    if not force:
        cached = _fresh_cache_payload()
        if cached is not None:
            return cached
    try:
        lock = _get_availability_lock()
    except Exception:
        return await _probe_availability()
    async with lock:
        try:
            if not force:
                cached = _fresh_cache_payload()
                if cached is not None:
                    return cached
            return await _probe_availability()
        except Exception:
            _store_availability(False, "sandbox unavailable")
            return _availability_payload(False, "sandbox unavailable")


def ensure_background_refresh() -> None:
    """Schedule a non-blocking background probe when the cache is cold/stale.

    Sync, never raises, never blocks: with a cold (never probed) or stale
    (age >= TTL) cache and a running event loop, a bounded
    :func:`check_availability` task is scheduled so subsequent reads observe
    a warmed cache. No-op when the cache is fresh or when no loop is running.
    Background-task exceptions are swallowed via a done-callback.
    """
    try:
        if _fresh_cache_payload() is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(check_availability())

        def _swallow(done_task: asyncio.Task) -> None:
            try:
                if not done_task.cancelled():
                    done_task.exception()
            except Exception:
                pass

        try:
            task.add_done_callback(_swallow)
        except Exception:
            pass
    except Exception:
        pass


def check_availability_sync() -> dict:
    """Cached-only availability snapshot. Fast, never probes, never raises.

    Used by registry views (``tools._public_tool``) so listing tools can
    never block on a ``docker inspect``. A stale cache reports the last
    known state; a cache that was never probed (``at == 0``) reports
    ``available=False`` with reason ``"Code interpreter status unknown"``
    so a cold cache never renders as silently disabled with an empty
    reason. Callers needing a fresh probe use :func:`check_availability`.
    """
    try:
        try:
            probed_at = float(_availability_cache.get("at") or 0)
        except (TypeError, ValueError):
            probed_at = 0
        if not probed_at:
            return _availability_payload(False, "Code interpreter status unknown")
        return _availability_payload(
            bool(_availability_cache.get("ok")),
            str(_availability_cache.get("reason") or ""),
        )
    except Exception:
        return {"available": False, "reason": "Code interpreter status unavailable"}


def status_payload(availability: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Allowlisted status projection shared by status endpoints.

    Returns exactly ``{available, reason, image, runtime, limits, policy}``
    with the static sandbox policy (network off, documented packages, tool
    name, permissions, risk). Never includes docker daemon output, host
    paths, container inventory, or secrets. ``availability`` defaults to
    the cached-only snapshot so registry views stay non-blocking; the
    ``GET /code-interpreter/status`` route passes a fresh probe instead.
    """
    source = availability
    if source is None:
        try:
            source = check_availability_sync()
        except Exception:
            source = {"available": False, "reason": "Code interpreter status unavailable"}
    if not isinstance(source, dict):
        source = {"available": False, "reason": "Code interpreter status unavailable"}
    return {
        "available": bool(source.get("available")),
        "reason": source.get("reason") or None,
        "image": source.get("image"),
        "runtime": source.get("runtime"),
        "limits": source.get("limits") or {},
        "policy": {
            "network": "off",
            "packages": list(PACKAGES),
            "tool": TOOL_NAME,
            "permissions": list(PERMISSIONS),
            "risk": RISK,
        },
    }


# ---------------------------------------------------------- docker command
def build_docker_command(
    *,
    staging_input: str,
    staging_output: str,
    timeout_s: int,
    memory_mb: Optional[int] = None,
    cpus: Optional[float] = None,
    pids_limit: Optional[int] = None,
    tmpfs_mb: Optional[int] = None,
) -> List[str]:
    """Build the hardened ``docker run`` invocation for one execution.

    ``timeout_s`` is enforced client-side (see :func:`execute_code`); it is
    accepted here so callers pass a single execution policy bundle. Staging
    paths are realpath-normalized (defeats symlink escape at mount time);
    sandbox-root containment for production flows is enforced by
    :func:`execute_code` via :func:`_ensure_within_root` before this is
    called. Untrusted code is streamed to ``python -`` over STDIN, never via
    argv or environment.
    """
    for label, value in (("staging_input", staging_input), ("staging_output", staging_output)):
        text = str(value or "")
        if "\x00" in text or not text.strip():
            raise CodeInterpreterError(
                "Invalid staging path (sandbox policy violation)", status_code=400
            )
    input_real = os.path.realpath(str(staging_input))
    output_real = os.path.realpath(str(staging_output))
    if not os.path.isabs(input_real) or not os.path.isabs(output_real):
        raise CodeInterpreterError(
            "Invalid staging path (sandbox policy violation)", status_code=400
        )

    image = str(config.CODE_INTERPRETER_IMAGE)
    # Optional resource overrides default to config and are always clamped to
    # the protected server-side ceilings (defense-in-depth: save-time
    # validation already rejects above-ceiling values). Isolation policy
    # (network/read-only/cap-drop/no-new-privileges/user) is never alterable.
    memory_mb = _clamp_int(
        memory_mb, int(config.CODE_INTERPRETER_MEMORY_MB), 64,
        int(config.CODE_INTERPRETER_MEMORY_MB),
    )
    cpus = _clamp_float(
        cpus, float(config.CODE_INTERPRETER_CPUS), 0.1,
        float(config.CODE_INTERPRETER_CPUS),
    )
    pids_limit = _clamp_int(
        pids_limit, int(config.CODE_INTERPRETER_PIDS_LIMIT), 1,
        int(config.CODE_INTERPRETER_PIDS_LIMIT),
    )
    tmpfs_mb = _clamp_int(
        tmpfs_mb, int(config.CODE_INTERPRETER_TMPFS_MB), 1,
        int(config.CODE_INTERPRETER_TMPFS_MB),
    )
    container_name = "omnilab-ci-" + uuid.uuid4().hex[:12]

    # NOTE: ``-i`` (keep STDIN open) is load-bearing, not optional: the
    # untrusted program is streamed to ``python -`` over STDIN via
    # ``proc.communicate(code)``. Without ``-i`` the container sees EOF
    # immediately, exits 0, and every execution silently becomes a no-op
    # (empty stdout, no files, false ``success``). ``-i`` attaches stdin
    # only; it does not weaken any isolation flag below.
    return [
        "docker", "run", "--rm", "-i",
        "--name", container_name,
        "--network", "none",
        "--read-only",
        "--tmpfs", f"/workspace:size={tmpfs_mb}m,mode=1777",
        "--tmpfs", "/tmp:size=16m,mode=1777",
        "-v", f"{input_real}:{_CONTAINER_INPUT_MOUNT}:ro",
        "-v", f"{output_real}:{_CONTAINER_OUTPUT_MOUNT}",
        "--memory", f"{memory_mb}m",
        "--memory-swap", f"{memory_mb}m",
        "--cpus", str(cpus),
        "--pids-limit", str(pids_limit),
        "--user", _CONTAINER_USER,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges=true",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--env", "PYTHONUNBUFFERED=1",
        "--env", "MPLBACKEND=Agg",
        "--workdir", _CONTAINER_WORKDIR,
        image,
        "python", "-",
    ]


def _container_name_from(command: List[str]) -> Optional[str]:
    try:
        index = list(command).index("--name")
        name = str(command[index + 1])
        return name or None
    except (ValueError, IndexError):
        return None


async def _docker_rm_best_effort(container_name: Optional[str]) -> None:
    """Remove a possibly-orphaned container; never raises."""
    if not container_name:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", container_name, stdout=PIPE, stderr=PIPE
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=10)
        except (asyncio.TimeoutError, TimeoutError):
            try:
                proc.kill()
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------- tracing
async def _start_span(tracing: Any, owner_id: str, trace_id: Optional[str],
                      span_type: str, name: str, run_id: Optional[str],
                      metadata: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    if not trace_id:
        return None
    try:
        return await tracing.start_span(
            owner_id, trace_id, span_type, name,
            run_id=run_id, metadata=dict(metadata or {}),
        )
    except Exception:
        return None


async def _finish_span(tracing: Any, span: Optional[dict], *,
                       status: str = "completed",
                       metadata: Optional[Dict[str, Any]] = None,
                       structured_error: Optional[Dict[str, Any]] = None,
                       started_perf: Optional[float] = None) -> None:
    if span is None:
        return
    try:
        await tracing.finish_span(
            span, status=status, metadata=metadata,
            structured_error=structured_error, started_perf=started_perf,
        )
    except Exception:
        pass


# ---------------------------------------------------------------- outputs
def _cap_bytes(data: bytes, limit: int) -> Tuple[bytes, bool]:
    if len(data) > limit:
        return data[:limit], True
    return data, False


def _extension_of(rel_path: str) -> str:
    base = rel_path.rsplit("/", 1)[-1]
    if "." not in base:
        return ""
    return base.rsplit(".", 1)[-1].lower()


def _collect_output_files(staging_out: Path, durable_dir: Path, outputs_root: Path,
                          *, max_files: int, max_file_bytes: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Copy allowed output files to durable storage; return (manifest, limited)."""
    collected: List[Dict[str, Any]] = []
    limited = False
    if not staging_out.is_dir():
        return collected, limited
    entries: List[Tuple[str, Path]] = []
    for root, dirs, files in os.walk(staging_out):
        dirs.sort()
        for name in sorted(files):
            full = Path(root) / name
            try:
                if full.is_symlink() or not full.is_file():
                    continue
                rel = full.relative_to(staging_out).as_posix()
            except (OSError, ValueError):
                continue
            if not rel or rel.startswith("..") or rel.startswith("/"):
                limited = True
                continue
            entries.append((rel, full))
    entries.sort(key=lambda item: item[0])
    if len(entries) > max_files:
        limited = True
    for rel, full in entries[:max_files]:
        try:
            if full.stat().st_size > max_file_bytes:
                limited = True
                continue
            with open(full, "rb") as handle:
                content = handle.read(max_file_bytes + 1)
        except OSError:
            continue
        if len(content) > max_file_bytes:
            limited = True
            continue
        ext = _extension_of(rel)
        mime = _OUTPUT_MIME_BY_EXT.get(ext, "application/octet-stream")
        entry: Dict[str, Any] = {
            "name": rel,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "mime_type": mime,
            "sandbox_path": f"{_CONTAINER_OUTPUT_MOUNT}/{rel}",
        }
        if ext in _ACTIVE_CONTENT_EXTENSIONS:
            entry["content_warning"] = "active-content"
        dest = Path(os.path.join(str(durable_dir), *rel.split("/")))
        try:
            _ensure_within_root(dest, outputs_root)
        except CodeInterpreterError:
            limited = True
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as handle:
                handle.write(content)
        except OSError:
            continue
        collected.append(entry)
    return collected, limited


# ---------------------------------------------------------------- execute
async def execute_code(
    *,
    owner_id: str,
    project_id: str,
    code: str,
    input_file_ids: Optional[List[str]] = None,
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    execution_index: int = 0,
    timeout_override: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute untrusted Python code inside the Docker sandbox.

    Never raises for sandbox/runtime outcomes: ``unavailable``, ``timeout``
    and ``error`` statuses are persisted to ``code_executions`` and returned.
    Only contract violations (empty/oversize code, unknown attachments) raise
    :class:`CodeInterpreterError`.

    Failure semantics: the persisted ``CodeExecution.error`` (``timeout`` /
    ``CODE_RUNTIME_ERROR`` / ``SANDBOX_UNAVAILABLE`` / ...) is the canonical
    record for a standalone execution. Inside an agentic run the caller
    threads ``run_id`` and the internal ``tool_call_id`` through, and the
    Run-level funnel (``failures.record_execution_failure``, idempotent per
    Run) owns FailureRecord creation — this function never creates one, so
    no duplicate FailureRecords arise. Correlation across the funnel is
    ``Run -> ToolCall -> CodeExecution -> FailureRecord -> Trace``.
    """
    from . import tracing as _tracing
    from . import attachments as _attachment_service

    if not isinstance(code, str) or not code.strip():
        raise CodeInterpreterError("Code must be a non-empty string", status_code=400)
    max_code_chars = int(config.CODE_INTERPRETER_MAX_CODE_CHARS)
    if len(code) > max_code_chars:
        raise CodeInterpreterError(
            f"Code exceeds the {max_code_chars}-character limit", status_code=413
        )

    limits = await resolve_execution_limits(owner_id, project_id)
    if timeout_override is not None:
        timeout_s = _clamp_int(
            timeout_override,
            limits["timeout_s"],
            1,
            int(config.CODE_INTERPRETER_TIMEOUT_SECONDS),
        )
    else:
        timeout_s = int(limits["timeout_s"])
    # Effective timeout is auditable on the persisted ``limits`` snapshot.
    limits["timeout_s"] = int(timeout_s)

    image = str(config.CODE_INTERPRETER_IMAGE)
    max_output_bytes = int(limits["max_output_bytes"])
    max_files = int(config.CODE_INTERPRETER_MAX_FILES)
    max_file_bytes = int(config.CODE_INTERPRETER_MAX_FILE_BYTES)

    if len(code) <= _FULL_CODE_STORE_LIMIT:
        stored_code, code_preview, code_truncated = code, code[:_MAX_CODE_PREVIEW_CHARS], False
    else:
        stored_code = code[:_MAX_CODE_PREVIEW_CHARS]
        code_preview, code_truncated = stored_code, True

    async def _insert(doc: Dict[str, Any]) -> Dict[str, Any]:
        try:
            await repo.code_executions.insert(doc)
        except Exception:
            pass
        return doc

    # --- availability gate: persist "unavailable", never raise ----------------
    availability = await check_availability()
    if not availability.get("available"):
        execution_id = new_id()
        error = {
            "category": "SANDBOX_UNAVAILABLE",
            "message": availability.get("reason") or "Code execution sandbox is unavailable",
        }
        await _insert({
            "id": execution_id, "owner_id": owner_id, "project_id": project_id,
            "run_id": run_id, "trace_id": trace_id, "tool_call_id": tool_call_id,
            "execution_index": int(execution_index or 0), "status": "unavailable",
            "language": "python", "image": image, "limits": limits,
            "code": stored_code, "code_preview": code_preview, "code_truncated": code_truncated,
            "stdout": "", "stderr": "", "stdout_truncated": False, "stderr_truncated": False,
            "output_limited": False, "exit_code": None, "duration_ms": 0,
            "files": [], "input_manifest": [], "error": error, "created_at": now_iso(),
        })
        return {
            "status": "unavailable", "stdout": "", "stderr": "",
            "stdout_truncated": False, "stderr_truncated": False,
            "exit_code": None, "duration_ms": 0, "files": [], "input_manifest": [],
            "limits": limits, "execution_id": execution_id, "image": image, "error": error,
        }

    # --- stage inputs under the contained sandbox root ------------------------
    staging_root = _staging_root()
    outputs_root = _outputs_root()
    execution_id = new_id()
    try:
        staging_exec = _ensure_within_root(staging_root / execution_id, staging_root)
        staging_in = _ensure_within_root(staging_exec / "input", staging_root)
        staging_out = _ensure_within_root(staging_exec / "output", staging_root)
        staging_in.mkdir(parents=True, exist_ok=True)
        staging_out.mkdir(parents=True, exist_ok=True)
        # Host-side write bit for the container's unprivileged uid (65532):
        # bind-mounted staging dirs inherit the host owner/mode (typically
        # 755), so without this the container cannot create output files and
        # every file-producing execution fails with PermissionError. This
        # changes no container isolation flag; the directory is ephemeral and
        # removed after every run (see ``finally`` below).
        os.chmod(staging_out, 0o777)
    except CodeInterpreterError:
        raise
    except Exception:
        raise CodeInterpreterError("Could not prepare sandbox staging", status_code=500)

    try:
        span_create = await _start_span(
            _tracing, owner_id, trace_id, "SANDBOX_CREATE", "sandbox-create",
            run_id, {"image": image, "execution_id": execution_id},
        )
        input_manifest: List[Dict[str, Any]] = []
        for index, raw_id in enumerate(list(input_file_ids or [])):
            attachment_id = str(raw_id)
            try:
                data, doc = await _attachment_service.read_attachment_bytes(owner_id, attachment_id)
            except Exception as exc:
                status_code = getattr(exc, "status_code", None) or 404
                message = getattr(exc, "message", None) or "Attachment not found"
                raise CodeInterpreterError(str(message), status_code=status_code)
            if doc.get("project_id") != project_id:
                raise CodeInterpreterError("Attachment not found", status_code=404)
            display_name = str(doc.get("filename") or "attachment")
            safe_name = _attachment_service.sanitize_filename(display_name)
            staged_name = f"{index:03d}_{safe_name}"
            if "/" in staged_name or "\\" in staged_name or staged_name.startswith(".."):
                raise CodeInterpreterError(
                    "Invalid staging path (sandbox policy violation)", status_code=400
                )
            try:
                with open(staging_in / staged_name, "wb") as handle:
                    handle.write(data)
            except OSError:
                raise CodeInterpreterError("Could not stage sandbox input", status_code=500)
            input_manifest.append({
                "attachment_id": attachment_id,
                "display_name": display_name,
                "sandbox_path": f"{_CONTAINER_INPUT_MOUNT}/{staged_name}",
                "mime_type": doc.get("mime_type") or "application/octet-stream",
            })
        await _finish_span(_tracing, span_create, metadata={"inputs": len(input_manifest)})

        command = build_docker_command(
            staging_input=str(staging_in),
            staging_output=str(staging_out),
            timeout_s=timeout_s,
            memory_mb=limits["memory_mb"],
            cpus=limits["cpus"],
        )
        container_name = _container_name_from(command)

        span_exec = await _start_span(
            _tracing, owner_id, trace_id, "CODE_EXECUTION", "code-execution",
            run_id, {"image": image, "timeout_s": timeout_s, "execution_id": execution_id},
        )
        exec_start = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *command, stdin=PIPE, stdout=PIPE, stderr=PIPE
            )
        except (FileNotFoundError, OSError):
            await _finish_span(_tracing, span_exec, status="failed",
                               structured_error={"category": "SANDBOX_CREATE_ERROR"})
            error = {"category": "SANDBOX_CREATE_ERROR",
                     "message": "Sandbox container could not be started"}
            await _insert({
                "id": execution_id, "owner_id": owner_id, "project_id": project_id,
                "run_id": run_id, "trace_id": trace_id, "tool_call_id": tool_call_id,
                "execution_index": int(execution_index or 0), "status": "error",
                "language": "python", "image": image, "limits": limits,
                "code": stored_code, "code_preview": code_preview, "code_truncated": code_truncated,
                "stdout": "", "stderr": "", "stdout_truncated": False, "stderr_truncated": False,
                "output_limited": False, "exit_code": None, "duration_ms": 0,
                "files": [], "input_manifest": input_manifest, "error": error,
                "created_at": now_iso(),
            })
            return {
                "status": "error", "stdout": "", "stderr": "",
                "stdout_truncated": False, "stderr_truncated": False,
                "exit_code": None, "duration_ms": 0, "files": [],
                "input_manifest": input_manifest, "limits": limits,
                "execution_id": execution_id, "image": image, "error": error,
            }
        except Exception:
            await _finish_span(_tracing, span_exec, status="failed",
                               structured_error={"category": "SANDBOX_CREATE_ERROR"})
            error = {"category": "SANDBOX_CREATE_ERROR",
                     "message": "Sandbox container could not be started"}
            await _insert({
                "id": execution_id, "owner_id": owner_id, "project_id": project_id,
                "run_id": run_id, "trace_id": trace_id, "tool_call_id": tool_call_id,
                "execution_index": int(execution_index or 0), "status": "error",
                "language": "python", "image": image, "limits": limits,
                "code": stored_code, "code_preview": code_preview, "code_truncated": code_truncated,
                "stdout": "", "stderr": "", "stdout_truncated": False, "stderr_truncated": False,
                "output_limited": False, "exit_code": None, "duration_ms": 0,
                "files": [], "input_manifest": input_manifest, "error": error,
                "created_at": now_iso(),
            })
            return {
                "status": "error", "stdout": "", "stderr": "",
                "stdout_truncated": False, "stderr_truncated": False,
                "exit_code": None, "duration_ms": 0, "files": [],
                "input_manifest": input_manifest, "limits": limits,
                "execution_id": execution_id, "image": image, "error": error,
            }

        timed_out = False
        try:
            raw_out, raw_err = await asyncio.wait_for(
                proc.communicate(code.encode("utf-8")), timeout=timeout_s
            )
        except (asyncio.TimeoutError, TimeoutError):
            timed_out = True
            raw_out, raw_err = b"", b""
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                pass
        duration_ms = int((time.perf_counter() - exec_start) * 1000)
        await _docker_rm_best_effort(container_name)

        if timed_out:
            await _finish_span(
                _tracing, span_exec, status="failed", started_perf=exec_start,
                structured_error={"category": "CODE_TIMEOUT"},
                metadata={"timeout_s": timeout_s},
            )
            error = {"category": "CODE_TIMEOUT",
                     "message": f"Execution exceeded the {timeout_s}s time limit"}
            await _insert({
                "id": execution_id, "owner_id": owner_id, "project_id": project_id,
                "run_id": run_id, "trace_id": trace_id, "tool_call_id": tool_call_id,
                "execution_index": int(execution_index or 0), "status": "timeout",
                "language": "python", "image": image, "limits": limits,
                "code": stored_code, "code_preview": code_preview, "code_truncated": code_truncated,
                "stdout": "", "stderr": "", "stdout_truncated": False, "stderr_truncated": False,
                "output_limited": False, "exit_code": None, "duration_ms": duration_ms,
                "files": [], "input_manifest": input_manifest, "error": error,
                "created_at": now_iso(),
            })
            return {
                "status": "timeout", "stdout": "", "stderr": "",
                "stdout_truncated": False, "stderr_truncated": False,
                "exit_code": None, "duration_ms": duration_ms, "files": [],
                "input_manifest": input_manifest, "limits": limits,
                "execution_id": execution_id, "image": image, "error": error,
            }

        exit_code = proc.returncode
        out_capped, out_truncated = _cap_bytes(raw_out or b"", max_output_bytes)
        err_capped, err_truncated = _cap_bytes(raw_err or b"", max_output_bytes)
        stdout_text = out_capped.decode("utf-8", errors="replace")
        stderr_text = err_capped.decode("utf-8", errors="replace")

        span_output = await _start_span(
            _tracing, owner_id, trace_id, "SANDBOX_OUTPUT", "sandbox-output",
            run_id, {"execution_id": execution_id},
        )
        durable_dir = _ensure_within_root(outputs_root / execution_id, outputs_root)
        durable_dir.mkdir(parents=True, exist_ok=True)
        files, output_limited = _collect_output_files(
            staging_out, durable_dir, outputs_root,
            max_files=max_files, max_file_bytes=max_file_bytes,
        )
        await _finish_span(_tracing, span_output, metadata={"file_count": len(files)})
        span_artifacts = await _start_span(
            _tracing, owner_id, trace_id, "ARTIFACT_CAPTURE", "artifact-capture",
            run_id, {"execution_id": execution_id, "file_count": len(files)},
        )
        await _finish_span(_tracing, span_artifacts)

        if exit_code == 0:
            status = "success"
            error = None
        else:
            status = "error"
            error = {"category": "CODE_RUNTIME_ERROR",
                     "message": f"Process exited with code {exit_code}"}
        if (out_truncated or err_truncated or output_limited) and error is None:
            error = {"category": "CODE_OUTPUT_LIMIT",
                     "message": "Output exceeded configured limits and was truncated"}
        await _finish_span(
            _tracing, span_exec,
            status="completed" if status == "success" else "failed",
            started_perf=exec_start,
            structured_error=None if status == "success" else error,
            metadata={"exit_code": exit_code},
        )
        if status not in CODE_EXECUTION_STATUSES:
            status = "error"
        await _insert({
            "id": execution_id, "owner_id": owner_id, "project_id": project_id,
            "run_id": run_id, "trace_id": trace_id, "tool_call_id": tool_call_id,
            "execution_index": int(execution_index or 0), "status": status,
            "language": "python", "image": image, "limits": limits,
            "code": stored_code, "code_preview": code_preview, "code_truncated": code_truncated,
            "stdout": stdout_text, "stderr": stderr_text,
            "stdout_truncated": out_truncated, "stderr_truncated": err_truncated,
            "output_limited": output_limited, "exit_code": exit_code,
            "duration_ms": duration_ms, "files": files,
            "input_manifest": input_manifest, "error": error, "created_at": now_iso(),
        })
        return {
            "status": status, "stdout": stdout_text, "stderr": stderr_text,
            "stdout_truncated": out_truncated, "stderr_truncated": err_truncated,
            "exit_code": exit_code, "duration_ms": duration_ms, "files": files,
            "input_manifest": input_manifest, "limits": limits,
            "execution_id": execution_id, "image": image, "error": error,
        }
    finally:
        shutil.rmtree(str(staging_exec), ignore_errors=True)


# ------------------------------------------------------------------ reads
async def get_execution(owner_id: str, execution_id: str) -> Dict[str, Any]:
    """Fetch one execution record or raise 404."""
    doc = await repo.code_executions.get(execution_id, owner_id)
    if not doc:
        raise CodeInterpreterError("Code execution not found", status_code=404)
    return doc


async def list_executions(
    owner_id: str,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List execution records for an owner, optionally filtered."""
    try:
        safe_limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        safe_limit = 50
    try:
        safe_offset = max(0, int(offset))
    except (TypeError, ValueError):
        safe_offset = 0
    query: Dict[str, Any] = {"owner_id": owner_id}
    if project_id:
        query["project_id"] = project_id
    if run_id:
        query["run_id"] = run_id
    return await repo.code_executions.list(query, limit=safe_limit, offset=safe_offset)


async def get_execution_file(
    owner_id: str, execution_id: str, filename: str
) -> Tuple[bytes, str, Optional[str]]:
    """Read a durable output file; returns (bytes, mime_type, content_warning).

    ``filename`` must exactly match a manifest entry name. Reads come from the
    durable per-execution output directory (never from ephemeral staging) with
    realpath containment enforced.
    """
    doc = await get_execution(owner_id, execution_id)
    entry = None
    for candidate in doc.get("files") or []:
        if isinstance(candidate, dict) and candidate.get("name") == filename:
            entry = candidate
            break
    if entry is None:
        raise CodeInterpreterError("Output file not found", status_code=404)
    outputs_root = _outputs_root()
    target = Path(os.path.join(str(outputs_root / execution_id), *str(filename).split("/")))
    try:
        resolved = _ensure_within_root(target, outputs_root)
    except CodeInterpreterError:
        raise CodeInterpreterError("Output file not found", status_code=404)
    try:
        with open(resolved, "rb") as handle:
            data = handle.read()
    except (OSError, ValueError):
        raise CodeInterpreterError("Output file not found", status_code=404)
    return data, str(entry.get("mime_type") or "application/octet-stream"), entry.get("content_warning")


async def delete_execution(owner_id: str, execution_id: str) -> Dict[str, Any]:
    """Delete an execution record and its durable output directory."""
    await get_execution(owner_id, execution_id)
    outputs_root = _outputs_root()
    try:
        target = _ensure_within_root(outputs_root / execution_id, outputs_root)
        shutil.rmtree(str(target), ignore_errors=True)
    except CodeInterpreterError:
        pass
    await repo.code_executions.delete(execution_id, owner_id)
    return {"deleted": True}
