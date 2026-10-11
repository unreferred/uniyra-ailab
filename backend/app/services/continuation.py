"""Continuation primitives and safe response assembly.

Implements Section F (Safe response assembly):
- Deterministic bounded suffix/prefix overlap detection without LLM calls.
- Deduplicates exact or strong overlap between continuation segments.
- Guards against destructive deletion on uncertain or short overlap.
- Preserves original raw segments internally.
- Reusable continuation policy and provenance helpers for multi-turn /
  coding agent / builder execution workflows.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

CONTINUATION_INSTRUCTION = (
    "The previous generation reached its output limit. "
    "Continue the same response from exactly where it stopped. "
    "Do not restart or repeat completed sections. "
    "Preserve the existing structure and numbering. "
    "Complete the remaining requested work."
)


def detect_suffix_prefix_overlap(
    text1: str,
    text2: str,
    min_overlap: int = 10,
    max_overlap: int = 500,
) -> int:
    """Detect deterministic exact suffix-to-prefix overlap between two text segments.

    Returns the number of characters at the end of text1 that match the start of text2.
    If no overlap >= min_overlap is found, returns 0.

    Requirements & Edge Cases:
    - Bounded search: only considers overlaps between min_overlap and max_overlap
      (or min(len(text1), len(text2), max_overlap)).
    - If overlap is short/ambiguous (< min_overlap), returns 0 so callers do NOT
      destructively truncate.
    - If text2 is completely identical to text1 or is entirely contained at the tail,
      detects the maximal valid overlap up to bounds.
    - Deterministic: checks from longest possible candidate overlap down to min_overlap.
    """
    if not text1 or not text2:
        return 0

    max_possible = min(len(text1), len(text2), max_overlap)
    if max_possible < min_overlap:
        return 0

    # Search from the largest overlap down to min_overlap to find the maximal match.
    for k in range(max_possible, min_overlap - 1, -1):
        if text1.endswith(text2[:k]):
            return k

    return 0


def detect_gross_restart(
    prior: str,
    next_chunk: str,
    min_match_len: int = 30,
) -> Optional[int]:
    """Detect if next_chunk begins by restarting from an earlier part of prior text.

    If an LLM hallucinates/restarts by repeating a gross block that is already present
    in prior, find where in next_chunk the new unrepeated text actually starts.

    Returns the character index in next_chunk from which unique content begins,
    or None if no gross restart is detected.
    """
    if not prior or not next_chunk:
        return None

    stripped_chunk = next_chunk.lstrip()
    offset = len(next_chunk) - len(stripped_chunk)

    test_lens = [500, 300, 200, 150, 100, 60, 40, 30]
    matched_pos = -1
    longest_prefix_len = 0

    for l in test_lens:
        if l <= len(stripped_chunk) and l >= min_match_len:
            candidate = stripped_chunk[:l]
            idx = prior.find(candidate)
            if idx != -1:
                matched_pos = idx
                longest_prefix_len = l
                break

    if longest_prefix_len == 0 or matched_pos == -1:
        return None

    prior_len = len(prior)
    chunk_len = len(stripped_chunk)
    k = longest_prefix_len
    while (
        matched_pos + k < prior_len
        and k < chunk_len
        and prior[matched_pos + k] == stripped_chunk[k]
    ):
        k += 1

    if k >= min_match_len:
        return offset + k

    return None


def merge_continuation_segment(
    prior: str,
    next_chunk: str,
    min_overlap: int = 10,
    max_overlap: int = 500,
) -> str:
    """Safely merge a next_chunk onto prior text using bounded overlap detection.

    Behavior:
    - If prior is empty, returns next_chunk.
    - If next_chunk is empty, returns prior.
    - If next_chunk is identical to prior: returns prior (avoids infinite loops or full duplication).
    - If bounded exact suffix/prefix overlap >= min_overlap is detected:
      deduplicates the overlap cleanly by appending next_chunk[overlap:].
    - If trailing/leading whitespace or normalized boundaries exist (e.g. prior ends with 'foo ',
      next_chunk starts with 'foo ' or 'bar'), overlap handles exact matching.
    - If no strong overlap is found (< min_overlap or non-matching):
      simply concatenates without destructive deletion.
    """
    if not prior:
        return next_chunk
    if not next_chunk:
        return prior

    if prior == next_chunk:
        return prior

    overlap_len = detect_suffix_prefix_overlap(
        prior, next_chunk, min_overlap=min_overlap, max_overlap=max_overlap
    )

    if overlap_len > 0:
        return prior + next_chunk[overlap_len:]

    # Fallback: check normalized whitespace suffix/prefix if segment boundary has identical word
    # but slight whitespace variation (e.g. prior ends with "\n\n### Heading" and next starts with "### Heading")
    # Only deduplicate if the stripped tail matches stripped head with length >= min_overlap
    trimmed_prior = prior.rstrip()
    trimmed_next = next_chunk.lstrip()
    if trimmed_prior and trimmed_next:
        trimmed_overlap = detect_suffix_prefix_overlap(
            trimmed_prior, trimmed_next, min_overlap=min_overlap, max_overlap=max_overlap
        )
        if trimmed_overlap > 0:
            # Suffix of prior (ignoring trailing whitespace) matches prefix of next_chunk (ignoring leading whitespace)
            # Reconstruct safely: keep prior as-is (up to end of matching text), append remainder of next_chunk
            remainder = trimmed_next[trimmed_overlap:]
            return trimmed_prior + (" " if (remainder and not remainder[0].isspace() and not trimmed_prior[-1].isspace()) else "") + remainder

    # Gross restart detection: model restarted from an earlier section of prior
    restart_cut = detect_gross_restart(prior, next_chunk, min_match_len=30)
    if restart_cut is not None and restart_cut > 0:
        remainder = next_chunk[restart_cut:]
        if not remainder.strip():
            return prior
        return merge_continuation_segment(prior, remainder, min_overlap=min_overlap, max_overlap=max_overlap)

    return prior + next_chunk


def assemble_continuation_segments(
    segments: List[str],
    min_overlap: int = 10,
    max_overlap: int = 500,
) -> str:
    """Deterministically assemble multiple continuation segments into a single logical response.

    Preserves exact text when overlap is uncertain or absent. Deduplicates strong overlap.
    """
    if not segments:
        return ""

    # Filter out None or empty segments
    non_empty = [s for s in segments if s]
    if not non_empty:
        return ""

    assembled = non_empty[0]
    for seg in non_empty[1:]:
        assembled = merge_continuation_segment(
            assembled, seg, min_overlap=min_overlap, max_overlap=max_overlap
        )
    return assembled


def build_continuation_context(
    original_messages: List[Dict[str, Any]],
    assembled_response: str,
    continuation_instruction: Optional[str] = None,
    max_prior_tail_chars: int = 4000,
) -> List[Dict[str, Any]]:
    """Build bounded, refitted context for continuation runs.

    Guarantees:
    - Never leaks internal continuation instructions into user history or planning graph.
    - Preserves the governing system / user prompt.
    - Replaces the target assistant response with assembled/bounded text (instead of duplicating assistant turns).
    - Appends a strict continuation directive instructing the model where to pick up.
    """
    instruction = (continuation_instruction or "").strip() or CONTINUATION_INSTRUCTION

    # Strip existing continuation instructions from user turns and strip assistant turns
    # that correspond to the continuation chain, because assembled_response will be appended.
    # Find all prior non-continuation turns, plus user turns that aren't continuation instructions.
    filtered_messages = []
    # If the history already has assistant messages at the end (or prior user continuation requests),
    # trim trailing assistant / user continuation prompt pairs:
    msgs_to_process = list(original_messages)
    # Remove trailing user continuation instruction if present
    if msgs_to_process and msgs_to_process[-1].get("role") == "user":
        content = msgs_to_process[-1].get("content") or ""
        if "output limit" in content or "where you left off" in content:
            msgs_to_process = msgs_to_process[:-1]

    # Remove trailing assistant turn if present (will be replaced by assembled_response)
    if msgs_to_process and msgs_to_process[-1].get("role") == "assistant":
        msgs_to_process = msgs_to_process[:-1]

    for m in msgs_to_process:
        role = m.get("role")
        content = m.get("content") or ""
        # Skip prior continuation instructions if any slipped into user messages
        if role == "user" and (
            "The previous generation reached its output limit" in content
            or "Please continue from where you left off" in content
        ):
            continue
        filtered_messages.append({"role": role, "content": content})

    # Append prior assistant content (tail-bounded to avoid context overflows while anchoring position)
    assistant_content = assembled_response
    if len(assistant_content) > max_prior_tail_chars:
        assistant_content = assistant_content[-max_prior_tail_chars:]

    filtered_messages.append({"role": "assistant", "content": assistant_content})
    filtered_messages.append({"role": "user", "content": instruction})

    return filtered_messages


class ContinuationPolicy(BaseModel):
    """Configuration governing automatic continuations when output token limit is reached."""

    auto_continue_on_output_limit: bool = True
    max_auto_continuations: int = Field(default=3, ge=0, le=10)
    max_total_output_tokens: Optional[int] = Field(default=None, gt=0)
    max_total_runtime_seconds: Optional[float] = Field(default=None, gt=0.0)
    cycle_index: int = Field(default=1, ge=1)

    def should_continue(
        self,
        current_continuation_count: int,
        finish_reason: Optional[str] = None,
        total_output_tokens_used: int = 0,
        elapsed_seconds: float = 0.0,
    ) -> bool:
        """Check whether another continuation run is permitted by this policy."""
        if not self.auto_continue_on_output_limit:
            return False

        reason_str = str(finish_reason or "").strip().lower()
        if reason_str not in ("length", "max_tokens", "max_output_tokens", "token_limit", "output_limit"):
            return False

        if current_continuation_count >= self.max_auto_continuations:
            return False

        if (
            self.max_total_output_tokens is not None
            and total_output_tokens_used >= self.max_total_output_tokens
        ):
            return False

        if (
            self.max_total_runtime_seconds is not None
            and elapsed_seconds >= self.max_total_runtime_seconds
        ):
            return False

        return True


class ContinuationProvenance(BaseModel):
    """Provenance tracking for continuation execution runs."""

    root_run_id: str
    parent_run_id: str
    continuation_index: int = Field(ge=1)
    continuation_reason: str = "output_limit"
    segments: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def add_segment(self, segment: str) -> None:
        """Add a raw segment while preserving the original chunk intact."""
        self.segments.append(segment)

    def get_assembled_text(
        self, min_overlap: int = 10, max_overlap: int = 500
    ) -> str:
        """Assemble all recorded segments deterministically."""
        return assemble_continuation_segments(
            self.segments, min_overlap=min_overlap, max_overlap=max_overlap
        )


def build_continuation_provenance(
    root_run_id: str,
    parent_run_id: str,
    continuation_index: int,
    continuation_reason: str = "output_limit",
    segments: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ContinuationProvenance:
    """Helper factory for creating ContinuationProvenance records."""
    return ContinuationProvenance(
        root_run_id=root_run_id,
        parent_run_id=parent_run_id,
        continuation_index=continuation_index,
        continuation_reason=continuation_reason,
        segments=list(segments or []),
        metadata=dict(metadata or {}),
    )
