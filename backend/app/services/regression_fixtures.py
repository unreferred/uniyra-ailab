"""V5 regression fixtures: a small, deterministic dataset that pins three
acceptance-critical scenarios -- knowledge ON grounding, knowledge OFF
isolation, and tool-instruction compliance -- as an immutable frozen dataset
version.

The materializer is idempotent: repeated calls reuse an existing fixture
dataset (and its draft version when one exists) instead of duplicating cases.
No run ids or environment specifics are baked in; the operator wires the
Knowledge collection using the returned ``knowledge_setup`` guidance.
"""
from typing import Any, Dict, List, Optional

from .. import repository as repo
from ..models import new_id, now_iso
from . import datasets as datasets_service

FIXTURE_NONCE = "NONCE-BLUE-7F3A9C"
FIXTURE_VERSION_NAME = "OmniLab V5 Regression Fixtures"

_KNOWLEDGE_ON_INPUT = (
    "Using ONLY the provided Knowledge, answer with the OmniLab V5 acceptance "
    "nonce and cite the marker exactly."
)

_NO_TOOLS_INPUT = (
    "Without using web search or external tools, answer in one short sentence: "
    "what is 2 + 2?"
)


def fixture_cases() -> List[Dict[str, Any]]:
    """The three canonical regression cases in stable ordering 1..3."""
    return [
        {
            "ordering": 1,
            "input_text": _KNOWLEDGE_ON_INPUT,
            "expected_output": FIXTURE_NONCE,
            "reference_answer": FIXTURE_NONCE,
            "metadata": {"fixture": "knowledge_on", "nonce": FIXTURE_NONCE},
            "assertions": [
                {"type": "output_contains", "value": FIXTURE_NONCE},
                {"type": "knowledge_marker_valid"},
                {"type": "grounded_citation"},
            ],
            "tool_policy": {},
            "knowledge_policy": {
                "knowledge_expected": True,
                "expect_retrieval": True,
                "expect_injection": True,
                "require_valid_markers": True,
            },
            "grounding_expectations": {
                "require_citation": True,
                "forbid_fabricated_markers": True,
            },
        },
        {
            "ordering": 2,
            "input_text": _KNOWLEDGE_ON_INPUT,
            "expected_output": "",
            "reference_answer": None,
            "metadata": {"fixture": "knowledge_off", "nonce": FIXTURE_NONCE},
            "assertions": [
                {"type": "output_not_contains", "value": FIXTURE_NONCE},
                {"type": "knowledge_absent"},
            ],
            "tool_policy": {},
            "knowledge_policy": {
                "knowledge_expected": False,
                "expect_retrieval": False,
                "expect_injection": False,
                "require_no_knowledge": True,
            },
            "grounding_expectations": {},
        },
        {
            "ordering": 3,
            "input_text": _NO_TOOLS_INPUT,
            "expected_output": "4",
            "reference_answer": "4",
            "metadata": {
                "fixture": "no_tools",
                "note": (
                    "Historical behavior observed a model invoking "
                    "code_interpreter here; this is a tool-policy violation, "
                    "not a Knowledge failure."
                ),
            },
            "assertions": [
                {"type": "tool_call_count_equals", "value": 0},
                {"type": "no_tools"},
            ],
            "tool_policy": {"allow_tools": False, "expected_tool_call_count": 0},
            "knowledge_policy": {},
            "grounding_expectations": {},
        },
    ]


def knowledge_setup() -> Dict[str, Any]:
    """Operator guidance for wiring the Knowledge side of case A (no side effects)."""
    return {
        "nonce": FIXTURE_NONCE,
        "document_text": f"The OmniLab V5 acceptance nonce is {FIXTURE_NONCE}.",
        "instructions": (
            "Create a Knowledge collection in the same project containing "
            "document_text, index it, then run case A with knowledge enabled "
            "referencing that collection."
        ),
    }


async def _insert_case(
    owner_id: str,
    dataset: Dict[str, Any],
    version: Dict[str, Any],
    source: Dict[str, Any],
    now: str,
) -> Dict[str, Any]:
    """Insert one fixture case using the same defaults as the datasets service."""
    case = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": dataset.get("project_id"),
        "dataset_id": dataset["id"],
        "dataset_version_id": version["id"],
        "input_text": source.get("input_text", ""),
        "expected_output": source.get("expected_output", ""),
        "metadata": source.get("metadata") or {},
        "tags": source.get("tags") or [],
        "ordering": source.get("ordering", 0),
        "evaluation_hints": source.get("evaluation_hints") or {},
        "asset_attachment_ids": list(source.get("asset_attachment_ids") or []),
        "reference_answer": source.get("reference_answer"),
        "assertions": source.get("assertions") or [],
        "tool_policy": source.get("tool_policy") or {},
        "knowledge_policy": source.get("knowledge_policy") or {},
        "grounding_expectations": source.get("grounding_expectations") or {},
        "created_at": now,
        "updated_at": now,
    }
    await repo.dataset_version_cases.insert(case)
    return case


async def _recount(owner_id: str, version: Dict[str, Any]) -> Dict[str, Any]:
    total = await repo.dataset_version_cases.count(
        {"owner_id": owner_id, "dataset_version_id": version["id"]}
    )
    return await repo.dataset_versions.update(
        version["id"], owner_id, {"case_count": total, "updated_at": now_iso()}
    )


async def _latest_or_new_draft(owner_id: str, dataset: Dict[str, Any]) -> Dict[str, Any]:
    versions = await repo.dataset_versions.list(
        {"owner_id": owner_id, "dataset_id": dataset["id"]},
        sort_field="version",
        descending=True,
        limit=500,
    )
    draft = next((v for v in versions if v.get("status") == "draft"), None)
    if draft is not None:
        return await _recount(owner_id, draft)
    latest_frozen = next((v for v in versions if v.get("status") == "frozen"), None)
    if latest_frozen is not None:
        return await datasets_service.create_version_from(
            owner_id, dataset, source_version_id=latest_frozen["id"]
        )
    return await datasets_service.create_version_from(owner_id, dataset)


async def materialize(
    owner_id: str, project_id: str, name: Optional[str] = None
) -> Dict[str, Any]:
    """Create or reuse the fixtures dataset and return its frozen/draft version."""
    dataset_name = name or FIXTURE_VERSION_NAME
    dataset = await repo.datasets.find_one(
        {"owner_id": owner_id, "project_id": project_id, "name": dataset_name}
    )
    if dataset is None:
        now = now_iso()
        dataset = {
            "id": new_id(),
            "owner_id": owner_id,
            "project_id": project_id,
            "name": dataset_name,
            "description": "Deterministic V5 regression fixtures (knowledge/tool policy).",
            "tags": ["v5", "regression-fixtures"],
            "created_at": now,
            "updated_at": now,
        }
        await repo.datasets.insert(dataset)
        version = await datasets_service.create_version_from(owner_id, dataset)
        for source in fixture_cases():
            await _insert_case(owner_id, dataset, version, source, now)
        version = await _recount(owner_id, version)
        version = await datasets_service.freeze_version(owner_id, version)
    else:
        version = await _latest_or_new_draft(owner_id, dataset)
    cases = await datasets_service.list_cases(owner_id, version["id"], limit=100)
    return {
        "dataset": dataset,
        "version": version,
        "cases": cases,
        "knowledge_setup": knowledge_setup(),
    }
