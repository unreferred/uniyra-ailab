import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Workspace from "@/pages/Workspace";
import {
  analyzePlanningImpact,
  api,
  getPlanningGraph,
  getPlanningGraphNode,
  getPlanningState,
  getPrdReadiness,
  listPrdVersions,
  uploadAttachments,
} from "@/lib/api";
import {
  ModelRecord,
  PlanningGraph,
  PlanningGraphNode,
  PlanningState,
  ToolRecord,
} from "@/lib/types";

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

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  apiError: (e: any) => (e && e.message) || "error",
  API_BASE: "/api",
  csrfHeader: () => ({}),
  uploadAttachments: jest.fn(),
  analyzePromptCapture: jest.fn(),
  savePromptCapture: jest.fn(),
  getWorkspaceModes: jest.fn(),
  listPromptCaptures: jest.fn(),
  getPlanningStateCatalog: jest.fn(),
  getPlanningState: jest.fn(),
  ingestPlanningConversation: jest.fn(),
  createPlanningItem: jest.fn(),
  updatePlanningItem: jest.fn(),
  reopenPlanningItem: jest.fn(),
  verifyPlanningItem: jest.fn(),
  listPlanningConflicts: jest.fn(),
  resolvePlanningConflict: jest.fn(),
  checkPlanningQuestions: jest.fn(),
  generateConsolidatedPlan: jest.fn(),
  getPlanningGraph: jest.fn(),
  syncPlanningGraph: jest.fn(),
  getPlanningGraphCatalog: jest.fn(),
  getPlanningGraphNode: jest.fn(),
  clearPlanningGraphReview: jest.fn(),
  analyzePlanningImpact: jest.fn(),
  getPrdReadiness: jest.fn(),
  generatePrd: jest.fn(),
  listPrdVersions: jest.fn(),
  getPrdVersion: jest.fn(),
  comparePrdVersions: jest.fn(),
  approvePrdVersion: jest.fn(),
}));

const mockedGet = api.get as jest.Mock;
const mockedPost = api.post as jest.Mock;
const mockedPatch = api.patch as jest.Mock;
const mockedDelete = api.delete as jest.Mock;
const mockedGetPlanningState = getPlanningState as jest.Mock;
const mockedGetPlanningGraph = getPlanningGraph as jest.Mock;
const mockedGetPlanningGraphNode = getPlanningGraphNode as jest.Mock;
const mockedAnalyzeImpact = analyzePlanningImpact as jest.Mock;
const mockedReadiness = getPrdReadiness as jest.Mock;
const mockedListVersions = listPrdVersions as jest.Mock;
const mockedUpload = uploadAttachments as jest.Mock;

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

function makeConversation(mode: string) {
  return {
    id: "c1",
    project_id: "p1",
    title: "Planning source",
    system_prompt: "",
    workspace_mode: mode,
    created_at: now,
    updated_at: now,
    messages: [
      {
        id: "m1",
        conversation_id: "c1",
        role: "user",
        content: "We are building a field app.",
        created_at: now,
      },
      {
        id: "m2",
        conversation_id: "c1",
        role: "assistant",
        content: "## Plan\nUse SQLite",
        model_id: "m1",
        model_name: "Model One",
        created_at: now,
      },
    ],
  };
}

let conversation = makeConversation("plan");

const planningState: PlanningState = {
  project_id: "p1",
  conversation_id: "c1",
  confirmed_decisions: [
    {
      id: "dec1",
      topic_key: "storage.engine",
      category: "decision",
      kind: "decision",
      value: "Use SQLite for local storage",
      status: "CONFIRMED_BY_USER",
    },
  ],
  assumptions: [
    {
      id: "asm1",
      topic_key: "platform.ios",
      kind: "assumption",
      value: "Might need iOS later",
      status: "ASSUMED_BY_MODEL",
    },
  ],
  counts: { confirmed_decisions: 1, assumptions: 1 },
};

const sectionNode: PlanningGraphNode = {
  id: "n-sec",
  project_id: "p1",
  node_key: "section.persistence",
  type: "SECTION",
  title: "Persistence",
  content: "Storage decisions",
  order: 0,
};

const confirmedNode: PlanningGraphNode = {
  id: "n-conf",
  project_id: "p1",
  node_key: "decision.storage.engine",
  type: "DECISION",
  title: "Use SQLite",
  content: "SQLite for local storage",
  status: "CONFIRMED_BY_USER",
  decision_id: "dec1",
  topic_key: "storage.engine",
  category: "decision",
  section_key: "section.persistence",
  conversation_id: "c1",
  order: 0,
  review_required: false,
};

const graph: PlanningGraph = {
  project_id: "p1",
  conversation_id: "c1",
  schema_version: "v1",
  nodes: [sectionNode, confirmedNode],
  edges: [{ id: "e1", source_node_id: "n-sec", target_node_id: "n-conf", relation: "contains" }],
  sections: [
    { key: "section.persistence", title: "Persistence", node_id: "n-sec", counts: { DECISION: 1 } },
  ],
  counts: { CONFIRMED_BY_USER: 1 },
};

const impact = {
  project_id: "p1",
  message: "Storage changes ripple through the plan",
  affected_areas: [
    {
      node_id: "n-asm",
      section_key: "section.persistence",
      title: "Persistence",
      reason: "Recompilation needed",
      review_required: true,
    },
  ],
  count: 1,
};

beforeEach(() => {
  jest.clearAllMocks();
  conversation = makeConversation("plan");

  mockedGetPlanningState.mockResolvedValue(planningState);
  mockedGetPlanningGraph.mockResolvedValue(graph);
  mockedGetPlanningGraphNode.mockResolvedValue({
    node: confirmedNode,
    decision: { id: "dec1", status: "CONFIRMED_BY_USER", value: "Use SQLite for local storage" },
    provenance: null,
    dependencies: [],
    dependents: [],
    affected: [],
    history: [],
  });
  mockedAnalyzeImpact.mockResolvedValue(impact);
  mockedReadiness.mockResolvedValue({
    project_id: "p1",
    conversation_id: "c1",
    dimensions: [],
    blockers: [],
    blocker_count: 0,
    ready: false,
    summary: "",
  });
  mockedListVersions.mockResolvedValue({ versions: [], latest_version: null });
  mockedPost.mockResolvedValue({ data: {} });
  mockedPatch.mockResolvedValue({ data: {} });
  mockedDelete.mockResolvedValue({ data: {} });

  mockedGet.mockImplementation((url: string) => {
    if (url === "/models") return Promise.resolve({ data: [model] });
    if (url === "/tools") return Promise.resolve({ data: { items: [tool], total: 1 } });
    if (url === "/code-interpreter/status")
      return Promise.resolve({ data: { available: true, reason: null } });
    if (url === "/attachments") {
      return Promise.resolve({ data: { items: [], total: 0, limit: 50, offset: 0 } });
    }
    if (url === "/planning-state") return Promise.resolve({ data: planningState });
    if (url.startsWith("/conversations/") && url.includes("stream-support")) {
      return Promise.resolve({ data: { supports_stream: false } });
    }
    if (url.startsWith("/conversations?")) return Promise.resolve({ data: [conversation] });
    if (url === "/conversations/c1") return Promise.resolve({ data: conversation });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });

  mockedUpload.mockResolvedValue([]);
});

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

async function enterPlanMode() {
  return screen.findByTestId("workspace-plan-tabs");
}

test("plan mode renders the four plan tabs and defaults to chat", async () => {
  renderWorkspace();

  expect(await enterPlanMode()).toBeInTheDocument();
  for (const key of ["chat", "plan-map", "prd", "decisions"]) {
    expect(screen.getByTestId(`workspace-plan-tab-${key}`)).toBeInTheDocument();
  }

  expect(screen.getByTestId("workspace-plan-chat")).toBeInTheDocument();
  expect(screen.getByTestId("workspace-input")).toBeInTheDocument();
  expect(screen.getByTestId("workspace-plan-split")).toBeDisabled();
});

test("chat mode does not render the plan tab strip", async () => {
  conversation = makeConversation("chat");
  renderWorkspace();

  await waitFor(() =>
    expect(screen.getByTestId("workspace-model-select")).toHaveTextContent("Model One")
  );
  expect(screen.queryByTestId("workspace-plan-tabs")).toBeNull();
  expect(screen.queryByTestId("workspace-plan-tab-plan-map")).toBeNull();
  expect(screen.queryByTestId("workspace-plan-chat")).toBeNull();
  expect(screen.getByTestId("workspace-input")).toBeInTheDocument();
});

test("selecting the Plan Map tab renders the real plan map and hides the composer", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();
  await enterPlanMode();

  await user.click(screen.getByTestId("workspace-plan-tab-plan-map"));

  expect(await screen.findByTestId("workspace-plan-plan-map")).toBeInTheDocument();
  expect(await screen.findByTestId("plan-map")).toBeInTheDocument();
  expect(await screen.findByTestId("plan-map-node-decision.storage.engine")).toBeInTheDocument();
  expect(screen.queryByTestId("workspace-input")).toBeNull();
  expect(mockedGetPlanningGraph).toHaveBeenCalledWith("p1", "c1");
});

test("selecting the PRD tab renders the PRD view and hides the composer", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();
  await enterPlanMode();

  await user.click(screen.getByTestId("workspace-plan-tab-prd"));

  expect(await screen.findByTestId("workspace-plan-prd")).toBeInTheDocument();
  expect(await screen.findByTestId("prd-view")).toBeInTheDocument();
  expect(await screen.findByTestId("prd-empty")).toBeInTheDocument();
  expect(screen.queryByTestId("workspace-input")).toBeNull();
});

test("selecting the Decisions tab renders the inline decisions view and planning groups", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();
  await enterPlanMode();

  await user.click(screen.getByTestId("workspace-plan-tab-decisions"));

  expect(await screen.findByTestId("workspace-plan-decisions")).toBeInTheDocument();
  expect(await screen.findByTestId("workspace-plan-decisions-view")).toBeInTheDocument();
  expect(await screen.findByTestId("planning-group-confirmed")).toBeInTheDocument();
  expect(await screen.findByTestId("planning-group-assumptions")).toBeInTheDocument();
  expect(mockedGetPlanningState).toHaveBeenCalledWith("p1", "c1");
  expect(screen.queryByTestId("workspace-input")).toBeNull();
});

test("split toggle shows chat and the selected panel side-by-side and toggles back", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();
  await enterPlanMode();

  await user.click(screen.getByTestId("workspace-plan-tab-plan-map"));

  const split = screen.getByTestId("workspace-plan-split");
  expect(split).not.toBeDisabled();
  await user.click(split);

  expect(await screen.findByTestId("workspace-plan-split-active")).toBeInTheDocument();
  expect(screen.getByTestId("workspace-plan-chat")).toBeInTheDocument();
  expect(screen.getByTestId("workspace-plan-plan-map")).toBeInTheDocument();
  expect(screen.getByTestId("plan-map")).toBeInTheDocument();
  expect(screen.getByTestId("workspace-input")).toBeInTheDocument();

  await user.click(screen.getByTestId("workspace-plan-split"));

  await waitFor(() => expect(screen.queryByTestId("workspace-plan-split-active")).toBeNull());
  expect(screen.queryByTestId("workspace-plan-chat")).toBeNull();
  expect(screen.getByTestId("workspace-plan-plan-map")).toBeInTheDocument();
  expect(screen.queryByTestId("workspace-input")).toBeNull();
});

test("analyze impact on a decision node renders the affected areas and refreshes the graph", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();
  await enterPlanMode();

  await user.click(screen.getByTestId("workspace-plan-tab-plan-map"));
  await user.click(await screen.findByTestId("plan-map-node-decision.storage.engine"));

  const before = mockedGetPlanningGraph.mock.calls.length;
  const analyze = await screen.findByTestId("plan-map-analyze-impact");
  await user.click(analyze);

  await waitFor(() =>
    expect(mockedAnalyzeImpact).toHaveBeenCalledWith({ project_id: "p1", node_id: "n-conf" })
  );
  const panel = await screen.findByTestId("plan-map-impact");
  expect(panel).toHaveTextContent("Storage changes ripple through the plan");
  expect(panel).toHaveTextContent("Persistence");
  expect(panel).toHaveTextContent("Recompilation needed");
  await waitFor(() => expect(mockedGetPlanningGraph.mock.calls.length).toBeGreaterThan(before));
});
