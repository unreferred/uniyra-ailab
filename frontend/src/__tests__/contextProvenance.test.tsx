import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ContextLab from "@/pages/ContextLab";
import { api, getRunAssembly } from "@/lib/api";

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
const mockedAssembly = getRunAssembly as jest.Mock;

const now = "2026-01-01T00:00:00.000Z";

const runAssembly = {
  run_id: "run-1",
  available: true,
  components: [
    {
      component_type: "SYSTEM_PROMPT",
      source_class: "system",
      ordering: 0,
      included: true,
      token_count: 100,
      estimated_tokens: 100,
      source_ref: { type: "prompt_version", id: "pv-9" },
      metadata: {},
    },
    {
      component_type: "RETRIEVED_CONTEXT",
      source_class: "knowledge",
      ordering: 1,
      included: true,
      token_count: null,
      estimated_tokens: null,
      metadata: { chunk_count: 3, deduplicated_count: 1 },
    },
    {
      component_type: "OTHER",
      source_class: "other",
      ordering: 2,
      included: true,
      token_count: null,
      estimated_tokens: null,
      metadata: {},
    },
  ],
  excluded_components: [
    { component_type: "HISTORY", source_class: "history", excluded_reason: "budget", estimated_tokens: 40 },
  ],
  sources_summary: [
    { source_class: "system", count: 1, included_count: 1, estimated_tokens: 100 },
    { source_class: "knowledge", count: 1, included_count: 1, estimated_tokens: null },
  ],
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
  mockedAssembly.mockResolvedValue(runAssembly);
  mockedApiGet.mockImplementation((url: string) => {
    if (url === "/observability") return Promise.resolve({ data: observability });
    if (url === "/runs/run-1/context") return Promise.resolve({ data: runContextWithSnapshot });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/context-lab?run=run-1"]}>
        <ContextLab />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

test("each assembly component explains why it is present using only real fields", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();

  await screen.findByTestId("context-assembly-component-0");

  await user.click(screen.getByTestId("context-why-provenance-toggle-0"));
  const first = screen.getByTestId("context-why-provenance-0");
  expect(first).toHaveTextContent("SYSTEM_PROMPT");
  expect(first).toHaveTextContent("system");
  expect(first).toHaveTextContent("Prompt version");
  const link = within(first).getByRole("link", { name: "pv-9" });
  expect(link).toHaveAttribute("href", "/prompt-lab/prompts/pv-9");

  await user.click(screen.getByTestId("context-why-provenance-toggle-1"));
  const second = screen.getByTestId("context-why-provenance-1");
  expect(second).toHaveTextContent("chunk count: 3");
  expect(second).toHaveTextContent("deduplicated count: 1");

  await user.click(screen.getByTestId("context-why-provenance-toggle-2"));
  expect(screen.getByTestId("context-why-provenance-2")).toHaveTextContent("No provenance recorded");

  await user.click(screen.getByTestId("context-why-provenance-excluded-toggle-0"));
  const excluded = screen.getByTestId("context-why-provenance-excluded-0");
  expect(excluded).toHaveTextContent("excluded: budget");
});

test("the why summary groups included sources by class with counts and n/a tokens when unknown", async () => {
  renderPage();

  const summary = await screen.findByTestId("context-why-summary");
  const rows = within(summary).getAllByTestId("context-why-summary-row");
  expect(rows).toHaveLength(3);

  const byClass = (label: string) =>
    rows.find((row) => within(row).getByTestId("context-why-summary-class").textContent === label);
  const system = byClass("system");
  const knowledge = byClass("knowledge");
  const other = byClass("other");
  if (!system || !knowledge || !other) throw new Error("missing summary row");

  expect(within(system).getByTestId("context-why-summary-count")).toHaveTextContent("1");
  expect(within(system).getByTestId("context-why-summary-tokens")).toHaveTextContent("100");
  expect(within(knowledge).getByTestId("context-why-summary-count")).toHaveTextContent("1");
  expect(within(knowledge).getByTestId("context-why-summary-tokens")).toHaveTextContent("n/a");
  expect(within(other).getByTestId("context-why-summary-tokens")).toHaveTextContent("n/a");

  await waitFor(() => expect(mockedAssembly).toHaveBeenCalledWith("run-1"));
});
