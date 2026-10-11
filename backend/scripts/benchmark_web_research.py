"""Offline benchmark helper for the V4.2.3.8 agentic web workflow comparison.

This module is a *formatter and documentation boundary*, not an orchestrator.
It contains no execution loop and no network code: the pytest test
``tests/test_v4238_research_efficiency.py`` drives the REAL
``app.services.execution.execute_model`` loop and passes genuine counters in,
and this helper renders them into a deterministic markdown artifact and
documents exactly what is simulated vs real.

Only ``render_report`` / ``write_report`` are used by the test; the live
acceptance command is emitted for human operators and is intentionally not
executed offline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

PROMPT = "coba cek berita forex bulan ini berdasarkan forexfactory yang high impact aja"

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / "test_reports" / "v4238_research_efficiency.md"

#: Metric rows rendered in the comparison table, in order.
#: (key, label, formatter kind)
_ROWS = (
    ("model_call_count", "model calls", "int"),
    ("search_requests", "search provider requests", "int"),
    ("fetch_provider_requests", "fetch provider requests", "int"),
    ("urls_fetched", "URLs fetched", "int"),
    ("external_requests", "external requests (search + fetch)", "int"),
    ("input_tokens", "input tokens", "int"),
    ("output_tokens", "output tokens", "int"),
    ("total_tokens", "total tokens", "int"),
    ("wall_ms", "wall time (ms, informational)", "int"),
    ("cost_usd", "cost", "cost"),
    ("tool_success_count", "tool successes", "int"),
)

_LIVE_COMMAND = """\
# Live acceptance run (requires a REAL TinyFish key + a REAL model/provider).
# The numbers below are only valid for a live run if you re-read the Run doc
# fields; they are NOT produced by this offline simulation.
export TINYFISH_API_KEY="<your-real-tinyfish-key>"
cd backend && .venv/bin/uvicorn server:app --host 0.0.0.0 --port 8001

# In another shell (auth as a real user; replace token/ids):
curl -sS -X POST "$BASE_URL/workspace/conversations/$CONVERSATION_ID/messages" \\
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \\
  -d '{
        "model_id": "<real-model-id>",
        "content": "coba cek berita forex bulan ini berdasarkan forexfactory yang high impact aja",
        "enabled_tool_ids": ["tool-research-0001"],
        "search_enabled": true,
        "search_provider_id": "prov-search-tinyfish",
        "fetch_enabled": true,
        "fetch_provider_id": "prov-fetch-tinyfish",
        "max_tool_rounds": 5
      }'
# Then compare against the atomic baseline by swapping enabled_tool_ids to
# ["tool-search-0001", "tool-fetch-0001"] and re-running the same prompt.
"""


def _fmt_int(value: Any) -> str:
    if value is None:
        return "n/a"
    return str(int(value))


def _fmt_cost(value: Any) -> str:
    if value is None:
        return "unavailable"
    return f"${float(value):.6f}"


def _fmt(kind: str, value: Any) -> str:
    if kind == "cost":
        return _fmt_cost(value)
    return _fmt_int(value)


def _delta(kind: str, new_value: Any, old_value: Any) -> str:
    if new_value is None or old_value is None:
        return "n/a"
    if kind == "cost":
        diff = float(new_value) - float(old_value)
        sign = "+" if diff >= 0 else "-"
        return f"{sign}${abs(diff):.6f}"
    diff = int(new_value) - int(old_value)
    return f"{diff:+d}"


def render_report(
    *,
    old: Dict[str, Any],
    new: Dict[str, Any],
    prompt: str = PROMPT,
    cost_note: Optional[str] = None,
) -> str:
    """Render the deterministic comparison markdown.

    ``old`` / ``new`` are plain dicts of *genuine* counters produced by the
    real orchestration loop (mocked network only). Everything except
    ``wall_ms`` is deterministic and reproducible.
    """
    lines = []
    lines.append("# V4.2.3.8 — Agentic web workflow efficiency (deterministic offline simulation)")
    lines.append("")
    lines.append(
        "> **This is a deterministic offline orchestration simulation, NOT a live "
        "benchmark.** It drives the real `execute_model` loop and the real "
        "`web_search` / `web_fetch` / `web_research` executors, but the network "
        "provider boundary (`TinyFishSearchProvider.search`, fetch providers' "
        "`fetch` / `fetch_many`) is replaced with deterministic in-process stubs. "
        "A live acceptance run against a real TinyFish key and a real model is "
        "still required before quoting these ratios as production gains."
    )
    lines.append("")
    lines.append("## Prompt (verbatim)")
    lines.append("")
    lines.append(f"`{prompt}`")
    lines.append("")
    lines.append("## Measured comparison")
    lines.append("")
    lines.append("| metric | OLD (atomic tools) | NEW (`web_research`) | delta (NEW - OLD) |")
    lines.append("| --- | ---: | ---: | ---: |")
    for key, label, kind in _ROWS:
        lines.append(
            f"| {label} | {_fmt(kind, old.get(key))} | {_fmt(kind, new.get(key))} "
            f"| {_delta(kind, new.get(key), old.get(key))} |"
        )
    lines.append(f"| run status | {old.get('status')} | {new.get('status')} | — |")
    lines.append("")
    lines.append("## What is real vs simulated")
    lines.append("")
    lines.append("- **Real (genuine counts from production code):** the agentic loop "
                 "(`execute_model`), model-continuation rounds, tool dispatch, the "
                 "`web_search` / `web_fetch` / `web_research` executors, the composite "
                 "search -> deterministic URL selection -> single batch fetch, run "
                 "counters, run status, and per-model-call usage as reported by the "
                 "scripted adapter.")
    lines.append("- **Simulated (no network):** the search provider `search()` method and "
                 "the fetch providers' `fetch()` / `fetch_many()` methods. They are "
                 "deterministic stubs that count invocations and return fixed payloads.")
    lines.append("- **Token caveat:** token counts are the scripted adapter's reported "
                 "usage using identical per-call constants (850 input / 120 output) in both "
                 "scenarios. They are NOT tokenizer measurements, so the token deltas reflect "
                 "the genuine difference in model continuations, not evidence size.")
    lines.append("- **Not measured:** real provider latency, rate limits, page payload "
                 "sizes, true tokenizer counts, and live network variance.")
    lines.append("")
    lines.append("### OLD (baseline, atomic tools)")
    lines.append("")
    lines.append("4 model calls: `web_search`; then `web_search` (refined) + `web_fetch` A; "
                 "then `web_fetch` B + `web_fetch` C; then the final answer. The model "
                 "drives each atomic provider call, so the fetch provider is hit once per "
                 "URL.")
    lines.append("")
    lines.append("### NEW (`web_research`)")
    lines.append("")
    lines.append("2 model calls: one `web_research` call (one search request + one batched "
                 "`fetch_many` request covering the same N URLs) then the final answer.")
    lines.append("")
    if cost_note:
        lines.append("### Cost")
        lines.append("")
        lines.append(cost_note)
        lines.append("")
    lines.append("## Live acceptance run")
    lines.append("")
    lines.append("```bash")
    lines.append(_LIVE_COMMAND.rstrip("\n"))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def write_report(
    path: Optional[Path] = None,
    *,
    old: Dict[str, Any],
    new: Dict[str, Any],
    prompt: str = PROMPT,
    cost_note: Optional[str] = None,
) -> Path:
    """Render and write the artifact; returns the written path."""
    target = Path(path) if path is not None else REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_report(old=old, new=new, prompt=prompt, cost_note=cost_note),
        encoding="utf-8",
    )
    return target


if __name__ == "__main__":  # pragma: no cover - operator helper only
    print(
        "This helper formats counters produced by "
        "tests/test_v4238_research_efficiency.py.\n"
        "Run: cd backend && .venv/bin/python -m pytest -n 0 "
        "tests/test_v4238_research_efficiency.py -q\n\n"
        "Live acceptance command:\n"
        + _LIVE_COMMAND
    )
