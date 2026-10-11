import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Failures from "@/pages/Failures";
import {
  api,
  analyzeDrift,
  getDriftAnalysis,
  getDriftCatalog,
  listDriftAnalyses,
  materializeDriftFindings,
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
  getDriftCatalog: jest.fn(),
  analyzeDrift: jest.fn(),
  listDriftAnalyses: jest.fn(),
  getDriftAnalysis: jest.fn(),
  materializeDriftFindings: jest.fn(),
}));

const mockedApiGet = api.get as jest.Mock;
const mockedCatalog = getDriftCatalog as jest.Mock;
const mockedList = listDriftAnalyses as jest.Mock;
const mockedAnalyze = analyzeDrift as jest.Mock;
const mockedGetAnalysis = getDriftAnalysis as jest.Mock;
const mockedMaterialize = materializeDriftFindings as jest.Mock;

const now = "2026-01-01T00:00:00.000Z";

const aggregate = {
  total: 3,
  execution_count: 2,
  quality_count: 1,
  by_category: [{ category: "TIMEOUT", count: 2 }],
  by_source: [{ source: "SYSTEM", count: 3 }],
  by_status: [{ status: "OPEN", count: 3 }],
  by_severity: [{ severity: "high", count: 1 }],
  by_model: [{ model: "Model One", count: 3 }],
  by_experiment: [],
  by_tool: [],
  recent: [
    {
      id: "f1",
      title: "Provider timeout",
      category: "TIMEOUT",
      source: "SYSTEM",
      severity: "high",
      status: "OPEN",
      created_at: now,
    },
  ],
};

const catalog = {
  drift_types: ["PROMPT_DRIFT", "DESIGN_DRIFT", "ARCHITECTURE_DRIFT", "LOGIC_DRIFT"],
  sources: ["deterministic", "judge_derived"],
  checks: [],
  scopes: ["all", "prompt", "design", "architecture", "logic"],
};

const analysisDoc = {
  id: "da1",
  project_id: "p1",
  scope: "all",
  version: 1,
  findings: [
    {
      drift_type: "PROMPT_DRIFT",
      category: "missing_invariant",
      severity: "high",
      message: "Required invariant INV-1 was dropped",
      evidence: { pattern: "INV-1" },
      source: "deterministic",
      provenance: {},
    },
    {
      drift_type: "DESIGN_DRIFT",
      category: "hardcoded_color",
      severity: "medium",
      message: "Hardcoded color introduced",
      evidence: { path: "src/app.ts", value: "#ff0000" },
      source: "judge_derived",
      provenance: { model: "judge-a", rubric: "rubric-1" },
    },
  ],
  summary: {
    total: 2,
    by_drift_type: { PROMPT_DRIFT: 1, DESIGN_DRIFT: 1 },
    deterministic_count: 1,
  },
  run_id: null,
  evaluation_id: null,
  created_at: now,
};

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Failures />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockedCatalog.mockResolvedValue(catalog);
  mockedList.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  mockedApiGet.mockImplementation((url: string) => {
    if (url === "/failures/aggregate") return Promise.resolve({ data: aggregate });
    if (url === "/failures") return Promise.resolve({ data: { items: [], total: 0, limit: 25, offset: 0 } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

async function openDriftTab() {
  const user = userEvent.setup({ delay: null });
  renderPage();
  await screen.findByTestId("failure-kpi-total");
  await user.click(screen.getByTestId("failures-tab-drift"));
  await screen.findByTestId("drift-analyze-form");
  return user;
}

test("Explorer tab renders the existing failure content and exposes the Drift Analysis tab", async () => {
  renderPage();

  expect(screen.getByTestId("failures-tab-explorer")).toBeInTheDocument();
  expect(screen.getByTestId("failures-tab-drift")).toBeInTheDocument();

  expect(await screen.findByTestId("failure-kpi-total")).toHaveTextContent("3");
  expect(screen.getByTestId("failure-recent")).toHaveTextContent("Provider timeout");
});

test("switching to the Drift Analysis tab renders the analysis form", async () => {
  await openDriftTab();

  expect(screen.getByTestId("drift-analyze-form")).toBeInTheDocument();
  expect(screen.getByTestId("drift-scope")).toBeInTheDocument();
  expect(screen.getByTestId("drift-changed-file-add")).toBeInTheDocument();
  expect(screen.getByTestId("drift-analyze-submit")).toBeInTheDocument();
});

test("adding a changed file and submitting calls analyzeDrift with the path", async () => {
  mockedAnalyze.mockResolvedValue(analysisDoc);
  const user = await openDriftTab();

  await user.click(screen.getByTestId("drift-changed-file-add"));
  await user.type(screen.getByTestId("drift-changed-file-path"), "src/app.ts");
  await user.click(screen.getByTestId("drift-analyze-submit"));

  await waitFor(() => expect(mockedAnalyze).toHaveBeenCalledTimes(1));
  const body = mockedAnalyze.mock.calls[0][0];
  expect(body.project_id).toBe("p1");
  expect(body.scope).toBe("all");
  expect(body.changed_files).toEqual([expect.objectContaining({ path: "src/app.ts" })]);
});

test("results render deterministic and judge-derived findings with provenance and n/a for missing counts", async () => {
  mockedAnalyze.mockResolvedValue(analysisDoc);
  const user = await openDriftTab();

  await user.click(screen.getByTestId("drift-changed-file-add"));
  await user.type(screen.getByTestId("drift-changed-file-path"), "src/app.ts");
  await user.click(screen.getByTestId("drift-analyze-submit"));

  const rows = await screen.findAllByTestId("drift-finding-row");
  expect(rows).toHaveLength(2);

  const deterministicRow = rows.find((r) =>
    within(r).getByTestId("drift-finding-source").textContent?.includes("deterministic")
  ) as HTMLElement;
  expect(within(deterministicRow).getByTestId("drift-finding-type")).toHaveTextContent("PROMPT_DRIFT");
  expect(within(deterministicRow).getByTestId("drift-finding-source")).toHaveTextContent("deterministic");
  expect(within(deterministicRow).getByTestId("drift-finding-evidence")).toHaveTextContent("pattern: INV-1");

  const judgeRow = rows.find((r) =>
    within(r).getByTestId("drift-finding-source").textContent?.includes("judge_derived")
  ) as HTMLElement;
  expect(within(judgeRow).getByTestId("drift-finding-type")).toHaveTextContent("DESIGN_DRIFT");
  expect(within(judgeRow).getByTestId("drift-judge-provenance")).toHaveTextContent("judge-a");
  expect(within(judgeRow).getByTestId("drift-judge-provenance")).toHaveTextContent("rubric-1");
  expect(within(judgeRow).getByTestId("drift-finding-evidence")).toHaveTextContent("path: src/app.ts");

  // judge_derived_count is absent in the payload: it must render n/a, never 0.
  expect(screen.getByTestId("drift-summary-judge-derived")).toHaveTextContent("n/a");
  expect(screen.getByTestId("drift-summary-total")).toHaveTextContent("2");
});

test("materializing findings calls the helper and links the created finding ids", async () => {
  mockedAnalyze.mockResolvedValue(analysisDoc);
  mockedMaterialize.mockResolvedValue({ created: [{ id: "finding-1" }, { id: "finding-2" }], existing: [] });
  const user = await openDriftTab();

  await user.click(screen.getByTestId("drift-changed-file-add"));
  await user.type(screen.getByTestId("drift-changed-file-path"), "src/app.ts");
  await user.click(screen.getByTestId("drift-analyze-submit"));

  const button = await screen.findByTestId("drift-materialize");
  await user.click(button);

  await waitFor(() => expect(mockedMaterialize).toHaveBeenCalledWith("da1", {}));
  expect(await screen.findByText("finding-1")).toBeInTheDocument();
  const links = screen.getAllByTestId("drift-materialize-created");
  links.forEach((link) => expect(link).toHaveAttribute("href", "/findings"));
});

test("analysis history rows load a stored analysis via getDriftAnalysis", async () => {
  mockedList.mockResolvedValue({
    items: [{ id: "da2", project_id: "p1", scope: "prompt", version: 1, findings: [], summary: null, created_at: now }],
    total: 1,
    limit: 50,
    offset: 0,
  });
  mockedGetAnalysis.mockResolvedValue({ ...analysisDoc, id: "da2", scope: "prompt" });

  const user = await openDriftTab();

  const row = await screen.findByTestId("drift-analysis-row");
  expect(within(row).getByText("da2")).toBeInTheDocument();
  await user.click(within(row).getByTestId("drift-analysis-view"));

  await waitFor(() => expect(mockedGetAnalysis).toHaveBeenCalledWith("da2"));
  expect(await screen.findByTestId("drift-results")).toBeInTheDocument();
});
