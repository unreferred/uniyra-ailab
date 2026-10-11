import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ComparePanel } from "@/components/experiment/ComparePanel";
import { api } from "@/lib/api";
import { ConfigurationRecord, CompareResponse } from "@/components/experiment/types";

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

const configs: ConfigurationRecord[] = [
  { id: "c1", label: "Baseline config", model_id: "m1" },
  { id: "c2", label: "Candidate config", model_id: "m2" },
];

const compare: CompareResponse = {
  experiment_id: "e1",
  comparable: true,
  deltas: {
    avg_score: 0.25,
    success_rate: null,
    avg_latency_ms: null,
    known_cost_total: 0.5,
  },
  case_counts: { improved: 3, regressed: 1, unchanged: 5, incomparable_error: 0 },
  changed_cases: [
    { case_id: "case-1", input_snippet: "What is 2+2?", classification: "improved", baseline_score: 0.5, candidate_score: 0.75, delta: 0.25 },
    { case_id: "case-2", input_snippet: "Capital of France?", classification: "regressed", baseline_score: 0.8, candidate_score: 0.4, delta: -0.4 },
  ],
};

beforeEach(() => {
  mockedGet.mockImplementation((url: string) => {
    if (url.startsWith("/experiments/e1/compare")) return Promise.resolve({ data: compare });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

function renderCompare() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ComparePanel experimentId="e1" configs={configs} enabled />
    </QueryClientProvider>
  );
}

test("compare panel renders deltas, n/a for null deltas, and classification counts", async () => {
  renderCompare();

  expect(await screen.findByTestId("delta-score")).toHaveTextContent("0.2500");
  expect(screen.getByTestId("delta-cost")).toHaveTextContent("$0.5000");

  // Null deltas must never be presented as zero.
  expect(screen.getByTestId("delta-success")).toHaveTextContent("n/a");
  expect(screen.getByTestId("delta-latency")).toHaveTextContent("n/a");

  const counts = screen.getByTestId("compare-counts");
  expect(counts).toHaveTextContent("Improved");
  expect(counts).toHaveTextContent("3");
  expect(counts).toHaveTextContent("Regressed");
  expect(counts).toHaveTextContent("1");
  expect(counts).toHaveTextContent("Unchanged");
  expect(counts).toHaveTextContent("5");
  expect(counts).toHaveTextContent("Incomparable");
  expect(counts).toHaveTextContent("0");

  // Classification of individual changed cases.
  expect(screen.getAllByTestId("compare-case-row")).toHaveLength(2);
  expect(screen.getByText("improved")).toBeInTheDocument();
  expect(screen.getByText("regressed")).toBeInTheDocument();
});

test("compare panel makes no statistical significance or confidence claim", async () => {
  renderCompare();

  await screen.findByTestId("delta-score");

  expect(screen.getByText(/No statistical significance is computed/i)).toBeInTheDocument();
  expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/\bp-value\b/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/\bp\s*[<>=]\s*0/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/statistically significant/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/\d+%\s*confidence/i)).not.toBeInTheDocument();
});
