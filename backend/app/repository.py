"""Generic repository abstraction over a persistence collection.

Services depend on this contract, never on the Mongo driver. Swapping the
persistence engine means providing a new Repository implementation with the
same async method surface. All records are addressed by stable UUID `id` and
scoped by `owner_id`; the driver's `_id` is always projected out.
"""
from typing import Any, Dict, List, Optional

from .database import db


class Repository:
    def __init__(self, collection_name: str):
        self._c = db[collection_name]

    async def insert(self, document: Dict[str, Any]) -> Dict[str, Any]:
        await self._c.insert_one({**document})
        document.pop("_id", None)
        return document

    async def get(self, id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        query: Dict[str, Any] = {"id": id}
        if owner_id is not None:
            query["owner_id"] = owner_id
        return await self._c.find_one(query, {"_id": 0})

    async def find_one(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return await self._c.find_one(query, {"_id": 0})

    async def list(
        self,
        query: Dict[str, Any],
        sort_field: str = "created_at",
        descending: bool = True,
        limit: int = 500,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        cursor = self._c.find(query, {"_id": 0}).sort(
            sort_field, -1 if descending else 1
        )
        if offset:
            cursor = cursor.skip(offset)
        cursor = cursor.limit(limit)
        return await cursor.to_list(length=limit)

    async def count(self, query: Dict[str, Any]) -> int:
        return await self._c.count_documents(query)

    async def update(self, id: str, owner_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        await self._c.update_one({"id": id, "owner_id": owner_id}, {"$set": patch})
        return await self.get(id, owner_id)

    async def update_if(
        self,
        id: str,
        owner_id: str,
        expected: Dict[str, Any],
        patch: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Atomically update an owned record when its state still matches."""
        query = {"id": id, "owner_id": owner_id, **dict(expected or {})}
        result = await self._c.update_one(query, {"$set": dict(patch or {})})
        if not result.matched_count:
            return None
        return await self.get(id, owner_id)

    async def update_global(self, id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a GLOBAL document addressed by ``id`` only (no owner match).

        Search/fetch provider docs are global (owner_id None), so the standard
        owner-scoped :meth:`update` can never match them. This id-only variant
        exists for those registries. Callers keep best-effort semantics.
        """
        await self._c.update_one({"id": id}, {"$set": patch})
        return await self._c.find_one({"id": id}, {"_id": 0})

    async def delete(self, id: str, owner_id: str) -> bool:
        res = await self._c.delete_one({"id": id, "owner_id": owner_id})
        return res.deleted_count > 0

    async def delete_many(self, query: Dict[str, Any]) -> int:
        res = await self._c.delete_many(query)
        return res.deleted_count


# Repository registry — a thin factory so callers never construct driver handles.
users = Repository("users")
projects = Repository("projects")
providers = Repository("providers")
models = Repository("models")
conversations = Repository("conversations")
messages = Repository("messages")
experiments = Repository("experiments")
runs = Repository("runs")
rubrics = Repository("rubrics")
evaluations = Repository("evaluations")
artifacts = Repository("artifacts")
usage_events = Repository("usage_events")
councils = Repository("councils")
research_sessions = Repository("research_sessions")
findings = Repository("findings")
datasets = Repository("datasets")
dataset_cases = Repository("dataset_cases")
auth_sessions = Repository("auth_sessions")
rate_limits = Repository("rate_limits")
reveal_events = Repository("reveal_events")
experiment_configs = Repository("experiment_configs")
dataset_versions = Repository("dataset_versions")
dataset_version_cases = Repository("dataset_version_cases")
prompts = Repository("prompts")
prompt_versions = Repository("prompt_versions")
benchmark_plans = Repository("benchmark_plans")

# ---------------------------------------------------------------- Phase 3 observability / context / tools
traces = Repository("traces")
trace_spans = Repository("trace_spans")
context_snapshots = Repository("context_snapshots")
attachments = Repository("attachments")
tool_calls = Repository("tool_calls")
failures = Repository("failures")
tools = Repository("tools")

# ---------------------------------------------------------------- Phase 4: workspace platform
project_files = Repository("project_files")
knowledge_collections = Repository("knowledge_collections")
knowledge_documents = Repository("knowledge_documents")
knowledge_chunks = Repository("knowledge_chunks")
tool_versions = Repository("tool_versions")
tool_credentials = Repository("tool_credentials")
code_executions = Repository("code_executions")
project_activity = Repository("project_activity")

# ---------------------------------------------------------------- Phase 4.2.3: search & fetch providers
search_providers = Repository("search_providers")
fetch_providers = Repository("fetch_providers")

# ---------------------------------------------------------------- Phase 4.2.3.2: valves
valve_values = Repository("valve_values")
# Safe, non-secret provider quota snapshots (never stores credentials).
provider_quota_telemetry = Repository("provider_quota_telemetry")

# ---------------------------------------------------------------- Phase 5: research quality
rubric_versions = Repository("rubric_versions")
regression_runs = Repository("regression_runs")
scorecards = Repository("scorecards")
certification_candidates = Repository("certification_candidates")

# ---------------------------------------------------------------- Phase V6: prompt & policy foundation
prompt_blocks = Repository("prompt_blocks")
project_policies = Repository("project_policies")
project_invariants = Repository("project_invariants")


# ---------------------------------------------------------------- Phase V6.2: Context Lab
context_packs = Repository("context_packs")
context_pack_versions = Repository("context_pack_versions")
context_budget_policies = Repository("context_budget_policies")

# ---------------------------------------------------------------- Phase V6.3: Prompt Compiler
compiled_prompts = Repository("compiled_prompts")

# ---------------------------------------------------------------- Phase V6.4: Agent Registry
agents = Repository("agents")
agent_versions = Repository("agent_versions")

# ---------------------------------------------------------------- Phase V6.14: Workspace Prompt Capture
prompt_captures = Repository("prompt_captures")

# ---------------------------------------------------------------- Phase V6.14: Planning Decision Memory
planning_decisions = Repository("planning_decisions")
planning_conflicts = Repository("planning_conflicts")

# ---------------------------------------------------------------- Phase V6.15: Structured Planning Graph
planning_nodes = Repository("planning_nodes")
planning_edges = Repository("planning_edges")

# ---------------------------------------------------------------- Phase V6.15: Living PRD (compiled artifact)
planning_prds = Repository("planning_prds")

# ---------------------------------------------------------------- Phase V6.9: Drift & Failure Analysis
drift_analyses = Repository("drift_analyses")
