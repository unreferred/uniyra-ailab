"""MongoDB connection + index management.

This is the ONLY module that imports the Mongo driver directly. Everything else
depends on the Repository abstraction, so the persistence engine can later be
swapped (e.g. to PostgreSQL) without touching services or routers.
"""
import logging

from motor.motor_asyncio import AsyncIOMotorClient

from . import config

logger = logging.getLogger("omnilab.indexes")

client = AsyncIOMotorClient(config.MONGO_URL)
db = client[config.DB_NAME]


async def _safe_index(collection_name: str, keys, **kwargs) -> None:
    """Create an index without ever letting startup crash.

    Mongo creates a collection on first index build, which is fine for core
    collections. For collections that may legitimately not exist yet (newly
    planned features, older deployments) a failure -- e.g. an incompatible
    legacy index or a duplicate-key surprise -- must be logged rather than
    aborting boot. Idempotent by Mongo's own semantics.
    """
    try:
        await db[collection_name].create_index(keys, **kwargs)
    except Exception as exc:  # pragma: no cover - defensive startup guard
        logger.warning("Index creation skipped for %s %s: %s", collection_name, keys, exc)


async def init_indexes() -> None:
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True)
    await db.login_attempts.create_index("identifier")
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)

    for coll in [
        "projects", "providers", "models", "conversations", "messages",
        "experiments", "runs", "rubrics", "evaluations", "artifacts", "usage_events",
        "councils", "research_sessions", "findings",
        "datasets", "dataset_cases",
        "experiment_configs", "dataset_versions", "dataset_version_cases",
        "prompts", "prompt_versions", "benchmark_plans",
    ]:
        await db[coll].create_index("id", unique=True)
        await db[coll].create_index("owner_id")

    await db.projects.create_index("owner_id")
    await db.models.create_index("provider_id")
    await db.messages.create_index("conversation_id")
    await db.runs.create_index("experiment_id")
    await db.runs.create_index("model_id")
    await db.runs.create_index("created_at")
    await db.evaluations.create_index("experiment_id")
    await db.usage_events.create_index("created_at")
    await db.usage_events.create_index("model_id")
    await db.dataset_cases.create_index("dataset_id")
    # --- auth sessions / rate limiting -------------------------------------
    await db.auth_sessions.create_index("jti", unique=True)
    await db.auth_sessions.create_index([("user_id", 1), ("revoked_at", 1)])
    await db.auth_sessions.create_index("expires_at", expireAfterSeconds=0)

    await db.login_attempts.create_index("expires_at", expireAfterSeconds=0)

    await db.rate_limits.create_index("identifier", unique=True)
    await db.rate_limits.create_index("expires_at", expireAfterSeconds=0)

    # --- Phase 1 observability / boundary hardening indexes ----------------
    # All additive and non-unique. Wrapped so a single incompatible legacy
    # collection can never stop the process from booting.
    await _safe_index("runs", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])
    await _safe_index("runs", [("owner_id", 1), ("experiment_id", 1), ("created_at", 1)])
    await _safe_index("runs", [("owner_id", 1), ("dataset_case_id", 1), ("configuration_id", 1), ("repetition_index", 1)])
    await _safe_index("runs", [("owner_id", 1), ("trace_id", 1)])
    await _safe_index("runs", [("owner_id", 1), ("council_id", 1)])
    await _safe_index("runs", [("owner_id", 1), ("conversation_id", 1)])

    await _safe_index("evaluations", [("owner_id", 1), ("run_id", 1)])
    await _safe_index("evaluations", [("owner_id", 1), ("experiment_id", 1)])

    await _safe_index("usage_events", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])
    await _safe_index("usage_events", [("owner_id", 1), ("run_id", 1)])

    # Unique per (owner, dataset, version). The earlier non-unique index (same
    # keys, default name) from a prior partial run is dropped first; legacy
    # duplicates -- if any -- are caught and logged rather than aborting boot.
    try:
        await db.dataset_versions.drop_index("owner_id_1_dataset_id_1_version_1")
    except Exception:
        pass
    await _safe_index(
        "dataset_versions", [("owner_id", 1), ("dataset_id", 1), ("version", 1)],
        unique=True, name="uniq_owner_dataset_version",
    )
    await _safe_index("dataset_versions", [("owner_id", 1), ("dataset_id", 1), ("status", 1)])
    await _safe_index("dataset_version_cases", [("owner_id", 1), ("dataset_version_id", 1), ("ordering", 1)])

    await _safe_index("dataset_cases", [("owner_id", 1), ("dataset_id", 1), ("ordering", 1)])

    await _safe_index("reveal_events", [("owner_id", 1), ("experiment_id", 1)])
    await _safe_index("reveal_events", [("owner_id", 1), ("created_at", 1)])

    await _safe_index("experiments", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])
    await _safe_index("experiments", [("owner_id", 1), ("status", 1)])

    await _safe_index("findings", [("owner_id", 1), ("project_id", 1)])
    await _safe_index("findings", [("owner_id", 1), ("updated_at", 1)])

    await _safe_index("research_sessions", [("owner_id", 1), ("project_id", 1)])
    await _safe_index("councils", [("owner_id", 1), ("project_id", 1)])

    # Prompts are owner+project scoped; the name index stays non-unique (names
    # are not required to be unique yet). Prompt versions are unique per
    # (owner, prompt, version); the earlier same-key non-unique index is dropped
    # first and duplicate-key surprises are logged, never fatal.
    await _safe_index("prompts", [("owner_id", 1), ("project_id", 1)])
    await _safe_index("prompts", [("owner_id", 1), ("project_id", 1), ("name", 1)])
    try:
        await db.prompt_versions.drop_index("owner_id_1_prompt_id_1_version_1")
    except Exception:
        pass
    await _safe_index(
        "prompt_versions", [("owner_id", 1), ("prompt_id", 1), ("version", 1)],
        unique=True, name="uniq_owner_prompt_version",
    )

    await _safe_index("messages", [("owner_id", 1), ("conversation_id", 1), ("created_at", 1)])


    # --- Phase 2: first-class Experiment protocol / configuration indexes -----
    await _safe_index("experiment_configs", [("owner_id", 1), ("experiment_id", 1)])
    await _safe_index(
        "experiment_configs", [("owner_id", 1), ("experiment_id", 1), ("label", 1)]
    )

    # benchmark_plans is consumed in Phase 2 part B; the collection and its
    # indexes are created now so the runner can rely on them.
    await _safe_index("benchmark_plans", [("owner_id", 1), ("experiment_id", 1)])
    await _safe_index("benchmark_plans", [("owner_id", 1), ("status", 1)])
    await _safe_index("benchmark_plans", [("owner_id", 1), ("id", 1)], unique=True,
                      name="uniq_owner_benchmark_plan_id")

    # Benchmark idempotency: one persisted attempt per
    # (experiment, case, configuration, repetition, attempt). Partial so it
    # only constrains benchmark-kind runs; legacy/other runs are unaffected.
    # Guarded because a legacy collision must not abort startup.
    await _safe_index(
        "runs",
        [("owner_id", 1), ("experiment_id", 1), ("dataset_case_id", 1),
         ("configuration_id", 1), ("repetition_index", 1), ("attempt_index", 1)],
        unique=True,
        name="uniq_benchmark_attempt",
        partialFilterExpression={"execution_kind": "benchmark"},
    )


    # --- Phase 2 (part B): benchmark runner / evaluation indexes -------------
    await _safe_index("runs", [
        ("owner_id", 1), ("experiment_id", 1), ("execution_kind", 1),
        ("configuration_id", 1), ("repetition_index", 1),
    ])
    await _safe_index("runs", [("owner_id", 1), ("benchmark_plan_id", 1)])

    await _safe_index("evaluations", [
        ("owner_id", 1), ("experiment_id", 1), ("configuration_id", 1),
    ])
    await _safe_index("evaluations", [
        ("owner_id", 1), ("run_id", 1), ("evaluator_type", 1),
    ])

    await _safe_index("benchmark_plans", [
        ("owner_id", 1), ("experiment_id", 1), ("created_at", 1),
    ])

    # --- Phase 3: observability / context / tools -----------------------------
    # Additive and best-effort: a legacy collection with an incompatible index
    # is logged by _safe_index, never fatal. ``tools`` may hold global builtins
    # with owner_id None, so its owner_id index is non-unique.
    for coll in [
        "traces", "trace_spans", "context_snapshots", "attachments",
        "tool_calls", "failures", "tools",
    ]:
        await _safe_index(coll, "id", unique=True)
        await _safe_index(coll, "owner_id")

    await _safe_index("traces", [("owner_id", 1), ("project_id", 1), ("started_at", 1)])
    await _safe_index("traces", [("owner_id", 1), ("root_run_id", 1)])
    await _safe_index("traces", [("owner_id", 1), ("experiment_id", 1)])
    await _safe_index("traces", [("owner_id", 1), ("council_id", 1)])

    await _safe_index("trace_spans", [("owner_id", 1), ("trace_id", 1), ("started_at", 1)])
    await _safe_index("trace_spans", [("trace_id", 1), ("ordering", 1)])

    await _safe_index(
        "context_snapshots", [("owner_id", 1), ("run_id", 1)],
        unique=True, name="uniq_owner_run_context",
    )

    await _safe_index("attachments", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])
    await _safe_index("attachments", [("owner_id", 1), ("sha256", 1)])
    await _safe_index("attachments", [("owner_id", 1), ("conversation_id", 1)])

    await _safe_index("tool_calls", [("owner_id", 1), ("trace_id", 1), ("started_at", 1)])
    await _safe_index("tool_calls", [("owner_id", 1), ("run_id", 1)])

    await _safe_index("failures", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])
    await _safe_index("failures", [("owner_id", 1), ("run_id", 1)])
    await _safe_index("failures", [("owner_id", 1), ("category", 1), ("created_at", 1)])

    await _safe_index("tools", [("scope", 1), ("name", 1)])
    await _safe_index("tools", [("owner_id", 1), ("project_id", 1)])

    # --- Phase 4: workspace platform ------------------------------------------
    # Additive and best-effort; guarded by _safe_index so a legacy collection
    # with an incompatible index is logged, never fatal.
    for coll in [
        "project_files", "knowledge_collections", "knowledge_documents",
        "knowledge_chunks", "tool_versions", "tool_credentials",
        "code_executions", "project_activity",
    ]:
        await _safe_index(coll, "id", unique=True)
        await _safe_index(coll, "owner_id")

    await _safe_index("project_files", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])
    await _safe_index("project_files", [("owner_id", 1), ("project_id", 1), ("sha256", 1)])
    await _safe_index("project_files", [("owner_id", 1), ("project_id", 1), ("attachment_id", 1)])
    await _safe_index("project_files", [("owner_id", 1), ("project_id", 1), ("status", 1)])

    await _safe_index("knowledge_collections", [("owner_id", 1), ("project_id", 1), ("name", 1)])
    await _safe_index("knowledge_collections", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])
    await _safe_index("knowledge_collections", [("owner_id", 1), ("project_id", 1), ("status", 1)])

    await _safe_index("knowledge_documents", [("owner_id", 1), ("collection_id", 1), ("created_at", 1)])
    await _safe_index("knowledge_documents", [("owner_id", 1), ("collection_id", 1), ("project_file_id", 1)])
    await _safe_index("knowledge_documents", [("owner_id", 1), ("collection_id", 1), ("status", 1)])
    await _safe_index("knowledge_documents", [("owner_id", 1), ("project_id", 1), ("ingestion_status", 1)])

    await _safe_index("knowledge_chunks", [("owner_id", 1), ("project_id", 1), ("active", 1), ("index_status", 1)])
    await _safe_index("knowledge_chunks", [("owner_id", 1), ("collection_id", 1), ("embedding_fingerprint", 1)])
    await _safe_index("knowledge_chunks", [("owner_id", 1), ("collection_id", 1), ("document_id", 1), ("chunk_index", 1)])
    await _safe_index("knowledge_chunks", [("owner_id", 1), ("document_id", 1)])
    await _safe_index("knowledge_chunks", [("owner_id", 1), ("collection_id", 1)])

    try:
        await db.tool_versions.drop_index("owner_id_1_tool_id_1_version_1")
    except Exception:
        pass
    await _safe_index(
        "tool_versions", [("owner_id", 1), ("tool_id", 1), ("version", 1)],
        unique=True, name="uniq_owner_tool_version",
    )
    await _safe_index("tool_versions", [("owner_id", 1), ("tool_id", 1), ("created_at", 1)])
    await _safe_index("tool_versions", [("owner_id", 1), ("tool_id", 1)])

    await _safe_index("tool_credentials", [("owner_id", 1), ("project_id", 1), ("name", 1)])

    # --- Phase 4.2.3.1: global search/fetch provider registries ----------------
    # Provider docs are global (owner_id None); address them by stable ``id``.
    # Mirrors the Phase 4 pattern: id unique + owner_id guard, best-effort.
    for coll in ["search_providers", "fetch_providers"]:
        await _safe_index(coll, "id", unique=True)
        await _safe_index(coll, "owner_id")

    # --- Provider quota telemetry: safe, non-secret quota snapshots -----------
    await _safe_index("provider_quota_telemetry", "created_at")
    await _safe_index("provider_quota_telemetry", "id", unique=True)

    await _safe_index("code_executions", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])
    await _safe_index("code_executions", [("owner_id", 1), ("run_id", 1)])
    await _safe_index("code_executions", [("owner_id", 1), ("trace_id", 1)])

    await _safe_index("project_activity", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])

    # --- Phase 5: research quality --------------------------------------------
    # Additive and best-effort; guarded by _safe_index so a legacy collection
    # with an incompatible index is logged, never fatal.
    await _safe_index("rubric_versions", [("owner_id", 1), ("rubric_id", 1), ("version", 1)])
    await _safe_index("rubric_versions", [("owner_id", 1), ("rubric_id", 1), ("created_at", 1)])

    await _safe_index("regression_runs", [("owner_id", 1), ("experiment_id", 1), ("created_at", 1)])
    await _safe_index("regression_runs", [("owner_id", 1), ("project_id", 1)])

    await _safe_index("scorecards", "id", unique=True)
    await _safe_index("scorecards", "owner_id")
    await _safe_index("scorecards", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])
    await _safe_index("scorecards", [("owner_id", 1), ("experiment_id", 1), ("created_at", 1)])
    await _safe_index("scorecards", [("owner_id", 1), ("run_id", 1)])
    await _safe_index("certification_candidates", "id", unique=True)
    await _safe_index("certification_candidates", "owner_id")
    await _safe_index("certification_candidates", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])

    # --- Phase V6: prompt & policy foundation ---------------------------------
    # Additive and best-effort; guarded by _safe_index so a legacy collection
    # with an incompatible index is logged, never fatal. Every record is
    # owner+project scoped.
    for coll in ["prompt_blocks", "project_policies", "project_invariants"]:
        await _safe_index(coll, "id", unique=True)
        await _safe_index(coll, "owner_id")

    await _safe_index("prompt_blocks", [("owner_id", 1), ("project_id", 1), ("status", 1)])
    await _safe_index("prompt_blocks", [("owner_id", 1), ("project_id", 1), ("category", 1)])

    await _safe_index("project_policies", [("owner_id", 1), ("project_id", 1), ("family_id", 1), ("version", 1)])
    await _safe_index("project_policies", [("owner_id", 1), ("project_id", 1), ("status", 1)])

    await _safe_index("project_invariants", [("owner_id", 1), ("project_id", 1), ("status", 1)])
    await _safe_index(
        "project_invariants", [("owner_id", 1), ("project_id", 1), ("code", 1)],
        unique=True, name="uniq_owner_project_invariant_code",
        partialFilterExpression={"status": "active"},
    )

    # --- Phase V6.2: Context Lab ---------------------------------------------
    # Additive and best-effort; guarded by _safe_index so a legacy collection
    # with an incompatible index is logged, never fatal. Every record is
    # owner+project scoped; pack versions are unique per (owner, pack, version).
    for coll in ["context_packs", "context_pack_versions", "context_budget_policies"]:
        await _safe_index(coll, "id", unique=True)
        await _safe_index(coll, "owner_id")

    await _safe_index("context_packs", [("owner_id", 1), ("project_id", 1), ("status", 1)])
    await _safe_index("context_packs", [("owner_id", 1), ("project_id", 1), ("name", 1)])
    try:
        await db.context_pack_versions.drop_index("owner_id_1_pack_id_1_version_1")
    except Exception:
        pass
    await _safe_index(
        "context_pack_versions", [("owner_id", 1), ("pack_id", 1), ("version", 1)],
        unique=True, name="uniq_owner_context_pack_version",
    )
    await _safe_index(
        "context_budget_policies", [("owner_id", 1), ("project_id", 1), ("status", 1)]
    )

    # --- Phase V6.3: Prompt Compiler -----------------------------------------
    # Additive and best-effort; compiled prompts are immutable, owner+project
    # scoped compilation records. Indexed for the common list filters.
    await _safe_index("compiled_prompts", "id", unique=True)
    await _safe_index("compiled_prompts", "owner_id")
    await _safe_index("compiled_prompts", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])
    await _safe_index("compiled_prompts", [("owner_id", 1), ("prompt_version_id", 1)])
    await _safe_index("compiled_prompts", [("owner_id", 1), ("agent_version_id", 1)])


    # --- Phase V6.4: Agent Registry & Versioning -----------------------------
    # Additive and best-effort; guarded by _safe_index so a legacy collection
    # with an incompatible index is logged, never fatal. Agents are
    # owner+project scoped; versions are unique per (owner, agent, version).
    for coll in ["agents", "agent_versions"]:
        await _safe_index(coll, "id", unique=True)
        await _safe_index(coll, "owner_id")

    await _safe_index("agents", [("owner_id", 1), ("project_id", 1), ("status", 1)])
    try:
        await db.agent_versions.drop_index("owner_id_1_agent_id_1_version_1")
    except Exception:
        pass
    await _safe_index(
        "agent_versions", [("owner_id", 1), ("agent_id", 1), ("version", 1)],
        unique=True, name="uniq_owner_agent_version",
    )
    await _safe_index("agent_versions", [("owner_id", 1), ("agent_id", 1), ("status", 1)])

    # --- Phase V6.14: Workspace Prompt Capture -------------------------------
    # Additive and best-effort; captures are owner+project scoped lineage
    # records linking an originating conversation to a created prompt version.
    await _safe_index("prompt_captures", "id", unique=True)
    await _safe_index("prompt_captures", "owner_id")
    await _safe_index(
        "prompt_captures", [("owner_id", 1), ("project_id", 1), ("created_at", 1)]
    )
    await _safe_index(
        "prompt_captures", [("owner_id", 1), ("conversation_id", 1)]
    )

    # --- Phase V6.14: Planning Decision Memory -------------------------------
    for coll in ["planning_decisions", "planning_conflicts"]:
        await _safe_index(coll, "id", unique=True)
        await _safe_index(coll, "owner_id")
        await _safe_index(coll, [("owner_id", 1), ("project_id", 1), ("conversation_id", 1), ("created_at", 1)])

    await _safe_index(
        "planning_decisions",
        [("owner_id", 1), ("project_id", 1), ("conversation_id", 1), ("topic_key", 1)],
    )
    await _safe_index(
        "planning_decisions",
        [("owner_id", 1), ("project_id", 1), ("conversation_id", 1), ("status", 1)],
    )
    await _safe_index(
        "planning_conflicts",
        [("owner_id", 1), ("project_id", 1), ("conversation_id", 1), ("status", 1)],
    )

    # --- Phase V6.15: Structured Planning Graph ------------------------------
    # Additive and best-effort. Nodes/edges are a *derived* view over the V6.14
    # decision ledger: owner+project scoped, a node is unique per
    # (owner, project, node_key) and an edge per
    # (owner, project, source_node_id, target_node_id, relation).
    for coll in ["planning_nodes", "planning_edges"]:
        await _safe_index(coll, "id", unique=True)
        await _safe_index(coll, "owner_id")

    try:
        await db.planning_nodes.drop_index("owner_id_1_project_id_1_node_key_1")
    except Exception:
        pass
    await _safe_index(
        "planning_nodes",
        [("owner_id", 1), ("project_id", 1), ("node_key", 1)],
        unique=True, name="uniq_owner_project_node_key",
    )
    await _safe_index(
        "planning_nodes", [("owner_id", 1), ("project_id", 1), ("type", 1)]
    )
    await _safe_index(
        "planning_nodes", [("owner_id", 1), ("project_id", 1), ("decision_id", 1)]
    )

    await _safe_index(
        "planning_edges",
        [("owner_id", 1), ("project_id", 1), ("source_node_id", 1),
         ("target_node_id", 1), ("relation", 1)],
        unique=True, name="uniq_owner_project_edge",
    )

    # --- Phase V6.15: Living PRD (compiled artifact) ---------------------------
    # Additive and best-effort. A PRD version is a *derived* compilation of the
    # decision ledger + planning graph; owner+project scoped and unique per
    # (owner, project, version). Versions are append-only history.
    await _safe_index("planning_prds", "id", unique=True)
    await _safe_index("planning_prds", "owner_id")
    try:
        await db.planning_prds.drop_index("owner_id_1_project_id_1_version_1")
    except Exception:
        pass
    await _safe_index(
        "planning_prds",
        [("owner_id", 1), ("project_id", 1), ("version", 1)],
        unique=True, name="uniq_owner_project_prd_version",
    )
    await _safe_index(
        "planning_prds", [("owner_id", 1), ("project_id", 1), ("created_at", 1)]
    )
    await _safe_index(
        "planning_prds", [("owner_id", 1), ("project_id", 1), ("status", 1)]
    )

    # --- Phase V6.9: Drift & Failure Analysis ---------------------------------
    # Additive and best-effort; analyses are owner+project scoped.
    await _safe_index("drift_analyses", "id", unique=True)
    await _safe_index("drift_analyses", "owner_id")
    await _safe_index("drift_analyses", [("owner_id", 1), ("project_id", 1), ("created_at", 1)])
    await _safe_index("drift_analyses", [("owner_id", 1), ("run_id", 1)])
