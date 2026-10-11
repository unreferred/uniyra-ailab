import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ExperimentDetail from "@/pages/ExperimentDetail";
import { api } from "@/lib/api";
import { BenchmarkPlan } from "@/lib/types";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
}));

// Avoid loading ESM-only markdown deps through the Arena results tree.
jest.mock("@/components/common/Markdown", () => ({
  MarkdownView: ({ content }: { content: string }) => <div data-testid="markdown-view">{content}</div>,
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

const now = new Date("2026-01-01T00:00:00.000Z").toISOString();

const plan: BenchmarkPlan = {
  id: "plan-1",
  experiment_id: "e-plan",
  configuration_ids: ["c1", "c2"],
  case_ids: ["case-1", "case-2", "case-3"],
  repetitions: 2,
  total_planned_calls: 12,
  plan_hash: "abcdef0123456789abcdef0123456789",
  status: "planned",
  progress: { total: 12, completed: 0, failed: 0, running: 0, pending: 12, cancelled: 0, skipped: 0 },
  estimated_cost: { available: false, unknown_pricing_config_count: 2 },
  requires_confirmation: true,
  created_at: now,
  updated_at: now,
};

const experiment = {
  id: "e-plan",
  project_id: "p1",
  title: "Plan review experiment",
  experiment_type: "benchmark",
  model_ids: [],
  blind: false,
  status: "READY",
  run_count: 0,
  created_at: now,
  updated_at: now,
  configurations: [],
  protocol: { repetitions: 2 },
};

beforeEach(() => {
  mockedGet.mockImplementation((url: string) => {
    if (url === "/models") return Promise.resolve({ data: [] });
    if (url === "/rubrics") return Promise.resolve({ data: [] });
    if (url === "/experiments/e-plan") return Promise.resolve({ data: experiment });
    if (url === "/experiments/e-plan/benchmark") {
      return Promise.resolve({ data: { plan, progress: null, configurations: [] } });
    }
    if (url === "/experiments/e-plan/results") return Promise.resolve({ data: { configurations: [] } });
    if (url === "/experiments/e-plan/provenance") return Promise.resolve({ data: { legacy: true } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

function renderDetail() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/experiments/e-plan"]}>
        <Routes>
          <Route path="/experiments/:id" element={<ExperimentDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

test("plan review shows planned calls and an unavailable estimate without fabricating cost", async () => {
  renderDetail();

  expect(await screen.findByTestId("plan-total-calls")).toHaveTextContent("12");
  expect(screen.getByTestId("benchmark-plan")).toBeInTheDocument();
  expect(screen.getByTestId("plan-estimated-cost")).toHaveTextContent("estimate unavailable");
  expect(screen.getByTestId("plan-estimated-cost")).not.toHaveTextContent(/\$/);
  expect(screen.getByTestId("benchmark-confirm")).toBeInTheDocument();
});

test("start is disabled until the large-run confirmation is checked", async () => {
  const user = userEvent.setup();
  renderDetail();

  const headerStart = await screen.findByTestId("benchmark-start");
  expect(headerStart).toBeDisabled();
  expect(screen.getByTestId("benchmark-start-panel")).toBeDisabled();

  await user.click(screen.getByTestId("benchmark-confirm-checkbox"));

  await waitFor(() => expect(screen.getByTestId("benchmark-start")).toBeEnabled());
  expect(screen.getByTestId("benchmark-start-panel")).toBeEnabled();
});
