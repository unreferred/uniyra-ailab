"""Process-local cancellation registry for live agentic streams.

The agentic stream is an *ephemeral live projection* of the canonical execution
loop (:func:`app.services.execution.execute_model`): the SSE route threads an
``asyncio.Event`` into the loop and the loop observes it between model/tool
steps. The handle for that event is therefore also process-local and
ephemeral — it lives only for the duration of an in-flight run on this event
loop.

This registry is **not** a replacement for the persisted Run. The Run document
remains the authoritative, durable execution record; losing a registration (a
process restart, a run that already finished, a different worker) simply means
``cancel`` reports that no live run was found, and the caller treats the run as
already finished. It deliberately has no imports from application modules so it
can never introduce an import cycle.
"""
from __future__ import annotations

import asyncio
from typing import Dict

__all__ = ["register", "cancel", "unregister", "is_registered"]

# run_id -> cancel event for runs currently streaming on this event loop.
_LIVE_RUNS: Dict[str, asyncio.Event] = {}


def register(run_id: str, cancel_event: asyncio.Event) -> None:
    """Register (or replace) the cancel event for a live run.

    Tolerant of empty ids / ``None`` events: those are silently ignored so a
    malformed event frame can never break the stream.
    """
    if not run_id or cancel_event is None:
        return
    _LIVE_RUNS[str(run_id)] = cancel_event


def cancel(run_id: str) -> bool:
    """Set the registered event for ``run_id``.

    Returns ``True`` only when a live run was found (and its event set);
    ``False`` when the id was never registered or was already unregistered.
    """
    if not run_id:
        return False
    event = _LIVE_RUNS.get(str(run_id))
    if event is None:
        return False
    event.set()
    return True


def unregister(run_id: str) -> None:
    """Drop a run's registration; safe for duplicate or missing ids."""
    if not run_id:
        return
    _LIVE_RUNS.pop(str(run_id), None)


def is_registered(run_id: str) -> bool:
    """Return True while a live cancel handle exists for ``run_id``."""
    if not run_id:
        return False
    return str(run_id) in _LIVE_RUNS
