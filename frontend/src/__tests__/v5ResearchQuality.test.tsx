import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ExperimentDetail from "@/pages/ExperimentDetail";
import DatasetDetail from "@/pages/DatasetDetail";
import { CaseTable } from "@/components/experiment/CaseTable";
import { api } from "@/lib/api";
import { CasesPage } from "@/components/experiment/types";

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
  api: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  apiError: (e: any) => (e && e.message) || "error",
}));

const mockedGet = api.get as jest.Mock;
const mockedPost = api.post as jest.Mock;
const mockedPatch = api.patch as jest.Mock;

const now = new Date("2026-01-01T00:00:00.000Z").toISOString();

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockedPost.mockResolvedValue({ data: {} });
  mockedPatch.mockResolvedValue({ data: {} });
});

// ---------------------------------------------------------------------------
// 1. ExperimentDetail integration
// ---------------------------------------------------------------------------

const benchmarkExperiment = {
  id: "e1",
  project_id: "p1",
  title: "V5 benchmark",
  experiment_type: "benchmark",
  model_ids: [],
  blind: false,
  status: "READY",
  run_count: 0,
  created_at: now,
  updated_at: now,
  configurations: [
    { id: "c1", label: "Baseline", model_id: "m1" },
    { id: "c2", label: "Candidate", model_id: "m2" },
  ],
  protocol: { repetitions: 1 },
};

const evaluationRow = {
  id: "eval-1",
  evaluation_type: "rubric",
  evaluator: { type: "rule", key: "v5_rubric" },
  result: {
    outcome: "PASS",
    status: "completed",
    passed: true,
    score: 0.9,
    threshold: 0.7,
    confidence: 0.8,
    reason: "ok",
  },
  created_at: now,
};

function mockExperimentGets() {
  mockedGet.mockImplementation((url: string) => {
    if (url === "/models") return Promise.resolve({ data: [] });
    if (url === "/rubrics") return Promise.resolve({ data: [] });
    if (url === "/experiments/e1") return Promise.resolve({ data: benchmarkExperiment });
    if (url === "/experiments/e1/benchmark") return Promise.resolve({ data: { plan: null, progress: null, configurations: [] } });
    if (url === "/experiments/e1/results") return Promise.resolve({ data: { configurations: [] } });
    if (url === "/experiments/e1/provenance") return Promise.resolve({ data: { legacy: true } });
    if (url === "/experiments/e1/evaluations?limit=50") return Promise.resolve({ data: { items: [evaluationRow], total: 1, limit: 50, offset: 0 } });
    if (url === "/experiments/e1/regression") return Promise.resolve({ data: { items: [] } });
    if (url.startsWith("/experiments/e1/compare")) return Promise.resolve({ data: { comparable: false, deltas: { avg_score: null, success_rate: null, avg_latency_ms: null, known_cost_total: null }, case_counts: { improved: 0, regressed: 0, unchanged: 0, incomparable_error: 0 }, changed_cases: [] } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

function renderExperiment() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter initialEntries={["/experiments/e1"]}>
        <Routes>
          <Route path="/experiments/:id" element={<ExperimentDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

test("ExperimentDetail renders the Evaluation & Regression section with regression panel and evaluation list", async () => {
  mockExperimentGets();
  renderExperiment();

  expect(await screen.findByTestId("evaluation-regression-section")).toBeInTheDocument();
  expect(await screen.findByTestId("regression-panel")).toBeInTheDocument();
  expect(await screen.findByTestId("evaluation-list")).toBeInTheDocument();

  // Existing sections must still be present.
  expect(screen.getByTestId("results-table")).toBeInTheDocument();
  expect(screen.getByTestId("compare-panel")).toBeInTheDocument();
  expect(screen.getByTestId("provenance-panel")).toBeInTheDocument();

  const list = screen.getByTestId("evaluation-list");
  expect(within(list).getByText("PASS")).toBeInTheDocument();
});

// ---------------------------------------------------------------------------
// 2. CaseTable outcome badges
// ---------------------------------------------------------------------------

const tableConfigs: any[] = [
  { configuration_id: "c-pass" },
  { configuration_id: "c-fail" },
  { configuration_id: "c-error" },
  { configuration_id: "c-none" },
];

const casesPage = {
  total: 1,
  limit: 25,
  offset: 0,
  items: [
    {
      dataset_case_id: "case-1",
      input_text: "What is 2+2?",
      expected_output: "4",
      configurations: {
        "c-pass": { run_id: "run-pass", status: "success", evaluation_id: "eval-pass", outcome: "PASS", score: 0.9, latency_ms: 10, input_tokens: 1, output_tokens: 2, cost: 0.001, cost_known: true },
        "c-fail": { run_id: "run-fail", status: "success", evaluation_id: "eval-fail", outcome: "FAIL", score: null, latency_ms: 11, input_tokens: 1, output_tokens: 2, cost: 0.001, cost_known: true },
        "c-error": { run_id: "run-error", status: "error", evaluation_id: "eval-error", evaluation: { status: "error" }, score: null, latency_ms: null, input_tokens: null, output_tokens: null, cost: null },
        "c-none": { run_id: "run-none", status: "completed", evaluation_id: "eval-none", parsed_score: null, score: null, latency_ms: 12, input_tokens: 3, output_tokens: 4, cost: 0.002, cost_known: true },
      },
    },
  ],
} as unknown as CasesPage;

function renderCaseTable() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter>
        <CaseTable
          cases={casesPage}
          configs={tableConfigs}
          loading={false}
          onPage={jest.fn()}
          onEvaluate={jest.fn()}
          onOpenRun={jest.fn()}
        />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

test("CaseTable renders PASS/FAIL/ERROR/NOT_EVALUATED and n/a for a null score (never 0)", async () => {
  renderCaseTable();

  expect(await screen.findByTestId("case-eval-outcome-run-pass")).toHaveTextContent("PASS");
  expect(screen.getByTestId("case-eval-outcome-run-fail")).toHaveTextContent("FAIL");
  expect(screen.getByTestId("case-eval-outcome-run-error")).toHaveTextContent("ERROR");
  expect(screen.getByTestId("case-eval-outcome-run-none")).toHaveTextContent("NOT_EVALUATED");

  // A null score must render n/a and never a fabricated zero.
  expect(screen.getByTestId("case-score-run-fail")).toHaveTextContent("score n/a");
  expect(screen.getByTestId("case-score-run-fail")).not.toHaveTextContent("0");
  expect(screen.getByTestId("case-score-run-pass")).toHaveTextContent("score 0.9000");

  // Details button is available for evaluated cells.
  expect(screen.getByTestId("case-eval-details-run-pass")).toBeInTheDocument();
});

// ---------------------------------------------------------------------------
// 3 & 4. DatasetDetail assertions editor + regression fixtures
// ---------------------------------------------------------------------------

const dataset = {
  id: "d1",
  project_id: "p1",
  name: "Research dataset",
  description: "",
  tags: [],
  created_at: now,
  updated_at: now,
  case_count: 0,
};

const draftVersion = {
  id: "v-draft",
  dataset_id: "d1",
  project_id: "p1",
  version: 1,
  status: "draft" as const,
  case_count: 0,
  content_hash: null,
  created_at: now,
  updated_at: now,
};

const draftVersionDoc = {
  ...draftVersion,
  cases: [],
  case_count: 0,
  limit: 50,
  offset: 0,
};

function mockDatasetGets() {
  mockedGet.mockImplementation((url: string) => {
    if (url === "/datasets/d1") return Promise.resolve({ data: dataset });
    if (url === "/datasets/d1/versions") return Promise.resolve({ data: [draftVersion] });
    if (url.startsWith("/datasets/d1/versions/")) return Promise.resolve({ data: draftVersionDoc });
    if (url.startsWith("/experiments")) return Promise.resolve({ data: [] });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

function renderDataset() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter initialEntries={["/datasets/d1"]}>
        <Routes>
          <Route path="/datasets/:id" element={<DatasetDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

test("DatasetDetail assertions editor rejects invalid JSON and accepts a valid array", async () => {
  const user = userEvent.setup();
  mockDatasetGets();
  renderDataset();

  await user.click(await screen.findByTestId("case-add"));

  await user.type(screen.getByTestId("case-input"), "Question one");
  fireEvent.change(screen.getByTestId("case-assertions"), { target: { value: "not-json" } });

  await user.click(screen.getByTestId("case-save-submit"));

  expect(await screen.findByTestId("case-assertions-error")).toBeInTheDocument();
  expect(mockedPost).not.toHaveBeenCalled();

  // Valid array of objects with a string "type" is accepted.
  fireEvent.change(screen.getByTestId("case-assertions"), { target: { value: '[{"type":"contains","value":"4"}]' } });
  await user.click(screen.getByTestId("case-save-submit"));

  await waitFor(() =>
    expect(mockedPost).toHaveBeenCalledWith(
      "/datasets/d1/versions/v-draft/cases",
      expect.objectContaining({ assertions: [{ type: "contains", value: "4" }] })
    )
  );
});

test("DatasetDetail fixtures button posts the active project id", async () => {
  const user = userEvent.setup();
  mockDatasetGets();
  mockedPost.mockResolvedValue({ data: { dataset, version: { id: "v2", version: 2 }, cases: [], knowledge_setup: {} } });

  renderDataset();

  const button = await screen.findByTestId("create-regression-fixtures");
  await user.click(button);

  await waitFor(() =>
    expect(mockedPost).toHaveBeenCalledWith("/datasets/regression-fixtures", { project_id: "p1" })
  );
});
