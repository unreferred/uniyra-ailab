"""V5 Marathon B debt #3: knowledge empty-retrieval metric source field.

``observability._knowledge_metrics`` used to read span metadata
``result_count`` for its empty-retrieval count, but KNOWLEDGE_RETRIEVAL spans
record ``returned_count`` (then ``chunk_count``; see
``services.evidence._span_retrieval_count``). These tests pin the preference
chain ``returned_count`` -> ``chunk_count`` -> legacy ``result_count`` and prove
the API-level metric reflects it via ``GET /api/observability``.
"""
from __future__ import annotations

from app import repository as repo
from app.database import db
from app.models import new_id, now_iso
from app.routers.observability import _retrieval_count_expr

API = "/api"


async def _seed_retrieval_spans(owner_id, metadatas):
    """Persist one trace plus a KNOWLEDGE_RETRIEVAL span per metadata dict."""
    trace_id = new_id()
    now = now_iso()
    await repo.traces.insert({
        "id": trace_id,
        "owner_id": owner_id,
        "project_id": None,
        "trace_type": "system",
        "root_run_id": None,
        "experiment_id": None,
        "conversation_id": None,
        "council_id": None,
        "dataset_case_id": None,
        "status": "completed",
        "started_at": now,
        "completed_at": now,
        "total_duration_ms": 5,
        "span_count": len(metadatas),
        "metadata": {},
        "schema_version": 1,
    })
    for index, metadata in enumerate(metadatas):
        await repo.trace_spans.insert({
            "id": new_id(),
            "owner_id": owner_id,
            "trace_id": trace_id,
            "parent_span_id": None,
            "span_type": "KNOWLEDGE_RETRIEVAL",
            "name": "Knowledge retrieval",
            "status": "completed",
            "started_at": now,
            "completed_at": now,
            "duration_ms": 5,
            "run_id": None,
            "metadata": metadata,
            "structured_error": None,
            "ordering": index,
        })


async def _resolved_counts(owner_id):
    pipeline = [
        {"$match": {"owner_id": owner_id, "span_type": "KNOWLEDGE_RETRIEVAL"}},
        {"$project": {"_id": 0, "metadata": 1, "count": _retrieval_count_expr()}},
    ]
    return await db.trace_spans.aggregate(pipeline).to_list(length=10)


def _knowledge_metrics(client):
    r = client.get(f"{API}/observability")
    assert r.status_code == 200, r.text
    return r.json()["knowledge_metrics"]


def test_retrieval_count_expr_prefers_returned_count():
    assert _retrieval_count_expr() == {
        "$ifNull": [
            "$metadata.returned_count",
            {"$ifNull": ["$metadata.chunk_count", "$metadata.result_count"]},
        ]
    }


def test_retrieval_count_expr_resolves_the_reported_value(app_client, as_user):
    owner = as_user["id"]
    app_client.portal.call(_seed_retrieval_spans, owner, [
        {"returned_count": 3, "result_count": 0},
        {"chunk_count": 2},
        {"result_count": 4},
        {},
    ])

    counts = app_client.portal.call(_resolved_counts, owner)
    resolved = {
        frozenset((row.get("metadata") or {}).items()): row.get("count")
        for row in counts
    }
    # returned_count=3 wins over the legacy result_count=0 in the same span.
    assert resolved[frozenset({("returned_count", 3), ("result_count", 0)})] == 3
    assert resolved[frozenset({("chunk_count", 2)})] == 2
    assert resolved[frozenset({("result_count", 4)})] == 4
    assert resolved[frozenset()] is None


def test_knowledge_metrics_honors_returned_count(app_client, as_user):
    owner = as_user["id"]
    app_client.portal.call(_seed_retrieval_spans, owner, [
        {"returned_count": 3, "result_count": 0},
        {"returned_count": 0},
    ])

    metrics = _knowledge_metrics(app_client)
    assert metrics["retrievals"] == 2
    # returned_count=3 wins over the legacy result_count=0, so only the
    # returned_count=0 span is empty. A pre-fix read of result_count would
    # have reported both as empty.
    assert metrics["empty_retrievals"] == 1


def test_knowledge_metrics_falls_back_to_chunk_count(app_client, as_user):
    owner = as_user["id"]
    app_client.portal.call(_seed_retrieval_spans, owner, [
        {"chunk_count": 0},
        {"chunk_count": 5},
    ])

    metrics = _knowledge_metrics(app_client)
    assert metrics["retrievals"] == 2
    assert metrics["empty_retrievals"] == 1


def test_knowledge_metrics_backward_compatible_with_result_count(app_client, as_user):
    owner = as_user["id"]
    app_client.portal.call(_seed_retrieval_spans, owner, [
        {"result_count": 0},
        {"result_count": 4},
    ])

    metrics = _knowledge_metrics(app_client)
    assert metrics["retrievals"] == 2
    assert metrics["empty_retrievals"] == 1


def test_knowledge_metrics_missing_count_is_not_empty(app_client, as_user):
    owner = as_user["id"]
    app_client.portal.call(_seed_retrieval_spans, owner, [{}])

    metrics = _knowledge_metrics(app_client)
    assert metrics["retrievals"] == 1
    assert metrics["empty_retrievals"] == 0
