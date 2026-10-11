"""Pure, deterministic model-call watchdog (OmniLab V4.2.3.12).

Wraps a provider's async event stream with three independent timeouts:

* ``connection``: no streamed event arrived at all yet (the provider never
  started a usable response).
* ``idle``: the stream started but no meaningful event arrived within the idle
  gap; every yielded event resets the idle clock.
* ``max_duration``: emergency wall-clock ceiling for the whole call regardless
  of streaming activity.

The module is pure: it imports only the standard library, :mod:`httpx` and
:mod:`app.services.stream_events` (for the event-type constants) and never
imports the execution engine, so there is no import cycle. It owns no background
tasks or worker supervisor; the wrapper is a plain async generator.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Callable, Dict

import httpx

from . import stream_events as se

__all__ = ["ModelCallTimeout", "watch_model_stream"]

#: Raw provider tool-call fragment event (not part of the public stream contract;
#: it is consumed by the execution engine and mapped to ``TOOL_CALL_DELTA``).
_MODEL_TOOL_CALL_DELTA = "MODEL_TOOL_CALL_DELTA"

_CONNECTION = "connection"
_IDLE = "idle"
_MAX_DURATION = "max_duration"

_ERROR_CODES = {
    _CONNECTION: "MODEL_CONNECTION_TIMEOUT",
    _IDLE: "MODEL_IDLE_TIMEOUT",
    _MAX_DURATION: "MODEL_CALL_MAX_DURATION",
}

_MESSAGES = {
    _CONNECTION: (
        "The model provider did not start a response before the connection timeout"
    ),
    _IDLE: (
        "The model provider stream stalled: no progress before the idle timeout"
    ),
    _MAX_DURATION: "The model call exceeded the maximum call duration",
}


class ModelCallTimeout(Exception):
    """Watchdog abort for one model call.

    ``kind`` is one of ``connection`` / ``idle`` / ``max_duration`` and
    :attr:`error_code` maps it to the canonical ``MODEL_*`` machine code. All
    attributes are safe, secret-free scalars suitable for telemetry.
    """

    def __init__(
        self,
        *,
        kind: str,
        elapsed_ms: int,
        last_progress_age_ms: int,
        had_reasoning: bool = False,
        had_content: bool = False,
        had_tool_call_delta: bool = False,
        connection_timeout_seconds: float = 0.0,
        idle_timeout_seconds: float = 0.0,
        max_duration_seconds: float = 0.0,
    ) -> None:
        if kind not in _ERROR_CODES:
            kind = _IDLE
        self.kind = kind
        self.elapsed_ms = int(elapsed_ms)
        self.last_progress_age_ms = int(last_progress_age_ms)
        self.had_reasoning = bool(had_reasoning)
        self.had_content = bool(had_content)
        self.had_tool_call_delta = bool(had_tool_call_delta)
        self.connection_timeout_seconds = float(connection_timeout_seconds)
        self.idle_timeout_seconds = float(idle_timeout_seconds)
        self.max_duration_seconds = float(max_duration_seconds)
        super().__init__(_MESSAGES[kind])

    @property
    def error_code(self) -> str:
        """Canonical, stable machine code for this timeout kind."""
        return _ERROR_CODES[self.kind]

    @property
    def message(self) -> str:
        """Short, safe, human-readable description of this timeout kind."""
        return _MESSAGES[self.kind]

    def to_details(self) -> Dict[str, Any]:
        """Secret-free, JSON-serializable timeout detail mapping."""
        return {
            "timeout_kind": self.kind,
            "elapsed_ms": self.elapsed_ms,
            "last_progress_age_ms": self.last_progress_age_ms,
            "connection_timeout_seconds": self.connection_timeout_seconds,
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "max_duration_seconds": self.max_duration_seconds,
        }


async def watch_model_stream(
    agen: AsyncIterator[Any],
    *,
    connection_timeout: float,
    idle_timeout: float,
    max_duration: float,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[Any]:
    """Yield events from ``agen``, aborting with :class:`ModelCallTimeout`.

    Before every ``__anext__`` the earliest applicable deadline is computed:
    ``connection`` (only while no event has arrived), ``idle``
    (``last_progress + idle_timeout``) and ``max_duration``
    (``start + max_duration``). A real :class:`httpx.TimeoutException` raised by
    the underlying stream is re-derived onto the same canonical kinds so an
    adapter read-timeout never surfaces as a generic timeout.

    The underlying stream is always closed in ``finally`` (swallowing every
    error), so a timed-out call never leaves a zombie provider generator.
    """
    connection_timeout = max(0.0, float(connection_timeout))
    idle_timeout = max(0.0, float(idle_timeout))
    max_duration = max(0.0, float(max_duration))

    start = clock()
    last_progress = start
    got_event = False
    had_reasoning = False
    had_content = False
    had_tool_call_delta = False

    def _next_deadline(now: float):
        candidates = [
            (last_progress + idle_timeout, _IDLE),
            (start + max_duration, _MAX_DURATION),
        ]
        if not got_event:
            candidates.append((start + connection_timeout, _CONNECTION))
        candidates.sort(key=lambda item: item[0])
        return candidates[0]

    def _timeout(kind: str, now: float) -> ModelCallTimeout:
        return ModelCallTimeout(
            kind=kind,
            elapsed_ms=int((now - start) * 1000),
            last_progress_age_ms=int((now - last_progress) * 1000),
            had_reasoning=had_reasoning,
            had_content=had_content,
            had_tool_call_delta=had_tool_call_delta,
            connection_timeout_seconds=connection_timeout,
            idle_timeout_seconds=idle_timeout,
            max_duration_seconds=max_duration,
        )

    try:
        while True:
            now = clock()
            deadline, kind = _next_deadline(now)
            remaining = deadline - now
            if remaining <= 0:
                raise _timeout(kind, now)
            try:
                evt = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                return
            except (asyncio.TimeoutError, TimeoutError):
                now2 = clock()
                expired, expired_kind = _next_deadline(now2)
                if expired > now2:
                    # ``wait_for`` fired marginally before the deadline we would
                    # recompute; keep the kind we were actually waiting on.
                    expired_kind = kind
                raise _timeout(expired_kind, now2)
            except httpx.TimeoutException:
                # A real adapter read-timeout: map it onto the canonical kinds
                # instead of leaking a generic provider timeout.
                now2 = clock()
                if not got_event:
                    rederived = _CONNECTION
                elif (now2 - start) >= max_duration:
                    rederived = _MAX_DURATION
                else:
                    rederived = _IDLE
                raise _timeout(rederived, now2)
            got_event = True
            last_progress = clock()
            if isinstance(evt, dict):
                etype = evt.get("type")
                reasoning_delta = evt.get("reasoning_delta") or ""
                if not reasoning_delta and etype == se.MODEL_REASONING_DELTA:
                    reasoning_delta = evt.get("delta") or ""
                if etype == se.MODEL_REASONING_DELTA or reasoning_delta:
                    had_reasoning = True
                if (
                    etype == se.MODEL_OUTPUT_DELTA
                    and (evt.get("delta") or "")
                    and not evt.get("done")
                ):
                    had_content = True
                if etype == _MODEL_TOOL_CALL_DELTA:
                    had_tool_call_delta = True
            yield evt
    finally:
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass
