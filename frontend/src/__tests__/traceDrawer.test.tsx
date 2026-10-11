import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TraceDrawer } from "@/components/workspace/TraceDrawer";
import { api } from "@/lib/api";
import { TraceDetail } from "@/lib/types";

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

const detail: TraceDetail = {
  available: true,
  run_id: "r1",
  trace: {
    id: "tr1",
    trace_type: "chat",
    status: "success",
    span_count: 2,
    total_duration_ms: 1500,
    started_at: now,
    completed_at: now,
  },
  spans: [
    {
      id: "s1",
      trace_id: "tr1",
      span_type: "llm",
      name: "LLM call",
      status: "success",
      started_at: now,
      duration_ms: 1200,
      ordering: 0,
      metadata: { model: "m1", tokens: 10 },
    },
    {
      id: "s2",
      trace_id: "tr1",
      span_type: "tool",
      name: "Tool call",
      status: "error",
      started_at: now,
      duration_ms: 300,
      ordering: 1,
      structured_error: { message: "boom" },
    },
  ],
};

let response: TraceDetail;

beforeEach(() => {
  mockedGet.mockReset();
  response = detail;
  mockedGet.mockImplementation((url: string) => {
    if (url === "/runs/r1/trace") return Promise.resolve({ data: response });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

function renderDrawer() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TraceDrawer runId="r1" open onOpenChange={() => {}} />
    </QueryClientProvider>
  );
}

test("renders timeline spans with names and durations and expands metadata", async () => {
  const user = userEvent.setup({ delay: null });
  renderDrawer();

  expect(await screen.findByText("LLM call")).toBeInTheDocument();
  expect(screen.getByText("Tool call")).toBeInTheDocument();

  // Duration formatting: >= 1s in seconds, < 1s in ms.
  expect(screen.getByText("1.20 s")).toBeInTheDocument();
  expect(screen.getByText("300 ms")).toBeInTheDocument();

  // Trace summary badges.
  expect(screen.getByText("2 spans")).toBeInTheDocument();
  expect(screen.getByText("chat")).toBeInTheDocument();

  // Success/failure status badges are rendered.
  expect(screen.getByTestId("trace-span-s2")).toHaveTextContent("error");

  await user.click(screen.getByTestId("trace-span-s1"));
  expect(await screen.findByText(/"tokens": 10/)).toBeInTheDocument();

  // The failed span surfaces its structured error when expanded.
  await user.click(screen.getByTestId("trace-span-s2"));
  expect(await screen.findByText("boom")).toBeInTheDocument();
});

test("renders a legacy unavailable trace without crashing", async () => {
  response = { available: false, message: "Legacy run — trace unavailable" };
  renderDrawer();

  expect(await screen.findByTestId("trace-drawer")).toBeInTheDocument();
  expect(await screen.findByText("Legacy run — trace unavailable")).toBeInTheDocument();
});
