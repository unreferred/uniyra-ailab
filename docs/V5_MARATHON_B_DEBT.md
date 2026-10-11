# V5 Marathon B Debt Decisions

Decision record for the six known debts carried out of Marathon A. The task was
to triage all six and to fix **only** the one that blocks Marathon B
(correct scorecards / research metrics). Everything else is documented here and
left untouched.

## Method

Each debt was inspected in the live working tree. A debt is a *blocker* only if a
Marathon B surface needs it to be correct right now. The Marathon B surfaces are:

- **Red team** (`app/services/red_team.py`, V5.7): runs through the service
  layer and the existing benchmark/evaluate endpoints; it does not drive the UI.
- **Scorecards** (`app/services/scorecards.py`, V5.8): read *stored*
  evaluations and evidence; they do not re-run evaluations or re-index.
- **Certification** (`app/services/certification.py`, V5.10): reads stored
  artifacts.
- **V5 acceptance / certification evidence handoff**: reads persisted records
  and artifacts.

## Summary

| # | Debt | Primary location | Blocks Marathon B? | Decision |
|---|------|------------------|--------------------|----------|
| 1 | ExperimentCreate / ConfigEditor does not expose Knowledge/tool benchmark config | `frontend/src/components/experiment/ConfigEditor.tsx:13`, `:63` | No (UI-only; backend + service layer already accept it) | DEFERRED |
| 2 | Workspace has no Evaluate Run button | `frontend/src/pages/Workspace.tsx` | No (endpoint + experiment UI exist) | DEFERRED |
| 3 | `observability._knowledge_metrics` reads `metadata.result_count` but spans use `returned_count` | `backend/app/routers/observability.py:84`, `:447` | **Yes** (wrong empty-retrieval metric feeds scorecards/research metrics) | **FIXED** |
| 4 | Marker -> chunk mapping is positional | `backend/app/services/evidence.py:81` (`marker_validity`) | No (scorecards consume stored validity) | DEFERRED |
| 5 | Judge regression is synchronous | `backend/app/services/regression.py:428`, `:481`; `backend/app/routers/benchmark.py:296` | No (results are persisted; latency only) | DEFERRED |
| 6 | Legacy `llm_judge` consumers may not understand `judge_model` | `backend/app/services/evaluation.py:38`, `:868`, `:1100`; `backend/app/models.py:1074` | No (V5 consumers already alias) | DEFERRED |

## 1. ExperimentCreate / ConfigEditor does not expose Knowledge/tool benchmark config

**Location.** `frontend/src/components/experiment/ConfigEditor.tsx`:
`ConfigDraft` (`:13`) carries only `key, label, model_id, temperature,
max_tokens, top_p, system_prompt, is_control`, and `configToPayload` (`:63`)
sends only those fields plus `prompt_version_id, metadata, is_control, order`.
`ExperimentCreate.tsx` builds its payload from that draft. The backend already
accepts the missing fields: `app/services/experiments.py:70-73` and `:192-195`
persist `enabled_tool_ids`, `knowledge_enabled`, `knowledge_collection_ids` and
`retrieval_options`.

**Marathon B dependency.** None for the new backend surfaces. The red-team
runner calls the execution/benchmark service layer directly and the benchmark /
evaluate endpoints accept these fields server-side; scorecards and certification
read stored records. The gap is purely that a researcher cannot arm a
knowledge/tool benchmark from the app UI. It becomes relevant only if V5
acceptance is scripted through the browser against the experiment-creation form.

**Decision: DEFERRED.** Follow-up: add a Knowledge toggle, collection
multi-select, `top_k`/threshold inputs and an enabled-tools picker to
`ConfigDraft`, `ConfigEditor` and `configToPayload`, then assert round-trip via
the existing configuration endpoint. No backend change required.

## 2. Workspace has no Evaluate Run button

**Location.** `frontend/src/pages/Workspace.tsx` (855 lines) has no evaluate
action among its buttons (`:509-723`). The backend endpoint exists:
`POST /api/runs/{run_id}/evaluate` at `backend/app/routers/runs.py:293`. The
only UI trigger today is the experiment results table:
`frontend/src/components/experiment/ResultsTable.tsx:149` -> `EvaluateDialog`.

**Marathon B dependency.** None. Red team, scorecards and certification operate
on stored runs and evaluations through services; a run can still be evaluated
through the experiment UI or the API. This is a workspace UX gap only.

**Decision: DEFERRED.** Follow-up: add an "Evaluate" action to the workspace run
drawer that reuses `EvaluateDialog` and calls `/runs/{run_id}/evaluate`.

## 3. `_knowledge_metrics` reads `metadata.result_count` but spans use `returned_count` (FIXED)

**Location.** `backend/app/routers/observability.py`. Before the fix the
knowledge span pipeline counted a retrieval as empty with
`{"$eq": ["$metadata.result_count", 0]}` (`_knowledge_metrics`, `empty` field).
KNOWLEDGE_RETRIEVAL spans do not carry `result_count`: they record
`returned_count` at `app/services/execution.py:1807`
(`"returned_count": int(_payload.get("result_count") or len(_results))`), and
`app/services/evidence.py:183` `_span_retrieval_count` already prefers
`returned_count` then `chunk_count`. Because `result_count` was absent,
`$eq` was always false and `empty_retrievals` was always 0, so the
empty-retrieval rate surfaced for scorecards / research metrics was wrong.

**Fix.** Added the pure helper `_retrieval_count_expr()` at
`observability.py:84`, returning
`{"$ifNull": ["$metadata.returned_count", {"$ifNull": ["$metadata.chunk_count", "$metadata.result_count"]}]}`,
and used it as the empty condition at `observability.py:447-448`. Preference
order is `returned_count` -> `chunk_count` -> legacy `result_count`; a span with
no count field still resolves to missing and is not counted empty (unchanged
behaviour). No other metric, match, or pipeline stage was changed.

**Tests.** `backend/tests/test_v5_knowledge_metrics_debt.py` (6 tests):
- pure expression shape;
- the expression resolves a span with `{"returned_count": 3, "result_count": 0}`
  to exactly **3** (and `chunk_count`/legacy `result_count` fallbacks, missing
  -> `None`) via a real `$project` aggregation;
- `GET /api/observability` reports `empty_retrievals == 1` for a
  `returned_count: 0` span even when a legacy `result_count: 0` is overridden by
  `returned_count: 3` on the other span;
- `chunk_count` fallback and legacy `result_count` backward compatibility;
- missing count metadata is not counted empty.

**Decision: FIXED.** No follow-up.

## 4. Marker -> chunk mapping is positional

**Location.** `backend/app/services/evidence.py:81` `marker_validity`: a marker
`[K#]` is valid iff `1 <= n <= len(provenance.chunk_ids)`. The marker number is
never associated with a specific `chunk_id`; it is interpreted by position in
the `chunk_ids` list. Consumers: `app/services/evaluation.py:268` (stores
`marker_validity` into evaluation evidence), `app/services/evaluators.py:460`,
`:489`, `:663`, and scorecards at `app/services/scorecards.py:255` which read the
stored `marker_validity` from evaluation evidence.

**Marathon B dependency.** None. Scorecards consume the already-stored
`marker_validity`; they do not recompute or resolve markers to chunks. The
positional scheme is self-consistent for the current pipeline (context is
injected in the same order as `chunk_ids`), and changing it would alter the
meaning of historical evidence. It is a latent risk only for a future consumer
that resolves a marker to a specific source/reference, or if injection ever
reorders/filters chunks relative to `chunk_ids`.

**Decision: DEFERRED.** Follow-up: persist an explicit ordered
marker -> `chunk_id` map (or store the resolved chunk id alongside each marker)
at evidence-collection time in `collect_run_evidence`, version the evidence
schema, and treat range-validity as a backward-compatible fallback.

## 5. Judge regression is synchronous

**Location.** `backend/app/services/regression.py:428` `run_regression` awaits
`evaluation_service.evaluate_run(...)` (`:481`) for every baseline/candidate run
inside the request. For `judge_model` each call runs the judge LLM
(`app/services/evaluation.py:586` `_run_judge_model`), so
`POST /api/benchmark/{experiment_id}/regression`
(`app/routers/benchmark.py:296`) blocks until all judge evaluations finish and
can time out on large experiments.

**Marathon B dependency.** Not a blocker. The regression document (metrics,
summary, cases, `evaluator_spec`) is persisted before the request returns
(`regression.py` writes `repo.regression_runs`), so scorecards and
certification read durable results. Red team does not use this endpoint. It is a
latency/robustness debt, not a correctness one.

**Decision: DEFERRED.** Follow-up: run regressions as a background job with a
`pending/running/completed` status, expose progress/cancel, and cap per-request
work (or document and enforce a timeout for the synchronous contract).

## 6. Legacy `llm_judge` consumers may not understand `judge_model`

**Location / evidence.**
`app/models.py:1074` `LEGACY_EVALUATOR_ALIASES` maps `llm_judge` -> `judge_model`
(and `ai_judge` -> `judge_model`); `app/services/evaluation.py:193`
`_resolve_evaluator_type` canonicalises on write, so stored
`evaluator_type` is `judge_model` (`:339`, `:570`). The legacy tuple
`EVALUATOR_TYPES = ("deterministic", "human", "llm_judge")` is still exported at
`evaluation.py:38`, and the judge path still writes
`raw_result.method = "llm_judge"` at `evaluation.py:868`. The score/cost
aggregation already matches both values
(`evaluation.py:1100` `{"$in": ["llm_judge", "judge_model"]}`), and scorecards
carry their own alias map (`app/services/scorecards.py:109`). The frontend still
sends `llm_judge` (`frontend/src/components/experiment/EvaluateDialog.tsx:32`,
`:62`, `:79`), which the server aliases, and a legacy test fixture uses it
(`frontend/src/__tests__/evaluationDetail.test.tsx:23`).

**Marathon B dependency.** None. Server-side write path is canonical, and both
the cost aggregation and the new scorecard builder already accept the legacy
alias. The residual risk is external/legacy consumers (outside the V5 surfaces)
that filter `evaluator_type == "llm_judge"` without the alias map, plus the
divergent `raw_result.method`.

**Decision: DEFERRED.** Follow-up: audit remaining `evaluator_type` readers and
route them through `LEGACY_EVALUATOR_ALIASES` (or document `llm_judge` as a
permanent read alias); decide whether `raw_result.method` should report the
canonical `judge_model`; keep `EVALUATOR_TYPES` as a deprecated export with a
comment pointing at `EVALUATION_TYPES`.
