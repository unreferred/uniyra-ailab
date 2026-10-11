"""Unit tests for the canonical failure taxonomy (V5 Marathon B).

Pure unit tests: no database, no network, no application state. They pin the
runtime-vs-quality separation that the whole taxonomy exists to guarantee.
"""
from __future__ import annotations

from app.services import failure_taxonomy as ft

_ENTRY_KEYS = (
    "code",
    "domain",
    "category",
    "label",
    "description",
    "severity_scale",
    "default_severity",
)


def test_domains_and_severity_scales():
    assert ft.DOMAINS == ("RUNTIME", "QUALITY", "RESEARCH", "SECURITY")
    assert ft.SEVERITY_SCALES == {
        "RUNTIME": "operational",
        "QUALITY": "quality",
        "RESEARCH": "research",
        "SECURITY": "security",
    }
    assert ft.severity_scale_for("RUNTIME") == "operational"
    assert ft.severity_scale_for("quality") == "quality"
    assert ft.severity_scale_for("Security") == "security"
    assert ft.severity_scale_for("bogus") is None
    assert ft.severity_scale_for(None) is None


def test_taxonomy_entries_have_stable_shape():
    assert len(ft.TAXONOMY_CODES) == len(set(ft.TAXONOMY_CODES))
    assert set(ft.TAXONOMY) == set(ft.TAXONOMY_CODES)
    for code in ft.TAXONOMY_CODES:
        entry = ft.TAXONOMY[code]
        assert tuple(entry.keys()) == _ENTRY_KEYS
        assert entry["code"] == code
        assert entry["domain"] in ft.DOMAINS
        assert entry["severity_scale"] == ft.SEVERITY_SCALES[entry["domain"]]
        assert entry["default_severity"] in ft.SEVERITIES_BY_SCALE[entry["severity_scale"]]
        assert entry["label"] and entry["description"]


def test_retained_category_names():
    assert ft.TAXONOMY["QUALITY.INSTRUCTION_FOLLOWING"]["category"] == "instruction_following"
    assert ft.TAXONOMY["QUALITY.CITATION"]["category"] == "citation"
    assert ft.TAXONOMY["QUALITY.TOOL_SELECTION"]["category"] == "tool_selection"
    assert ft.TAXONOMY["SECURITY.CREDENTIAL_EXPOSURE"]["category"] == "credential_exposure"
    assert ft.TAXONOMY["RESEARCH.INSUFFICIENT_SOURCES"]["category"] == "insufficient_sources"


# ------------------------------------------------------------------ 1. runtime
def test_runtime_error_categories_map_to_runtime_domain():
    cases = (
        (("PROVIDER", "", ""), "RUNTIME.PROVIDER_ERROR"),
        (("TIMEOUT", "", ""), "RUNTIME.TIMEOUT"),
        (("NETWORK", "", ""), "RUNTIME.TIMEOUT"),
        (("MALFORMED_RESPONSE", "", ""), "RUNTIME.MALFORMED_PROVIDER_RESPONSE"),
        (("INTERNAL", "", "tool execution crashed"), "RUNTIME.TOOL_RUNTIME_ERROR"),
        (("INTERNAL", "", "finalization failed"), "RUNTIME.FINALIZATION_FAILURE"),
        (("INTERNAL", "", "context window overflow"), "RUNTIME.RUNTIME_FAILURE"),
        (("INTERNAL", "", "unexpected internal state"), "RUNTIME.PROVIDER_ERROR"),
    )
    for (category, code, message), expected in cases:
        result = ft.classify_execution(category, code, message)
        assert result["code"] == expected, (category, code, message)
        assert result["domain"] == "RUNTIME"
        assert result["severity_scale"] == "operational"
        assert result["default_severity"] in ("low", "medium", "high", "critical")


def test_runtime_classification_text_priority_and_error_code_hint():
    # tool text outranks a timeout category, per the documented rule order.
    assert ft.classify_execution("TIMEOUT", "", "tool call timed out")["code"] == "RUNTIME.TOOL_RUNTIME_ERROR"
    assert ft.classify_execution("INTERNAL", "FINALIZATION_ERROR")["code"] == "RUNTIME.FINALIZATION_FAILURE"
    assert ft.classify_execution("INTERNAL", "CONTEXT_ASSEMBLY_FAILED")["code"] == "RUNTIME.RUNTIME_FAILURE"
    assert ft.classify_execution("INTERNAL", "", "request timed out")["code"] == "RUNTIME.TIMEOUT"


# ------------------------------------------------- 2. the documented real case
def test_instruction_following_with_tools_is_quality_not_runtime():
    result = ft.classify_finding("instruction_following", "model called code_interpreter 4 times")
    assert result["domain"] == "QUALITY"
    assert result["code"] == "QUALITY.INSTRUCTION_FOLLOWING"
    assert "QUALITY.TOOL_SELECTION" in result["related_codes"]
    assert result["code"] != "RUNTIME.RUNTIME_FAILURE"
    assert result["domain"] != "RUNTIME"
    assert not result["code"].startswith("RESEARCH.")
    assert result["code"] not in ("QUALITY.CORRECTNESS", "QUALITY.HALLUCINATION")

    forbidden = ft.classify_finding("instruction_following", "used a forbidden tool")
    assert forbidden["code"] == "QUALITY.INSTRUCTION_FOLLOWING"
    assert forbidden["domain"] == "QUALITY"
    assert "QUALITY.TOOL_SELECTION" in forbidden["related_codes"]


# --------------------------------------------------------- 3. tool selection
def test_tool_selection_maps_to_quality_tool_selection():
    plain = ft.classify_finding("tool_selection")
    assert plain["code"] == "QUALITY.TOOL_SELECTION"
    assert plain["domain"] == "QUALITY"

    with_instruction = ft.classify_finding("tool_selection", "selected tools that ignored the instruction")
    assert with_instruction["code"] == "QUALITY.TOOL_SELECTION"
    assert "QUALITY.INSTRUCTION_FOLLOWING" in with_instruction["related_codes"]


# -------------------------------------------------------------- 4. security
def test_security_category_stays_distinct():
    result = ft.classify_finding("security", "credential value exposed")
    assert result["domain"] == "SECURITY"
    assert result["code"] == "SECURITY.CREDENTIAL_EXPOSURE"
    assert result["severity_scale"] == "security"
    assert result["default_severity"] not in ("low", "medium", "high", "critical", "info")
    assert result["default_severity"].startswith("security_")

    assert ft.classify_security("cross-project data access")["code"] == "SECURITY.CROSS_PROJECT_ACCESS"
    assert ft.classify_finding("security", "unsafe tool invocation")["code"] == "SECURITY.UNSAFE_TOOL_ACCESS"
    assert ft.classify_finding("security", "generic policy issue")["code"] == "SECURITY.POLICY_BOUNDARY_VIOLATION"


# -------------------------------------------------------------- 5. research
def test_research_categories():
    assert ft.classify_finding("grounding", "insufficient sources for claim")["code"] == "RESEARCH.INSUFFICIENT_SOURCES"
    assert ft.classify_finding("citation", "sources disagree on the number")["code"] == "RESEARCH.SOURCE_DISAGREEMENT"
    assert ft.classify_finding("correctness", "evidence is stale")["code"] == "RESEARCH.STALE_EVIDENCE"
    assert ft.classify_finding("grounding", "unreliable source used")["code"] == "RESEARCH.POOR_SOURCE_QUALITY"
    assert ft.classify_finding("grounding", "unsupported conclusion reached")["code"] == "RESEARCH.UNSUPPORTED_SYNTHESIS"
    assert ft.classify_finding("source_quality")["code"] == "RESEARCH.POOR_SOURCE_QUALITY"
    for code in (
        "RESEARCH.INSUFFICIENT_SOURCES",
        "RESEARCH.SOURCE_DISAGREEMENT",
        "RESEARCH.STALE_EVIDENCE",
    ):
        assert ft.TAXONOMY[code]["severity_scale"] == "research"


# ------------------------------------------------------------ 6. no conflation
def test_no_conflation_across_domains():
    runtime_codes = {c for c in ft.TAXONOMY_CODES if ft.TAXONOMY[c]["domain"] == "RUNTIME"}
    quality_codes = {c for c in ft.TAXONOMY_CODES if ft.TAXONOMY[c]["domain"] == "QUALITY"}
    assert runtime_codes and quality_codes
    assert all(ft.TAXONOMY[c]["severity_scale"] == "operational" for c in runtime_codes)
    assert all(ft.TAXONOMY[c]["severity_scale"] == "quality" for c in quality_codes)

    domain_sets = {
        domain: {c for c in ft.TAXONOMY_CODES if ft.TAXONOMY[c]["domain"] == domain}
        for domain in ft.DOMAINS
    }
    for first in ft.DOMAINS:
        for second in ft.DOMAINS:
            if first != second:
                assert domain_sets[first].isdisjoint(domain_sets[second])

    union = set().union(*domain_sets.values())
    assert union == set(ft.TAXONOMY_CODES)
    assert domain_sets["SECURITY"]
    assert all(
        ft.TAXONOMY[c]["default_severity"].startswith("security_")
        for c in domain_sets["SECURITY"]
    )


# ------------------------------------------- 7. normalize / describe / domain
def test_normalize_describe_domain_of_round_trips():
    assert ft.normalize_code("QUALITY.CITATION") == "QUALITY.CITATION"
    assert ft.normalize_code("citation") == "QUALITY.CITATION"
    assert ft.normalize_code("  InStRuCtIoN_FoLlOwInG  ") == "QUALITY.INSTRUCTION_FOLLOWING"
    assert ft.normalize_code("nope") is None
    assert ft.normalize_code("") is None
    assert ft.normalize_code(None) is None

    assert ft.describe("citation")["code"] == "QUALITY.CITATION"
    copied = ft.describe("citation")
    copied["label"] = "mutated"
    assert ft.describe("citation")["label"] != "mutated"
    assert ft.describe("nope") is None

    assert ft.domain_of("QUALITY.CITATION") == "QUALITY"
    assert ft.domain_of("citation") == "QUALITY"
    assert ft.domain_of("SECURITY") == "SECURITY"
    assert ft.domain_of("nope") is None

    for code in ft.TAXONOMY_CODES:
        assert ft.normalize_code(code) == code
        assert ft.describe(ft.normalize_code(ft.TAXONOMY[code]["category"]))["code"] == code


def test_signals_override_and_domain_hint():
    assert ft.classify_finding("correctness", signals={"taxonomy_code": "RESEARCH.STALE_EVIDENCE"})["code"] == "RESEARCH.STALE_EVIDENCE"
    assert ft.classify_finding("correctness", signals={"taxonomy_code": "not-a-code"})["code"] == "QUALITY.CORRECTNESS"
    hinted = ft.classify_finding("correctness", "the evidence is stale", signals={"domain": "RESEARCH"})
    assert hinted["domain"] == "RESEARCH"
    assert hinted["code"] == "RESEARCH.STALE_EVIDENCE"
