import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ContextLab from "@/pages/ContextLab";
import {
  api,
  compareContextPackVersions,
  createStarterContextPacks,
  freezeContextPackVersion,
  getRunAssembly,
  listContextBudgetPolicies,
  listContextCatalog,
  listContextPackVersions,
  listContextPacks,
  previewContextAssembly,
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
  listContextCatalog: jest.fn(),
  listContextPacks: jest.fn(),
  createContextPack: jest.fn(),
  updateContextPack: jest.fn(),
  deleteContextPack: jest.fn(),
  listContextPackVersions: jest.fn(),
  createContextPackVersion: jest.fn(),
  updateContextPackVersion: jest.fn(),
  freezeContextPackVersion: jest.fn(),
  deprecateContextPackVersion: jest.fn(),
  compareContextPackVersions: jest.fn(),
  createStarterContextPacks: jest.fn(),
  getRunAssembly: jest.fn(),
  previewContextAssembly: jest.fn(),
  diffRunContexts: jest.fn(),
  listContextBudgetPolicies: jest.fn(),
  createContextBudgetPolicy: jest.fn(),
  updateContextBudgetPolicy: jest.fn(),
  deleteContextBudgetPolicy: jest.fn(),
}));

const mockedApiGet = api.get as jest.Mock;
const mockedCatalog = listContextCatalog as jest.Mock;
const mockedPacks = listContextPacks as jest.Mock;
const mockedVersions = listContextPackVersions as jest.Mock;
const mockedStarters = createStarterContextPacks as jest.Mock;
const mockedFreeze = freezeContextPackVersion as jest.Mock;
const mockedCompare = compareContextPackVersions as jest.Mock;
const mockedPolicies = listContextBudgetPolicies as jest.Mock;
const mockedPreview = previewContextAssembly as jest.Mock;
const mockedAssembly = getRunAssembly as jest.Mock;

const now = "2026-01-01T00:00:00.000Z";

const catalog = {
  source_types: ["system_prompt", "file", "knowledge", "policy", "custom"],
  starter_packs: [{ key: "core", name: "Core", description: "Core pack", item_count: 3 }],
  budget_fields: ["max_context_tokens", "reserve_output_tokens"],
  pack_statuses: ["draft", "frozen", "deprecated", "archived"],
};

const packs = [
  {
    id: "pack-1",
    project_id: "p1",
    name: "Release guardrails",
    description: "Guardrails for releases",
    status: "active",
    latest_version: 2,
    created_at: now,
    updated_at: now,
  },
];

const versions = [
  {
    id: "pv-2",
    pack_id: "pack-1",
    version: 2,
    status: "draft",
    description: "Second",
    items: [{ key: "b", title: "B", source_type: "policy", content: "b" }],
    content_hash: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    created_at: now,
  },
  {
    id: "pv-1",
    pack_id: "pack-1",
    version: 1,
    status: "frozen",
    description: "First",
    items: [{ key: "a", title: "A", source_type: "system_prompt", content: "a" }],
    content_hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    created_at: now,
    frozen_at: now,
  },
];

const packCompare = {
  from: { id: "pv-1", version: 1 },
  to: { id: "pv-2", version: 2 },
  items: {
    added: ["new-item"],
    removed: ["old-item"],
    changed: [{ key: "changed-item", fields: [{ field: "content", from: "x", to: "y" }] }],
  },
  summary: { items_added: 1, items_removed: 1, items_changed: 1, content_changed: true, token_delta: 12 },
};

const budgetPolicies = [
  {
    id: "bp-1",
    project_id: "p1",
    name: "Default budget",
    max_context_tokens: 8000,
    reserve_output_tokens: 1000,
    knowledge_budget: 2000,
    history_budget: null,
    file_budget: null,
    policy_budget: null,
    status: "active",
    created_at: now,
  },
];

const previewResult = {
  assembly: {
    components: [
      { component_type: "SYSTEM_PROMPT", source_class: "system", ordering: 0, included: true, token_count: 120, estimated_tokens: 120, estimated_method: "heuristic" },
      { component_type: "RETRIEVED_CONTEXT", source_class: "knowledge", ordering: 1, included: true, token_count: null, estimated_tokens: null, estimated_method: null },
    ],
    excluded_components: [{ component_type: "HISTORY", source_class: "history", excluded_reason: "budget", estimated_tokens: 50 }],
    estimated_total_tokens: 120,
    estimated_method: "heuristic",
    known_token_count: 120,
    unknown_token_component_count: 1,
    context_hash: "cccccccccccccccccccccccccccccccc",
    strategy: "FULL",
    over_budget: false,
    warnings: [],
    budget_policy_applied: true,
    budget_excluded_count: 1,
  },
  budget_policy: { id: "bp-1", name: "Default budget" },
  applied_max_context_tokens: 8000,
  applied_reserved_output_tokens: 1000,
};

const runAssembly = {
  run_id: "run-1",
  available: true,
  components: [
    { component_type: "SYSTEM_PROMPT", source_class: "system", ordering: 0, included: true, token_count: 100, estimated_tokens: 100 },
    { component_type: "RETRIEVED_CONTEXT", source_class: "knowledge", ordering: 1, included: true, token_count: null, estimated_tokens: null, estimated_method: null, metadata: { deduplicated: true } },
    { component_type: "HISTORY", source_class: "history", ordering: 2, included: false, excluded_reason: "budget", estimated_tokens: 40, truncated: true, truncation_reason: "budget" },
  ],
  excluded_components: [{ component_type: "HISTORY", source_class: "history", excluded_reason: "budget", estimated_tokens: 40 }],
  sources_summary: [{ source_class: "system", count: 1, included_count: 1, estimated_tokens: 100 }],
  snapshot_reference: { id: "snap-1", available: true },
};

const runContextWithSnapshot = {
  available: true,
  run_id: "run-1",
  snapshot: {
    id: "snap-1",
    run_id: "run-1",
    components: [
      { component_type: "SYSTEM_PROMPT", ordering: 0, content: "hello", included: true, token_count: null, estimated_tokens: null },
    ],
    known_token_count: null,
    estimated_total_tokens: null,
    unknown_token_component_count: 1,
    created_at: now,
  },
};

const observability = {
  series: [],
  models: [],
  providers: [],
  totals: { requests: 0, failures: 0, avg_latency_ms: null, total_tokens: null, total_cost: null, cost_available: false },
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedCatalog.mockResolvedValue(catalog);
  mockedPacks.mockResolvedValue(packs);
  mockedVersions.mockResolvedValue(versions);
  mockedStarters.mockResolvedValue({ created: [], existing: [] });
  mockedFreeze.mockResolvedValue({});
  mockedCompare.mockResolvedValue(packCompare);
  mockedPolicies.mockResolvedValue(budgetPolicies);
  mockedPreview.mockResolvedValue(previewResult);
  mockedAssembly.mockResolvedValue(runAssembly);
  mockedApiGet.mockImplementation((url: string) => {
    if (url === "/observability") return Promise.resolve({ data: observability });
    if (url === "/context-lab/runs/run-1/assembly") return Promise.resolve({ data: runAssembly });
    if (url === "/runs/run-1/context") return Promise.resolve({ data: runContextWithSnapshot });
    if (url === "/runs/run-missing/context") return Promise.resolve({ data: { available: true, run_id: "run-missing", snapshot: null } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

function renderPage(entry = "/context-lab") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <ContextLab />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

async function openPacksAndExpand() {
  const user = userEvent.setup({ delay: null });
  renderPage();
  await user.click(screen.getByTestId("context-lab-tab-packs"));
  const expand = await screen.findByTestId("context-pack-expand");
  await user.click(expand);
  await screen.findAllByTestId("context-pack-version-row");
  return user;
}

function versionRowWithStatus(status: string): HTMLElement {
  const rows = screen.getAllByTestId("context-pack-version-row");
  const row = rows.find((r) => within(r).getByTestId("context-pack-version-status").textContent === status);
  if (!row) throw new Error(`no ${status} version row`);
  return row;
}

test("renders the context lab page and all four tabs", async () => {
  renderPage();
  expect(screen.getByTestId("context-lab-page")).toBeInTheDocument();
  expect(screen.getByTestId("context-lab-tab-inspector")).toBeInTheDocument();
  expect(screen.getByTestId("context-lab-tab-packs")).toBeInTheDocument();
  expect(screen.getByTestId("context-lab-tab-diff")).toBeInTheDocument();
  expect(screen.getByTestId("context-lab-tab-budget")).toBeInTheDocument();
});

test("packs tab lists packs and the starter action calls the helper", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();

  await user.click(screen.getByTestId("context-lab-tab-packs"));
  const rows = await screen.findAllByTestId("context-pack-row");
  expect(within(rows[0]).getByText("Release guardrails")).toBeInTheDocument();
  expect(mockedPacks).toHaveBeenCalledWith("p1");

  await user.click(screen.getByTestId("context-packs-starters"));
  await waitFor(() => expect(mockedStarters).toHaveBeenCalledWith("p1"));
});

test("the freeze action calls the helper for a draft pack version", async () => {
  const user = await openPacksAndExpand();

  await user.click(within(versionRowWithStatus("draft")).getByTestId("context-pack-version-freeze"));
  await waitFor(() => expect(mockedFreeze).toHaveBeenCalledWith("pack-1", "pv-2"));
  expect(mockedFreeze).toHaveBeenCalledTimes(1);
});

test("a pack version compare renders added and removed items", async () => {
  const user = await openPacksAndExpand();

  const compare = await screen.findByTestId("context-pack-compare");
  await waitFor(() => expect(compare).not.toBeDisabled());
  await user.click(compare);

  await waitFor(() => expect(mockedCompare).toHaveBeenCalledWith("pack-1", "pv-1", "pv-2"));

  const result = await screen.findByTestId("context-pack-compare-result");
  expect(within(result).getByTestId("context-pack-compare-added")).toHaveTextContent("new-item");
  expect(within(result).getByTestId("context-pack-compare-removed")).toHaveTextContent("old-item");
  expect(within(result).getByTestId("context-pack-compare-changed")).toHaveTextContent("changed-item");
  expect(within(result).getByTestId("context-pack-compare-summary")).toHaveTextContent("added: 1");
});

test("budget tab lists policies and the preview calls the helper and renders components", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();

  await user.click(screen.getByTestId("context-lab-tab-budget"));
  const rows = await screen.findAllByTestId("context-budget-policy-row");
  expect(within(rows[0]).getByText("Default budget")).toBeInTheDocument();
  expect(mockedPolicies).toHaveBeenCalledWith("p1");

  await user.click(screen.getByTestId("context-preview-submit"));
  await waitFor(() => expect(mockedPreview).toHaveBeenCalledWith(expect.objectContaining({ project_id: "p1" })));

  const components = await screen.findAllByTestId("context-preview-component");
  expect(components).toHaveLength(2);
  expect(within(components[0]).getByTestId("context-preview-component-source")).toHaveTextContent("system");
});

test("the inspector renders n/a for unknown tokens instead of zero", async () => {
  renderPage("/context-lab?run=run-1");

  const known = await screen.findByTestId("context-total-known");
  expect(known).toHaveTextContent("n/a");

  const assemblyToken = await screen.findByTestId("context-assembly-token-1");
  expect(assemblyToken).toHaveTextContent("n/a");
});

test("the inspector shows a clear no-snapshot state", async () => {
  renderPage("/context-lab?run=run-missing");

  const state = await screen.findByTestId("context-lab-no-snapshot");
  expect(state).toHaveTextContent("No context snapshot for this run");
  expect(screen.queryByTestId("context-totals")).not.toBeInTheDocument();
});
