import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PromptLab from "@/pages/PromptLab";
import {
  createProjectPolicyVersion,
  createStarterPromptBlocks,
  getInvariantCatalog,
  getPolicyCatalog,
  getPromptBlockCatalog,
  listProjectInvariants,
  listProjectPolicies,
  listPromptBlocks,
  listPrompts,
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
  listPrompts: jest.fn(),
  createPrompt: jest.fn(),
  getPrompt: jest.fn(),
  updatePrompt: jest.fn(),
  deletePrompt: jest.fn(),
  listPromptBlocks: jest.fn(),
  getPromptBlockCatalog: jest.fn(),
  createPromptBlock: jest.fn(),
  updatePromptBlock: jest.fn(),
  deletePromptBlock: jest.fn(),
  createStarterPromptBlocks: jest.fn(),
  listProjectPolicies: jest.fn(),
  getPolicyCatalog: jest.fn(),
  createProjectPolicy: jest.fn(),
  getProjectPolicy: jest.fn(),
  listProjectPolicyVersions: jest.fn(),
  createProjectPolicyVersion: jest.fn(),
  updateProjectPolicyStatus: jest.fn(),
  listProjectInvariants: jest.fn(),
  getInvariantCatalog: jest.fn(),
  createProjectInvariant: jest.fn(),
  updateProjectInvariant: jest.fn(),
  deleteProjectInvariant: jest.fn(),
}));

const mockedListPrompts = listPrompts as jest.Mock;
const mockedListBlocks = listPromptBlocks as jest.Mock;
const mockedBlockCatalog = getPromptBlockCatalog as jest.Mock;
const mockedListPolicies = listProjectPolicies as jest.Mock;
const mockedPolicyCatalog = getPolicyCatalog as jest.Mock;
const mockedListInvariants = listProjectInvariants as jest.Mock;
const mockedInvariantCatalog = getInvariantCatalog as jest.Mock;
const mockedStarters = createStarterPromptBlocks as jest.Mock;
const mockedNewVersion = createProjectPolicyVersion as jest.Mock;

const now = "2026-01-01T00:00:00.000Z";

const prompts = [
  {
    id: "ps-1",
    project_id: "p1",
    name: "Research synthesis",
    purpose: "Summarise evidence",
    status: "active",
    versions_count: 3,
    updated_at: now,
  },
  {
    id: "ps-2",
    project_id: "p1",
    name: "Triage",
    purpose: null,
    status: "draft",
    versions_count: null,
    updated_at: null,
  },
];

const blocks = [
  { id: "pb-1", project_id: "p1", name: "Role preamble", category: "role", version: 2, status: "active" },
];

const policies = [
  { id: "pol-1", project_id: "p1", name: "Safety policy", domain: "safety", version: 1, status: "active" },
];

const invariants = [
  { id: "inv-1", project_id: "p1", code: "NO_PII", domain: "privacy", severity: "high", enforcement_mode: "block", status: "active" },
];

beforeEach(() => {
  jest.clearAllMocks();
  mockedListPrompts.mockResolvedValue(prompts);
  mockedListBlocks.mockResolvedValue(blocks);
  mockedBlockCatalog.mockResolvedValue({
    categories: [{ key: "role", label: "Role" }],
    statuses: ["active", "draft"],
  });
  mockedListPolicies.mockResolvedValue(policies);
  mockedPolicyCatalog.mockResolvedValue({ domains: [{ key: "safety", label: "Safety" }], statuses: ["active"] });
  mockedListInvariants.mockResolvedValue(invariants);
  mockedInvariantCatalog.mockResolvedValue({
    domains: [{ key: "privacy", label: "Privacy" }],
    severities: ["low", "high"],
    enforcement_modes: ["block", "warn"],
    statuses: ["active"],
  });
  mockedStarters.mockResolvedValue({ created: [{ id: "pb-new" }], existing: [] });
  mockedNewVersion.mockResolvedValue({ id: "pol-1-v2", version: 2 });
});

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PromptLab />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

test("renders the prompt lab page with prompt spec rows", async () => {
  renderPage();
  expect(screen.getByTestId("prompt-lab-page")).toBeInTheDocument();
  const rows = await screen.findAllByTestId("prompt-spec-row");
  expect(rows).toHaveLength(2);
  expect(within(rows[0]).getByText("Research synthesis")).toBeInTheDocument();
  expect(mockedListPrompts).toHaveBeenCalledWith("p1");
});

test("a missing prompt value renders n/a and never a fabricated value", async () => {
  renderPage();
  const rows = await screen.findAllByTestId("prompt-spec-row");
  const triage = rows.find((r) => within(r).queryByText("Triage")) as HTMLElement;
  expect(triage).toBeTruthy();
  expect(within(triage).getAllByText("n/a").length).toBeGreaterThanOrEqual(2);
});

test("blocks tab renders blocks and the starter action calls the helper", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();
  await screen.findAllByTestId("prompt-spec-row");

  await user.click(screen.getByTestId("prompt-lab-tab-blocks"));
  const rows = await screen.findAllByTestId("prompt-block-row");
  expect(within(rows[0]).getByText("Role preamble")).toBeInTheDocument();

  await user.click(screen.getByTestId("prompt-block-starters"));
  await waitFor(() => expect(mockedStarters).toHaveBeenCalledWith("p1"));
});

test("policies tab renders rows and the new-version action calls the helper", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();
  await screen.findAllByTestId("prompt-spec-row");

  await user.click(screen.getByTestId("prompt-lab-tab-policies"));
  const rows = await screen.findAllByTestId("prompt-policy-row");
  expect(within(rows[0]).getByText("Safety policy")).toBeInTheDocument();

  await user.click(screen.getByTestId("prompt-policy-new-version"));
  const content = await screen.findByTestId("prompt-policy-version-content");
  await user.type(content, "Updated policy content");
  await user.click(screen.getByTestId("prompt-policy-new-version-submit"));

  await waitFor(() =>
    expect(mockedNewVersion).toHaveBeenCalledWith("pol-1", expect.objectContaining({ content: "Updated policy content" }))
  );
});

test("invariants tab renders rows with code, severity and enforcement", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();
  await screen.findAllByTestId("prompt-spec-row");

  await user.click(screen.getByTestId("prompt-lab-tab-invariants"));
  const rows = await screen.findAllByTestId("prompt-invariant-row");
  expect(within(rows[0]).getByText("NO_PII")).toBeInTheDocument();
  expect(within(rows[0]).getByText("high")).toBeInTheDocument();
  expect(within(rows[0]).getByText("block")).toBeInTheDocument();
});
