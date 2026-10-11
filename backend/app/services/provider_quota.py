"""In-memory provider (TinyFish) quota accounting for OmniLab Lab mode.

This module is intentionally pure and dependency-free: it imports only the
standard library and reads limits lazily from :mod:`app.config`. It is
importable without a running Mongo instance.

Single-process in-memory accounting is acceptable for current Lab mode. A
future multi-worker deployment requires shared Redis/DB quota state; do NOT add
Redis now. The lock critical sections here are fully synchronous (no awaits) so
consumption is atomic within the event loop and across threads.
"""
from __future__ import annotations

import math
import threading
import time

PROVIDER_TINYFISH = "tinyfish"

RESOURCE_SEARCH = "search"
RESOURCE_FETCH = "fetch"

# resource -> ordered tuple of (window_name, window_seconds)
WINDOWS = {
    RESOURCE_SEARCH: (("minute", 60), ("hour", 3600)),
    RESOURCE_FETCH: (("minute", 60), ("day", 86400)),
}


class ProviderRateLimited(Exception):
    """Raised when a provider quota window would be exceeded.

    ``.error`` is a safe, non-secret structured dict describing the block.
    """

    def __init__(self, error):
        self.error = error
        super().__init__(error.get("message", "provider quota exceeded"))


def _config_get(name, default):
    try:
        from .. import config  # lazy: keep module importable without config/Mongo
    except Exception:
        return default
    return getattr(config, name, default)


def default_limits(provider, resource):
    """Return the default window limits for a (provider, resource) pair.

    Reads ``app.config`` lazily with ``getattr`` fallbacks. Unknown pairs
    return an empty mapping (no limits).
    """
    if provider != PROVIDER_TINYFISH:
        return {}
    if resource == RESOURCE_SEARCH:
        return {
            "minute": _config_get("TINYFISH_SEARCH_RPM_LIMIT", 30),
            "hour": _config_get("TINYFISH_SEARCH_HOURLY_LIMIT", 500),
        }
    if resource == RESOURCE_FETCH:
        return {
            "minute": _config_get("TINYFISH_FETCH_URLS_PER_MINUTE_LIMIT", 150),
            "day": _config_get("TINYFISH_FETCH_URLS_PER_DAY_LIMIT", 1000),
        }
    return {}


def limits_from_config(provider, resource):
    """Convenience alias for :func:`default_limits`."""
    return default_limits(provider, resource)


class ProviderQuotaManager:
    """Synchronous, single-process, rolling-window quota accounting."""

    def __init__(self):
        self._lock = threading.Lock()
        # (provider, resource) -> {window_name: [timestamps]}
        self._events = {}
        # set of (provider, resource) pairs that have been consumed at least once
        self._pairs = set()

    # -- internals (all called while holding self._lock) --------------------
    @staticmethod
    def _prune(events, now, window_seconds):
        # keep only events strictly newer than now - window_seconds
        cutoff = now - window_seconds
        return [ts for ts in events if ts > cutoff]

    @staticmethod
    def _retry_after(live, now, window_seconds, units, limit):
        # number of oldest events that must expire to make room
        need = len(live) + units - limit
        if need <= 0:
            return max(1, int(math.ceil(window_seconds)))
        if not live or need > len(live):
            # e.g. units > limit: can never fit; fall back to a full window
            return max(1, int(math.ceil(window_seconds)))
        ordered = sorted(live)
        oldest_to_expire = ordered[need - 1]
        remaining = window_seconds - (now - oldest_to_expire)
        return max(1, int(math.ceil(remaining)))

    def _telemetry(self, provider, resource, window_name, window_seconds,
                   live, limit, now):
        limit_val = int(limit) if (limit is not None and limit > 0) else None
        used = len(live)
        if limit_val is None:
            remaining = None
        else:
            remaining = max(limit_val - used, 0)
        if live:
            reset = max(0, int(math.ceil(window_seconds - (now - min(live)))))
        else:
            reset = 0
        return {
            "provider": provider,
            "resource": resource,
            "window": window_name,
            "window_seconds": int(window_seconds),
            "units_consumed": used,
            "window_limit": limit_val,
            "window_remaining": remaining,
            "window_reset_seconds": reset,
        }

    # -- public API ---------------------------------------------------------
    def consume(self, provider, resource, units=1, limits=None, *, now=None):
        """Consume ``units`` of ``resource`` for ``provider``.

        Raises :class:`ProviderRateLimited` (recording nothing) if any window
        would be exceeded. Otherwise records ``units`` timestamps and returns a
        telemetry dict.
        """
        if now is None:
            now = time.time()
        if limits is None:
            limits = default_limits(provider, resource)

        windows = WINDOWS.get(resource, ())

        with self._lock:
            pair = (provider, resource)
            store = self._events.setdefault(pair, {})
            self._pairs.add(pair)

            live_by_window = {}
            for window_name, window_seconds in windows:
                live = self._prune(store.get(window_name, []), now, window_seconds)
                live_by_window[window_name] = live
                limit = limits.get(window_name)
                if limit is not None and limit > 0 and len(live) + units > limit:
                    retry = self._retry_after(
                        live, now, window_seconds, units, limit
                    )
                    raise ProviderRateLimited({
                        "code": "PROVIDER_RATE_LIMITED",
                        "category": "RATE_LIMIT",
                        "message": (
                            f"TinyFish {resource} provider quota exceeded "
                            f"({window_name} window)"
                        ),
                        "provider": provider,
                        "resource": resource,
                        "limit": int(limit),
                        "window_seconds": int(window_seconds),
                        "retry_after_seconds": int(retry),
                    })

            # all-or-nothing: only record once every window passed
            result_windows = []
            for window_name, window_seconds in windows:
                store.setdefault(window_name, []).extend([now] * units)
                live = self._prune(
                    store.get(window_name, []), now, window_seconds
                )
                result_windows.append(self._telemetry(
                    provider, resource, window_name, window_seconds,
                    live, limits.get(window_name), now,
                ))

            return {
                "provider": provider,
                "resource": resource,
                "windows": result_windows,
            }

    def snapshot(self, now=None):
        """Return a flat, deterministic list of per-window telemetry dicts."""
        if now is None:
            now = time.time()

        known = [
            (PROVIDER_TINYFISH, RESOURCE_SEARCH),
            (PROVIDER_TINYFISH, RESOURCE_FETCH),
        ]
        with self._lock:
            pairs = set(known) | set(self._pairs) | set(self._events.keys())

        def window_index(resource, name):
            order = [w for w, _ in WINDOWS.get(resource, ())]
            return order.index(name) if name in order else len(order)

        flat = []
        for provider, resource in sorted(pairs):
            windows = WINDOWS.get(resource)
            if windows is None:
                with self._lock:
                    names = sorted((self._events.get((provider, resource)) or {}).keys())
                windows = tuple((n, 0) for n in names)
            limits = default_limits(provider, resource)
            with self._lock:
                store = self._events.get((provider, resource), {})
                for window_name, window_seconds in windows:
                    live = self._prune(
                        list(store.get(window_name, [])), now, window_seconds
                    )
                    flat.append(self._telemetry(
                        provider, resource, window_name, window_seconds,
                        live, limits.get(window_name), now,
                    ))

        flat.sort(key=lambda d: (
            d["provider"], d["resource"],
            window_index(d["resource"], d["window"]),
        ))
        return flat

    def reset(self):
        """Clear all in-memory state (used by tests)."""
        with self._lock:
            self._events.clear()
            self._pairs.clear()


manager = ProviderQuotaManager()


def consume_search(limits=None, units=1, *, now=None):
    return manager.consume(PROVIDER_TINYFISH, RESOURCE_SEARCH, units, limits, now=now)


def consume_fetch(limits=None, units=1, *, now=None):
    return manager.consume(PROVIDER_TINYFISH, RESOURCE_FETCH, units, limits, now=now)


def snapshot(now=None):
    return manager.snapshot(now=now)


def reset():
    manager.reset()
