import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ArenaResults } from "@/components/arena/ArenaResults";
import { api } from "@/lib/api";
import { Experiment, Run } from "@/lib/types";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
}));

// react-markdown / remark-gfm ship as ESM-only; the component under test only
// needs the response text, so render it verbatim.
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
const mockedPost = api.post as jest.Mock;

const now = new Date("2026-01-01T00:00:00.000Z").toISOString();

function makeRun(id: string, content: string): Run {
  return {
    id,
    context: "arena",
    status: "success",
    content,
    normalized_response: null,
    model_name: undefined,
    family: null,
    latency_ms: 120,
    input_tokens: 10,
    output_tokens: 20,
    estimated_cost: null,
    cost_available: false,
    error: null,
    created_at: now,
  };
}

const hiddenRuns: Run[] = [makeRun("run-a", "Alpha response body"), makeRun("run-b", "Beta response body")];

const blindExperiment: Experiment = {
  id: "exp-blind",
  project_id: "p1",
  title: "Blind arena",
  experiment_type: "arena",
  model_ids: [],
  blind: true,
  revealed: false,
  status: "COMPLETED",
  run_count: hiddenRuns.length,
  created_at: now,
  updated_at: now,
  user_prompt: "Which answer is better?",
  runs: hiddenRuns,
  evaluations: [],
};

const REVEALED: Record<string, string> = { "run-a": "gpt-4o", "run-b": "claude-3-opus" };

function Harness() {
  const [experiment, setExperiment] = React.useState<Experiment>(blindExperiment);
  return (
    <ArenaResults
      experiment={experiment}
      onRefetch={async () => {
        // Simulate the server refetch returning the revealed payload.
        setExperiment((prev) => ({
          ...prev,
          revealed: true,
          runs: (prev.runs || []).map((r) => ({ ...r, model_name: REVEALED[r.id] ?? r.model_name })),
        }));
      }}
    />
  );
}

function renderArena() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <Harness />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  mockedGet.mockImplementation((url: string) => {
    if (url === "/models") return Promise.resolve({ data: [] });
    if (url === "/rubrics") return Promise.resolve({ data: [] });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  mockedPost.mockResolvedValue({ data: {} });
});

test("blind arena hides identities and cost, showing labels and response content", async () => {
  renderArena();

  expect(await screen.findByText("Response A")).toBeInTheDocument();
  expect(screen.getByText("Response B")).toBeInTheDocument();
  expect(screen.getByText("Alpha response body")).toBeInTheDocument();
  expect(screen.getByText("Beta response body")).toBeInTheDocument();

  // No model/provider identities and no cost telemetry while blind.
  expect(screen.queryByText(/gpt-4o/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/claude-3-opus/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/openai/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/\$/)).not.toBeInTheDocument();
  expect(screen.queryByTestId("telemetry-row")).not.toBeInTheDocument();
  expect(screen.getAllByTestId("blind-telemetry-row").length).toBeGreaterThan(0);
  expect(screen.getByTestId("arena-reveal")).toBeInTheDocument();
});

test("revealing models persists via POST and shows identities after refetch", async () => {
  const user = userEvent.setup();
  renderArena();

  await screen.findByText("Response A");
  await user.click(screen.getByTestId("arena-reveal"));

  expect(await screen.findByText(/Reveal model identities\?/i)).toBeInTheDocument();
  await user.click(screen.getByTestId("arena-reveal-confirm"));

  await waitFor(() => expect(mockedPost).toHaveBeenCalledWith("/experiments/exp-blind/reveal"));

  expect(await screen.findByText(/gpt-4o/i)).toBeInTheDocument();
  expect(screen.getByText(/claude-3-opus/i)).toBeInTheDocument();
  expect(screen.queryByText("Response A")).not.toBeInTheDocument();
  expect(screen.queryByTestId("blind-telemetry-row")).not.toBeInTheDocument();
});
