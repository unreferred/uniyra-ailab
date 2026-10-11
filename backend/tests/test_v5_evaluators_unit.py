"""Pure-function unit tests for the V5 evidence + evaluator engine.

No database, no app fixtures: every function under test is deterministic and
side-effect free.
"""
from app.services import evaluators
from app.services.evidence import (
    extract_marker_labels,
    marker_validity,
    parse_knowledge_markers,
)


def _fake_result(status, passed=None, error=None):
    return {
        "evaluator_key": "x",
        "evaluator_type": "rule",
        "status": status,
        "passed": passed,
        "score": None,
        "threshold": None,
        "confidence": None,
        "metrics": {},
        "findings": [],
        "scores": {},
        "details": {},
        "error": error,
        "reason": None,
    }


# 1 -------------------------------------------------------------------------
def test_deterministic_exact_match_pass_and_fail():
    case = {"expected_output": "Hello World"}

    passed = evaluators.run_deterministic(case, {"candidate": "hello   world"}, "exact_match")
    assert passed["status"] == "completed"
    assert passed["passed"] is True
    assert passed["score"] == 1.0
    assert passed["evaluator_key"] == "exact_match"
    assert passed["evaluator_type"] == "deterministic"

    failed = evaluators.run_deterministic(case, {"candidate": "goodbye"}, "exact_match")
    assert failed["status"] == "completed"
    assert failed["passed"] is False
    assert failed["score"] == 0.0


# 2 -------------------------------------------------------------------------
def test_missing_expected_output_is_not_evaluated_not_failed():
    result = evaluators.run_deterministic(
        {"expected_output": ""}, {"candidate": "anything"}, "exact_match"
    )
    assert result["status"] == "not_evaluated"
    assert result["passed"] is None
    assert result["score"] is None
    assert result["reason"] == "missing_expected_output"


# 3 -------------------------------------------------------------------------
def test_unknown_required_assertion_type_errors():
    case = {"assertions": [{"type": "not_a_real_assertion", "value": "x"}]}
    result = evaluators.run_assertions(case, {"candidate": "x"})
    assert result["status"] == "error"
    assert result["passed"] is None


# 4 -------------------------------------------------------------------------
def test_malformed_assertion_missing_type_errors():
    case = {"assertions": [{"required": True, "value": "x"}]}
    result = evaluators.run_assertions(case, {"candidate": "x"})
    assert result["status"] == "error"
    assert result["passed"] is None


# 5 -------------------------------------------------------------------------
def test_valid_knowledge_marker_passes():
    case = {"assertions": [{"type": "knowledge_marker_valid"}]}
    evidence = {
        "candidate": "uses [K1]",
        "knowledge_markers": [1],
        "knowledge_provenance": {"chunk_ids": ["c1"], "chunk_count": 1},
    }
    result = evaluators.run_assertions(case, evidence)
    assert result["status"] == "completed"
    assert result["passed"] is True


# 6 -------------------------------------------------------------------------
def test_fabricated_marker_fails():
    case = {"assertions": [{"type": "knowledge_marker_valid"}]}
    evidence = {
        "candidate": "uses [K99]",
        "knowledge_markers": [99],
        "knowledge_provenance": {"chunk_ids": ["c1"], "chunk_count": 1},
    }
    result = evaluators.run_assertions(case, evidence)
    assert result["status"] == "completed"
    assert result["passed"] is False
    assert result["findings"]
    assert {f["category"] for f in result["findings"]} & {"citation", "grounding"}


# 7 -------------------------------------------------------------------------
def test_cited_marker_without_provenance_fails():
    case = {"assertions": [{"type": "grounded_citation", "params": {"required": True}}]}
    evidence = {
        "candidate": "uses [K1]",
        "knowledge_markers": [1],
        "knowledge_provenance": {},
    }
    result = evaluators.run_assertions(case, evidence)
    assert result["status"] == "completed"
    assert result["passed"] is False

    grounding = evaluators.run_knowledge_grounding(
        {"knowledge_policy": {"require_valid_markers": True}}, evidence
    )
    assert grounding["passed"] is False
    assert any(f["message"] == "cited_without_provenance" for f in grounding["findings"])


# 8 -------------------------------------------------------------------------
def test_retrieved_but_unused_without_require_citation_is_informational():
    case = {"knowledge_policy": {"expect_retrieval": True}}
    evidence = {
        "candidate": "no markers here",
        "knowledge_markers": [],
        "knowledge_retrieval_count": 2,
        "knowledge_injection": True,
        "knowledge_provenance": {"chunk_ids": ["c1", "c2"], "chunk_count": 2},
    }
    result = evaluators.run_knowledge_grounding(case, evidence)
    assert result["status"] == "completed"
    assert result["passed"] is True
    assert result["findings"]
    assert all(f["severity"] == "info" for f in result["findings"])


# 9 -------------------------------------------------------------------------
def test_knowledge_off_policy_pass_and_fail():
    case = {"knowledge_policy": {"knowledge_expected": False}}

    off = evaluators.run_knowledge_grounding(case, {"knowledge_retrieval_count": 0})
    assert off["status"] == "completed"
    assert off["passed"] is True

    on = evaluators.run_knowledge_grounding(case, {"knowledge_retrieval_count": 2})
    assert on["status"] == "completed"
    assert on["passed"] is False
    assert any(
        f["message"] == "unexpected_knowledge_retrieval" for f in on["findings"]
    )


# 10 ------------------------------------------------------------------------
def test_tool_policy_allow_tools_false():
    case = {"tool_policy": {"allow_tools": False}}

    violation = evaluators.run_tool_policy(
        case,
        {
            "tool_call_count": 3,
            "tool_failure_count": 0,
            "tool_success_count": 3,
            "tool_names": ["search"],
            "tool_count_source": "run",
        },
    )
    assert violation["status"] == "completed"
    assert violation["passed"] is False
    assert any(
        f["category"] == "instruction_following" for f in violation["findings"]
    )

    clean = evaluators.run_tool_policy(
        case,
        {
            "tool_call_count": 0,
            "tool_failure_count": 0,
            "tool_success_count": 0,
            "tool_names": [],
            "tool_count_source": "run",
        },
    )
    assert clean["status"] == "completed"
    assert clean["passed"] is True


# 11 ------------------------------------------------------------------------
def test_expected_tool_call_count_zero_with_none_count_not_evaluated():
    case = {"tool_policy": {"expected_tool_call_count": 0}}
    result = evaluators.run_tool_policy(case, {"tool_call_count": None})
    assert result["status"] == "not_evaluated"
    assert result["passed"] is None


# 12 ------------------------------------------------------------------------
def test_zero_max_pass_and_unknown_cost_not_evaluated():
    result = evaluators.run_tool_policy(
        {"tool_policy": {"max_tool_calls": 0}},
        {"tool_call_count": 0, "tool_count_source": "run"},
    )
    assert result["status"] == "completed"
    assert result["passed"] is True

    cost = evaluators.run_assertions(
        {"assertions": [{"type": "max_cost", "params": {"max": 1.0}}]},
        {"candidate": "x", "cost_available": False, "estimated_cost": None},
    )
    assert cost["status"] == "not_evaluated"
    assert cost["passed"] is None


# 13 ------------------------------------------------------------------------
def test_combine_results_error_and_all_not_evaluated():
    errored = evaluators.combine_results([_fake_result("error", error="bad")])
    assert errored["status"] == "error"
    assert errored["passed"] is None

    empty = evaluators.combine_results(
        [_fake_result("not_evaluated"), _fake_result("not_evaluated")]
    )
    assert empty["status"] == "not_evaluated"
    assert empty["passed"] is None


# 14 ------------------------------------------------------------------------
def test_marker_duplicates_reported_but_valid():
    markers = parse_knowledge_markers("[K1] then [K1] and [K2]")
    assert markers == [1, 1, 2]

    labels = extract_marker_labels("[K1] [K1] [K2]")
    assert labels == ["[K1]", "[K2]"]

    validity = marker_validity(markers, {"chunk_ids": ["c1", "c2"]})
    assert validity["total"] == 3
    assert validity["unique"] == 2
    assert validity["valid_count"] == 3
    assert validity["valid"] == [1, 2]
    assert validity["invalid"] == []
    assert validity["duplicates"] == [1]
    assert validity["has_valid"] is True
    assert validity["has_invalid"] is False
    assert validity["provenance_available"] is True
