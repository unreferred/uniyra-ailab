"""V6 end-to-end definition-of-done integration test.

This is a *real* integration test: it drives the complete V6 chain through the
actual HTTP surface (``app_client``) and the real service/repository layers over
MongoDB. The only model/provider interaction is served by a scripted adapter
installed with ``monkeypatch`` (no network).

Chain under test:

1.  project + provider + model (scripted adapter);
2.  prompt spec -> version with an attached block -> freeze;
3.  ARCHITECTURE project policy + active blocking invariant;
4.  context pack + frozen version with items;
5.  context budget policy;
6.  agent identity + frozen version bound to model / prompt version / context
    pack version / tool + knowledge policies / runtime policy / output contract
    / evaluation profile / ``routing {"mode": "fixed"}``;
7.  prompt compiler preview (not persisted) + compile (persisted) with a stable
    checksum, trusted policy/instruction sections and the invariant + block in
    the rendered prompt;
8.  agent runtime execution with provenance, an ``agent`` trace and applied
    permissions;
9.  agent evaluation with agent provenance and a deterministic outcome;
10. deterministic architecture drift detection + materialization into findings;
11. finding -> regression promotion (new frozen dataset version, V5 reuse);
12. run scorecard (dimensions present, no composite without a policy, missing
    states stay unknown rather than fabricated zeros);
13. certification candidate (subject ``agent``) at DRAFT + readiness that is
    never CERTIFIED and never automatic.

Every step asserts real, persisted ids/provenance -- nothing is fabricated.
All records belong to a throwaway ``pytest-`` owner purged by ``as_user``.
"""
from __future__ import annotations

import json
import uuid

from app import repository as repo
from app import services
from app.models import now_iso
from app.services import execution as execution_module

from _support import (
    ScriptedAdapter,
    create_model,
    create_provider,
    new_id,
    result_success,
)

API = "/api"

_ARCH_POLICY_CONTENT = "All billing must flow through app/services/billing.py."
_INVARIANT_CODE = "INV_NO_PARALLEL_BILLING"
_INVARIANT_TITLE = "No parallel billing subsystem"
_BLOCK_CONTENT = "Use the existing billing service; do not add a parallel subsystem."


class RuntimeAdapter(ScriptedAdapter):
    """Scripted adapter that can declare tool-calling support."""

    supports_model_discovery = False
    supports_tools = False
    supports_vision = False

    def __init__(self, executor, *, tools=False):
        super().__init__(executor)
        self.supports_tools = bool(tools)


def _install(monkeypatch, executor, *, tools=False):
    factory = lambda provider: RuntimeAdapter(executor, tools=tools)
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)


def _post(client, path, payload=None, expect=200):
    r = client.post(path, json=payload if payload is not None else {})
    assert r.status_code == expect, f"POST {path} -> {r.status_code}: {r.text}"
    return r.json()


def _seed_tool(client, owner, project_id):
    tool_id = new_id()

    async def _seed():
        now = now_iso()
        await repo.tools.insert({
            "id": tool_id,
            "owner_id": owner,
            "project_id": project_id,
            "name": "pytest-e2e-calculator",
            "display_name": "pytest-e2e-calculator",
            "tool_type": "function",
            "implementation": "builtin:calculator",
            "enabled": True,
            "scope": "owner",
            "permissions": ["calculator.execute"],
            "created_at": now,
            "updated_at": now,
        })

    client.portal.call(_seed)
    return tool_id


def test_v6_definition_of_done_chain(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    suffix = uuid.uuid4().hex[:8]

    # ------------------------------------------------------------- 1. project/model
    project = _post(app_client, f"{API}/projects", {"name": f"pytest-v6-e2e-{suffix}"})
    project_id = project["id"]

    async def _make_model():
        provider_id = await create_provider(owner)
        return await create_model(
            owner,
            provider_id=provider_id,
            display_name=f"pytest-v6-e2e-model-{suffix}",
            model_identifier=f"pytest/{suffix}",
            capabilities={"tools": True},
        )

    model_id = app_client.portal.call(_make_model)
    assert app_client.portal.call(repo.models.get, model_id, owner)["id"] == model_id

    # ------------------------------------------------------------- 2. prompt
    block = _post(app_client, f"{API}/prompt-blocks", {
        "project_id": project_id,
        "name": "Reuse existing billing",
        "category": "reuse_existing_services",
        "content": _BLOCK_CONTENT,
    })
    prompt = _post(app_client, f"{API}/prompts", {
        "project_id": project_id,
        "name": f"billing-prompt-{suffix}",
        "status": "draft",
    })
    prompt_version = _post(app_client, f"{API}/prompts/{prompt['id']}/versions", {
        "content": "Implement the requested billing change.",
        "blocks": [block["id"]],
    })
    frozen_prompt = _post(
        app_client,
        f"{API}/prompts/{prompt['id']}/versions/{prompt_version['id']}/freeze",
    )
    assert frozen_prompt["status"] == "frozen"
    assert frozen_prompt["content_hash"]
    prompt_version_id = prompt_version["id"]

    # ------------------------------------------------------------- 3. policy + invariant
    policy = _post(app_client, f"{API}/project-policies", {
        "project_id": project_id,
        "name": f"single-billing-pipeline-{suffix}",
        "domain": "ARCHITECTURE",
        "content": _ARCH_POLICY_CONTENT,
    })
    invariant = _post(app_client, f"{API}/project-invariants", {
        "project_id": project_id,
        "code": _INVARIANT_CODE,
        "domain": "ARCHITECTURE",
        "title": _INVARIANT_TITLE,
        "description": "Reuse the canonical billing service.",
        "severity": "critical",
        "enforcement_mode": "blocking",
        "status": "active",
    })
    assert invariant["status"] == "active"
    assert invariant["enforcement_mode"] == "blocking"

    # ------------------------------------------------------------- 4. context pack
    pack = _post(app_client, f"{API}/context-lab/packs", {
        "project_id": project_id,
        "name": f"e2e-pack-{suffix}",
    })
    pack_version = _post(app_client, f"{API}/context-lab/packs/{pack['id']}/versions", {
        "description": "e2e context pack",
        "items": [
            {"key": "billing_rule", "title": "Billing rule", "source_type": "POLICY",
             "order": 1,
             "content": "Billing changes must reuse the canonical billing service."},
        ],
    })
    frozen_pack = _post(
        app_client,
        f"{API}/context-lab/packs/{pack['id']}/versions/{pack_version['id']}/freeze",
    )
    assert frozen_pack["status"] == "frozen"
    pack_version_id = pack_version["id"]

    # ------------------------------------------------------------- 5. budget policy
    budget = _post(app_client, f"{API}/context-lab/budget-policies", {
        "project_id": project_id,
        "name": f"e2e-budget-{suffix}",
        "max_context_tokens": 4000,
        "reserve_output_tokens": 256,
    })
    assert budget["id"]

    # ------------------------------------------------------------- tool + knowledge
    tool_id = _seed_tool(app_client, owner, project_id)
    collection = _post(app_client, f"{API}/knowledge", {
        "project_id": project_id,
        "name": f"e2e-kb-{suffix}",
    })
    collection_id = collection["id"]

    # ------------------------------------------------------------- 6. agent + frozen version
    agent = _post(app_client, f"{API}/agents", {
        "project_id": project_id,
        "name": f"Billing Agent {suffix}",
        "description": "Implements billing changes safely.",
        "role": "engineer",
        "purpose": "Implement billing changes without parallel subsystems.",
        "responsibilities": ["Reuse the canonical billing service"],
        "non_responsibilities": ["Introduce a parallel billing subsystem"],
    })
    agent_version = _post(app_client, f"{API}/agents/{agent['id']}/versions", {
        "model_id": model_id,
        "prompt_version_id": prompt_version_id,
        "context_policy": {
            "context_pack_version_ids": [pack_version_id],
            "budget_policy_id": budget["id"],
            "strategy": "FULL",
        },
        "tool_policy": {"allowed_tool_ids": [tool_id], "denied_tool_ids": []},
        "knowledge_policy": {
            "knowledge_enabled": True,
            "allowed_collection_ids": [collection_id],
        },
        "runtime_policy": {"temperature": 0.2, "max_output_tokens": 128},
        "output_contract": {"format": "text"},
        "evaluation_profile": {"evaluator_keys": ["agent_policy_compliance"]},
        "routing": {"mode": "fixed"},
    })
    assert agent_version["routing"] == {"mode": "fixed"}
    frozen_agent_version = _post(
        app_client,
        f"{API}/agents/{agent['id']}/versions/{agent_version['id']}/freeze",
    )
    assert frozen_agent_version["status"] == "frozen"
    agent_version_id = agent_version["id"]

    # ------------------------------------------------------------- 7. prompt compiler
    compile_body = {
        "project_id": project_id,
        "prompt_version_id": prompt_version_id,
        "agent_version_id": agent_version_id,
    }
    preview = _post(app_client, f"{API}/prompt-compiler/preview", compile_body)
    assert preview["persisted"] is False
    assert preview["id"] is None

    compiled = _post(app_client, f"{API}/prompt-compiler/compile", compile_body)
    compiled_again = _post(app_client, f"{API}/prompt-compiler/compile", compile_body)
    assert compiled["persisted"] is True
    assert compiled["id"]
    assert compiled["checksum"] == compiled_again["checksum"] == preview["checksum"]

    section_kinds = {s["kind"] for s in compiled["sections"]}
    section_trusts = {s["trust"] for s in compiled["sections"]}
    assert {"TRUSTED_POLICY", "TRUSTED_INSTRUCTION"} <= section_trusts
    assert {"policy", "invariant", "instruction", "block"} <= section_kinds
    assert invariant["id"] in {entry["id"] for entry in compiled["invariants_used"]}
    assert block["id"] in {entry["id"] for entry in compiled["blocks_used"]}
    assert policy["id"] in {entry["id"] for entry in compiled["policies_used"]}
    assert _INVARIANT_CODE in compiled["rendered_text"]
    assert _BLOCK_CONTENT in compiled["rendered_text"]

    stored_compiled = app_client.portal.call(repo.compiled_prompts.get, compiled["id"], owner)
    assert stored_compiled["checksum"] == compiled["checksum"]

    # ------------------------------------------------------------- 8. execute
    _install(
        monkeypatch,
        lambda model, req: result_success("Implemented via the canonical billing service."),
        tools=True,
    )
    run_body = _post(app_client, f"{API}/agent-runtime/run", {
        "project_id": project_id,
        "agent_id": agent["id"],
        "agent_version_id": agent_version_id,
        "task": "Add a billing adjustment.",
    })
    run = run_body["run"]
    run_id = run["id"]
    assert run["status"] == "success"
    assert run["execution_kind"] == "agent"
    assert run["agent_id"] == agent["id"]
    assert run["agent_version_id"] == agent_version_id
    assert run["prompt_version_id"] == prompt_version_id
    assert run["compiled_prompt_id"]
    assert run["context_snapshot_id"]
    assert run["runtime_policy_applied"]["applied"]["temperature"] == 0.2
    assert run["runtime_policy_applied"]["applied"]["max_output_tokens"] == 128

    assert run["tool_policy_applied"]["enabled_tool_ids"] == [tool_id]
    assert run["permission_denials"] == []
    assert run["knowledge_policy_applied"]["knowledge_enabled"] is True
    assert collection_id in run["knowledge_policy_applied"]["allowed_collection_ids"]
    assert pack_version_id in run["context_policy_applied"]["context_pack_version_ids"]

    trace_id = run_body["trace_id"]
    assert trace_id
    trace = app_client.portal.call(repo.traces.get, trace_id, owner)
    assert trace is not None and trace["trace_type"] == "agent"

    runtime_compiled = app_client.portal.call(
        repo.compiled_prompts.get, run["compiled_prompt_id"], owner
    )
    assert runtime_compiled is not None
    assert runtime_compiled["agent_version_id"] == agent_version_id
    assert runtime_compiled["prompt_version_id"] == prompt_version_id
    assert invariant["id"] in {entry["id"] for entry in runtime_compiled["invariants_used"]}
    assert pack_version_id in {ref["ref_id"] for ref in runtime_compiled["context_refs"]}

    # ------------------------------------------------------------- 9. evaluate
    evaluation = _post(app_client, f"{API}/agent-evaluation/runs/{run_id}", {
        "evaluator_keys": ["agent_policy_compliance"],
    })
    rule = evaluation["rule_evaluation"]
    assert rule is not None
    assert rule["agent"]["agent_version_id"] == agent_version_id
    assert rule["agent"]["compiled_prompt_id"] == run["compiled_prompt_id"]
    assert rule["provenance"]["agent_version_id"] == agent_version_id
    assert rule["provenance"]["compiled_prompt_id"] == run["compiled_prompt_id"]
    assert rule["provenance"]["context_snapshot_id"] == run["context_snapshot_id"]
    assert rule["result"]["outcome"] in ("PASS", "FAIL")
    assert rule["result"]["outcome"] == "PASS"
    stored_evaluation = app_client.portal.call(repo.evaluations.get, rule["id"], owner)
    assert stored_evaluation["provenance"]["agent_version_id"] == agent_version_id
    assert stored_evaluation["project_id"] == project_id

    # ------------------------------------------------------------- 10. drift
    drift = _post(app_client, f"{API}/drift/analyze", {
        "project_id": project_id,
        "scope": "architecture",
        "changed_files": [{"path": "app/services/legacy/billing.py"}],
        "contracts": {
            "canonical_services": [
                {"name": "billing", "paths": ["app/services/billing.py"]},
            ]
        },
    })
    assert drift["summary"]["total"] >= 1
    assert any(f["drift_type"] == "ARCHITECTURE_DRIFT" for f in drift["findings"])

    materialized = _post(
        app_client, f"{API}/drift/analyses/{drift['id']}/findings"
    )
    created_findings = materialized["created"]
    assert len(created_findings) == 1
    drift_finding_id = created_findings[0]["id"]
    stored_finding = app_client.portal.call(repo.findings.get, drift_finding_id, owner)
    assert stored_finding is not None
    assert stored_finding["project_id"] == project_id
    assert stored_finding["evidence"][0]["type"] == "drift_analysis"

    # ------------------------------------------------------------- 11. finding -> regression
    promotion = _post(
        app_client, f"{API}/findings/{drift_finding_id}/promote/regression", {}
    )
    assert promotion["promotion_type"] == "regression"
    assert promotion["version"]["status"] == "frozen"
    assert promotion["version"]["content_hash"]
    assert promotion["case"]["metadata"]["source_finding_id"] == drift_finding_id
    assert promotion["case"]["metadata"]["generated_from"] == "finding"
    dataset_version_id = promotion["version"]["id"]
    stored_dataset_version = app_client.portal.call(
        repo.dataset_versions.get, dataset_version_id, owner
    )
    assert stored_dataset_version["status"] == "frozen"
    assert stored_dataset_version["project_id"] == project_id

    # ------------------------------------------------------------- 12. scorecard
    scorecard = _post(app_client, f"{API}/scorecards", {
        "project_id": project_id,
        "scope": "run",
        "run_id": run_id,
    })
    dimensions = {d["key"]: d for d in scorecard["dimensions"]}
    assert {"policy_compliance", "invariant_compliance", "context_usage", "drift"} <= set(
        dimensions
    )
    assert scorecard["composite"]["available"] is False
    assert scorecard["composite"]["reason"] == "no_scoring_policy"
    for dimension in dimensions.values():
        if dimension["state"] == "not_available":
            assert dimension["value"] is None
    scorecard_id = scorecard["id"]
    assert app_client.portal.call(repo.scorecards.get, scorecard_id, owner)["id"] == scorecard_id

    # ------------------------------------------------------------- 13. certification handoff
    candidate = _post(app_client, f"{API}/certification/candidates", {
        "project_id": project_id,
        "label": f"pytest-v6-e2e-candidate-{suffix}",
        "subject": {
            "type": "agent",
            "agent_version_id": agent_version_id,
            "label": "E2E Billing Agent",
        },
        "evidence": {
            "agent_version_ids": [agent_version_id],
            "prompt_version_ids": [prompt_version_id],
            "context_pack_version_ids": [pack_version_id],
            "scorecard_ids": [scorecard_id],
            "dataset_version_ids": [dataset_version_id],
            "run_ids": [run_id],
            "evaluation_ids": [rule["id"]],
        },
        "environment": {"runner_version": "1", "os": "linux"},
        "notes": "V6 end-to-end handoff",
    })
    assert candidate["state"] == "DRAFT"
    assert candidate["automatic_certification"] is False
    assert "CERTIFIED" not in json.dumps(candidate)

    readiness = _post(
        app_client,
        f"{API}/certification/candidates/{candidate['id']}/readiness",
    )
    assert readiness["state"] in {"READY_FOR_CERTIFICATION", "NOT_READY", "BLOCKED"}
    assert readiness["state"] != "CERTIFIED"
    assert readiness["automatic_certification"] is False
    assert "CERTIFIED" not in json.dumps(readiness)

    stored_candidate = app_client.portal.call(
        repo.certification_candidates.get, candidate["id"], owner
    )
    assert stored_candidate is not None
    assert stored_candidate["state"] in {
        "DRAFT", "READY_FOR_CERTIFICATION", "NOT_READY", "BLOCKED",
    }
    assert stored_candidate["automatic_certification"] is False

    # knowledge_collections is not covered by the shared owner purge helper.
    app_client.portal.call(
        repo.knowledge_collections.delete_many, {"owner_id": owner}
    )
