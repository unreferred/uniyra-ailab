# OMNILAB V4.2.3.11 — Final Answer Reliability / Agent Refusal Closure

This release fixes the runtime contract that produced the vague Workspace
terminations:

- `Model did not produce a final answer without tools`
- `Model did not produce a final answer`

Both originated exclusively in the forced-finalization block of the canonical
execution loop (`app/services/execution.py`). Research/tool execution could
succeed (e.g. 20/20 searches) yet the run still failed because the single
forced-final call either returned no visible content (empty or reasoning-only)
or leaked tool calls even though tools were disabled.

## 1. Canonical per-turn termination states

Every model turn resolves into exactly one state (`_classify_turn`):

| State | Meaning |
| --- | --- |
| `TOOL_REQUEST` | The model emitted tool calls; the loop continues. |
| `FINAL_ANSWER` | Visible assistant content (non-empty `text`). |
| `EMPTY_RESPONSE` | Success with no visible content and no reasoning. |
| `REASONING_ONLY` | Success with reasoning but no visible content. |
| `PROVIDER_ERROR` | Non-success result from the provider. |
| `MALFORMED_RESPONSE` | Result missing / not an `ExecutionResult`. |

`EMPTY_RESPONSE` and `REASONING_ONLY` are **not** an immediate whole-run
failure once usable evidence exists. Hidden reasoning is never surfaced as the
final answer.

## 2. Forced finalization contract

When the loop stops because evidence is sufficient, progress stalled, tool
budgets/rounds are exhausted, or the provider errored after evidence was
gathered, a dedicated finalization call runs with:

- `tools = None` / no tool schemas
- `tool_choice = None` (the adapter only sends a tool payload when tools are
  present, so finalization cannot request a tool)
- an explicit tools-free instruction (`_FINALIZATION_PHASE_INSTRUCTION`)

No Search, Fetch, Research, Code Interpreter or other tool executes during
finalization. Tool calls spuriously emitted during finalization are never
executed; if visible content is present it is still accepted as the final
answer.

## 3. Bounded finalization recovery

- Attempt 1: normal forced finalization.
- If the result is `EMPTY_RESPONSE`, `REASONING_ONLY`, or a tool-call leak with
  no visible content: Attempt 2 (fresh call, tools disabled, stronger
  `_FINALIZATION_RECOVERY_INSTRUCTION`).
- Maximum attempts: 2 (configurable via `MAX_FINALIZATION_ATTEMPTS`, capped at
  2). No infinite retry.

If both attempts fail with usable evidence present, the run ends with:

- `error.code = FINAL_ANSWER_EMPTY`
- `error.message = "Research completed, but the model failed to generate the final response."`

The run keeps its tool results, evidence counters, `tool_rounds`, reasoning
telemetry, artifacts and trace. Workspace renders the error message; it never
pretends a final answer exists.

Back-compatible semantics are preserved for `TOOL_LOOP_NO_PROGRESS` and
`TOOL_LOOP_LIMIT_REACHED`.

## 4. Streaming accumulator

The OpenAI-compatible streaming path accumulates `reasoning_buffer`,
`content_buffer` and tool-call fragments separately. Only genuine
`MODEL_OUTPUT_DELTA` events feed the content buffer; reasoning and tool-call
deltas can never append to, clear or overwrite accumulated assistant content.
At stream end the normalized assistant content equals the accumulated content
buffer.

## 5. Observability

Finalization is traced with `FINALIZATION_STARTED` (carrying `attempt`,
`recovery`, `tools_enabled=false`), `FINALIZATION_DELTA`,
`FINALIZATION_COMPLETED` and `FINALIZATION_EMPTY`. Safe telemetry is persisted
on the Run and carried by the `RUN_COMPLETED` / `RUN_FAILED` events:

`finalization_attempts`, `finalization_recovered`, `finalization_outcome`,
`provider_finish_reason`, `had_reasoning`, `had_content`, `content_length`.

## 6. Retry final answer — DEFERRED (documented, not built)

A true *Retry final answer* action must reuse the existing Run's evidence and
context without repeating Search/Fetch, creating a new finalization attempt/run
related to the original (`parent_run_id`) with tools disabled.

At present the canonical Run persists the evidence summary only implicitly:
tool outputs live in `ToolCall` documents and per-round metadata (queries,
`result_urls`) lives in `run["tool_rounds"]`. Reconstructing the model-facing
evidence conversation would require a new finalization-only execution surface
plus a router endpoint and Workspace wiring. That is an architecture change, so
per the V4.2.3.11 spec it is deferred rather than hacked.

The existing Workspace **Regenerate** action re-runs the entire user turn
(including research) and therefore does **not** satisfy the evidence-only retry
requirement. All state needed to implement the deferred feature (evidence,tool
results, trace, tool rounds, finalization telemetry) is preserved on the failed
Run so the retry surface can be added additively later.
