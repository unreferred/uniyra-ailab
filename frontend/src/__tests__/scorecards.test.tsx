import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Scorecards from "@/pages/Scorecards";
import {
  api,
  buildScorecard,
  compareExperimentScorecards,
  getExperimentScorecard,
  getRunScorecard,
  getScorecard,
  listScorecards,
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
  getRunScorecard: jest.fn(),
  getExperimentScorecard: jest.fn(),
  compareExperimentScorecards: jest.fn(),
  listScorecards: jest.fn(),
  buildScorecard: jest.fn(),
  getScorecard: jest.fn(),
}));

const mockedGetRun = getRunScorecard as jest.Mock;
const mockedList = listScorecards as jest.Mock;
const mockedGetScorecard = getScorecard as jest.Mock;
const mockedBuild = buildScorecard as jest.Mock;
const mockedCompare = compareExperimentScorecards as jest.Mock;
const mockedGetExperiment = getExperimentScorecard as jest.Mock;

const runScorecard = {
  id: "sc-1",
  scope: "run",
  run_id: "run-1",
  builder_version: "v5.8.0",
  dimensions: [
    {
      key: "avg_score",
      label: "Average score",
      value: 0.82,
      unit: "score",
      state: "available",
      source: "deterministic",
      deterministic: true,
      judge_derived: false,
      explanation: "Mean of deterministic rubric scores.",
      evidence: [{ type: "run", ref_id: "run-1" }],
    },
    {
      key: "answer_quality",
      label: "Answer quality",
      value: null,
      unit: null,
      state: "not_evaluated",
      source: "judge_derived",
      judge_derived: true,
      deterministic: false,
      explanation: "Judge has not evaluated this run.",
      evidence: [],
    },
    {
      key: "latency_ms",
      label: "Latency",
      value: null,
      unit: "ms",
      state: "not_available",
      source: "telemetry",
      explanation: "No latency telemetry recorded.",
      evidence: [],
    },
  ],
  composite: { available: false, reason: "no scoring policy configured" },
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedList.mockResolvedValue({ data: { items: [], total: 0, limit: 25, offset: 0 } });
  mockedGetRun.mockResolvedValue(runScorecard);
  mockedGetExperiment.mockResolvedValue(runScorecard);
  mockedCompare.mockResolvedValue({ scope: "comparison", rows: [] });
  mockedGetScorecard.mockResolvedValue(runScorecard);
  mockedBuild.mockResolvedValue(runScorecard);
});

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Scorecards />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

async function loadRunScorecard() {
  renderPage();
  fireEvent.change(screen.getByTestId("scorecard-run-id"), { target: { value: "run-1" } });
  await screen.findAllByTestId("scorecard-dimension-row");
}

test("renders the scorecards page and dimensions table", async () => {
  await loadRunScorecard();
  expect(screen.getByTestId("scorecards-page")).toBeInTheDocument();
  expect(screen.getByTestId("scorecard-scope")).toBeInTheDocument();
  expect(screen.getByTestId("scorecard-build")).toBeInTheDocument();
  expect(screen.getAllByTestId("scorecard-dimension-row")).toHaveLength(3);
  expect(screen.getByText("Average score")).toBeInTheDocument();
});

test("a not_available dimension renders n/a and never 0", async () => {
  await loadRunScorecard();
  const rows = screen.getAllByTestId("scorecard-dimension-row");
  const latency = rows.find((r) => within(r).queryByText("Latency")) as HTMLElement;
  const value = within(latency).getByTestId("scorecard-dimension-value");
  expect(value).toHaveTextContent("n/a");
  expect(value).not.toHaveTextContent("0");
});

test("a NOT_EVALUATED dimension is labeled distinctly from unavailable", async () => {
  await loadRunScorecard();
  const rows = screen.getAllByTestId("scorecard-dimension-row");
  const quality = rows.find((r) => within(r).queryByText("Answer quality")) as HTMLElement;
  expect(within(quality).getByTestId("scorecard-dimension-state")).toHaveTextContent("NOT_EVALUATED");

  const latency = rows.find((r) => within(r).queryByText("Latency")) as HTMLElement;
  const latencyState = within(latency).getByTestId("scorecard-dimension-state");
  expect(latencyState).toHaveTextContent("not available");
  expect(latencyState).not.toHaveTextContent("NOT_EVALUATED");
});

test("renders deterministic vs judge-derived source badges", async () => {
  await loadRunScorecard();
  const sources = screen.getAllByTestId("scorecard-dimension-source").map((b) => b.textContent);
  expect(sources).toContain("deterministic");
  expect(sources).toContain("judge-derived");
});

test("composite unavailable renders the no-policy text and never a numeric score", async () => {
  await loadRunScorecard();
  const composite = screen.getByTestId("scorecard-composite");
  expect(composite).toHaveTextContent("No composite score (no scoring policy)");
  expect(composite.textContent || "").not.toMatch(/[0-9]/);
});

test("renders run evidence links to the trace viewer", async () => {
  await loadRunScorecard();
  const link = screen.getByTestId("scorecard-evidence");
  expect(link).toHaveAttribute("href", "/trace?run=run-1");
});
