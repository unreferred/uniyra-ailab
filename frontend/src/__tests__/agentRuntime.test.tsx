import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AgentDetail from "@/pages/AgentDetail";
import {
  api,
  cloneAgentVersion,
  compareAgentVersions,
  createAgentVersion,
  deprecateAgentVersion,
  freezeAgentVersion,
  getAgent,
  getAgentCatalog,
  getAgentRun,
  getAgentUsage,
  listAgentRuns,
  listAgentVersions,
  runAgentVersion,
  updateAgent,
} from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
}));

jest.mock("@/components/common/Markdown", () => ({
  MarkdownView: ({ content }: { content: string }) => <div data-testid="markdown-view">{content}</div>,
}));

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({
    activeId: "p1",
    activeProject: { id: "p1", name: "Project one" },
    projects: [{ id: "p1", name: "Project one" }],
    isLoading: false,
    setActiveId: jest.fn(),
    refresh: jest.fn(),
  }),
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  getAgentCatalog: jest.fn(),
  getAgent: jest.fn(),
  updateAgent: jest.fn(),
  listAgentVersions: jest.fn(),
  createAgentVersion: jest.fn(),
  freezeAgentVersion: jest.fn(),
  deprecateAgentVersion: jest.fn(),
  cloneAgentVersion: jest.fn(),
  compareAgentVersions: jest.fn(),
  getAgentUsage: jest.fn(),
  runAgentVersion: jest.fn(),
  listAgentRuns: jest.fn(),
  getAgentRun: jest.fn(),
}));

const mockedApiGet = api.get as jest.Mock;
const mockedCatalog = getAgentCatalog as jest.Mock;
const mockedGetAgent = getAgent as jest.Mock;
const mockedListVersions = listAgentVersions as jest.Mock;
const mockedGetUsage = getAgentUsage as jest.Mock;
const mockedRunAgentVersion = runAgentVersion as jest.Mock;
const mockedListAgentRuns = listAgentRuns as jest.Mock;

const now = "2026-01-01T00:00:00.000Z";

const catalog = {
  roles: ["engineer", "reviewer"],
  statuses: ["active", "archived"],
  version_states: ["draft", "frozen", "deprecated"],
};

const agent = {
  id: "a1",
  project_id: "p1",
  name: "Release reviewer",
  description: "Reviews releases",
  role: "reviewer",
  purpose: "Review release candidates",
  responsibilities: ["review diffs"],
  non_responsibilities: ["deploy"],
  status: "active",
  latest_version: 2,
  created_at: now,
  updated_at: now,
};

const models = [
  { id: "m1", display_name: "Model One", family: "openai", enabled: true, model_identifier: "model-one", capabilities: {}, tags: [], notes: "" },
];

const versions = [
  {
    id: "av2",
    agent_id: "a1",
    project_id: "p1",
    version: 2,
    status: "draft",
    model_id: "m1",
    prompt_version_id: "pv1",
    content_hash: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    created_at: now,
  },
  {
    id: "av1",
    agent_id: "a1",
    project_id: "p1",
    version: 1,
    status: "frozen",
    model_id: "m1",
    prompt_version_id: null,
    runtime_policy: { max_tool_rounds: 3, reasoning_effort: "high" },
    content_hash: null,
    created_at: now,
    frozen_at: now,
  },
];

const usage = {
  agent_id: "a1",
  version_id: "av1",
  runs: [],
  evaluations: [],
  findings: [],
  scorecards: [],
};

const runResult = {
  run: {
    id: "run-9",
    status: "success",
    text: "**Hello** from the runtime",
    normalized_response: null,
    latency_ms: 321,
    input_tokens: 12,
    output_tokens: 7,
    estimated_cost: null,
    cost_available: false,
    tool_call_count: 4,
  },
  run_id: "run-9",
  trace_id: "t-1",
  compiled_prompt_id: "cp-1",
  agent_id: "a1",
  agent_version_id: "av1",
  runtime_policy_applied: {
    applied: { temperature: 0.2, max_tool_rounds: 3 },
    unsupported: [
      { field: "max_tool_rounds", reason: "not enforced by this runtime" },
      { field: "reasoning_effort", reason: "provider does not support it" },
    ],
    params: { temperature: 0.2 },
  },
  output_contract: { format: "text" },
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedCatalog.mockResolvedValue(catalog);
  mockedGetAgent.mockResolvedValue(agent);
  mockedListVersions.mockResolvedValue(versions);
  mockedGetUsage.mockResolvedValue(usage);
  mockedRunAgentVersion.mockResolvedValue(runResult);
  mockedListAgentRuns.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  mockedApiGet.mockImplementation((url: string) => {
    if (url === "/models") return Promise.resolve({ data: models });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

function renderAt(entry: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/agents/:id" element={<AgentDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function rowWithStatus(status: string): HTMLElement {
  const rows = screen.getAllByTestId("agent-version-row");
  const row = rows.find((r) => within(r).getByTestId("agent-version-status").textContent === status);
  if (!row) throw new Error(`no ${status} version row`);
  return row;
}

async function openRunDialog() {
  const user = userEvent.setup({ delay: null });
  renderAt("/agents/a1");
  await screen.findAllByTestId("agent-version-row");
  const frozen = rowWithStatus("frozen");
  await user.click(within(frozen).getByTestId("agent-run-open"));
  expect(await screen.findByTestId("agent-run-dialog")).toBeInTheDocument();
  return user;
}

test("a frozen row exposes Run while a draft row does not", async () => {
  renderAt("/agents/a1");
  await screen.findAllByTestId("agent-version-row");

  expect(within(rowWithStatus("frozen")).getByTestId("agent-run-open")).toBeInTheDocument();
  expect(within(rowWithStatus("draft")).queryByTestId("agent-run-open")).not.toBeInTheDocument();
});

test("submitting the dialog calls runAgentVersion with the task and renders the real result", async () => {
  const user = await openRunDialog();

  await user.type(screen.getByTestId("agent-run-task"), "Summarize the release notes");
  await user.click(screen.getByTestId("agent-run-submit"));

  await waitFor(() =>
    expect(mockedRunAgentVersion).toHaveBeenCalledWith(
      expect.objectContaining({
        project_id: "p1",
        agent_id: "a1",
        agent_version_id: "av1",
        task: "Summarize the release notes",
      })
    )
  );

  const result = await screen.findByTestId("agent-run-result");
  expect(within(result).getByTestId("agent-run-status")).toHaveTextContent("success");

  const traceLink = within(result).getByTestId("agent-run-trace-link");
  expect(traceLink).toHaveAttribute("href", "/trace?run=run-9");
  expect(traceLink.getAttribute("href")).toContain("run-9");

  expect(within(result).getByTestId("agent-run-output")).toHaveTextContent("Hello");
  expect(within(result).getByTestId("agent-run-tool-calls")).toHaveTextContent("4");
});

test("unsupported runtime fields render with their reasons and are never presented as applied", async () => {
  const user = await openRunDialog();
  await user.type(screen.getByTestId("agent-run-task"), "Check the boundary");
  await user.click(screen.getByTestId("agent-run-submit"));

  const unsupported = await screen.findByTestId("agent-run-unsupported");
  expect(unsupported).toHaveTextContent("Not applied by runtime");
  expect(unsupported).toHaveTextContent("max_tool_rounds");
  expect(unsupported).toHaveTextContent("not enforced by this runtime");
  expect(unsupported).toHaveTextContent("reasoning_effort");
  expect(unsupported).toHaveTextContent("provider does not support it");

  const applied = screen.getByTestId("agent-run-applied");
  expect(applied).toHaveTextContent("temperature");
});

test("missing cost, tokens and tool call count render n/a", async () => {
  mockedRunAgentVersion.mockResolvedValue({
    run: {
      id: "run-10",
      status: "success",
      text: null,
      normalized_response: null,
      latency_ms: null,
      input_tokens: null,
      output_tokens: null,
      estimated_cost: null,
      cost_available: false,
      tool_call_count: null,
    },
    run_id: "run-10",
    trace_id: null,
    compiled_prompt_id: null,
    runtime_policy_applied: { applied: {}, unsupported: [], params: {} },
  });

  const user = await openRunDialog();
  await user.type(screen.getByTestId("agent-run-task"), "Do the thing");
  await user.click(screen.getByTestId("agent-run-submit"));

  const result = await screen.findByTestId("agent-run-result");
  expect(within(result).getByTestId("agent-run-cost")).toHaveTextContent("n/a");
  expect(within(result).getByTestId("agent-run-input-tokens")).toHaveTextContent("n/a");
  expect(within(result).getByTestId("agent-run-output-tokens")).toHaveTextContent("n/a");
  expect(within(result).getByTestId("agent-run-tool-calls")).toHaveTextContent("n/a");
});

test("the Runs tab lists agent runtime runs with a trace link", async () => {
  mockedListAgentRuns.mockResolvedValue({
    items: [
      { id: "run-77", run_id: "run-77", status: "success", latency_ms: 150, created_at: now },
    ],
    total: 1,
    limit: 20,
    offset: 0,
  });

  const user = userEvent.setup({ delay: null });
  renderAt("/agents/a1");
  await screen.findAllByTestId("agent-version-row");

  expect(screen.getByTestId("agent-run-runs-tab")).toBeInTheDocument();
  await user.click(screen.getByTestId("agent-run-runs-tab"));

  const row = await screen.findByTestId("agent-run-row");
  expect(within(row).getByTestId("agent-run-row-status")).toHaveTextContent("success");
  expect(within(row).getByRole("link", { name: "Trace" })).toHaveAttribute("href", "/trace?run=run-77");
  await waitFor(() =>
    expect(mockedListAgentRuns).toHaveBeenCalledWith(
      expect.objectContaining({ agent_id: "a1", agent_version_id: "av1", project_id: "p1" })
    )
  );
});
