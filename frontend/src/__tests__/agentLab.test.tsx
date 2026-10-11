import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AgentLab from "@/pages/AgentLab";
import AgentDetail from "@/pages/AgentDetail";
import {
  api,
  cloneAgentVersion,
  compareAgentVersions,
  createAgent,
  createAgentVersion,
  deprecateAgentVersion,
  freezeAgentVersion,
  getAgent,
  getAgentCatalog,
  getAgentUsage,
  listAgentVersions,
  listAgents,
  listContextBudgetPolicies,
  listContextPackVersions,
  listContextPacks,
  listPromptVersions,
  listPrompts,
  updateAgent,
} from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
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
  listAgents: jest.fn(),
  createAgent: jest.fn(),
  getAgent: jest.fn(),
  updateAgent: jest.fn(),
  deleteAgent: jest.fn(),
  listAgentVersions: jest.fn(),
  createAgentVersion: jest.fn(),
  getAgentVersion: jest.fn(),
  updateAgentVersion: jest.fn(),
  freezeAgentVersion: jest.fn(),
  deprecateAgentVersion: jest.fn(),
  cloneAgentVersion: jest.fn(),
  compareAgentVersions: jest.fn(),
  getAgentUsage: jest.fn(),
  listPrompts: jest.fn(),
  listPromptVersions: jest.fn(),
  listContextPacks: jest.fn(),
  listContextPackVersions: jest.fn(),
  listContextBudgetPolicies: jest.fn(),
}));

const mockedApiGet = api.get as jest.Mock;
const mockedCatalog = getAgentCatalog as jest.Mock;
const mockedListAgents = listAgents as jest.Mock;
const mockedCreateAgent = createAgent as jest.Mock;
const mockedCreateAgentVersion = createAgentVersion as jest.Mock;
const mockedGetAgent = getAgent as jest.Mock;
const mockedUpdateAgent = updateAgent as jest.Mock;
const mockedListVersions = listAgentVersions as jest.Mock;
const mockedGetUsage = getAgentUsage as jest.Mock;
const mockedFreeze = freezeAgentVersion as jest.Mock;
const mockedDeprecate = deprecateAgentVersion as jest.Mock;
const mockedClone = cloneAgentVersion as jest.Mock;
const mockedCompare = compareAgentVersions as jest.Mock;
const mockedListPrompts = listPrompts as jest.Mock;
const mockedListPromptVersions = listPromptVersions as jest.Mock;
const mockedListPacks = listContextPacks as jest.Mock;
const mockedListPackVersions = listContextPackVersions as jest.Mock;
const mockedListBudgets = listContextBudgetPolicies as jest.Mock;

const now = "2026-01-01T00:00:00.000Z";

const catalog = {
  roles: ["engineer", "architect", "reviewer", "researcher", "analyst", "writer", "other"],
  statuses: ["active", "archived"],
  version_states: ["draft", "frozen", "deprecated"],
  output_formats: ["text", "json", "markdown"],
  routing_modes: ["fixed"],
};

const agents = [
  {
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
  },
  {
    id: "a2",
    project_id: "p1",
    name: "Draft analyst",
    description: null,
    role: null,
    purpose: null,
    responsibilities: [],
    non_responsibilities: [],
    status: "active",
    latest_version: null,
    created_at: now,
    updated_at: now,
  },
];

const models = [{ id: "m1", display_name: "Model One", family: "openai", enabled: true, model_identifier: "model-one", capabilities: {}, tags: [], notes: "" }];
const tools = [{ id: "t1", name: "calculator", display_name: "Calculator", enabled: true }];
const rubrics = [{ id: "r1", name: "Quality", description: "", dimensions: [], created_at: now, updated_at: now }];
const collections = [{ id: "col1", project_id: "p1", name: "Docs collection" }];
const prompts = [{ id: "ps1", project_id: "p1", name: "Research prompt", status: "active", versions_count: 2 }];
const promptVersions = [
  { id: "pv2", prompt_id: "ps1", version: 2, status: "draft" },
  { id: "pv1", prompt_id: "ps1", version: 1, status: "frozen" },
];
const packs = [{ id: "pack1", project_id: "p1", name: "Core pack" }];
const packVersions = [{ id: "cpv1", pack_id: "pack1", version: 1, status: "frozen" }];
const budgets = [{ id: "bud1", project_id: "p1", name: "Standard budget" }];

const versions = [
  {
    id: "av2",
    agent_id: "a1",
    project_id: "p1",
    version: 2,
    status: "draft",
    model_id: "m1",
    prompt_version_id: "pv1",
    context_policy: { context_pack_version_ids: ["cpv1"], budget_policy_id: "bud1", strategy: "FULL", context_options: {} },
    tool_policy: { default_deny: true, allowed_tool_ids: ["t1"], denied_tool_ids: [], mode: "allowlist" },
    knowledge_policy: { knowledge_enabled: true, allowed_collection_ids: ["col1"], retrieval_top_k: 5, citation_required: false, knowledge_only_mode: false },
    runtime_policy: { temperature: 0.2 },
    output_contract: { format: "text" },
    evaluation_profile: { evaluator_keys: ["assertions"] },
    routing: { mode: "fixed" },
    metadata: {},
    content_hash: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    created_at: now,
    frozen_at: null,
    deprecated_at: null,
  },
  {
    id: "av1",
    agent_id: "a1",
    project_id: "p1",
    version: 1,
    status: "frozen",
    model_id: null,
    prompt_version_id: null,
    context_policy: null,
    tool_policy: null,
    knowledge_policy: null,
    runtime_policy: null,
    output_contract: null,
    evaluation_profile: null,
    routing: null,
    metadata: null,
    content_hash: null,
    created_at: now,
    frozen_at: now,
    deprecated_at: null,
  },
];

const usage = {
  agent_id: "a1",
  version_id: "av1",
  runs: [{ run_id: "run-1", experiment_id: "exp-1", status: "success", created_at: now }],
  evaluations: [{ id: "ev-1", run_id: "run-1", outcome: "PASS", evaluator_type: "rubric" }],
  findings: [{ id: "f-1", failure_domain: "grounding", taxonomy_code: "HALLUCINATION", severity: "high", status: "open" }],
  scorecards: [],
};

const compareResult = {
  from: { id: "av1", version: 1, status: "frozen" },
  to: { id: "av2", version: 2, status: "draft" },
  fields: [
    { field: "model_id", changed: true, from: null, to: "m1" },
    { field: "prompt_version_id", changed: true, from: null, to: "pv1" },
  ],
  policy_diff: {
    context: { added: ["cpv1"], removed: [], changed: [] },
    tools: { allowed_added: ["t1"], allowed_removed: [], denied_added: [], denied_removed: [] },
    knowledge: { added: ["col1"], removed: [], changed: [] },
    runtime: { changed_fields: [{ field: "temperature", from: null, to: 0.2 }] },
    output_contract: { changed_fields: [{ field: "format", from: "text", to: "text" }] },
    evaluation: { changed_fields: [] },
  },
  summary: { fields_changed: 2 },
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedCatalog.mockResolvedValue(catalog);
  mockedListAgents.mockResolvedValue(agents);
  mockedCreateAgent.mockResolvedValue({ id: "a-new", name: "Release reviewer" });
  mockedCreateAgentVersion.mockResolvedValue({ id: "av-new" });
  mockedGetAgent.mockImplementation((id: string) =>
    Promise.resolve(id === "a2" ? agents[1] : agents[0])
  );
  mockedUpdateAgent.mockResolvedValue({});
  mockedListVersions.mockImplementation((id: string) => Promise.resolve(id === "a2" ? [] : versions));
  mockedGetUsage.mockResolvedValue(usage);
  mockedFreeze.mockResolvedValue({});
  mockedDeprecate.mockResolvedValue({});
  mockedClone.mockResolvedValue({});
  mockedCompare.mockResolvedValue(compareResult);
  mockedListPrompts.mockResolvedValue(prompts);
  mockedListPromptVersions.mockResolvedValue(promptVersions);
  mockedListPacks.mockResolvedValue(packs);
  mockedListPackVersions.mockResolvedValue(packVersions);
  mockedListBudgets.mockResolvedValue(budgets);

  mockedApiGet.mockImplementation((url: string) => {
    if (url === "/models") return Promise.resolve({ data: models });
    if (url === "/rubrics") return Promise.resolve({ data: rubrics });
    if (url === "/tools") return Promise.resolve({ data: { items: tools, total: tools.length } });
    if (url === "/knowledge") return Promise.resolve({ data: collections });
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
          <Route path="/agents" element={<AgentLab />} />
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

test("the list renders real agent rows with role, status and latest version", async () => {
  renderAt("/agents");

  expect(await screen.findByTestId("agent-lab-page")).toBeInTheDocument();
  const rows = await screen.findAllByTestId("agent-row");
  expect(rows).toHaveLength(2);
  expect(within(rows[0]).getByTestId("agent-name")).toHaveTextContent("Release reviewer");
  expect(within(rows[0]).getByTestId("agent-role")).toHaveTextContent("reviewer");
  expect(within(rows[0]).getByTestId("agent-latest-version")).toHaveTextContent("v2");
  expect(mockedListAgents).toHaveBeenCalledWith("p1", false);
});

test("missing latest_version renders n/a in the list", async () => {
  renderAt("/agents");
  await screen.findAllByTestId("agent-row");
  const latest = screen.getAllByTestId("agent-latest-version");
  expect(latest.some((node) => node.textContent === "n/a")).toBe(true);
});

test("the wizard advances through all nine steps and Create posts agent + version", async () => {
  const user = userEvent.setup({ delay: null });
  renderAt("/agents");
  await screen.findAllByTestId("agent-row");

  await user.click(screen.getByTestId("agent-create"));
  expect(await screen.findByTestId("agent-create-dialog")).toBeInTheDocument();
  expect(screen.getByTestId("agent-wizard-step")).toHaveAttribute("data-step", "0");

  await user.type(screen.getByTestId("agent-name-input"), "Release reviewer");

  for (let i = 1; i <= 8; i += 1) {
    await user.click(screen.getByTestId("agent-create-next"));
    await waitFor(() =>
      expect(screen.getByTestId("agent-wizard-step")).toHaveAttribute("data-step", String(i))
    );
  }

  const submit = screen.getByTestId("agent-create-submit");
  expect(submit).not.toBeDisabled();
  await user.click(submit);

  await waitFor(() => expect(mockedCreateAgent).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(mockedCreateAgentVersion).toHaveBeenCalledTimes(1));
  expect(mockedCreateAgent).toHaveBeenCalledWith(
    expect.objectContaining({ project_id: "p1", name: "Release reviewer", role: "engineer" })
  );
  expect(mockedCreateAgentVersion).toHaveBeenCalledWith(
    "a-new",
    expect.objectContaining({
      model_id: "m1",
      routing: { mode: "fixed" },
      tool_policy: expect.objectContaining({ default_deny: true, mode: "allowlist" }),
    })
  );
});

test("the detail page renders version rows and the required tabs", async () => {
  renderAt("/agents/a1");

  expect(await screen.findByTestId("agent-detail-page")).toBeInTheDocument();
  const rows = await screen.findAllByTestId("agent-version-row");
  expect(rows).toHaveLength(2);
  expect(within(rowWithStatus("frozen")).getByText("v1")).toBeInTheDocument();
  expect(within(rowWithStatus("draft")).getByText("v2")).toBeInTheDocument();

  expect(screen.getByTestId("agent-tab-overview")).toBeInTheDocument();
  expect(screen.getByTestId("agent-tab-versions")).toBeInTheDocument();
  expect(screen.getByTestId("agent-tab-runs")).toBeInTheDocument();
  expect(screen.getByTestId("agent-tab-evaluations")).toBeInTheDocument();
  expect(screen.getByTestId("agent-tab-findings")).toBeInTheDocument();
});

test("missing model, prompt and hash render n/a in a version row", async () => {
  renderAt("/agents/a1");
  await screen.findAllByTestId("agent-version-row");

  const frozen = rowWithStatus("frozen");
  expect(within(frozen).getByTestId("agent-version-model")).toHaveTextContent("n/a");
  expect(within(frozen).getByTestId("agent-version-prompt")).toHaveTextContent("n/a");
  expect(within(frozen).getByTestId("agent-version-hash")).toHaveTextContent("n/a");
});

test("the Runs tab reads getAgentUsage and renders a real run row", async () => {
  const user = userEvent.setup({ delay: null });
  renderAt("/agents/a1");
  await screen.findAllByTestId("agent-version-row");

  await user.click(screen.getByTestId("agent-tab-runs"));

  const runRow = await screen.findByTestId("agent-usage-run");
  expect(within(runRow).getByRole("link", { name: "run-1" })).toHaveAttribute("href", "/trace?run=run-1");
  expect(within(runRow).getByRole("link", { name: "exp-1" })).toHaveAttribute("href", "/experiments/exp-1");
  await waitFor(() => expect(mockedGetUsage).toHaveBeenCalledWith("a1", "av1"));
});

test("the Evaluations and Findings tabs render real usage rows", async () => {
  const user = userEvent.setup({ delay: null });
  renderAt("/agents/a1");
  await screen.findAllByTestId("agent-version-row");

  await user.click(screen.getByTestId("agent-tab-evaluations"));
  const evaluation = await screen.findByTestId("agent-usage-evaluation");
  expect(evaluation).toHaveTextContent("PASS");

  await user.click(screen.getByTestId("agent-tab-findings"));
  const finding = await screen.findByTestId("agent-usage-finding");
  expect(finding).toHaveTextContent("HALLUCINATION");
});

test("the compare panel renders field and policy diffs from the backend", async () => {
  const user = userEvent.setup({ delay: null });
  renderAt("/agents/a1");
  await screen.findAllByTestId("agent-version-row");

  await waitFor(() => expect(screen.getByTestId("agent-compare")).not.toBeDisabled());
  await user.click(screen.getByTestId("agent-compare"));

  await waitFor(() => expect(mockedCompare).toHaveBeenCalledWith("a1", "av1", "av2"));
  const result = await screen.findByTestId("agent-compare-result");
  expect(within(result).getByTestId("agent-compare-fields-changed")).toHaveTextContent("2");
  expect(within(result).getByTestId("agent-policy-diff-context")).toHaveTextContent("added: 1");
  expect(within(result).getByTestId("agent-policy-diff-tools")).toHaveTextContent("allowed added: 1");
});

test("there is no Scorecards tab on the agent detail page", async () => {
  renderAt("/agents/a1");
  await screen.findByTestId("agent-detail-page");
  expect(screen.queryByTestId("agent-tab-scorecards")).not.toBeInTheDocument();
  expect(screen.queryByText(/scorecards/i)).not.toBeInTheDocument();
});
