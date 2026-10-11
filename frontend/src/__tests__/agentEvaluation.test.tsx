import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import AgentDetail from "@/pages/AgentDetail";
import {
  api,
  cloneAgentVersion,
  compareAgentVersions,
  createAgentVersion,
  deprecateAgentVersion,
  evaluateAgentRun,
  freezeAgentVersion,
  getAgent,
  getAgentCatalog,
  getAgentEvaluationCatalog,
  getAgentRun,
  getAgentUsage,
  listAgentRunEvaluations,
  listAgentRuns,
  listAgentVersions,
  runAgentVersion,
  updateAgent,
} from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
}));

// react-markdown/remark-gfm are ESM-only and break Jest transforms; the page
// lazy-loads its renderer, so stub it for the run output panel.
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
  getAgentCatalog: jest.fn(),
  getAgent: jest.fn(),
  updateAgent: jest.fn(),
  listAgentVersions: jest.fn(),
  createAgentVersion: jest.fn(),
  freezeAgentVersion: jest.fn(),
  deprecateAgentVersion: jest.fn(),
  cloneAgentVersion: jest.fn(),
  compareAgentVersions: jest.fn(),
  getAgentUsage: jest.fn(),
  runAgentVersion: jest.fn(),
  listAgentRuns: jest.fn(),
  getAgentRun: jest.fn(),
  getAgentEvaluationCatalog: jest.fn(),
  evaluateAgentRun: jest.fn(),
  listAgentRunEvaluations: jest.fn(),
}));

const mockedApiGet = api.get as jest.Mock;
const mockedCatalog = getAgentCatalog as jest.Mock;
const mockedEvalCatalog = getAgentEvaluationCatalog as jest.Mock;
const mockedGetAgent = getAgent as jest.Mock;
const mockedListVersions = listAgentVersions as jest.Mock;
const mockedGetUsage = getAgentUsage as jest.Mock;
const mockedRunAgentVersion = runAgentVersion as jest.Mock;
const mockedListAgentRuns = listAgentRuns as jest.Mock;
const mockedListEvaluations = listAgentRunEvaluations as jest.Mock;
const mockedEvaluateAgentRun = evaluateAgentRun as jest.Mock;
const mockedToast = toast as unknown as { success: jest.Mock; error: jest.Mock };

const now = "2026-01-01T00:00:00.000Z";

const catalog = {
  roles: ["engineer", "reviewer"],
  statuses: ["active", "archived"],
  version_states: ["draft", "frozen", "deprecated"],
};

const evaluationCatalog = {
  agent_rule_keys: ["assertions", "tool_usage"],
  prompt_rule_keys: ["prompt_contract"],
  agent_dimensions: ["correctness", "coverage"],
  prompt_dimensions: ["instruction_following"],
};

const agent = {
  id: "a1",
  project_id: "p1",
  name: "Release reviewer",
  description: "Reviews releases",
  role: "reviewer",
  purpose: "Review release candidates",
  responsibilities: ["review diffs"],
  non_responsibilities: ["deploy"],
  status: "active",
  latest_version: 2,
  created_at: now,
  updated_at: now,
};

const models = [
  { id: "m1", display_name: "Model One", family: "openai", enabled: true, model_identifier: "model-one", capabilities: {}, tags: [], notes: "" },
];

const versions = [
  {
    id: "av1",
    agent_id: "a1",
    project_id: "p1",
    version: 1,
    status: "frozen",
    model_id: "m1",
    prompt_version_id: null,
    content_hash: null,
    created_at: now,
    frozen_at: now,
  },
];

const usage = {
  agent_id: "a1",
  version_id: "av1",
  runs: [],
  evaluations: [],
  findings: [],
  scorecards: [],
};

const runResult = {
  run: {
    id: "run-9",
    status: "success",
    text: "done",
    normalized_response: null,
    latency_ms: 120,
    input_tokens: 10,
    output_tokens: 5,
    estimated_cost: null,
    cost_available: false,
    tool_call_count: 1,
  },
  run_id: "run-9",
  trace_id: "t-1",
  compiled_prompt_id: null,
  agent_id: "a1",
  agent_version_id: "av1",
  runtime_policy_applied: { applied: {}, unsupported: [], params: {} },
};

const evaluations = [
  {
    id: "ev-rule",
    run_id: "run-9",
    evaluation_type: "rubric",
    evaluator_type: "deterministic_rule",
    result: {
      outcome: "PASS",
      status: "completed",
      passed: true,
      score: 0.912,
      threshold: 0.7,
      confidence: null,
      reason: "met criteria",
    },
    scores: { correctness: 0.9 },
    provenance: { agent_version_id: "av1", compiled_prompt_id: null },
  },
  {
    id: "ev-judge",
    run_id: "run-9",
    evaluation_type: "llm_judge",
    evaluator_type: "judge_model",
    result: {
      outcome: "FAIL",
      status: "completed",
      passed: false,
      score: 0.4,
      threshold: 0.7,
      confidence: 0.8,
      reason: "weak",
    },
  },
  {
    id: "ev-none",
    run_id: "run-9",
    evaluation_type: "rubric",
    evaluator_type: "deterministic_rule",
    result: {
      outcome: "NOT_EVALUATED",
      status: "not_evaluated",
      passed: null,
      score: null,
      threshold: null,
      confidence: null,
      reason: null,
    },
  },
];

const judgeDetail = {
  id: "ev-judge",
  evaluation_type: "llm_judge",
  evaluator_type: "judge_model",
  evaluator: { type: "judge_model" },
  result: {
    outcome: "FAIL",
    status: "completed",
    passed: false,
    score: 0.4,
    threshold: 0.7,
    confidence: 0.8,
    reason: "weak",
  },
  run_id: "run-9",
  judge: { model: "m1", provider: "openai" },
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedCatalog.mockResolvedValue(catalog);
  mockedEvalCatalog.mockResolvedValue(evaluationCatalog);
  mockedGetAgent.mockResolvedValue(agent);
  mockedListVersions.mockResolvedValue(versions);
  mockedGetUsage.mockResolvedValue(usage);
  mockedRunAgentVersion.mockResolvedValue(runResult);
  mockedListAgentRuns.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  mockedListEvaluations.mockResolvedValue({
    items: evaluations,
    total: evaluations.length,
    limit: 20,
    offset: 0,
  });
  mockedEvaluateAgentRun.mockResolvedValue({
    run_id: "run-9",
    evaluations,
    rule_evaluation: evaluations[0],
    judge_evaluation: evaluations[1],
    profile_type: "agent",
  });
  mockedApiGet.mockImplementation((url: string) => {
    if (url === "/models") return Promise.resolve({ data: models });
    if (url === "/evaluations/ev-judge") return Promise.resolve({ data: judgeDetail });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

function renderAt(entry: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/agents/:id" element={<AgentDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

async function runAgent() {
  const user = userEvent.setup({ delay: null });
  renderAt("/agents/a1");
  await screen.findAllByTestId("agent-version-row");
  const frozen = screen
    .getAllByTestId("agent-version-row")
    .find((row) => within(row).getByTestId("agent-version-status").textContent === "frozen");
  if (!frozen) throw new Error("no frozen version row");
  await user.click(within(frozen).getByTestId("agent-run-open"));
  await screen.findByTestId("agent-run-dialog");
  await user.type(screen.getByTestId("agent-run-task"), "Evaluate this run");
  await user.click(screen.getByTestId("agent-run-submit"));
  await screen.findByTestId("agent-run-result");
  return user;
}

function rowContaining(text: string): HTMLElement {
  const row = screen.getAllByTestId("agent-eval-row").find((r) => r.textContent?.includes(text));
  if (!row) throw new Error(`no evaluation row containing ${text}`);
  return row;
}

test("the Evaluate action opens the evaluation dialog for a completed run", async () => {
  const user = await runAgent();
  const result = await screen.findByTestId("agent-run-result");

  await user.click(within(result).getByTestId("agent-run-evaluate"));

  const dialog = await screen.findByTestId("agent-evaluate-dialog");
  expect(dialog).toBeInTheDocument();
  expect(within(dialog).getByTestId("agent-evaluate-profile-type")).toBeInTheDocument();
});

test("submitting the dialog calls evaluateAgentRun with the selected rule keys", async () => {
  const user = await runAgent();
  const result = await screen.findByTestId("agent-run-result");
  await user.click(within(result).getByTestId("agent-run-evaluate"));
  await screen.findByTestId("agent-evaluate-dialog");

  await user.click(screen.getByTestId("agent-evaluate-rule-assertions"));
  await user.click(screen.getByTestId("agent-evaluate-submit"));

  await waitFor(() =>
    expect(mockedEvaluateAgentRun).toHaveBeenCalledWith(
      "run-9",
      expect.objectContaining({
        profile_type: "agent",
        evaluator_keys: ["assertions"],
        use_agent_profile: false,
        record_failures: false,
      })
    )
  );
  await waitFor(() => expect(mockedToast.success).toHaveBeenCalled());
});

test("evaluations render with outcome badges, derivation labels and n/a scores", async () => {
  await runAgent();
  await screen.findAllByTestId("agent-eval-row");

  const passRow = rowContaining("ev-rule");
  expect(within(passRow).getByTestId("agent-eval-outcome")).toHaveTextContent("PASS");
  expect(within(passRow).getByTestId("agent-eval-kind")).toHaveTextContent("deterministic");
  expect(within(passRow).getByTestId("agent-eval-score")).toHaveTextContent("0.912");

  const judgeRow = rowContaining("ev-judge");
  expect(within(judgeRow).getByTestId("agent-eval-outcome")).toHaveTextContent("FAIL");
  expect(within(judgeRow).getByTestId("agent-eval-kind")).toHaveTextContent("judge-derived");
  expect(within(judgeRow).getByTestId("agent-eval-evaluator")).toHaveTextContent("judge_model");

  const noneRow = rowContaining("ev-none");
  expect(within(noneRow).getByTestId("agent-eval-outcome")).toHaveTextContent("NOT_EVALUATED");
  expect(within(noneRow).getByTestId("agent-eval-score")).toHaveTextContent("n/a");
});

test("catalog dimensions are informational and unscored dimensions render NOT_EVALUATED", async () => {
  await runAgent();
  await screen.findAllByTestId("agent-eval-row");

  const dimensions = screen.getByTestId("agent-run-eval-dimensions");
  expect(dimensions).toHaveTextContent("correctness");
  expect(dimensions).toHaveTextContent("0.900");
  expect(dimensions).toHaveTextContent("coverage");
  expect(dimensions).toHaveTextContent("NOT_EVALUATED");
});

test("Details opens the existing evaluation detail dialog for GET /evaluations/{id}", async () => {
  const user = await runAgent();
  await screen.findAllByTestId("agent-eval-row");

  await user.click(within(rowContaining("ev-judge")).getByTestId("agent-eval-details"));

  const dialog = await screen.findByTestId("evaluation-detail-dialog");
  expect(dialog).toBeInTheDocument();
  await waitFor(() => expect(mockedApiGet).toHaveBeenCalledWith("/evaluations/ev-judge"));
});
