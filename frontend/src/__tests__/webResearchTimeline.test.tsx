import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RunStreamPanel } from "@/components/workspace/RunStreamPanel";
import {
  runStreamReducer,
  initialRunStreamState,
  RunStreamState,
} from "@/hooks/useRunStream";

function apply(events: any[]): RunStreamState {
  return events.reduce((state, evt) => runStreamReducer(state, evt), initialRunStreamState);
}

const event = (type: string, extra: Record<string, any> = {}) => ({ type, ...extra });

// Thought 1 -> web_research tool (search + 4 fetches) -> Thought 2 -> answer.
const LIVE: any[] = [
  event("RUN_STARTED", { run_id: "r1", trace_id: "t1" }),
  event("MODEL_STARTED", { model_call_id: "mc1", round_index: 0 }),
  event("MODEL_REASONING_DELTA", { model_call_id: "mc1", delta: "I should research this. " }),
  event("MODEL_COMPLETED", { model_call_id: "mc1", duration_ms: 1200 }),
  event("TOOL_CALL_STARTED", { tool_name: "web_research", provider_tool_call_id: "ptc1" }),
  event("TOOL_CALL_COMPLETED", {
    tool_name: "web_research",
    provider_tool_call_id: "ptc1",
    arguments: { query: "quantum computing" },
  }),
  event("SEARCH_STARTED", {
    internal: true,
    operation_id: "op1",
    provider_tool_call_id: "ptc1",
    query: "quantum computing",
    provider: "tavily",
  }),
  event("SEARCH_COMPLETED", {
    internal: true,
    operation_id: "op1",
    provider_tool_call_id: "ptc1",
    query: "quantum computing",
    count: 5,
    duration_ms: 800,
  }),
  event("FETCH_STARTED", { internal: true, provider_tool_call_id: "ptc1", url: "https://a.test/1" }),
  event("FETCH_COMPLETED", {
    internal: true,
    provider_tool_call_id: "ptc1",
    url: "https://a.test/1",
    duration_ms: 4000,
  }),
  event("FETCH_STARTED", { internal: true, provider_tool_call_id: "ptc1", url: "https://b.test/2" }),
  event("FETCH_COMPLETED", {
    internal: true,
    provider_tool_call_id: "ptc1",
    url: "https://b.test/2",
    duration_ms: 2500,
  }),
];

// Mid-flight: source c and d are still being fetched.
const LIVE_EXTRA: any[] = [
  event("FETCH_STARTED", { internal: true, provider_tool_call_id: "ptc1", url: "https://c.test/3" }),
  event("FETCH_STARTED", { internal: true, provider_tool_call_id: "ptc1", url: "https://d.test/4" }),
];

const FINAL_EXTRA: any[] = [
  event("FETCH_COMPLETED", {
    internal: true,
    provider_tool_call_id: "ptc1",
    url: "https://c.test/3",
    duration_ms: 1500,
  }),
  event("FETCH_FAILED", {
    internal: true,
    provider_tool_call_id: "ptc1",
    error: {
      code: "FETCH_TIMEOUT",
      message: "timed out",
      url: "https://d.test/4",
      timeout_seconds: 20,
    },
  }),
  event("TOOL_RESULT_COMPLETED", {
    tool_name: "web_research",
    provider_tool_call_id: "ptc1",
    duration_ms: 12400,
    result: {
      query: "quantum computing",
      search: { results: [{ title: "Quantum", url: "https://q.test" }], count: 5 },
      sources: [
        { url: "https://a.test/1", status: "success", duration_ms: 4000 },
        { url: "https://b.test/2", status: "success", duration_ms: 2500 },
        { url: "https://c.test/3", status: "success", duration_ms: 1500 },
        { url: "https://d.test/4", status: "failed", error: { code: "FETCH_TIMEOUT" }, timeout_seconds: 20 },
      ],
      summary: { searched: 5, selected: 4, fetched: 3, failed: 1 },
      outcome: "success",
    },
  }),
  event("MODEL_STARTED", { model_call_id: "mc2", round_index: 1 }),
  event("MODEL_REASONING_DELTA", { model_call_id: "mc2", delta: "Now I can answer." }),
  event("MODEL_COMPLETED", { model_call_id: "mc2", duration_ms: 900 }),
  event("MODEL_OUTPUT_DELTA", { delta: "The final answer is 42." }),
  event("RUN_COMPLETED", { run: { id: "r1", normalized_response: "The final answer is 42." } }),
];

const finalState = () => apply([...LIVE, ...LIVE_EXTRA, ...FINAL_EXTRA]);
const liveState = () => apply([...LIVE, ...LIVE_EXTRA]);

describe("web_research activity timeline", () => {
  test("orders thought -> tool -> thought chronologically and segments reasoning per model call", () => {
    const state = finalState();
    expect(state.activities.map((a) => a.kind)).toEqual(["thought", "tool", "thought"]);
    expect((state.activities[1] as any).name).toBe("web_research");

    const thoughts = state.activities.filter((a) => a.kind === "thought") as any[];
    expect(thoughts).toHaveLength(2);
    expect(thoughts[0].modelCallId).toBe("mc1");
    expect(thoughts[0].text).toBe("I should research this. ");
    expect(thoughts[1].modelCallId).toBe("mc2");
    expect(thoughts[1].text).toBe("Now I can answer.");
  });

  test("renders a distinct thought row per model call, collapsed by default and expandable", async () => {
    render(<RunStreamPanel state={finalState()} />);

    expect(screen.getAllByTestId("run-thought")).toHaveLength(2);
    // Collapsed by default: neither reasoning stream is visible yet.
    expect(screen.queryByText(/I should research this/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Now I can answer/)).not.toBeInTheDocument();

    await userEvent.click(screen.getAllByTestId("run-thought-toggle")[0]);
    expect(screen.getByTestId("run-thought-content")).toHaveTextContent("I should research this.");

    await userEvent.click(screen.getAllByTestId("run-thought-toggle")[1]);
    const contents = screen.getAllByTestId("run-thought-content");
    expect(contents).toHaveLength(2);
    expect(contents[1]).toHaveTextContent("Now I can answer.");
  });

  test("summarizes the completed research row with fetched/total sources and duration", () => {
    render(<RunStreamPanel state={finalState()} />);
    const row = screen.getByTestId("run-research");
    expect(row).toHaveTextContent("Researched the web");
    expect(row).toHaveTextContent("fetched 3/4 sources");
    expect(row).toHaveTextContent("12.4s");
  });

  test("renders a partial fetch timeout without breaking the run", async () => {
    render(<RunStreamPanel state={finalState()} />);
    await userEvent.click(screen.getByTestId("run-research-toggle"));
    expect(screen.getByTestId("run-fetch-1")).toHaveTextContent("b.test");
    expect(screen.getByTestId("run-fetch-3")).toHaveTextContent(/timeout/);
  });

  test("a live research row shows a spinner and live fetch progress", () => {
    const { container } = render(<RunStreamPanel state={liveState()} />);
    const row = screen.getByTestId("run-research");
    expect(within(row).getByText("Fetching 2/4…")).toBeInTheDocument();
    expect(container.querySelectorAll("svg.animate-spin").length).toBeGreaterThan(0);
  });

  test("never renders the final answer inside the activity panel", () => {
    const state = finalState();
    expect(state.answerText).toBe("The final answer is 42.");
    render(<RunStreamPanel state={state} />);
    expect(screen.getByTestId("message-streaming")).toBeInTheDocument();
    expect(screen.queryByTestId("run-answer")).not.toBeInTheDocument();
    expect(screen.queryByText("The final answer is 42.")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Explicit V4.2.3.8 acceptance coverage
// ---------------------------------------------------------------------------

// One successful, one timed-out and one hard-failed source on the same
// research call: a sub-span failure must never break the run.
const MIXED_SOURCES: any[] = [
  event("RUN_STARTED", { run_id: "r2", trace_id: "t2" }),
  event("MODEL_STARTED", { model_call_id: "mcA", round_index: 0 }),
  event("MODEL_REASONING_DELTA", { model_call_id: "mcA", delta: "Research first." }),
  event("MODEL_COMPLETED", { model_call_id: "mcA", duration_ms: 100 }),
  event("TOOL_CALL_STARTED", { tool_name: "web_research", provider_tool_call_id: "ptcX" }),
  event("FETCH_STARTED", { internal: true, provider_tool_call_id: "ptcX", url: "https://ok.test/1" }),
  event("FETCH_COMPLETED", {
    internal: true,
    provider_tool_call_id: "ptcX",
    url: "https://ok.test/1",
    duration_ms: 500,
  }),
  event("FETCH_STARTED", { internal: true, provider_tool_call_id: "ptcX", url: "https://slow.test/2" }),
  event("FETCH_FAILED", {
    internal: true,
    provider_tool_call_id: "ptcX",
    error: { code: "FETCH_TIMEOUT", message: "timed out", url: "https://slow.test/2", timeout_seconds: 20 },
  }),
  event("FETCH_STARTED", { internal: true, provider_tool_call_id: "ptcX", url: "https://bad.test/3" }),
  event("FETCH_FAILED", {
    internal: true,
    provider_tool_call_id: "ptcX",
    error: { code: "FETCH_ERROR", message: "boom", url: "https://bad.test/3" },
  }),
  event("TOOL_RESULT_COMPLETED", {
    tool_name: "web_research",
    provider_tool_call_id: "ptcX",
    duration_ms: 900,
    result: { query: "mixed", outcome: "partial" },
  }),
];

describe("activity timeline acceptance (V4.2.3.8)", () => {
  test("renders thought, research and final thought rows in chronological DOM order", () => {
    const { container } = render(<RunStreamPanel state={finalState()} />);
    const rows = Array.from(
      container.querySelectorAll('[data-testid="run-thought"], [data-testid="run-research"]')
    );
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "run-thought",
      "run-research",
      "run-thought",
    ]);
  });

  test("does not duplicate a source when FETCH_STARTED is replayed for the same url", () => {
    const state = apply([
      event("TOOL_CALL_STARTED", { tool_name: "web_fetch", provider_tool_call_id: "ptcY" }),
      event("FETCH_STARTED", { internal: true, provider_tool_call_id: "ptcY", url: "https://dup.test/x" }),
      event("FETCH_STARTED", { internal: true, provider_tool_call_id: "ptcY", url: "https://dup.test/x" }),
      event("FETCH_COMPLETED", {
        internal: true,
        provider_tool_call_id: "ptcY",
        url: "https://dup.test/x",
        duration_ms: 300,
      }),
    ]);
    const tool = state.activities.find((a) => a.kind === "tool") as any;
    expect(tool.sources).toHaveLength(1);
    expect(tool.sources[0].status).toBe("success");
  });

  test("renders per-source ✓ / ⚠ timeout / ✗ failed and survives a partial failure", async () => {
    const state = apply(MIXED_SOURCES);
    expect(state.status).not.toBe("error");

    render(<RunStreamPanel state={state} />);
    await userEvent.click(screen.getByTestId("run-research-toggle"));

    const ok = screen.getByTestId("run-fetch-0");
    expect(ok).toHaveAttribute("data-status", "success");
    expect(ok).toHaveTextContent("✓");
    expect(ok).toHaveTextContent("ok.test");

    const timedOut = screen.getByTestId("run-fetch-1");
    expect(timedOut).toHaveAttribute("data-status", "timeout");
    expect(timedOut).toHaveTextContent("⚠");
    expect(timedOut).toHaveTextContent(/timeout/);

    const failed = screen.getByTestId("run-fetch-2");
    expect(failed).toHaveAttribute("data-status", "failed");
    expect(failed).toHaveTextContent("✗");
    expect(failed).toHaveTextContent(/failed/);
  });

  test("never renders raw stream protocol identifiers in the UI", () => {
    const { container } = render(<RunStreamPanel state={finalState()} />);
    expect(container.textContent || "").not.toMatch(
      /MODEL_REASONING_DELTA|TOOL_RESULT_STARTED|provider_tool_call_id|argument_fingerprint/
    );
  });
});
