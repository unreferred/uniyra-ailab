import React from "react";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RegressionPanel } from "@/components/evaluation/RegressionPanel";
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
const mockedPost = api.post as jest.Mock;

const configs = [
  { id: "c1", label: "Baseline" },
  { id: "c2", label: "Candidate" },
];

const regression: any = {
  id: "reg-1",
  experiment_id: "e1",
  baseline_configuration_id: "c1",
  candidate_configuration_id: "c2",
  created_at: "2026-01-02T00:00:00Z",
  metrics: [
    { metric: "avg_score", baseline: 0.5, candidate: 0.7, delta: 0.2, classification: "improved" },
    { metric: "latency_ms", baseline: 100, candidate: 100, delta: 0, classification: "unchanged" },
    { metric: "cost", baseline: 0.5, candidate: 0.9, delta: 0.4, classification: "degraded" },
    { metric: "ttft_ms", baseline: null, candidate: null, delta: null, classification: "unknown" },
    { metric: "tool_call_count", baseline: null, candidate: 3, delta: null, classification: "unknown" },
    { metric: "zero_metric", baseline: 0, candidate: 0, delta: 0, classification: "unchanged" },
  ],
  cases: [
    { case_id: "case-1", baseline_score: 0.4, candidate_score: 0.8, classification: "improved" },
    { case_id: "case-2", baseline_score: 0.8, candidate_score: 0.3, classification: "regressed" },
    { case_id: "case-3", baseline_score: null, candidate_score: 0.6, classification: "incomparable" },
    { case_id: "case-4", baseline_score: null, candidate_score: null, classification: "unknown" },
  ],
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedGet.mockImplementation((url: string) => {
    if (url === "/experiments/e1/regression") {
      return Promise.resolve({ data: { items: [regression] } });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  mockedPost.mockResolvedValue({ data: regression });
});

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <RegressionPanel experimentId="e1" configurations={configs} />
    </QueryClientProvider>
  );
}

test("renders improved, unchanged, degraded and unknown metric classifications", async () => {
  renderPanel();

  const metrics = await screen.findByTestId("regression-metrics");
  expect(within(metrics).getByText("improved")).toBeInTheDocument();
  expect(within(metrics).getByText("degraded")).toBeInTheDocument();
  expect(within(metrics).getAllByText("unchanged").length).toBeGreaterThan(0);
  expect(within(metrics).getAllByText("unknown").length).toBeGreaterThan(0);
});

test("missing (null) metric renders n/a while a real 0 renders 0", async () => {
  renderPanel();

  await screen.findByTestId("regression-metrics");

  expect(screen.getByTestId("regression-metric-ttft_ms")).toHaveTextContent("n/a");
  expect(screen.getByTestId("regression-metric-tool_call_count")).toHaveTextContent("n/a");

  const zeroRow = screen.getByTestId("regression-metric-zero_metric");
  expect(zeroRow).toHaveTextContent("0");
  expect(zeroRow).not.toHaveTextContent("n/a");
});

test("renders case-level classifications and n/a for null scores", async () => {
  renderPanel();

  const cases = await screen.findByTestId("regression-cases");
  expect(within(cases).getByText("improved")).toBeInTheDocument();
  expect(within(cases).getByText("regressed")).toBeInTheDocument();
  expect(within(cases).getByText("incomparable")).toBeInTheDocument();
  expect(within(cases).getByText("unknown")).toBeInTheDocument();

  const rows = within(cases).getAllByTestId("regression-case-row");
  expect(rows).toHaveLength(4);
  expect(rows[2]).toHaveTextContent("n/a");
});

test("clicking Run regression posts the selected configuration ids", async () => {
  const user = userEvent.setup();
  renderPanel();

  const button = await screen.findByTestId("run-regression");
  await waitFor(() => expect(button).toBeEnabled());
  await user.click(button);

  await waitFor(() =>
    expect(mockedPost).toHaveBeenCalledWith("/experiments/e1/regression", {
      baseline_configuration_id: "c1",
      candidate_configuration_id: "c2",
    })
  );
});

test("states no aggregate score and no statistical significance", async () => {
  renderPanel();

  await screen.findByTestId("regression-metrics");
  expect(screen.getByText(/No single aggregate score is computed/i)).toBeInTheDocument();
  expect(screen.getByText(/No statistical significance is computed/i)).toBeInTheDocument();
});
