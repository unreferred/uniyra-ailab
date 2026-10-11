"""V5.6 failure-taxonomy integration: canonical taxonomy codes, failure
domains, domain filtering/aggregation, credential exclusion and the
runtime/quality/security non-conflation guarantees.

All execution is offline (scripted adapters); records belong to throwaway
``pytest-`` owners purged by the ``as_user`` fixture.
"""
from __future__ import annotations

import json

import pytest

from app import providers as providers_module
from app import repository as repo
from app import services
from app.services import execution as execution_module

from _support import (
    CapableAdapter,
    create_model,
    create_provider,
    result_error,
)

API = "/api"

_SECRET_A = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX"
_SECRET_B = "sk-proj-ZZZZZZZZZZZZZZZZZZZZZZZZ"


def _install(monkeypatch, executor):
    factory = lambda provider: CapableAdapter(executor)  # noqa: E731
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


def _make_project(client, name="pytest-v5-failures"):
    r = client.post(f"{API}/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _post_failure(client, project_id, **body):
    r = client.post(f"{API}/failures", params={"project_id": project_id}, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _owner_model(client, owner):
    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(owner, provider_id=provider_id)

    return client.portal.call(_setup)


def _error_run(client, owner, project_id, model_id):
    async def _execute():
        return await execution_module.execute_model(
            owner,
            model_id,
            "",
            [{"role": "user", "content": "hi"}],
            {},
            project_id=project_id,
        )

    return client.portal.call(_execute)


def _failure_for_run(client, owner, run_id):
    async def _lookup():
        return await repo.failures.find_one({"owner_id": owner, "run_id": run_id})

    return client.portal.call(_lookup)


# ---------------------------------------------------- 1. runtime execution
def test_system_provider_error_is_runtime(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-runtime")
    model_id = _owner_model(app_client, owner)
    _install(
        monkeypatch,
        lambda model, req: result_error(category="PROVIDER", message="upstream down"),
    )

    run = _error_run(app_client, owner, project_id, model_id)
    assert run["status"] == "error"

    failure = _failure_for_run(app_client, owner, run["id"])
    assert failure is not None
    assert failure["failure_class"] == "execution"
    assert failure["failure_domain"] == "RUNTIME"
    assert failure["taxonomy_code"] == "RUNTIME.PROVIDER_ERROR"
    assert failure["severity_scale"] == "operational"
    assert failure["taxonomy"]["domain"] == "RUNTIME"


# ------------------------------------------------- 2. quality, tool linked
def test_instruction_violation_is_quality_with_tool_selection_link(app_client, as_user):
    project_id = _make_project(app_client, "pytest-v5-quality")
    failure = _post_failure(
        app_client,
        project_id,
        source="RULE",
        category="INSTRUCTION_VIOLATION",
        title="Instruction violation",
        description="the model called code_interpreter and tools despite instructions",
    )
    assert failure["failure_class"] == "quality"
    assert failure["failure_domain"] == "QUALITY"
    assert failure["taxonomy_code"] == "QUALITY.INSTRUCTION_FOLLOWING"
    assert failure["severity_scale"] == "quality"
    assert "QUALITY.TOOL_SELECTION" in failure["taxonomy"]["related_codes"]


# ------------------------------------------------------------ 3. security
def test_security_failure_is_security_domain(app_client, as_user):
    project_id = _make_project(app_client, "pytest-v5-security")
    failure = _post_failure(
        app_client,
        project_id,
        source="RULE",
        category="SECURITY_FAILURE",
        title="Credential leak",
        description="credential value exposed",
    )
    assert failure["failure_domain"] == "SECURITY"
    assert failure["taxonomy_code"] == "SECURITY.CREDENTIAL_EXPOSURE"
    assert failure["severity_scale"] == "security"


# ------------------------------------------------- 4. domain list filters
def test_failure_domain_filter_returns_only_matching_domain(app_client, as_user):
    project_id = _make_project(app_client, "pytest-v5-filter")
    security = _post_failure(
        app_client,
        project_id,
        source="RULE",
        category="SECURITY_FAILURE",
        title="security issue",
        description="credential value exposed",
    )
    runtime = _post_failure(
        app_client,
        project_id,
        source="SYSTEM",
        category="PROVIDER",
        title="provider error",
        description="upstream provider failed",
    )

    r = app_client.get(
        f"{API}/failures", params={"project_id": project_id, "failure_domain": "SECURITY"}
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ids = {item["id"] for item in items}
    assert security["id"] in ids
    assert runtime["id"] not in ids
    assert all(item["failure_domain"] == "SECURITY" for item in items)

    r = app_client.get(
        f"{API}/failures", params={"project_id": project_id, "failure_domain": "RUNTIME"}
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ids = {item["id"] for item in items}
    assert runtime["id"] in ids
    assert security["id"] not in ids
    assert all(item["failure_domain"] == "RUNTIME" for item in items)


# -------------------------------------------------------- 5. aggregation
def test_aggregate_includes_by_domain(app_client, as_user):
    project_id = _make_project(app_client, "pytest-v5-aggregate")
    _post_failure(
        app_client,
        project_id,
        source="RULE",
        category="SECURITY_FAILURE",
        title="security issue",
        description="credential value exposed",
    )
    _post_failure(
        app_client,
        project_id,
        source="RULE",
        category="INSTRUCTION_VIOLATION",
        title="instruction issue",
        description="ignored the instruction",
    )
    _post_failure(
        app_client,
        project_id,
        source="SYSTEM",
        category="PROVIDER",
        title="provider error",
        description="upstream provider failed",
    )

    r = app_client.get(f"{API}/failures/aggregate", params={"project_id": project_id})
    assert r.status_code == 200, r.text
    body = r.json()
    by_domain = {entry["domain"]: entry["count"] for entry in body["by_domain"]}
    assert by_domain.get("SECURITY") == 1
    assert by_domain.get("QUALITY") == 1
    assert by_domain.get("RUNTIME") == 1
    assert body["runtime_count"] == 1
    assert body["security_count"] == 1
    assert body["research_count"] == 0
    assert body["execution_count"] == 1
    assert body["quality_count"] == 2


# --------------------------------------------------- 6. credential exclusion
def test_secret_shaped_values_are_redacted(app_client, as_user):
    project_id = _make_project(app_client, "pytest-v5-redact")
    created = _post_failure(
        app_client,
        project_id,
        source="HUMAN",
        category="OTHER",
        title=f"leaked {_SECRET_A}",
        description=f"the credential {_SECRET_A} was exposed",
        evidence={"api_key": _SECRET_B},
    )
    serialized = json.dumps(created)
    assert _SECRET_A not in serialized
    assert _SECRET_B not in serialized
    assert "[REDACTED]" in serialized

    r = app_client.get(f"{API}/failures/{created['id']}")
    assert r.status_code == 200, r.text
    fetched = json.dumps(r.json())
    assert _SECRET_A not in fetched
    assert _SECRET_B not in fetched
    assert "[REDACTED]" in fetched


# -------------------------------------------------------- 7. link fields
def test_link_fields_are_preserved(app_client, as_user):
    project_id = _make_project(app_client, "pytest-v5-links")
    failure = _post_failure(
        app_client,
        project_id,
        source="HUMAN",
        category="OTHER",
        title="linked failure",
        dataset_case_id="case-123",
        span_id="span-456",
        finding_id="finding-789",
    )
    assert failure["dataset_case_id"] == "case-123"
    assert failure["span_id"] == "span-456"
    assert failure["finding_id"] == "finding-789"

    r = app_client.get(f"{API}/failures/{failure['id']}")
    assert r.status_code == 200, r.text
    stored = r.json()["failure"]
    assert stored["dataset_case_id"] == "case-123"
    assert stored["span_id"] == "span-456"
    assert stored["finding_id"] == "finding-789"


# ---------------------------------------------------- 8. no conflation
def test_runtime_and_quality_domains_never_conflate(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-conflation")
    model_id = _owner_model(app_client, owner)
    _install(
        monkeypatch,
        lambda model, req: result_error(category="PROVIDER", message="upstream down"),
    )

    run = _error_run(app_client, owner, project_id, model_id)
    runtime_failure = _failure_for_run(app_client, owner, run["id"])
    assert runtime_failure is not None
    assert runtime_failure["failure_domain"] not in ("QUALITY", "RESEARCH")

    quality_failure = _post_failure(
        app_client,
        project_id,
        source="RULE",
        category="INSTRUCTION_VIOLATION",
        title="instruction issue",
        description="the model ignored the instruction",
    )
    assert quality_failure["failure_domain"] != "RUNTIME"
    assert quality_failure["taxonomy_code"].startswith("QUALITY.")
