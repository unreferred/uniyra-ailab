# OmniLab V4.2.3.9 — Provider Quota Valves (Lab Mode)

## 1. Principle

OmniLab is an R&D laboratory, not public SaaS. Tiny per-run budgets are
**removed as the practical limit**. Provider quota is the primary practical
throttle. Runaway-agent protection remains:

- max rounds,
- consecutive no-progress detection,
- run-local dedup/reuse,
- forced finalization.

## 2. Run budgets (emergency ceilings only)

| Setting | Value | Notes |
| --- | --- | --- |
| `MAX_SEARCH_CALLS_PER_RUN` | `30` | emergency ceiling |
| `MAX_FETCH_CALLS_PER_RUN` | `100` | alias `MAX_FETCH_URLS_PER_RUN=100` |
| `MAX_TOTAL_TOOL_CALLS_PER_RUN` | `150` | emergency ceiling |
| `MAX_RESEARCH_CALLS_PER_RUN` | `30` | emergency composite `web_research` ceiling |

`TOOL_BUDGET_EXHAUSTED` semantics are **unchanged**; it only fires at these high
ceilings.

## 3. TinyFish provider quotas (defaults, env-overridable)

| Resource | Window | Default |
| --- | --- | --- |
| Search | rolling 60s | 30 requests |
| Search | rolling 3600s | 500 requests |
| Fetch | rolling 60s | 150 URLs |
| Fetch | rolling 86400s | 1000 URLs |

Fetch quota is **URL-based**: a batch request with `urls=[A, B, C]` consumes
**3** units, not 1.

## 4. Valves (provider schema `tinyfish`)

| Valve | Default | Max | Ceiling env |
| --- | --- | --- | --- |
| `search_rpm_limit` | 30 | 120 | `TINYFISH_SEARCH_RPM_CEILING` |
| `search_hourly_limit` | 500 | 5000 | `TINYFISH_SEARCH_HOURLY_CEILING` |
| `fetch_urls_per_minute_limit` | 150 | 500 | `TINYFISH_FETCH_URLS_PER_MINUTE_CEILING` |
| `fetch_urls_per_day_limit` | 1000 | 10000 | `TINYFISH_FETCH_URLS_PER_DAY_CEILING` |

Operators may **LOWER** these; server ceilings prevent raising above the listed
maxima. TinyFish's real limits are never clamped below provider values.

## 5. Errors

Local quota exhaustion is a single semantic: `PROVIDER_RATE_LIMITED`
(category `RATE_LIMIT`) with resource metadata:

```json
{
  "provider": "tinyfish",
  "resource": "search",
  "limit": 30,
  "window_seconds": 60,
  "retry_after_seconds": 12
}
```

This is distinct from:

- upstream 429s, which remain `SEARCH_RATE_LIMITED` / `FETCH_RATE_LIMITED`;
- `TOOL_LOOP_NO_PROGRESS`;
- `TOOL_LOOP_LIMIT_REACHED`;
- `TOOL_BUDGET_EXHAUSTED`.

## 6. Quota accounting

`app/services/provider_quota.py` — pure stdlib, single-process in-memory rolling
windows, `threading.Lock`-guarded, synchronous all-or-nothing consumption across
**all** windows. State is shared across all Runs in the backend instance. Only
actual outbound TinyFish operations consume quota; run-local reused Search/Fetch
results consume **ZERO**.

## 7. Observability

Safe, non-secret telemetry:

```json
{
  "provider": "tinyfish",
  "resource": "fetch",
  "units_consumed": 3,
  "window_limit": 150,
  "window_remaining": 147,
  "window_reset_seconds": 41
}
```

Exposed as `provider_quota` on `GET /api/observability`, rendered in the
Observability page **"Provider quota"** card, and a best-effort snapshot is
persisted to the `provider_quota_telemetry` collection. No secrets are ever
included.

## 8. Remaining debt

- In-memory quota state is **single-process only**; a future multi-worker /
  multi-replica deployment requires shared Redis/DB quota state. **Do NOT add
  Redis now.**
- The `AGENT_TOOL_BUDGET_SECONDS` wall-clock budget remains defined but is not
  enforced by the loop (pre-existing).

## Tests

`backend/tests/test_provider_quota.py` covers:

- minute/hour limits,
- rolling reset,
- fetch URL units + batched 3-URL accounting,
- daily cap,
- concurrency sharing,
- reuse-zero-quota,
- run-safety under high ceilings.
