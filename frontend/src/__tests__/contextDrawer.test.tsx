import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ContextDrawer } from "@/components/workspace/ContextDrawer";
import { api } from "@/lib/api";
import { ContextResponse } from "@/lib/types";

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

const response: ContextResponse = {
  available: true,
  run_id: "r1",
  provider_input_tokens: 500,
  snapshot: {
    id: "ctx1",
    run_id: "r1",
    strategy: "FULL",
    over_budget: true,
    estimated_total_tokens: 1200,
    warnings: ["History truncated to fit the budget"],
    components: [
      {
        component_type: "SYSTEM",
        ordering: 0,
        source_ref: "system_prompt",
        content: "You are helpful",
        token_count: 20,
        estimated_tokens: 25,
        included: true,
      },
      {
        component_type: "SYSTEM",
        ordering: 1,
        content: "More system instructions",
        token_count: 10,
        estimated_tokens: 12,
        included: true,
      },
      {
        component_type: "HISTORY",
        ordering: 2,
        source_ref: "message-1",
        content: "hello world",
        token_count: 5,
        estimated_tokens: 8,
        included: true,
        truncated: true,
        truncation_reason: "token budget",
      },
    ],
    excluded_components: [
      {
        component_type: "TOOLS",
        ordering: 3,
        content: "",
        estimated_tokens: 30,
        included: false,
        excluded_reason: "not selected",
      },
    ],
    created_at: now,
  },
};

let apiResponse: ContextResponse;

beforeEach(() => {
  mockedGet.mockReset();
  apiResponse = response;
  mockedGet.mockImplementation((url: string) => {
    if (url === "/runs/r1/context") return Promise.resolve({ data: apiResponse });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

function renderDrawer() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ContextDrawer runId="r1" open onOpenChange={() => {}} />
    </QueryClientProvider>
  );
}

test("renders grouped token summary, reveals content and shows warnings/excluded components", async () => {
  const user = userEvent.setup({ delay: null });
  renderDrawer();

  // Grouped summary: SYSTEM has 2 components / 37 estimated tokens; HISTORY 1 / 8.
  const systemGroup = await screen.findByTestId("context-group-SYSTEM");
  expect(systemGroup).toHaveTextContent("2 · 37");
  expect(screen.getByTestId("context-group-HISTORY")).toHaveTextContent("1 · 8");

  // Snapshot-level header badges.
  expect(screen.getByText("FULL")).toBeInTheDocument();
  expect(screen.getByText("over budget")).toBeInTheDocument();
  const drawer = screen.getByTestId("context-drawer");
  expect(drawer).toHaveTextContent("estimated 1.2k");
  expect(drawer).toHaveTextContent("provider input 500");

  // Warnings and excluded components.
  expect(screen.getByText("History truncated to fit the budget")).toBeInTheDocument();
  expect(screen.getByText("TOOLS")).toBeInTheDocument();
  expect(screen.getByText("not selected")).toBeInTheDocument();

  // Content is hidden until the user reveals it.
  await user.click(systemGroup);
  expect(drawer).toHaveTextContent("Content hidden (15 chars).");
  expect(screen.queryByText("You are helpful")).not.toBeInTheDocument();

  await user.click(screen.getAllByText("Reveal content")[0]);
  expect(await screen.findByText("You are helpful")).toBeInTheDocument();
  expect(screen.getByText("Hide content")).toBeInTheDocument();
});

test("renders a legacy unavailable context response without crashing", async () => {
  apiResponse = { available: false, run_id: "r1", message: "Legacy run — context unavailable" };
  renderDrawer();

  expect(await screen.findByTestId("context-drawer")).toBeInTheDocument();
  expect(await screen.findByText("Legacy run — context unavailable")).toBeInTheDocument();
});
