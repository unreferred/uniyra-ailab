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
const mockedGetAgentRun = getAgentRun as jest.Mock;

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
    runtime_policy: { max_tool_rounds: 3 },
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

const runWithPermissions = {
  run: {
    id: "run-perm",
    status: "success",
    text: "done",
    normalized_response: null,
    latency_ms: 100,
    input_tokens: 5,
    output_tokens: 3,
    estimated_cost: null,
    cost_available: false,
    tool_call_count: 1,
  },
  run_id: "run-perm",
  trace_id: "t-1",
  compiled_prompt_id: null,
  agent_id: "a1",
  agent_version_id: "av1",
  permission_denials: [
    { kind: "tool_denied", resource_type: "tool", resource_id: "t-secret", name: "Secrets reader", reason: "denied by tool policy" },
  ],
  tool_policy_applied: {
    default_deny: true,
    allowed_tool_ids: ["t1"],
    denied_tool_ids: ["t-secret"],
    enabled_tool_ids: ["t1"],
    model_capability: { supports_tools: false },
  },
  knowledge_policy_applied: {
    knowledge_enabled: true,
    allowed_collection_ids: ["col1"],
    requested_collection_ids: ["col1", "col2"],
    retrieval_top_k: null,
    citation_required: false,
    knowledge_only_mode: false,
  },
  runtime_policy_applied: {
    applied: {},
    unsupported: [
      { field: "knowledge_only_mode", reason: "not_supported_by_runtime" },
      { field: "citation_required", reason: "evaluation_only_not_runtime_enforced" },
    ],
    params: {},
  },
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedCatalog.mockResolvedValue(catalog);
  mockedGetAgent.mockResolvedValue(agent);
  mockedListVersions.mockResolvedValue(versions);
  mockedGetUsage.mockResolvedValue(usage);
  mockedRunAgentVersion.mockResolvedValue(runWithPermissions);
  mockedListAgentRuns.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  mockedGetAgentRun.mockResolvedValue(runWithPermissions);
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

async function runAndGetResult() {
  const user = userEvent.setup({ delay: null });
  renderAt("/agents/a1");
  await screen.findAllByTestId("agent-version-row");
  const frozen = rowWithStatus("frozen");
  await user.click(within(frozen).getByTestId("agent-run-open"));
  expect(await screen.findByTestId("agent-run-dialog")).toBeInTheDocument();
  await user.type(screen.getByTestId("agent-run-task"), "Check permissions");
  await user.click(screen.getByTestId("agent-run-submit"));
  return screen.findByTestId("agent-run-result");
}

test("a denied tool renders a permission denial row with resource and reason", async () => {
  const result = await runAndGetResult();

  const permissions = within(result).getByTestId("agent-run-permissions");
  const denial = within(permissions).getByTestId("agent-run-denial-row");
  expect(denial).toHaveTextContent("tool_denied");
  expect(denial).toHaveTextContent("Secrets reader");
  expect(denial).toHaveTextContent("denied by tool policy");
  expect(within(permissions).queryByText("No permission denials")).not.toBeInTheDocument();
});

test("an empty denial list renders the no-denials message", async () => {
  mockedRunAgentVersion.mockResolvedValue({
    ...runWithPermissions,
    permission_denials: [],
  });

  const result = await runAndGetResult();
  const permissions = within(result).getByTestId("agent-run-permissions");
  expect(within(permissions).getByText("No permission denials")).toBeInTheDocument();
  expect(within(permissions).queryByTestId("agent-run-denial-row")).not.toBeInTheDocument();
});

test("model_capability.supports_tools false renders the tool use denied note", async () => {
  const result = await runAndGetResult();
  const note = within(result).getByTestId("agent-run-model-tools-note");
  expect(note).toHaveTextContent("Model does not support tools — tool use denied");
});

test("tool policy renders applied counts and enabled tool ids", async () => {
  const result = await runAndGetResult();
  const toolPolicy = within(result).getByTestId("agent-run-tool-policy");
  expect(toolPolicy).toHaveTextContent("default deny: Yes");
  expect(toolPolicy).toHaveTextContent("enabled tools: 1");
  expect(toolPolicy).toHaveTextContent("allowed / denied: 1 / 1");
  expect(within(toolPolicy).getByTestId("agent-run-tool-ids")).toHaveTextContent("t1");
});

test("unsupported policy fields merge without duplicates and render not runtime-enforced", async () => {
  const result = await runAndGetResult();
  const policyUnsupported = within(result).getByTestId("agent-run-policy-unsupported");
  expect(policyUnsupported).toHaveTextContent("knowledge_only_mode");
  expect(policyUnsupported).toHaveTextContent("citation_required");

  const knowledge = within(result).getByTestId("agent-run-knowledge-policy");
  expect(knowledge).toHaveTextContent("knowledge_only_mode: not runtime-enforced");
  expect(knowledge).toHaveTextContent("citation_required: not runtime-enforced");
});

test("missing retrieval_top_k renders n/a and knowledge counts come from the payload", async () => {
  const result = await runAndGetResult();
  const knowledge = within(result).getByTestId("agent-run-knowledge-policy");
  expect(knowledge).toHaveTextContent("retrieval top k: n/a");
  expect(knowledge).toHaveTextContent("knowledge enabled: Yes");
  expect(knowledge).toHaveTextContent("allowed collections: 1");
});

test("loading a past run via getAgentRun renders its permission denials", async () => {
  mockedListAgentRuns.mockResolvedValue({
    items: [{ id: "run-perm", run_id: "run-perm", status: "success", latency_ms: 100, created_at: now }],
    total: 1,
    limit: 20,
    offset: 0,
  });

  const user = userEvent.setup({ delay: null });
  renderAt("/agents/a1");
  await screen.findAllByTestId("agent-version-row");

  await user.click(screen.getByTestId("agent-run-runs-tab"));
  const view = await screen.findByTestId("agent-run-view");
  await user.click(view);

  await waitFor(() => expect(mockedGetAgentRun).toHaveBeenCalledWith("run-perm"));
  const dialog = await screen.findByTestId("agent-run-view-dialog");
  const denial = await within(dialog).findByTestId("agent-run-denial-row");
  expect(denial).toHaveTextContent("tool_denied");
  expect(denial).toHaveTextContent("Secrets reader");
});
