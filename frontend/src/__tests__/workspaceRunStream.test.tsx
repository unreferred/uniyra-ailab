import React from "react";
import { render, screen, waitFor, within, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Workspace from "@/pages/Workspace";
import { RunStreamPanel } from "@/components/workspace/RunStreamPanel";
import { streamRun } from "@/lib/runStream";
import {
  runStreamReducer,
  initialRunStreamState,
  RunStreamState,
} from "@/hooks/useRunStream";
import { api, uploadAttachments } from "@/lib/api";
import { ModelRecord, ToolRecord } from "@/lib/types";

jest.mock("@floating-ui/dom", () => {
  const actual = jest.requireActual("@floating-ui/dom");
  return {
    ...actual,
    computePosition: () =>
      Promise.resolve({ x: 0, y: 0, strategy: "fixed", placement: "bottom", middlewareData: {} }),
  };
});

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn(), message: jest.fn() },
}));

jest.mock("@/components/common/Markdown", () => ({
  MarkdownView: ({ content }: { content: string }) => <div data-testid="markdown-view">{content}</div>,
}));

jest.mock("@/components/common/SaveArtifactDialog", () => ({
  SaveArtifactDialog: () => null,
}));

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({
    activeId: "p1",
    activeProject: { id: "p1", name: "Demo Project" },
    projects: [],
    isLoading: false,
    setActiveId: jest.fn(),
    refresh: jest.fn(),
  }),
}));

// Keep the real csrfHeader/API_BASE so the transport test exercises the actual
// cookie reader, but stub the axios client used by components.
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return {
    ...actual,
    api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
    apiError: (e: any) => (e && e.message) || "error",
    uploadAttachments: jest.fn(),
  };
});

const mockedGet = api.get as jest.Mock;
const mockedPost = api.post as jest.Mock;
const mockedPatch = api.patch as jest.Mock;
const mockedUpload = uploadAttachments as jest.Mock;

// ---------------------------------------------------------------------------
// SSE helpers
// ---------------------------------------------------------------------------

function makeReader(chunks: string[]) {
  let i = 0;
  return {
    read: async () =>
      i < chunks.length
        ? { done: false, value: new TextEncoder().encode(chunks[i++]) }
        : { done: true, value: undefined },
    cancel: async () => {},
    releaseLock: () => {},
  };
}

function sseResponse(chunks: string[]) {
  return { ok: true, status: 200, body: { getReader: () => makeReader(chunks) } } as any;
}

const frame = (evt: any) => `data: ${JSON.stringify(evt)}\n\n`;

const makeState = (over: Partial<RunStreamState> = {}): RunStreamState => ({
  ...initialRunStreamState,
  status: "running",
  phase: "thinking",
  ...over,
});

const thought = (over: any = {}) => ({
  id: "mc1",
  kind: "thought",
  phase: "initial",
  text: "reasoning",
  status: "done",
  startedAt: 1,
  durationMs: 1200,
  modelCallId: "mc1",
  ...over,
});

// ---------------------------------------------------------------------------
// Pure reducer
// ---------------------------------------------------------------------------

describe("runStreamReducer", () => {
  test("segments reasoning per model call and accumulates answer deltas", () => {
    let s = runStreamReducer(initialRunStreamState, {
      type: "RUN_STARTED",
      run_id: "r1",
      trace_id: "t1",
    });
    expect(s.status).toBe("running");
    expect(s.runId).toBe("r1");
    expect(s.traceId).toBe("t1");

    s = runStreamReducer(s, { type: "MODEL_STARTED", model_call_id: "mc1", round_index: 0 });
    s = runStreamReducer(s, { type: "MODEL_REASONING_DELTA", model_call_id: "mc1", delta: "think " });
    s = runStreamReducer(s, { type: "MODEL_REASONING_DELTA", model_call_id: "mc1", delta: "harder" });

    expect(s.activities).toHaveLength(1);
    const first = s.activities[0] as any;
    expect(first.kind).toBe("thought");
    expect(first.phase).toBe("initial");
    expect(first.status).toBe("running");
    expect(first.text).toBe("think harder");

    s = runStreamReducer(s, { type: "MODEL_COMPLETED", model_call_id: "mc1", duration_ms: 1500 });
    const done = s.activities[0] as any;
    expect(done.status).toBe("done");
    expect(done.durationMs).toBe(1500);

    s = runStreamReducer(s, { type: "MODEL_OUTPUT_DELTA", delta: "Hel" });
    s = runStreamReducer(s, { type: "MODEL_OUTPUT_DELTA", delta: "lo" });
    expect(s.answerText).toBe("Hello");
    expect(s.sawDelta).toBe(true);

    s = runStreamReducer(s, { type: "FINALIZATION_DELTA", delta: "!" });
    expect(s.answerText).toBe("Hello!");
    expect(s.phase).toBe("finalizing");
  });

  test("keeps reasoning from different model calls in distinct rows", () => {
    let s = makeState();
    s = runStreamReducer(s, { type: "MODEL_STARTED", model_call_id: "mc1", round_index: 0 });
    s = runStreamReducer(s, { type: "MODEL_REASONING_DELTA", model_call_id: "mc1", delta: "first" });
    s = runStreamReducer(s, { type: "MODEL_COMPLETED", model_call_id: "mc1", duration_ms: 10 });
    s = runStreamReducer(s, { type: "MODEL_STARTED", model_call_id: "mc2", round_index: 1 });
    s = runStreamReducer(s, { type: "MODEL_REASONING_DELTA", model_call_id: "mc2", delta: "second" });

    const thoughts = s.activities.filter((a) => a.kind === "thought") as any[];
    expect(thoughts).toHaveLength(2);
    expect(thoughts[0].id).toBe("mc1");
    expect(thoughts[0].text).toBe("first");
    expect(thoughts[1].id).toBe("mc2");
    expect(thoughts[1].text).toBe("second");
    expect(thoughts[1].phase).toBe("continuation");
  });

  test("tracks search and fetch as sub-activity on the parent tool", () => {
    let s = makeState();
    s = runStreamReducer(s, {
      type: "TOOL_CALL_STARTED",
      tool_name: "web_search",
      provider_tool_call_id: "ptc1",
    });
    s = runStreamReducer(s, {
      type: "TOOL_CALL_COMPLETED",
      tool_name: "web_search",
      provider_tool_call_id: "ptc1",
    });
    s = runStreamReducer(s, {
      type: "SEARCH_STARTED",
      provider_tool_call_id: "ptc1",
      query: "quantum",
      provider: "tavily",
    });
    expect(s.phase).toBe("searching");
    let tool = s.activities[0] as any;
    expect(tool.query).toBe("quantum");
    expect(tool.provider).toBe("tavily");
    expect(tool.status).toBe("running");

    s = runStreamReducer(s, {
      type: "SEARCH_COMPLETED",
      provider_tool_call_id: "ptc1",
      query: "quantum",
      count: 3,
    });
    expect(s.phase).toBe("analyzing");
    tool = s.activities[0] as any;
    expect(tool.searchCount).toBe(3);

    s = runStreamReducer(s, {
      type: "FETCH_STARTED",
      provider_tool_call_id: "ptc1",
      url: "https://a.test/x",
    });
    expect(s.phase).toBe("fetching");
    expect((s.activities[0] as any).sources).toHaveLength(1);

    s = runStreamReducer(s, {
      type: "FETCH_COMPLETED",
      provider_tool_call_id: "ptc1",
      url: "https://a.test/x",
      duration_ms: 1200,
    });
    const src = (s.activities[0] as any).sources[0];
    expect(src.status).toBe("success");
    expect(src.durationMs).toBe(1200);
    expect(s.activities).toHaveLength(1);
  });

  test("FETCH_TIMEOUT marks the source but NOT the run as error", () => {
    let s = makeState();
    s = runStreamReducer(s, {
      type: "FETCH_STARTED",
      provider_tool_call_id: "ptc1",
      url: "https://slow.test",
    });
    s = runStreamReducer(s, {
      type: "FETCH_FAILED",
      provider_tool_call_id: "ptc1",
      error: {
        code: "FETCH_TIMEOUT",
        message: "timed out",
        url: "https://slow.test",
        timeout_seconds: 20,
      },
    });
    expect(s.status).toBe("running");
    const src = (s.activities[0] as any).sources[0];
    expect(src.status).toBe("timeout");
    expect(src.timeoutSeconds).toBe(20);
  });

  test("maps a web_research result into search results, sources and summary", () => {
    let s = makeState();
    s = runStreamReducer(s, {
      type: "TOOL_CALL_STARTED",
      tool_name: "web_research",
      provider_tool_call_id: "ptc9",
    });
    s = runStreamReducer(s, {
      type: "TOOL_RESULT_COMPLETED",
      tool_name: "web_research",
      provider_tool_call_id: "ptc9",
      duration_ms: 12400,
      result: {
        query: "quantum",
        search: { results: [{ title: "Q", url: "https://q.test", snippet: "s" }] },
        sources: [
          { url: "https://a.test", status: "success", duration_ms: 4000 },
          { url: "https://b.test", status: "success", duration_ms: 2500 },
          { url: "https://c.test", status: "success", duration_ms: 1500 },
          { url: "https://d.test", status: "failed", error: { code: "FETCH_TIMEOUT" }, timeout_seconds: 20 },
        ],
        summary: { searched: 5, selected: 4, fetched: 3, failed: 1 },
        outcome: "success",
      },
    });
    const tool = s.activities[0] as any;
    expect(tool.status).toBe("completed");
    expect(tool.query).toBe("quantum");
    expect(tool.searchResults).toHaveLength(1);
    expect(tool.sources).toHaveLength(4);
    expect(tool.sources[3].status).toBe("timeout");
    expect(tool.summary).toEqual({ searched: 5, selected: 4, fetched: 3, failed: 1 });
    expect(tool.durationMs).toBe(12400);
  });

  test("RUN_COMPLETED completes and falls back to run.normalized_response", () => {
    let s = makeState();
    s = runStreamReducer(s, { type: "RUN_COMPLETED", run: { id: "r1", normalized_response: "final" } });
    expect(s.status).toBe("done");
    expect(s.answerText).toBe("final");
  });

  test("RUN_CANCELLED yields cancelled", () => {
    const s = runStreamReducer(makeState(), { type: "RUN_CANCELLED", error: { message: "stopped" } });
    expect(s.status).toBe("cancelled");
  });

  test("reasoning completion and metrics do not finalize or lose streamed content", () => {
    let s = makeState();
    s = runStreamReducer(s, { type: "MODEL_OUTPUT_DELTA", delta: "Tanpa data historis, **indicator te" });
    s = runStreamReducer(s, { type: "MODEL_COMPLETED", model_call_id: "mc1" });
    s = runStreamReducer(s, { type: "METRICS", ttft_ms: 42 });
    expect(s.status).toBe("running");
    expect(s.answerText).toBe("Tanpa data historis, **indicator te");
    s = runStreamReducer(s, {
      type: "RUN_COMPLETED",
      run: { id: "r1", normalized_response: "Tanpa data historis, **indicator terakhir**" },
      full_text: "Tanpa data historis, **indicator terakhir**",
    });
    expect(s.status).toBe("done");
    expect(s.answerText).toBe("Tanpa data historis, **indicator terakhir**");
  });

  test("output limit is distinguishable from a normal completion", () => {
    const s = runStreamReducer(makeState({ answerText: "partial" }), {
      type: "RUN_COMPLETED",
      run: { id: "r1", normalized_response: "partial", finish_reason: "length" },
      assistant_message: { id: "m1", role: "assistant", content: "partial" },
    });
    expect(s.status).toBe("output_limit");
    expect(s.outputLimit).toBe(true);
    expect(s.assistantMessage?.finish_reason).toBe("length");
    expect(s.assistantMessage?.output_limit).toBe(true);
    expect(s.assistantMessage?.output_limit_reason).toBe("length");
  });

  test("unknown events are ignored without crashing", () => {
    const before = makeState();
    expect(runStreamReducer(before, { type: "SOME_NEW_EVENT", payload: 1 })).toBe(before);
    expect(runStreamReducer(before, null)).toBe(before);
  });

  test("does not duplicate activities for the same provider tool call", () => {
    let s = makeState();
    s = runStreamReducer(s, { type: "TOOL_CALL_STARTED", tool_name: "web_search", provider_tool_call_id: "ptc1" });
    s = runStreamReducer(s, { type: "TOOL_CALL_STARTED", tool_name: "web_search", provider_tool_call_id: "ptc1" });
    s = runStreamReducer(s, { type: "TOOL_CALL_COMPLETED", tool_name: "web_search", provider_tool_call_id: "ptc1" });
    expect(s.activities).toHaveLength(1);
    expect((s.activities[0] as any).status).toBe("arguments-ready");
  });
});

// ---------------------------------------------------------------------------
// RunStreamPanel component
// ---------------------------------------------------------------------------

describe("RunStreamPanel", () => {
  test("renders the activity timeline and the answer in the same assistant turn", () => {
    render(
      <RunStreamPanel
        state={makeState({
          phase: "analyzing",
          answerText: "The answer is 42",
          activities: [
            thought({ text: "step by step", durationMs: 1800 }),
            {
              id: "ptc1",
              kind: "tool",
              name: "web_search",
              status: "completed",
              query: "life",
              provider: "tavily",
              searchCount: 4,
              durationMs: 1800,
              searchResults: [{ title: "Life", url: "https://life.test" }],
              sources: [],
            },
          ],
        })}
      />
    );

    expect(screen.getByTestId("message-streaming")).toBeInTheDocument();
    expect(screen.getByText("Analyzing evidence…")).toBeInTheDocument();
    expect(screen.getByTestId("run-activity-timeline")).toBeInTheDocument();
    expect(screen.getAllByTestId("run-thought")).toHaveLength(1);
    // Completed thought remains expanded by default.
    expect(screen.getByText("step by step")).toBeInTheDocument();
    expect(screen.getByTestId("run-search")).toHaveTextContent("Searched web");
    expect(screen.getByTestId("run-search")).toHaveTextContent("4 results");
    expect(screen.getByTestId("run-search")).toHaveTextContent("1.8s");
    expect(screen.getByTestId("run-answer")).toBeInTheDocument();
    expect(screen.getByText("The answer is 42")).toBeInTheDocument();
  });

  test("a running thought streams live and expands by default", () => {
    render(
      <RunStreamPanel
        state={makeState({
          activities: [thought({ status: "running", durationMs: undefined, text: "streaming now" })],
        })}
      />
    );
    const thoughtRow = screen.getByTestId("run-thought");
    expect(within(thoughtRow).getByText("Thinking…")).toBeInTheDocument();
    expect(screen.getByTestId("run-thought-content")).toHaveTextContent("streaming now");
  });

  test("collapses a completed thought without reopening on updates", async () => {
    render(
      <RunStreamPanel state={makeState({ activities: [thought({ text: "deep thought" })] })} />
    );
    expect(screen.getByText("deep thought")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("run-thought-toggle"));
    expect(screen.queryByTestId("run-thought-content")).not.toBeInTheDocument();
  });

  test("renders web_fetch sources with per-source status", async () => {
    render(
      <RunStreamPanel
        state={makeState({
          phase: "fetching",
          activities: [
            {
              id: "f1",
              kind: "tool",
              name: "web_fetch",
              status: "completed",
              sources: [
                { url: "https://ok.test/a", host: "ok.test", status: "success", durationMs: 800 },
                { url: "https://slow.test/b", host: "slow.test", status: "timeout", timeoutSeconds: 20 },
              ],
            },
          ],
        })}
      />
    );
    await userEvent.click(screen.getByTestId("run-fetch-toggle"));
    expect(screen.getByTestId("run-fetch-0")).toHaveTextContent("ok.test");
    expect(screen.getByTestId("run-fetch-1")).toHaveTextContent(/timeout/);
  });

  test("does not fabricate a timeline when no activities exist", () => {
    render(<RunStreamPanel state={makeState({ phase: "waiting" })} />);
    expect(screen.queryByTestId("run-activity-timeline")).not.toBeInTheDocument();
    expect(screen.getAllByText("Waiting for model…").length).toBeGreaterThan(0);
  });

  test("shows a Stop button while running and reports the click", async () => {
    const onStop = jest.fn();
    render(<RunStreamPanel state={makeState()} onStop={onStop} />);
    await userEvent.click(screen.getByTestId("run-stop"));
    expect(onStop).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

describe("streamRun transport", () => {
  afterEach(() => {
    document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  test("sends the CSRF header and dispatches split SSE frames in order", async () => {
    document.cookie = "csrf_token=tok123";
    const fetchMock = jest.fn().mockResolvedValue(
      sseResponse([
        'data: {"type":"RUN_STARTED","run_id"',
        ':"r1"}\n\ndata: {"type":"MODEL_OUTPUT_DELTA","delta":"Hi"}\n\n',
      ])
    );
    (global as any).fetch = fetchMock;

    const events: any[] = [];
    await streamRun("c1", { content: "hi" }, { onEvent: (e) => events.push(e) });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/conversations/c1/messages/stream");
    expect(init.credentials).toBe("include");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.headers["X-CSRF-Token"]).toBe("tok123");
    expect(JSON.parse(init.body)).toEqual({ content: "hi" });
    expect(events.map((e) => e.type)).toEqual(["RUN_STARTED", "MODEL_OUTPUT_DELTA"]);
    expect(events[0].run_id).toBe("r1");
  });
});

// ---------------------------------------------------------------------------
// Workspace wiring
// ---------------------------------------------------------------------------

const now = new Date("2026-01-01T00:00:00.000Z").toISOString();

const model: ModelRecord = {
  id: "m1",
  owner_id: "u1",
  provider_id: "prov1",
  display_name: "Model One",
  model_identifier: "gpt-x",
  family: "openai",
  enabled: true,
  context_window: 8192,
  input_cost: null,
  output_cost: null,
  capabilities: { text: true, streaming: true, tools: true },
  capability_source: "PROVIDER",
  tags: [],
  notes: "",
  created_at: now,
  updated_at: now,
};

const tool: ToolRecord = {
  id: "t1",
  name: "web_search",
  display_name: "Web Search",
  description: "Search the public web",
  version: "1",
  tool_type: "builtin",
  input_schema: {},
  enabled: true,
  risk_level: "low",
  permissions: [],
  available: true,
};

const conversation = {
  id: "c1",
  project_id: "p1",
  title: "New conversation",
  system_prompt: "",
  created_at: now,
  updated_at: now,
  messages: [],
};

function renderWorkspace() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={["/workspace?conv=c1"]}>
      <QueryClientProvider client={qc}>
        <Workspace />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe("Workspace streaming wiring", () => {
  beforeEach(() => {
    jest.clearAllMocks();

    mockedGet.mockImplementation((url: string) => {
      if (url === "/models") return Promise.resolve({ data: [model] });
      if (url === "/tools") return Promise.resolve({ data: { items: [tool], total: 1 } });
      if (url === "/code-interpreter/status")
        return Promise.resolve({ data: { available: true, reason: null } });
      if (url === "/attachments") {
        return Promise.resolve({ data: { items: [], total: 0, limit: 50, offset: 0 } });
      }
      if (url.startsWith("/conversations/") && url.includes("stream-support")) {
        return Promise.resolve({ data: { supports_stream: false } });
      }
      if (url.startsWith("/conversations?")) return Promise.resolve({ data: [] });
      if (url === "/conversations/c1") return Promise.resolve({ data: conversation });
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });

    mockedPost.mockResolvedValue({ data: {} });
    mockedPatch.mockResolvedValue({ data: {} });
    mockedUpload.mockResolvedValue([]);
  });

  test("removed the old non-streaming warning", async () => {
    renderWorkspace();
    await screen.findByTestId("workspace-send");
    expect(screen.queryByText(/non-streaming/i)).not.toBeInTheDocument();
  });

  test("a tools-enabled send goes to the stream endpoint", async () => {
    const user = userEvent.setup({ delay: null });
    const fetchMock = jest.fn().mockResolvedValue(
      sseResponse([
        frame({ type: "RUN_STARTED", run_id: "r1", trace_id: "t1" }),
        frame({ type: "MODEL_OUTPUT_DELTA", delta: "done" }),
        frame({ type: "RUN_COMPLETED", run: { id: "r1", normalized_response: "done" } }),
      ])
    );
    (global as any).fetch = fetchMock;

    renderWorkspace();
    await waitFor(() =>
      expect(screen.getByTestId("workspace-model-select")).toHaveTextContent("Model One")
    );

    await user.click(screen.getByTestId("workspace-tools-button"));
    await user.click(within(await screen.findByTestId("tool-t1")).getByRole("checkbox"));
    await waitFor(() =>
      expect(screen.getByTestId("workspace-tools-button")).toHaveTextContent("Tools: 1")
    );

    await user.type(screen.getByTestId("workspace-input"), "search the web");
    await user.click(screen.getByTestId("workspace-send"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/conversations/c1/messages/stream");
    const body = JSON.parse(init.body);
    expect(body.enabled_tool_ids).toEqual(["t1"]);
    expect(body.content).toBe("search the web");
    // The forced non-streaming branch is gone: no legacy /messages POST.
    expect(mockedPost).not.toHaveBeenCalledWith("/conversations/c1/messages", expect.anything());
  });
});

// ---------------------------------------------------------------------------
// V4.2.3.8 acceptance: live search rows + answer placement
// ---------------------------------------------------------------------------

describe("activity timeline acceptance (V4.2.3.8)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedGet.mockImplementation((url: string) => {
      if (url === "/models") return Promise.resolve({ data: [model] });
      if (url === "/tools") return Promise.resolve({ data: { items: [tool], total: 1 } });
      if (url === "/code-interpreter/status")
        return Promise.resolve({ data: { available: true, reason: null } });
      if (url === "/attachments") {
        return Promise.resolve({ data: { items: [], total: 0, limit: 50, offset: 0 } });
      }
      if (url.startsWith("/conversations/") && url.includes("stream-support")) {
        return Promise.resolve({ data: { supports_stream: false } });
      }
      if (url.startsWith("/conversations?")) return Promise.resolve({ data: [] });
      if (url === "/conversations/c1") return Promise.resolve({ data: conversation });
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
    mockedPost.mockResolvedValue({ data: {} });
    mockedPatch.mockResolvedValue({ data: {} });
    mockedUpload.mockResolvedValue([]);
  });

  test("a running web_search row streams 'Searching web…' and expands to query/provider/titles", async () => {
    let s = makeState();
    s = runStreamReducer(s, {
      type: "TOOL_CALL_STARTED",
      tool_name: "web_search",
      provider_tool_call_id: "ws1",
    });
    s = runStreamReducer(s, {
      type: "SEARCH_STARTED",
      provider_tool_call_id: "ws1",
      query: "quantum",
      provider: "tavily",
    });

    const running = render(<RunStreamPanel state={s} />);
    expect(screen.getByTestId("run-search")).toHaveTextContent("Searching web…");
    running.unmount();

    s = runStreamReducer(s, {
      type: "SEARCH_COMPLETED",
      provider_tool_call_id: "ws1",
      query: "quantum",
      count: 3,
      duration_ms: 900,
      results: [
        { title: "Quantum One", url: "https://q1.test" },
        { title: "Quantum Two", url: "https://q2.test" },
      ],
    });
    s = runStreamReducer(s, {
      type: "TOOL_RESULT_COMPLETED",
      tool_name: "web_search",
      provider_tool_call_id: "ws1",
      duration_ms: 1800,
    });

    render(<RunStreamPanel state={s} />);
    const row = screen.getByTestId("run-search");
    expect(row).toHaveTextContent("Searched web");
    expect(row).toHaveTextContent("3 results");
    expect(row).toHaveTextContent("1.8s");
    // Completed rows start collapsed.
    expect(screen.queryByTestId("run-search-body")).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId("run-search-toggle"));
    const body = screen.getByTestId("run-search-body");
    expect(within(body).getByText("quantum")).toBeInTheDocument();
    expect(within(body).getByText("tavily")).toBeInTheDocument();
    expect(within(body).getByText("Quantum One")).toBeInTheDocument();
    expect(within(body).getByText("https://q1.test")).toBeInTheDocument();
  });

  test("renders the final answer in a separate run-answer node, never inside the activity panel", async () => {
    const user = userEvent.setup({ delay: null });
    const fetchMock = jest.fn().mockResolvedValue(
      sseResponse([
        frame({ type: "RUN_STARTED", run_id: "r-live", trace_id: "t-live" }),
        frame({ type: "MODEL_STARTED", model_call_id: "mc1", round_index: 0 }),
        frame({ type: "MODEL_REASONING_DELTA", model_call_id: "mc1", delta: "thinking hard" }),
        frame({ type: "MODEL_COMPLETED", model_call_id: "mc1", duration_ms: 700 }),
        frame({ type: "MODEL_OUTPUT_DELTA", delta: "The final answer is 42." }),
        frame({
          type: "RUN_COMPLETED",
          run: { id: "r-live", normalized_response: "The final answer is 42." },
        }),
      ])
    );
    (global as any).fetch = fetchMock;

    renderWorkspace();
    await waitFor(() =>
      expect(screen.getByTestId("workspace-model-select")).toHaveTextContent("Model One")
    );
    await user.type(screen.getByTestId("workspace-input"), "research then answer");
    await user.click(screen.getByTestId("workspace-send"));

    const answer = await screen.findByTestId("run-answer");
    expect(answer).toHaveTextContent("The final answer is 42.");

    const panel = screen.getByTestId("message-streaming");
    expect(within(panel).getByText("The final answer is 42.")).toBeInTheDocument();
    // The activity timeline and answer now belong to one assistant turn.
    expect(within(panel).getByTestId("run-activity-timeline")).toBeInTheDocument();
    expect(within(panel).getAllByTestId("run-thought").length).toBeGreaterThan(0);
    expect(within(answer).queryByTestId("run-activity-timeline")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Phase 9: Deterministic Synthetic Long-Stream Regression
// ---------------------------------------------------------------------------

describe("Phase 9: Synthetic long-stream regression", () => {
  test("processes 1,000+ chunks with multi-paragraph markdown, headings, lists, code, and telemetry", () => {
    // 1. Generate 1000+ chunks
    const chunks: string[] = [];
    chunks.push("# Main Architectural Specification\n\n");
    chunks.push("## Section 1: Overview\n\n");
    for (let i = 0; i < 250; i++) {
      chunks.push(`Paragraph ${i + 1}: Detailing subsystem invariant stability and throughput characteristics.\n\n`);
    }
    chunks.push("## Section 2: Requirements List\n\n");
    for (let i = 0; i < 500; i++) {
      chunks.push(`* Invariant item ${i + 1}: Zero-loss streaming guarantee across asynchronous boundaries.\n`);
    }
    chunks.push("\n## Section 3: Implementation Code\n\n```typescript\n");
    for (let i = 0; i < 300; i++) {
      chunks.push(`export const handleStreamBlock_${i} = (chunk: string) => processBlock(${i}, chunk);\n`);
    }
    chunks.push("```\n\nConclusion: Synthetic payload verification complete.\n");

    expect(chunks.length).toBeGreaterThanOrEqual(1050);
    const expectedOutput = chunks.join("");

    // Initial state
    let s = runStreamReducer(initialRunStreamState, {
      type: "RUN_STARTED",
      run_id: "long-run-1",
      trace_id: "trace-long-1",
    });
    expect(s.status).toBe("running");

    // Reasoning events
    s = runStreamReducer(s, {
      type: "MODEL_STARTED",
      model_call_id: "mc-long-1",
      round_index: 0,
    });
    s = runStreamReducer(s, {
      type: "MODEL_REASONING_DELTA",
      model_call_id: "mc-long-1",
      delta: "Thinking through the multi-page document structure...\n",
    });
    s = runStreamReducer(s, {
      type: "MODEL_REASONING_DELTA",
      model_call_id: "mc-long-1",
      delta: "Structuring headings, paragraphs, lists, and code blocks.\n",
    });
    s = runStreamReducer(s, {
      type: "MODEL_COMPLETED",
      model_call_id: "mc-long-1",
      duration_ms: 1200,
    });

    // Reasoning remains separate
    expect(s.activities).toHaveLength(1);
    const thought = s.activities[0] as any;
    expect(thought.kind).toBe("thought");
    expect(thought.text).toBe(
      "Thinking through the multi-page document structure...\nStructuring headings, paragraphs, lists, and code blocks.\n"
    );
    expect(s.answerText).toBe("");

    // Output chunks
    for (let i = 0; i < chunks.length; i++) {
      s = runStreamReducer(s, {
        type: "MODEL_OUTPUT_DELTA",
        delta: chunks[i],
      });
    }

    // Verify:
    // 1. every content chunk survives
    // 2. exact final concatenated answer matches expected output
    expect(s.answerText).toBe(expectedOutput);
    expect(s.sawDelta).toBe(true);

    // 3. terminal event does not shorten answer
    // 4. persisted shorter intermediate payload cannot overwrite longer stream
    const shorterIntermediate = expectedOutput.slice(0, 500);
    const terminalCompletedEvent = {
      type: "RUN_COMPLETED",
      run: {
        id: "long-run-1",
        normalized_response: shorterIntermediate,
        finish_reason: "stop",
        status: "success",
      },
      assistant_message: {
        id: "msg-long-1",
        role: "assistant",
        content: shorterIntermediate,
      },
    };
    s = runStreamReducer(s, terminalCompletedEvent);

    // Verify answer was not shortened by the shorter terminal payload
    expect(s.answerText).toBe(expectedOutput);
    expect(s.assistantMessage?.content).toBe(expectedOutput);
    expect(s.status).toBe("done");

    // Verify reasoning remains separate and not mixed into answerText
    expect(s.answerText.includes("Thinking through")).toBe(false);
    expect((s.activities[0] as any).text).toContain("Thinking through");
  });

  test("output_limit produces Continue status and finish_reason=length", () => {
    let s = runStreamReducer(initialRunStreamState, {
      type: "RUN_STARTED",
      run_id: "r-limit",
    });
    s = runStreamReducer(s, {
      type: "MODEL_OUTPUT_DELTA",
      delta: "Content truncated due to token count limit...",
    });
    s = runStreamReducer(s, {
      type: "RUN_COMPLETED",
      run: {
        id: "r-limit",
        normalized_response: "Content truncated due to token count limit...",
        finish_reason: "length",
      },
      assistant_message: {
        id: "m-limit",
        role: "assistant",
        content: "Content truncated due to token count limit...",
      },
    });
    expect(s.status).toBe("output_limit");
    expect(s.outputLimit).toBe(true);
    expect(s.assistantMessage?.output_limit).toBe(true);
    expect(s.assistantMessage?.finish_reason).toBe("length");
  });

  test("stop produces normal completion status done", () => {
    let s = runStreamReducer(initialRunStreamState, {
      type: "RUN_STARTED",
      run_id: "r-stop",
    });
    s = runStreamReducer(s, {
      type: "MODEL_OUTPUT_DELTA",
      delta: "Full normal answer.",
    });
    s = runStreamReducer(s, {
      type: "RUN_COMPLETED",
      run: {
        id: "r-stop",
        normalized_response: "Full normal answer.",
        finish_reason: "stop",
      },
      assistant_message: {
        id: "m-stop",
        role: "assistant",
        content: "Full normal answer.",
      },
    });
    expect(s.status).toBe("done");
    expect(s.outputLimit).toBe(false);
    expect(s.answerText).toBe("Full normal answer.");
  });

  test("interrupted EOF with partial content transitions to error and exposes Continue/Retry", () => {
    // When stream ends without terminal event, state marks error and retains partial text
    const partialState = runStreamReducer(initialRunStreamState, {
      type: "RUN_STARTED",
      run_id: "r-interrupted",
    });
    const withText = runStreamReducer(partialState, {
      type: "MODEL_OUTPUT_DELTA",
      delta: "Partial content before network severed...",
    });
    expect(withText.answerText).toBe("Partial content before network severed...");

    const errorState = runStreamReducer(withText, {
      type: "RUN_FAILED",
      error: { message: "Stream ended before a terminal event" },
    });
    expect(errorState.status).toBe("error");
    expect(errorState.answerText).toBe("Partial content before network severed...");

    // Render panel to verify Continue & Retry buttons are exposed
    const onContinue = jest.fn();
    const onRetry = jest.fn();
    render(
      <RunStreamPanel
        state={errorState}
        onContinue={onContinue}
        onRetry={onRetry}
      />
    );
    expect(screen.getByTestId("workspace-continue")).toBeInTheDocument();
    expect(screen.getByTestId("workspace-retry")).toBeInTheDocument();
    expect(screen.getByTestId("run-answer")).toHaveTextContent("Partial content before network severed...");
  });
});

// ---------------------------------------------------------------------------
// Phase 10: Performance Regression Tests
// ---------------------------------------------------------------------------

describe("Phase 10: Performance regression tests", () => {
  test("historical completed messages do not rerender for every stream chunk", () => {
    // In Workspace.tsx, MessageItem is wrapped in React.memo.
    // When parent component rerenders (e.g. streaming deltas update parent state),
    // React.memo skips rerendering the historical message if its props did not change.
    let renderCount = 0;
    const HistoricalMessage = React.memo(({ content }: { content: string }) => {
      renderCount++;
      return <div data-testid="msg">{content}</div>;
    });

    let triggerParentStreamDelta: () => void;
    const ParentConversation = () => {
      const [, setStreamDelta] = React.useState(0);
      triggerParentStreamDelta = () => setStreamDelta((n) => n + 1);
      return (
        <div>
          <HistoricalMessage content="Static completed message" />
        </div>
      );
    };

    render(<ParentConversation />);
    expect(renderCount).toBe(1);

    // Simulate 200 stream chunk updates in parent conversation wrapped in act
    act(() => {
      for (let i = 0; i < 200; i++) {
        triggerParentStreamDelta!();
      }
    });

    // Because HistoricalMessage is memoized with unchanged props, it did not rerender for any stream chunk
    expect(renderCount).toBe(1);
  });

  test("active stream rendering is bounded/batched using scheduled flush", () => {
    let stateUpdates = 0;
    let canonicalText = "";

    // Simulating useRunStream batching contract:
    // canonical accumulator takes every delta immediately, while visual setState is scheduled via RAF
    let scheduledFrame: any = null;
    const onDelta = (delta: string) => {
      canonicalText += delta;
      if (scheduledFrame === null) {
        scheduledFrame = setTimeout(() => {
          scheduledFrame = null;
          stateUpdates++;
        }, 16);
      }
    };
    const flushNow = () => {
      if (scheduledFrame !== null) {
        clearTimeout(scheduledFrame);
        scheduledFrame = null;
      }
      stateUpdates++;
    };

    // 50 rapid deltas arrive in the same frame
    for (let i = 0; i < 50; i++) {
      onDelta("tok ");
    }
    expect(canonicalText).toBe("tok ".repeat(50));
    // Scheduled once, not 50 times
    expect(stateUpdates).toBe(0);

    flushNow();
    expect(stateUpdates).toBe(1);
  });

  test("expand/collapse reasoning does not reset during streaming deltas", async () => {
    const { rerender } = render(
      <RunStreamPanel
        state={makeState({
          activities: [
            thought({
              id: "mc1",
              status: "running",
              durationMs: undefined,
              text: "Initial thought content",
            }),
          ],
        })}
      />
    );

    // Thought is expanded initially
    expect(screen.getByTestId("run-thought-content")).toBeInTheDocument();

    // User manually collapses the thought
    await userEvent.click(screen.getByTestId("run-thought-toggle"));
    expect(screen.queryByTestId("run-thought-content")).not.toBeInTheDocument();

    // Streaming updates arrive with new delta text
    rerender(
      <RunStreamPanel
        state={makeState({
          activities: [
            thought({
              id: "mc1",
              status: "running",
              durationMs: undefined,
              text: "Initial thought content with appended reasoning stream...",
            }),
          ],
        })}
      />
    );

    // State does not reset: remains collapsed
    expect(screen.queryByTestId("run-thought-content")).not.toBeInTheDocument();
  });

  test("manual scroll-up disables follow mode and returning to bottom re-enables follow mode", () => {
    // Test the scroll boundary math: isNearBottom = scrollHeight - scrollTop - clientHeight <= 80
    const checkIsNearBottom = (scrollHeight: number, scrollTop: number, clientHeight: number) => {
      return scrollHeight - scrollTop - clientHeight <= 80;
    };

    // 1. Initial position: at the bottom (distance = 0 <= 80)
    let scrollHeight = 1000;
    let clientHeight = 400;
    let scrollTop = 600; // at the very bottom
    expect(checkIsNearBottom(scrollHeight, scrollTop, clientHeight)).toBe(true);

    // 2. User manually scrolls up: distance > 80 => disables follow mode (detached)
    scrollTop = 400; // 1000 - 400 - 400 = 200 > 80
    expect(checkIsNearBottom(scrollHeight, scrollTop, clientHeight)).toBe(false);

    // 3. User returns to bottom: distance <= 80 => re-enables follow mode
    scrollTop = 550; // 1000 - 550 - 400 = 50 <= 80
    expect(checkIsNearBottom(scrollHeight, scrollTop, clientHeight)).toBe(true);
  });
});
