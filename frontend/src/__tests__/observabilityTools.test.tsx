import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Observability from "@/pages/Observability";
import { api } from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
}));

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({ projects: [], activeId: null }),
}));

const mockedGet = api.get as jest.Mock;

function payload(tool_metrics: any, knowledge_metrics: any, provider_quota: any = null) {
  return {
    series: [],
    models: [],
    providers: [],
    totals: {
      requests: 0,
      failures: 0,
      success_rate: null,
      avg_latency_ms: null,
      avg_ttft_ms: null,
      total_tokens: 0,
      input_tokens: 0,
      output_tokens: 0,
      total_cost: null,
      cost_available: false,
    },
    model_usage: [],
    provider_quota,
    tool_metrics,
    knowledge_metrics,
    scope: "global",
    project_id: null,
  };
}

function renderObs() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <Observability />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

test("null metrics render Unknown without fabrication", async () => {
  mockedGet.mockReset();
  mockedGet.mockImplementation(() =>
    Promise.resolve({
      data: payload(
        { total_calls: null, success_rate: null, failures: null, avg_latency_ms: null, by_tool: null, code_interpreter: null },
        { retrievals: null, avg_latency_ms: null, empty_retrievals: null, indexing_failures: null, chunks_indexed: null }
      ),
    })
  );
  renderObs();

  expect(await screen.findByTestId("obs-tool-usage")).toBeInTheDocument();
  expect(screen.getByTestId("obs-tool-total-calls")).toHaveTextContent("Unknown");
  expect(screen.getByTestId("obs-tool-avg-latency")).toHaveTextContent("Unknown");
  expect(screen.getByTestId("obs-tool-table")).toHaveTextContent("Unknown");
  expect(screen.getByTestId("obs-knowledge")).toBeInTheDocument();
  expect(screen.getByTestId("obs-knowledge-retrievals")).toHaveTextContent("Unknown");
  expect(screen.getByTestId("obs-knowledge-empty-note")).toHaveTextContent("informational only");
});

test("by_tool rows render with names and latency", async () => {
  mockedGet.mockReset();
  mockedGet.mockImplementation(() =>
    Promise.resolve({
      data: payload(
        {
          total_calls: 7,
          success_rate: 0.8571,
          failures: 1,
          avg_latency_ms: 120,
          by_tool: [
            { tool_id: "t1", tool_name: "web_search", calls: 5, failures: 0, avg_latency_ms: 100 },
            { tool_id: "t2", tool_name: null, calls: 2, failures: 1, avg_latency_ms: null },
          ],
          code_interpreter: { executions: 3, timeouts: 1, errors: 1 },
        },
        { retrievals: 4, avg_latency_ms: 42, empty_retrievals: 1, indexing_failures: 0, chunks_indexed: 25 }
      ),
    })
  );
  renderObs();

  const rows = await screen.findAllByTestId("obs-tool-row");
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveTextContent("web_search");
  expect(rows[1]).toHaveTextContent("Unknown");
  expect(screen.getByTestId("obs-tool-total-calls")).toHaveTextContent("7");
  expect(screen.getByTestId("obs-tool-ci-executions")).toHaveTextContent("3");
  expect(screen.getByTestId("obs-knowledge-retrievals")).toHaveTextContent("4");
  expect(screen.getByTestId("obs-knowledge-empty-note")).toHaveTextContent("25.0%");
});

test("provider quota rows render consumed versus window limits", async () => {
  mockedGet.mockReset();
  mockedGet.mockImplementation(() =>
    Promise.resolve({
      data: payload(null, null, [
        { provider: "tinyfish", resource: "search", window: "minute", window_seconds: 60, units_consumed: 7, window_limit: 30, window_remaining: 23, window_reset_seconds: 42 },
        { provider: "tinyfish", resource: "search", window: "hour", window_seconds: 3600, units_consumed: 126, window_limit: 500, window_remaining: 374, window_reset_seconds: 1200 },
        { provider: "tinyfish", resource: "fetch", window: "minute", window_seconds: 60, units_consumed: 22, window_limit: 150, window_remaining: 128, window_reset_seconds: 42 },
        { provider: "tinyfish", resource: "fetch", window: "day", window_seconds: 86400, units_consumed: 381, window_limit: 1000, window_remaining: 619, window_reset_seconds: 3600 },
      ]),
    })
  );
  renderObs();

  const card = await screen.findByTestId("obs-provider-quota");
  expect(card).toBeInTheDocument();
  expect(card).toHaveTextContent("7 / 30");
  expect(card).toHaveTextContent("126 / 500");
  expect(card).toHaveTextContent("22 / 150");
  expect(card).toHaveTextContent("381 / 1000");
  expect(card).toHaveTextContent("TinyFish Search");
  expect(card).toHaveTextContent("TinyFish Fetch");
  expect(screen.getAllByTestId("obs-provider-quota-row")).toHaveLength(2);
});

test("provider quota renders empty state when absent", async () => {
  mockedGet.mockReset();
  mockedGet.mockImplementation(() =>
    Promise.resolve({ data: payload(null, null) })
  );
  renderObs();

  expect(await screen.findByTestId("obs-provider-quota-empty")).toHaveTextContent("No provider quota telemetry yet.");
  expect(screen.queryAllByTestId("obs-provider-quota-row")).toHaveLength(0);
});
