import React from "react";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { EvaluationDetailDialog } from "@/components/evaluation/EvaluationDetailDialog";
import { api } from "@/lib/api";

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

const passFixture: any = {
  id: "e-pass",
  evaluation_type: "rubric",
  evaluator: { type: "llm_judge", key: "rubric_v2", version: "2" },
  result: {
    outcome: "PASS",
    status: "completed",
    passed: true,
    score: 0.912,
    threshold: 0.7,
    confidence: 0.8,
    reason: "met criteria",
  },
  rubric: { id: "r1", version: 2, threshold: 0.7 },
  scores: { accuracy: 0.9, completeness: null },
  findings: [{ category: "safety", severity: "high", message: "contains flagged content" }],
  metrics: { tool_call_count: 3, cost: null },
  run_id: "run-1",
  dataset_case_id: "case-1",
  provenance: { context_snapshot_id: "snap-1" },
  judge: { model: "gpt-4o", provider: "openai" },
  created_at: "2026-01-01T00:00:00Z",
};

function mockGet(data: any) {
  mockedGet.mockImplementation((url: string) => {
    if (url === `/evaluations/${data.id}`) return Promise.resolve({ data });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

function renderDialog(id: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <EvaluationDetailDialog evaluationId={id} open onOpenChange={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
});

test.each(["PASS", "FAIL", "ERROR", "NOT_EVALUATED"])(
  "renders the %s outcome from the structured result",
  async (outcome) => {
    const data = { ...passFixture, id: `e-${outcome}`, result: { ...passFixture.result, outcome } };
    mockGet(data);
    renderDialog(data.id);

    await screen.findByTestId("evaluation-result");
    const badge = screen.getByTestId("evaluation-outcome");
    expect(badge).toHaveTextContent(outcome);
  }
);

test("falls back to ERROR from the legacy status when no structured result exists", async () => {
  const data = { id: "e-legacy", evaluation_type: "legacy", status: "error" };
  mockGet(data);
  renderDialog(data.id);

  await screen.findByTestId("evaluation-result");
  expect(screen.getByTestId("evaluation-outcome")).toHaveTextContent("ERROR");
});

test("renders evaluator, result, rubric, scores, findings and metrics", async () => {
  mockGet(passFixture);
  renderDialog(passFixture.id);

  await screen.findByTestId("evaluation-result");
  const evaluator = screen.getByTestId("evaluation-evaluator");
  expect(evaluator).toHaveTextContent("llm_judge");
  expect(evaluator).toHaveTextContent("rubric_v2");

  const result = screen.getByTestId("evaluation-result");
  expect(result).toHaveTextContent("true");
  expect(result).toHaveTextContent("0.912");
  expect(result).toHaveTextContent("0.700");
  expect(result).toHaveTextContent("Confidence");

  expect(screen.getByTestId("evaluation-rubric")).toHaveTextContent("r1");

  const scores = screen.getByTestId("evaluation-scores");
  expect(within(scores).getAllByTestId("evaluation-score-row")).toHaveLength(2);
  expect(scores).toHaveTextContent("accuracy");
  expect(scores).toHaveTextContent("n/a");

  const findings = screen.getByTestId("evaluation-findings");
  expect(within(findings).getByText("safety")).toBeInTheDocument();
  expect(within(findings).getByText("high")).toBeInTheDocument();
  expect(within(findings).getByText("contains flagged content")).toBeInTheDocument();

  const metrics = screen.getByTestId("evaluation-metrics");
  expect(within(metrics).getByTestId("evaluation-metric-tool_call_count")).toHaveTextContent("3");
});

test("missing score, confidence and metrics render n/a and never 0", async () => {
  const data: any = {
    id: "e-missing",
    evaluation_type: "rubric",
    evaluator: { type: "rule" },
    result: {
      outcome: "NOT_EVALUATED",
      status: "not_evaluated",
      passed: null,
      score: null,
      threshold: null,
      confidence: null,
      reason: null,
    },
    metrics: { tool_call_count: null, cost: null },
  };
  mockGet(data);
  renderDialog(data.id);

  const result = await screen.findByTestId("evaluation-result");
  expect(result).toHaveTextContent("n/a");
  expect(result).not.toHaveTextContent(/\b0\b/);
  expect(screen.queryByText("Confidence")).not.toBeInTheDocument();

  const metrics = screen.getByTestId("evaluation-metrics");
  expect(within(metrics).getByTestId("evaluation-metric-tool_call_count")).toHaveTextContent("n/a");
  expect(within(metrics).getByTestId("evaluation-metric-cost")).toHaveTextContent("n/a");
  expect(metrics).not.toHaveTextContent(/\b0\b/);
});

test("renders the empty findings state", async () => {
  const data = { ...passFixture, id: "e-no-findings", findings: [] };
  mockGet(data);
  renderDialog(data.id);

  expect(await screen.findByTestId("evaluation-findings")).toHaveTextContent("No findings");
});

test("shows an error message when the fetch fails without crashing", async () => {
  mockedGet.mockRejectedValue(new Error("boom"));
  renderDialog("e-error");

  expect(await screen.findByText("boom")).toBeInTheDocument();
});
