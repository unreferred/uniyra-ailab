"""Unit tests for Section F (Safe response assembly) in backend/app/services/continuation.py."""

import pytest
from app.services.continuation import (
    CONTINUATION_INSTRUCTION,
    ContinuationPolicy,
    ContinuationProvenance,
    assemble_continuation_segments,
    build_continuation_provenance,
    detect_suffix_prefix_overlap,
    merge_continuation_segment,
)


def test_continuation_instruction_content():
    """Verify CONTINUATION_INSTRUCTION contains the core directives."""
    assert "output limit" in CONTINUATION_INSTRUCTION
    assert "Continue the same response from exactly where it stopped" in CONTINUATION_INSTRUCTION
    assert "Do not restart or repeat completed sections" in CONTINUATION_INSTRUCTION
    assert "Preserve the existing structure and numbering" in CONTINUATION_INSTRUCTION
    assert "Complete the remaining requested work" in CONTINUATION_INSTRUCTION


class TestOverlapDetection:
    def test_example_from_spec(self):
        """Example from Section F:
        segment 1: '...architecture and security'
        segment 2: 'architecture and security are handled...'
        logical output: '...architecture and security are handled...'
        """
        seg1 = "System components include architecture and security"
        seg2 = "architecture and security are handled by dedicated modules."
        overlap = detect_suffix_prefix_overlap(seg1, seg2, min_overlap=10)
        assert overlap == len("architecture and security")
        merged = merge_continuation_segment(seg1, seg2)
        assert merged == "System components include architecture and security are handled by dedicated modules."

    def test_no_overlap_simple_concatenation(self):
        seg1 = "First part of text."
        seg2 = " Second part of text."
        assert detect_suffix_prefix_overlap(seg1, seg2, min_overlap=5) == 0
        merged = merge_continuation_segment(seg1, seg2)
        assert merged == "First part of text. Second part of text."

    def test_short_overlap_under_threshold_not_destructively_truncated(self):
        """If overlap is short/ambiguous (< minimum safe threshold, e.g. 3 chars or 1 common word like 'the'),
        do NOT destructively truncate!"""
        seg1 = "This is the end of the"
        seg2 = "the next section begins here"
        # Overlap of "the" is only 3 chars, default min_overlap is 10
        assert detect_suffix_prefix_overlap(seg1, seg2, min_overlap=10) == 0
        merged = merge_continuation_segment(seg1, seg2, min_overlap=10)
        # Should NOT truncate, simply concatenate
        assert merged == "This is the end of thethe next section begins here"

    def test_sentence_and_phrase_overlap_bounds(self):
        phrase = "This is a substantial overlap phrase that repeats at the seam."
        seg1 = "Prefix content. " + phrase
        seg2 = phrase + " Suffix continuation content."
        overlap = detect_suffix_prefix_overlap(seg1, seg2, min_overlap=10, max_overlap=300)
        assert overlap == len(phrase)
        merged = merge_continuation_segment(seg1, seg2)
        assert merged == "Prefix content. " + phrase + " Suffix continuation content."

    def test_max_overlap_bounding(self):
        """Overlap search must be bounded by max_overlap."""
        long_phrase = "A" * 100
        seg1 = "Start: " + long_phrase
        seg2 = long_phrase + " End."
        # If max_overlap is 50, it will only check up to 50 chars and find match of length 50 (since all 'A')
        overlap = detect_suffix_prefix_overlap(seg1, seg2, min_overlap=10, max_overlap=50)
        assert overlap == 50

    def test_identical_segment_not_duplicated_or_looped(self):
        seg1 = "Entire message repeated by model."
        seg2 = "Entire message repeated by model."
        merged = merge_continuation_segment(seg1, seg2)
        assert merged == seg1

    def test_whitespace_boundary_handling(self):
        seg1 = "Section 1\n### Overview\n"
        seg2 = "### Overview\nDetails follow."
        merged = merge_continuation_segment(seg1, seg2)
        assert "### Overview\nDetails follow." in merged
        # Must not duplicate ### Overview
        assert merged.count("### Overview") == 1

    def test_empty_or_none_handling(self):
        assert detect_suffix_prefix_overlap("", "foo") == 0
        assert detect_suffix_prefix_overlap("foo", "") == 0
        assert merge_continuation_segment("", "foo") == "foo"
        assert merge_continuation_segment("foo", "") == "foo"
        assert merge_continuation_segment("", "") == ""


class TestAssembleContinuationSegments:
    def test_multi_segment_assembly(self):
        segments = [
            "Part 1: The system begins with authentication and security",
            "authentication and security protocols. Part 2: Data ingestion pipeline",
            "Data ingestion pipeline processes messages in real time.",
        ]
        result = assemble_continuation_segments(segments)
        expected = (
            "Part 1: The system begins with authentication and security protocols. "
            "Part 2: Data ingestion pipeline processes messages in real time."
        )
        assert result == expected

    def test_assembly_preserves_segments_when_no_overlap(self):
        segments = ["Alpha", "Beta", "Gamma"]
        result = assemble_continuation_segments(segments)
        assert result == "AlphaBetaGamma"

    def test_assembly_empty_list(self):
        assert assemble_continuation_segments([]) == ""
        assert assemble_continuation_segments(["", ""]) == ""


class TestContinuationPolicy:
    def test_policy_defaults(self):
        policy = ContinuationPolicy()
        assert policy.auto_continue_on_output_limit is True
        assert policy.max_auto_continuations == 3
        assert policy.max_total_output_tokens is None
        assert policy.max_total_runtime_seconds is None

    def test_should_continue_finish_reason(self):
        policy = ContinuationPolicy(max_auto_continuations=2)
        # Valid finish reasons
        assert policy.should_continue(0, finish_reason="length") is True
        assert policy.should_continue(0, finish_reason="max_tokens") is True
        assert policy.should_continue(0, finish_reason="output_limit") is True

        # Invalid finish reasons
        assert policy.should_continue(0, finish_reason="stop") is False
        assert policy.should_continue(0, finish_reason="tool_calls") is False
        assert policy.should_continue(0, finish_reason=None) is False

    def test_should_continue_max_auto_continuations(self):
        policy = ContinuationPolicy(max_auto_continuations=2)
        assert policy.should_continue(0, finish_reason="length") is True
        assert policy.should_continue(1, finish_reason="length") is True
        assert policy.should_continue(2, finish_reason="length") is False

    def test_should_continue_token_and_time_limits(self):
        policy = ContinuationPolicy(
            max_auto_continuations=3,
            max_total_output_tokens=1000,
            max_total_runtime_seconds=30.0,
        )
        assert policy.should_continue(0, "length", total_output_tokens_used=500, elapsed_seconds=10.0) is True
        assert policy.should_continue(0, "length", total_output_tokens_used=1000, elapsed_seconds=10.0) is False
        assert policy.should_continue(0, "length", total_output_tokens_used=500, elapsed_seconds=30.0) is False

    def test_policy_disabled(self):
        policy = ContinuationPolicy(auto_continue_on_output_limit=False)
        assert policy.should_continue(0, finish_reason="length") is False


class TestContinuationProvenance:
    def test_provenance_preserves_raw_segments_and_assembles(self):
        prov = build_continuation_provenance(
            root_run_id="run-root-1",
            parent_run_id="run-parent-1",
            continuation_index=1,
            continuation_reason="output_limit",
            segments=["Chapter 1: The beginning...", "beginning... and Chapter 2: The middle."],
        )
        assert prov.root_run_id == "run-root-1"
        assert prov.parent_run_id == "run-parent-1"
        assert prov.continuation_index == 1
        assert prov.continuation_reason == "output_limit"
        assert len(prov.segments) == 2
        # Verify raw segments remain preserved
        assert prov.segments[0] == "Chapter 1: The beginning..."
        assert prov.segments[1] == "beginning... and Chapter 2: The middle."

        # Verify assembly
        assembled = prov.get_assembled_text()
        assert assembled == "Chapter 1: The beginning... and Chapter 2: The middle."

        # Add another segment
        prov.add_segment("Chapter 2: The middle. Chapter 3: The end.")
        assembled2 = prov.get_assembled_text()
        assert assembled2 == "Chapter 1: The beginning... and Chapter 2: The middle. Chapter 3: The end."


def test_gross_restart_deduplication():
    """Verify detect_gross_restart and merge_continuation_segment deduplicate repeated blocks."""
    prior = "First section detailing the architecture. Then second section covering database schemas with PostgreSQL and Redis."
    # Model hallucinated / restarted from "Then second section covering database schemas" and added new text
    restarted_segment = "Then second section covering database schemas with PostgreSQL and Redis. Finally, third section on security policies."

    assembled = merge_continuation_segment(prior, restarted_segment)
    assert assembled == "First section detailing the architecture. Then second section covering database schemas with PostgreSQL and Redis. Finally, third section on security policies."


def test_build_continuation_context_bounded_and_clean():
    """Verify build_continuation_context filters internal instructions and truncates prior tail safely."""
    from app.services.continuation import build_continuation_context

    original = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Write a long story."},
        {"role": "assistant", "content": "Once upon a time..."},
        {"role": "user", "content": "The previous generation reached its output limit. Please continue from where you left off."},
    ]

    res = build_continuation_context(original, assembled_response="Once upon a time there was a kingdom.", max_prior_tail_chars=20)
    # Leaked instruction should be filtered out
    assert len(res) == 4
    assert res[0]["role"] == "system"
    assert res[1]["role"] == "user"
    assert res[1]["content"] == "Write a long story."
    assert res[2]["role"] == "assistant"
    # Bound to last 20 chars of assembled response ("there was a kingdom." is 20 chars)
    assert res[2]["content"] == "there was a kingdom."
    assert res[3]["role"] == "user"
    assert "output limit" in res[3]["content"]
