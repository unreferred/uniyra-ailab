import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Arena from "@/pages/Arena";
import {
  api,
  createAgentArena,
  createPromptArena,
  getArenaV6Catalog,
  getArenaV6Summary,
  listAgentVersions,
  listAgents,
  listArenasV6,
  listPromptVersions,
  listPrompts,
} from "@/lib/api";

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
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  uploadAttachments: jest.fn(),
  getArenaV6Catalog: jest.fn(),
  createAgentArena: jest.fn(),
  createPromptArena: jest.fn(),
  listArenasV6: jest.fn(),
  getArenaV6Summary: jest.fn(),
  listAgents: jest.fn(),
  listAgentVersions: jest.fn(),
  listPrompts: jest.fn(),
  listPromptVersions: jest.fn(),
}));

const mockedApiGet = api.get as jest.Mock;
const mockedCatalog = getArenaV6Catalog as jest.Mock;
const mockedCreateAgentArena = createAgentArena as jest.Mock;
const mockedCreatePromptArena = createPromptArena as jest.Mock;
const mockedListArenas = listArenasV6 as jest.Mock;
const mockedSummary = getArenaV6Summary as jest.Mock;
const mockedListAgents = listAgents as jest.Mock;
const mockedListAgentVersions = listAgentVersions as jest.Mock;
const mockedListPrompts = listPrompts as jest.Mock;
const mockedListPromptVersions = listPromptVersions as jest.Mock;

const now = "2026-01-01T00:00:00.000Z";

const models = [
  { id: "m1", display_name: "Model One", family: "openai", enabled: true, model_identifier: "model-one", capabilities: {}, tags: [], notes: "" },
];

const rubrics = [{ id: "r1", name: "Quality", description: "", dimensions: [], created_at: now, updated_at: now }];
const catalog = { arena_types: ["agent_arena", "prompt_arena"], evaluation_keys: ["assertions", "rubric"], routing_modes: ["fixed"] };

const datasets = [{ id: "d1", project_id: "p1", name: "Dataset One", description: "", tags: [], created_at: now, updated_at: now }];
const datasetVersions = [
  { id: "dv1", dataset_id: "d1", project_id: "p1", version: 1, status: "frozen", case_count: 2, created_at: now },
  { id: "dv2", dataset_id: "d1", project_id: "p1", version: 2, status: "draft", case_count: 3, created_at: now },
];

const agents = [{ id: "a1", project_id: "p1", name: "Agent One", status: "active", created_at: now, updated_at: now }];
const agentVersions = [
  { id: "av1", agent_id: "a1", project_id: "p1", version: 1, status: "frozen", model_id: "m1", created_at: now, frozen_at: now },
  { id: "av2", agent_id: "a1", project_id: "p1", version: 2, status: "deprecated", model_id: null, created_at: now, deprecated_at: now },
  { id: "av3", agent_id: "a1", project_id: "p1", version: 3, status: "draft", model_id: "m1", created_at: now },
];

const prompts = [{ id: "ps1", project_id: "p1", name: "Prompt One", status: "active" }];
const promptVersions = [
  { id: "pv1", prompt_id: "ps1", project_id: "p1", version: 1, status: "frozen", created_at: now, frozen_at: now },
  { id: "pv2", prompt_id: "ps1", project_id: "p1", version: 2, status: "draft", created_at: now },
];

const summary = {
  experiment: { id: "exp-a", title: "Arena one", status: "READY", experiment_type: "agent_arena" },
  configurations: [
    {
      configuration_id: "cfg1",
      label: "Agent One",
      model_id: "m1",
      agent_version_id: "av1",
      prompt_version_id: null,
      run_count: 2,
      completed: 1,
      failed: 1,
      completion_rate: 0.5,
      failure_rate: 0.5,
      avg_score: null,
      tool_violations: null,
      policy_violations: 1,
      latency_ms: null,
      input_tokens: null,
      output_tokens: null,
      total_tokens: null,
      estimated_cost: null,
      evaluation_outcomes: null,
    },
  ],
  summary: {},
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedCatalog.mockResolvedValue(catalog);
  mockedCreateAgentArena.mockResolvedValue({ id: "exp-agent", title: "Agent arena one", status: "READY" });
  mockedCreatePromptArena.mockResolvedValue({ id: "exp-prompt", title: "Prompt arena one", status: "READY" });
  mockedListArenas.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  mockedSummary.mockResolvedValue(summary);
  mockedListAgents.mockResolvedValue(agents);
  mockedListAgentVersions.mockResolvedValue(agentVersions);
  mockedListPrompts.mockResolvedValue(prompts);
  mockedListPromptVersions.mockResolvedValue(promptVersions);

  mockedApiGet.mockImplementation((url: string) => {
    if (url === "/models") return Promise.resolve({ data: models });
    if (url === "/rubrics") return Promise.resolve({ data: rubrics });
    if (url === "/attachments") return Promise.resolve({ data: { items: [], total: 0, limit: 20, offset: 0 } });
    if (url === "/knowledge") return Promise.resolve({ data: [] });
    if (url === "/datasets") return Promise.resolve({ data: datasets });
    if (url === "/datasets/d1/versions") return Promise.resolve({ data: datasetVersions });
    if (url.endsWith("/benchmark")) return Promise.resolve({ data: { plan: null, progress: null, configurations: [] } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Arena />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

async function selectOption(user: ReturnType<typeof userEvent.setup>, testId: string, value: string) {
  await user.selectOptions(await screen.findByTestId(testId), value);
}

test("renders the three arena tabs and preserves the Model Arena", async () => {
  renderPage();

  expect(screen.getByTestId("arena-v6-page")).toBeInTheDocument();
  expect(screen.getByTestId("arena-tab-model")).toBeInTheDocument();
  expect(screen.getByTestId("arena-tab-agent")).toBeInTheDocument();
  expect(screen.getByTestId("arena-tab-prompt")).toBeInTheDocument();

  // The existing Model Arena content is still mounted on the default tab.
  expect(screen.getByTestId("arena-config")).toBeInTheDocument();
  expect(screen.getByTestId("arena-run")).toBeInTheDocument();
  expect(screen.getByTestId("arena-task-input")).toBeInTheDocument();
});

test("Agent Arena create calls createAgentArena with the selected frozen agent version", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();

  await user.click(screen.getByTestId("arena-tab-agent"));
  expect(await screen.findByTestId("arena-agent-create")).toBeInTheDocument();

  await selectOption(user, "arena-agent-dataset", "d1");
  await selectOption(user, "arena-agent-dataset-version", "dv1");

  // Only frozen/deprecated agent versions are selectable.
  const agentVersionsBox = await screen.findByTestId("arena-agent-versions");
  expect(within(agentVersionsBox).getByTestId("arena-agent-version-av1")).toBeInTheDocument();
  expect(within(agentVersionsBox).getByTestId("arena-agent-version-av2")).toBeInTheDocument();
  expect(within(agentVersionsBox).queryByTestId("arena-agent-version-av3")).not.toBeInTheDocument();

  await user.click(within(agentVersionsBox).getByTestId("arena-agent-version-av1"));
  await user.type(screen.getByTestId("arena-agent-title"), "Agent arena one");
  await user.click(screen.getByTestId("arena-agent-submit"));

  await waitFor(() => expect(mockedCreateAgentArena).toHaveBeenCalledTimes(1));
  expect(mockedCreateAgentArena).toHaveBeenCalledWith(
    expect.objectContaining({
      project_id: "p1",
      title: "Agent arena one",
      dataset_id: "d1",
      dataset_version_id: "dv1",
      agent_version_ids: ["av1"],
    })
  );
});

test("the summary renders config rows and n/a for null avg score and cost", async () => {
  mockedListArenas.mockResolvedValue({ items: [{ id: "exp-a", title: "Arena one", status: "READY" }], total: 1, limit: 50, offset: 0 });
  renderPage();

  const rows = await screen.findAllByTestId("arena-v6-config-row");
  expect(rows).toHaveLength(1);
  const row = rows[0];
  expect(within(row).getByTestId("arena-v6-avg-score")).toHaveTextContent("n/a");
  expect(within(row).getByTestId("arena-v6-cost")).toHaveTextContent("n/a");
  expect(within(row).getByTestId("arena-v6-tool-violations")).toHaveTextContent("n/a");
  // Known values are still rendered verbatim (never fabricated zeros).
  expect(within(row).getByTestId("arena-v6-completion")).toHaveTextContent("50.0%");

  expect(await screen.findByTestId("arena-v6-list-link")).toHaveAttribute("href", "/experiments/exp-a");
  await waitFor(() => expect(mockedSummary).toHaveBeenCalledWith("exp-a"));
});

test("Prompt Arena create calls createPromptArena with the model and frozen prompt version", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();

  await user.click(screen.getByTestId("arena-tab-prompt"));
  expect(await screen.findByTestId("arena-prompt-create")).toBeInTheDocument();

  await selectOption(user, "arena-prompt-dataset", "d1");
  await selectOption(user, "arena-prompt-dataset-version", "dv1");
  await selectOption(user, "arena-prompt-model", "m1");

  const promptVersionsBox = await screen.findByTestId("arena-prompt-versions");
  expect(within(promptVersionsBox).getByTestId("arena-prompt-version-pv1")).toBeInTheDocument();
  expect(within(promptVersionsBox).queryByTestId("arena-prompt-version-pv2")).not.toBeInTheDocument();

  await user.click(within(promptVersionsBox).getByTestId("arena-prompt-version-pv1"));
  await user.type(screen.getByTestId("arena-prompt-title"), "Prompt arena one");
  await user.click(screen.getByTestId("arena-prompt-submit"));

  await waitFor(() => expect(mockedCreatePromptArena).toHaveBeenCalledTimes(1));
  expect(mockedCreatePromptArena).toHaveBeenCalledWith(
    expect.objectContaining({
      project_id: "p1",
      title: "Prompt arena one",
      dataset_id: "d1",
      dataset_version_id: "dv1",
      model_id: "m1",
      prompt_version_ids: ["pv1"],
    })
  );
});
