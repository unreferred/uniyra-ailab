import React from "react";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Certification from "@/pages/Certification";
import {
  createCertificationCandidate,
  evaluateCertificationReadiness,
  getCertificationCandidate,
  getCertificationPolicy,
  listCertificationCandidates,
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
}));

const mockedList = listCertificationCandidates as jest.Mock;
const mockedDetail = getCertificationCandidate as jest.Mock;

const readiness = {
  candidate_id: "cand-1",
  state: "NOT_READY",
  ready: false,
  policy: { id: "pol-1", version: "v5.10" },
  criteria: [
    { key: "evidence-complete", passed: true, detail: "8 evidence ids attached" },
    { key: "no-unresolved-failures", passed: false, detail: "1 unresolved failure" },
  ],
  unresolved_failures: [
    {
      id: "f1",
      run_id: "r1",
      failure_domain: "QUALITY",
      taxonomy_code: "INSTRUCTION_FOLLOWING",
      severity: "high",
      status: "OPEN",
      title: "Instruction following failure",
    },
  ],
  evaluated_at: "2026-01-01T00:00:00Z",
  automatic_certification: false,
  certification_performed: false,
};

const candidates = [
  {
    id: "cand-1",
    label: "Support agent v3",
    state: "NOT_READY",
    project_id: "p1",
    subject: { type: "model", model_id: "m1" },
    evidence: { run_ids: ["r1"], evaluation_ids: ["e1"] },
    environment: {},
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
    readiness: { ...readiness, candidate_id: "cand-3", state: "READY_FOR_CERTIFICATION", ready: true, unresolved_failures: [] },
  },
];

beforeEach(() => {
  jest.clearAllMocks();
  mockedList.mockResolvedValue({ items: candidates, total: candidates.length, limit: 50, offset: 0 });
  mockedDetail.mockResolvedValue(candidates[0]);
  (getCertificationPolicy as jest.Mock).mockResolvedValue({ id: "pol-1", version: "v5.10", criteria: [{ key: "evidence-complete", description: "Evidence must be complete" }] });
  (createCertificationCandidate as jest.Mock).mockResolvedValue({ id: "cand-new" });
  (evaluateCertificationReadiness as jest.Mock).mockResolvedValue(readiness);
});

function renderPage() {
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

test("renders candidate list and all readiness state badges", async () => {
  renderPage();
  expect(screen.getByTestId("certification-page")).toBeInTheDocument();

  const rows = await screen.findAllByTestId("certification-candidate-row");
  expect(rows).toHaveLength(3);

  const states = screen.getAllByTestId("certification-state").map((b) => b.textContent);
  expect(states).toContain("NOT_READY");
  expect(states).toContain("BLOCKED");
  expect(states).toContain("READY_FOR_CERTIFICATION");
  expect(screen.getByTestId("certification-evaluate-readiness")).toBeInTheDocument();
});

test("renders criteria pass/fail and unresolved failures", async () => {
  renderPage();
  await screen.findAllByTestId("certification-candidate-row");

  const criteria = screen.getAllByTestId("certification-criterion");
  expect(criteria).toHaveLength(2);
  const passing = criteria.find((c) => within(c).queryByText("evidence-complete")) as HTMLElement;
  expect(within(passing).getByText("pass")).toBeInTheDocument();
  const failing = criteria.find((c) => within(c).queryByText("no-unresolved-failures")) as HTMLElement;
  expect(within(failing).getByText("fail")).toBeInTheDocument();

  const failures = screen.getAllByTestId("certification-unresolved-failure");
  expect(failures).toHaveLength(1);
  expect(failures[0]).toHaveTextContent("Instruction following failure");
  expect(failures[0]).toHaveTextContent("QUALITY");
  expect(failures[0]).toHaveTextContent("INSTRUCTION_FOLLOWING");
});

test("shows policy version and never claims a certification", async () => {
  renderPage();
  await screen.findAllByTestId("certification-candidate-row");

  expect(screen.getByTestId("certification-policy-version")).toHaveTextContent("v5.10");
  const note = screen.getByTestId("certification-no-auto");
  expect(note).toHaveTextContent(/V7/);

  // No state is ever presented as "Certified".
  expect(screen.queryByText(/Certified/)).not.toBeInTheDocument();
});
