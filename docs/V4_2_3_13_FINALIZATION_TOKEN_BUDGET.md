# OmniLab V4.2.3.13 — Finalization Token Budget / Empty-Final-Answer Closure

Status: implemented. This release is strictly additive to the V4.2.3.11
finalization contract and the V4.2.3.12 watchdog/quota work. It gives forced
finalization its own output budget so a reasoning-capable model can no longer
spend the entire completion budget on hidden reasoning and return empty visible
content twice in a row.

---

## 1. Root cause

`_invoke_model` sent `max_tokens=params["max_tokens"]` for **every** model call.
When `max_tokens` was unset the key was omitted from the provider payload, so
the provider default applied. Forced finalization had no budget of its own:

* Both finalization attempts reused the identical `max_tokens` value from the
  ordinary run params (or the provider default when unset).
* Both attempts reused the identical tools-free finalization instruction.
* Hidden reasoning tokens count against the completion budget in the provider
  protocol. A reasoning-capable model could therefore consume the whole budget
  on hidden reasoning and return `finish_reason="length"` with **empty visible
  content** on attempt 1 and again on attempt 2.

When both attempts failed with usable evidence present, the run ended with:

* `error.code = FINAL_ANSWER_EMPTY`
* `error.message = "Research completed, but the model failed to generate the final response."`

Research/tool execution had succeeded, but the run had no visible final answer.

---

## 2. What changed

Additive only; the loop shape, termination semantics and attempt ceiling are
unchanged.

* **Separate finalization output budgets** — attempt 1 and recovery no longer
  inherit the run's ordinary `max_tokens`; each resolves its own budget.
* **Length-aware recovery mode** — attempt 2 is classified as a length recovery
  or an empty/reasoning-only recovery and gets a stronger direct-answer
  instruction.
* **Capability-gated reasoning-effort reduction** — finalization may request a
  lower reasoning effort only when the resolved adapter and the model both
  declare the capability. No parameter is invented otherwise.
* **Bounded finalization evidence view** — a deduplicated, provenance-preserving
  projection can replace the full raw conversation for finalization only.
* **Partial-output preservation** — a truncated-but-usable visible answer is
  kept; a tiny length fragment is only used as a last resort, never silently
  dropped.
* **Telemetry** — per-attempt and run-level finalization diagnostics.

Preserved unchanged: V4.2.3.11 `_classify_turn` and forced-finalization
termination; V4.2.3.12 model-call watchdog and provider-quota semantics;
streaming accumulation; SSRF/URL guard; sandbox; secret handling; permission
checks; and trace/event invariants. `MAX_FINALIZATION_ATTEMPTS` remains capped
at **2** — there is no attempt 3.

---

## 3. Configuration (server-side only)

These are emergency/runtime synthesis budgets, **not** user quota and not
provider/project valves. They are read server-side in `app/config.py`:

| Env var | Default | Meaning |
| --- | --- | --- |
| `FINALIZATION_MAX_OUTPUT_TOKENS` | `8192` | attempt-1 forced-finalization output budget |
| `FINALIZATION_RECOVERY_MAX_OUTPUT_TOKENS` | `16384` | recovery (attempt-2) output budget; deliberately larger so a LENGTH-truncated first attempt has fresh room |
| `FINALIZATION_PARTIAL_MIN_CHARS` | `400` | minimum visible length below which a `finish_reason="length"` response is treated as an unusable fragment |
| `FINALIZATION_EVIDENCE_VIEW_MODE` | `auto` | `auto` \| `full` \| `evidence` |
| `FINALIZATION_EVIDENCE_VIEW_THRESHOLD_TOKENS` | `20000` | `auto` switches to the bounded evidence projection at/above this estimated input size |
| `FINALIZATION_EVIDENCE_VIEW_MAX_CHARS` | `24000` | hard cap on the projected evidence blob |
| `FINALIZATION_REASONING_EFFORT` | `low` | effort requested during finalization only, and only when the model explicitly declares the capability |

---

## 4. Budget semantics

For each finalization attempt `_resolve_finalization_budget` resolves:

1. **Base** = `FINALIZATION_RECOVERY_MAX_OUTPUT_TOKENS` for recovery, otherwise
   `FINALIZATION_MAX_OUTPUT_TOKENS`.
2. **Raise to explicit request** — if the run params set an explicit
   `max_tokens` larger than the base, the requested budget is raised to that
   value. The base is never lowered.
3. **Clamp to remaining context** — when the model declares a `context_window`
   and the input tokens can be estimated, `remaining = context_window -
   estimated_input_tokens`. The effective budget is clamped to `remaining`
   only when `0 < remaining < requested` **and** `remaining >= 1024` (a floor
   guards against demanding an impossible budget).

The resolution reports `requested_max_output_tokens`,
`effective_max_output_tokens`, `context_window` and
`remaining_context_capacity`. Clamping only ever uses a provider-declared
context window or an estimate; it **never fabricates a provider limit**. When
neither is known the effective budget equals the requested budget.

---

## 5. Recovery modes

Attempt 1 uses the normal forced-finalization instruction. If it does not yield
usable visible content, attempt 2 (recovery) is chosen by cause:

| Constant | Trigger | Recovery instruction |
| --- | --- | --- |
| `FINALIZATION_RECOVERY_LENGTH_MODE` | attempt 1 `finish_reason` is a length finish (`length`, `max_tokens`, `max_output_tokens`, `token_limit`) with no usable visible content | `_FINALIZATION_LENGTH_RECOVERY_INSTRUCTION`: state that the previous attempt was cut off, note the budget was increased, output the answer directly and concisely, minimize deliberation |
| `FINALIZATION_RECOVERY_EMPTY_MODE` | any other empty / reasoning-only attempt with no usable visible content | `_FINALIZATION_RECOVERY_INSTRUCTION` (V4.2.3.11 stronger direct-answer instruction) |

Recovery always uses the **larger recovery budget** and a stronger
direct-answer instruction. The reason is surfaced as `recovery_reason`
(`length` or `empty`) alongside `recovery_mode`. Maximum attempts stays 2.

---

## 6. Reasoning-effort control (capability-gated)

`_finalization_reasoning_effort` returns `(effort, applied)` and is applied
**only** when both gates pass:

1. the resolved adapter declares `supports_reasoning_effort`, **and**
2. the model declares `capabilities.reasoning_effort`.

The model capability value may be:

| Form | Behavior |
| --- | --- |
| list/tuple of allowed effort strings | attempt 1 uses `FINALIZATION_REASONING_EFFORT` when it is allowed, otherwise the lowest present of `minimal`/`low`/`medium`/`high` (falling back to the first allowed); recovery always uses the lowest available |
| `true` (bool) | use `FINALIZATION_REASONING_EFFORT` as-is |
| non-empty string | use that concrete effort string |
| empty / false / absent | no reasoning-effort parameter is sent |

If either gate is absent, no parameter is **invented** — the request is sent
exactly as it would be without this feature. Whether the reduction was applied
is recorded as `reasoning_reduction_applied`.

---

## 7. Partial-output semantics (deterministic)

For a successful attempt whose `finish_reason` is a length finish:

* **Length + zero visible content** → recovery (LENGTH mode). Nothing is
  accepted yet.
* **Length + tiny fragment (`< FINALIZATION_PARTIAL_MIN_CHARS`, default 400)** →
  recovery. The fragment is retained as `best_partial`. If recovery also fails
  and usable evidence exists, that **best fragment is returned** as the final
  answer with `finalization_partial_answer=true` and
  `finalization_truncated=true`.
* **Length + substantial visible content (`>= FINALIZATION_PARTIAL_MIN_CHARS`)** →
  the content is **preserved immediately** as the final answer with
  `finalization_truncated=true` and `finalization_partial_answer=false`.

Any visible content is always accepted as the final answer, even if the model
spuriously emitted tool calls that are never executed. Useful visible content is
**never** replaced by a generic failure.

---

## 8. Finalization evidence view

Two context modes exist for finalization:

* `FULL` — the original conversation plus the finalization instruction.
* `EVIDENCE_VIEW` — a bounded, deduplicated projection.

**Auto selection** (`FINALIZATION_EVIDENCE_VIEW_MODE=auto`) chooses
`EVIDENCE_VIEW` when reused tool results `> 0` **or** the estimated original
input is `>= FINALIZATION_EVIDENCE_VIEW_THRESHOLD_TOKENS`; otherwise `FULL`.
An explicit `full` forces `FULL`; `evidence`/`evidence_view` forces
`EVIDENCE_VIEW`.

**The projection preserves** the original user task messages, successful
evidence only, source provenance (title `<url>` for searches, title + URL +
bounded snippet for fetches) and a partial-failure note plus success count. It
**omits** duplicate/cached items, raw tool payloads and lifecycle chatter, and
is capped at `FINALIZATION_EVIDENCE_VIEW_MAX_CHARS`.

The canonical Run / Trace conversation is **never modified**: the projection is
call-only, rebuilt for the finalization call. The persisted comparison fields
are:

`finalization_context_mode`, `original_context_estimated_tokens`,
`finalization_context_estimated_tokens`, `evidence_item_count`, `source_count`,
`deduplicated_item_count`.

---

## 9. Telemetry

Every field below is safe (secret-free) and persisted on the Run. **Null means
unavailable**; provider telemetry is never fabricated.

Run-level finalization fields:

`forced_finalization`, `finalization_reason`, `finalization_attempts`,
`finalization_recovered`, `finalization_outcome`, `provider_finish_reason`,
`had_reasoning`, `had_content`, `content_length`,
`finalization_recovery_mode`, `finalization_context_mode`,
`original_context_estimated_tokens`, `finalization_context_estimated_tokens`,
`evidence_item_count`, `source_count`, `deduplicated_item_count`,
`finalization_length_failure_kind`, `finalization_partial_answer`,
`finalization_truncated`, `finalization_requested_max_output_tokens`,
`finalization_effective_max_output_tokens`, `finalization_error`.

Per-attempt entries are appended to `finalization_telemetry`, each carrying:
`finalization_attempt`, `finalization_recovery`, `recovery_mode`,
`finish_reason`, `requested_max_output_tokens`,
`effective_max_output_tokens`, `input_tokens`, `output_tokens`,
`reasoning_tokens`, `visible_content_tokens`, `total_completion_tokens`,
`context_window`, `estimated_context_tokens`, `remaining_context_capacity`,
`had_reasoning`, `had_visible_content`, `length_failure_kind`,
`reasoning_effort`, `reasoning_reduction_applied`.

`finalization_length_failure_kind` is one of `CONTEXT_WINDOW_EXHAUSTED`,
`OUTPUT_BUDGET_EXHAUSTED`, `PROVIDER_LENGTH_LIMIT`, `UNKNOWN_LENGTH`.

Event additions:

* `FINALIZATION_STARTED` — `attempt`, `recovery`, `recovery_reason`,
  `recovery_mode`, `finalization_context_mode`,
  `requested_max_output_tokens`, `effective_max_output_tokens`,
  `reasoning_effort`, `reasoning_reduction_applied`, `tools_enabled=false`.
* `FINALIZATION_DELTA` — the streaming delta type used for finalization content
  deltas (replacing `MODEL_OUTPUT_DELTA` for finalization calls).
* `FINALIZATION_COMPLETED` — `finish_reason`, `tool_call_count` plus the
  per-attempt telemetry fields above.
* `FINALIZATION_EMPTY` — emitted for a failed or partial attempt with
  `attempt`, `classification` (including `PARTIAL_LENGTH`), `had_reasoning`,
  `had_content`, `content_length`, `finish_reason`, `length_failure_kind`,
  `recovery_mode` plus the per-attempt telemetry fields.

Blind-safe trace projections expose the non-identity finalization diagnostics
listed above (see `_blind_run_view`); `finalization_telemetry` is deliberately
**not** projected because it may carry provider-specific detail.

---

## 10. Preserved invariants

* V4.2.3.11 termination states, forced-finalization tools-free contract and the
  `FINAL_ANSWER_EMPTY` terminal error are unchanged.
* `MAX_FINALIZATION_ATTEMPTS` stays capped at 2 — no attempt 3.
* V4.2.3.12 model-call watchdog and provider-quota layers are unchanged; a
  timeout/quota error after usable evidence still degrades to forced
  finalization.
* Streaming content/reasoning/tool-call accumulators remain separate; only
  genuine content deltas feed the visible buffer.
* SSRF/URL-guard, sandbox, secret, permission and trace/event invariants are
  untouched.

---

## 11. Tests

* `backend/tests/test_v42313_finalization_token_budget.py` — finalization budget
  resolution (base, explicit raise, context clamp), recovery modes, capability
  gating, partial-output preservation and telemetry.
* Regression suites: `backend/tests/test_v42311_final_answer_reliability.py`,
  `backend/tests/test_v42312_model_watchdog.py`,
  `backend/tests/test_v42312_web_research_lab_mode.py`.

---

## 12. Live acceptance

Live acceptance against real providers is reported separately. This document
describes the implemented contract only and does not claim live run results.
