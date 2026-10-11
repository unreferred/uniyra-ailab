import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Certification from "@/pages/Certification";
import Scorecards from "@/pages/Scorecards";
import {
  buildScorecard,
  compareExperimentScorecards,
  createCertificationCandidate,
  evaluateCertificationReadiness,
  getCertificationCandidate,
  getCertificationPolicy,
  getExperimentScorecard,
  getRunScorecard,
  getScorecard,
  listCertificationCandidates,
  listScorecards,
} from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
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
  getCertificationPolicy: jest.fn(),
  listCertificationCandidates: jest.fn(),
  createCertificationCandidate: jest.fn(),
  getCertificationCandidate: jest.fn(),
  evaluateCertificationReadiness: jest.fn(),
  getRunScorecard: jest.fn(),
  getExperimentScorecard: jest.fn(),
  compareExperimentScorecards: jest.fn(),
  listScorecards: jest.fn(),
  buildScorecard: jest.fn(),
  getScorecard: jest.fn(),
}));

const mockedList = listCertificationCandidates as jest.Mock;
const mockedDetail = getCertificationCandidate as jest.Mock;
const mockedCreate = createCertificationCandidate as jest.Mock;
const mockedGetRun = getRunScorecard as jest.Mock;

const readiness = {
  candidate_id: "cand-1",
  state: "NOT_READY",
  ready: false,
  policy: { id: "pol-v6", version: "v6.0.0" },
  criteria: [
    { key: "evidence_present", passed: true, detail: "experiments=1, runs=1" },
    { key: "agent_versions_frozen", passed: true, detail: "1 frozen agent version" },
    { key: "prompt_versions_frozen", passed: false, detail: "non-frozen prompt version(s): pv1" },
    { key: "context_pack_versions_frozen", passed: true, detail: "1 frozen context pack version" },
    { key: "no_unresolved_high_drift", passed: false, detail: "1 unresolved high drift failure" },
  ],
  unresolved_failures: [
    {
      id: "f1",
      failure_domain: "QUALITY",
      taxonomy_code: "INSTRUCTION_FOLLOWING",
      severity: "high",
      status: "OPEN",
      title: "Instruction following failure",
    },
  ],
  unresolved_drift_failures: [
    {
      id: "df1",
      title: "Route drift",
      drift_type: "ROUTE_DRIFT",
      failure_domain: "DRIFT",
      taxonomy_code: "DRIFT.ROUTE",
      severity: "high",
      status: "OPEN",
    },
  ],
  bundle: {
    agent_versions: [{ id: "av1", version: 3, status: "frozen", exists: true }],
    prompt_versions: [{ id: "pv1", version: 2, status: "draft", exists: true }],
    context_pack_versions: [{ id: "cpv1", version: 1, status: "frozen", exists: true }],
    drift_analyses: [{ id: "da1", version: 1, status: "completed", exists: true }],
  },
  evaluated_at: "2026-01-01T00:00:00Z",
  automatic_certification: false,
  certification_performed: false,
};

const candidates = [
  {
    id: "cand-1",
    label: "Support agent v6",
    state: "NOT_READY",
    project_id: "p1",
    subject: { type: "model", model_id: "m1" },
    evidence: { run_ids: ["r1"], agent_version_ids: ["av1"] },
    readiness,
  },
  {
    id: "cand-2",
    label: "Research agent",
    state: "BLOCKED",
    project_id: "p1",
    subject: { type: "configuration", configuration_id: "cfg-2" },
    evidence: {},
    readiness: { ...readiness, candidate_id: "cand-2", state: "BLOCKED", ready: false },
  },
  {
    id: "cand-3",
    label: "Triage agent",
    state: "READY_FOR_CERTIFICATION",
    project_id: "p1",
    subject: { type: "run", run_id: "r3" },
    evidence: { run_ids: ["r3"] },
    readiness: {
      ...readiness,
      candidate_id: "cand-3",
      state: "READY_FOR_CERTIFICATION",
      ready: true,
      unresolved_failures: [],
      unresolved_drift_failures: [],
    },
  },
];

const v6Scorecard = {
  id: "sc-v6",
  scope: "run",
  run_id: "run-v6",
  builder_version: "v6.9.0",
  dimensions: [
    { key: "policy_compliance", value: 0.91, unit: "score", state: "available", source: "deterministic" },
    { key: "invariant_compliance", value: null, unit: "score", state: "not_available", source: "deterministic" },
    { key: "task_completion", value: 0.75, unit: "score", state: "available", source: "judge_derived" },
    { key: "context_usage", value: null, unit: "ratio", state: "not_evaluated", source: "deterministic" },
    { key: "drift", value: 2, unit: "count", state: "available", source: "deterministic" },
  ],
  composite: { available: false, reason: "no scoring policy configured" },
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedList.mockResolvedValue({ items: candidates, total: candidates.length, limit: 50, offset: 0 });
  mockedDetail.mockResolvedValue(candidates[0]);
  (getCertificationPolicy as jest.Mock).mockResolvedValue({
    id: "pol-v6",
    version: "v6.0.0",
    criteria: [{ key: "agent_versions_frozen", description: "Every referenced agent version is frozen" }],
  });
  mockedCreate.mockResolvedValue({ id: "cand-new" });
  (evaluateCertificationReadiness as jest.Mock).mockResolvedValue(readiness);
  mockedGetRun.mockResolvedValue(v6Scorecard);
  (getExperimentScorecard as jest.Mock).mockResolvedValue(v6Scorecard);
  (compareExperimentScorecards as jest.Mock).mockResolvedValue({ scope: "comparison", rows: [] });
  (listScorecards as jest.Mock).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
  (buildScorecard as jest.Mock).mockResolvedValue(v6Scorecard);
  (getScorecard as jest.Mock).mockResolvedValue(v6Scorecard);
});

function renderCertification() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Certification />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function renderScorecards() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Scorecards />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

test("create form accepts the new V6 evidence inputs and submits them", async () => {
  renderCertification();
  await screen.findAllByTestId("certification-candidate-row");

  fireEvent.click(screen.getByTestId("certification-create-open"));
  fireEvent.change(screen.getByTestId("certification-label"), { target: { value: "V6 agent" } });
  fireEvent.change(screen.getByTestId("certification-evidence-agent-versions"), { target: { value: "av1, av2" } });
  fireEvent.change(screen.getByTestId("certification-evidence-prompt-versions"), { target: { value: "pv1\npv2" } });
  fireEvent.change(screen.getByTestId("certification-evidence-context-versions"), { target: { value: "cpv1" } });
  fireEvent.change(screen.getByTestId("certification-evidence-drift"), { target: { value: "da1, da2" } });
  fireEvent.click(screen.getByTestId("certification-create-submit"));

  await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
  const body = mockedCreate.mock.calls[0][0];
  expect(body.project_id).toBe("p1");
  expect(body.evidence.agent_version_ids).toEqual(["av1", "av2"]);
  expect(body.evidence.prompt_version_ids).toEqual(["pv1", "pv2"]);
  expect(body.evidence.context_pack_version_ids).toEqual(["cpv1"]);
  expect(body.evidence.drift_analysis_ids).toEqual(["da1", "da2"]);
});

test("candidate detail renders the new criteria, snapshots, and drift failure rows", async () => {
  renderCertification();
  await screen.findAllByTestId("certification-candidate-row");

  const criteria = screen.getAllByTestId("certification-criterion");
  const keys = criteria.map((c) => c.textContent || "");
  expect(keys.some((k) => k.includes("agent_versions_frozen"))).toBe(true);
  expect(keys.some((k) => k.includes("prompt_versions_frozen"))).toBe(true);
  expect(keys.some((k) => k.includes("context_pack_versions_frozen"))).toBe(true);
  expect(keys.some((k) => k.includes("no_unresolved_high_drift"))).toBe(true);

  expect(screen.getByTestId("certification-snapshot-agent-versions")).toHaveTextContent("av1");
  expect(screen.getByTestId("certification-snapshot-prompt-versions")).toHaveTextContent("pv1");
  expect(screen.getByTestId("certification-snapshot-context-pack-versions")).toHaveTextContent("cpv1");
  expect(screen.getByTestId("certification-snapshot-drift-analyses")).toHaveTextContent("da1");

  const driftRows = screen.getAllByTestId("certification-drift-failure");
  expect(driftRows).toHaveLength(1);
  expect(driftRows[0]).toHaveTextContent("Route drift");
  expect(driftRows[0]).toHaveTextContent("DRIFT.ROUTE");
  expect(driftRows[0]).toHaveTextContent("high");
  expect(driftRows[0]).toHaveTextContent("OPEN");
});

test("READY_FOR_CERTIFICATION renders but no Certified state or claim is shown", async () => {
  renderCertification();
  await screen.findAllByTestId("certification-candidate-row");

  const states = screen.getAllByTestId("certification-state").map((b) => b.textContent);
  expect(states).toContain("READY_FOR_CERTIFICATION");
  expect(states).toContain("NOT_READY");
  expect(states).toContain("BLOCKED");
  expect(screen.getByTestId("certification-no-auto")).toHaveTextContent(/V7/);
  expect(screen.queryByText(/Certified/)).not.toBeInTheDocument();
});

test("scorecard V6 dimensions render with source labels and n/a for null values", async () => {
  renderScorecards();
  fireEvent.change(screen.getByTestId("scorecard-run-id"), { target: { value: "run-v6" } });
  const rows = await screen.findAllByTestId("scorecard-dimension-row");
  expect(rows).toHaveLength(5);

  const byLabel = (label: string) =>
    rows.find((r) => within(r).queryByText(label)) as HTMLElement;

  expect(byLabel("Policy compliance")).toBeTruthy();
  expect(byLabel("Invariant compliance")).toBeTruthy();
  expect(byLabel("Task completion")).toBeTruthy();
  expect(byLabel("Context usage")).toBeTruthy();
  expect(byLabel("Drift")).toBeTruthy();

  const invariant = byLabel("Invariant compliance");
  expect(within(invariant).getByTestId("scorecard-dimension-value")).toHaveTextContent("n/a");
  const invariantState = within(invariant).getByTestId("scorecard-dimension-state");
  expect(invariantState).toHaveTextContent("not available");
  expect(invariantState).not.toHaveTextContent("NOT_EVALUATED");

  const context = byLabel("Context usage");
  expect(within(context).getByTestId("scorecard-dimension-value")).toHaveTextContent("n/a");
  expect(within(context).getByTestId("scorecard-dimension-state")).toHaveTextContent("NOT_EVALUATED");

  const sources = screen.getAllByTestId("scorecard-dimension-source").map((b) => b.textContent);
  expect(sources).toContain("deterministic");
  expect(sources).toContain("judge-derived");
});
