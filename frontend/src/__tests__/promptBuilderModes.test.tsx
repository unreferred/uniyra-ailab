import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PromptDetail from "@/pages/PromptDetail";
import {
  compilePrompt,
  createPromptVersion,
  getCompiledPrompt,
  getPrompt,
  getPromptCompilerCatalog,
  getPromptUsage,
  listCompiledPrompts,
  listProjectPolicies,
  listPromptBlocks,
  listPromptVersions,
  previewPromptCompilation,
  updatePromptVersion,
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
  getPrompt: jest.fn(),
  updatePrompt: jest.fn(),
  listPromptVersions: jest.fn(),
  createPromptVersion: jest.fn(),
  updatePromptVersion: jest.fn(),
  freezePromptVersion: jest.fn(),
  deprecatePromptVersion: jest.fn(),
  clonePromptVersion: jest.fn(),
  restorePromptVersion: jest.fn(),
  comparePromptVersions: jest.fn(),
  getPromptUsage: jest.fn(),
  listPromptBlocks: jest.fn(),
  listProjectPolicies: jest.fn(),
  getPromptCompilerCatalog: jest.fn(),
  previewPromptCompilation: jest.fn(),
  compilePrompt: jest.fn(),
  listCompiledPrompts: jest.fn(),
  getCompiledPrompt: jest.fn(),
}));

const mockedGetPrompt = getPrompt as jest.Mock;
const mockedListVersions = listPromptVersions as jest.Mock;
const mockedGetUsage = getPromptUsage as jest.Mock;
const mockedListBlocks = listPromptBlocks as jest.Mock;
const mockedListPolicies = listProjectPolicies as jest.Mock;
const mockedCreateVersion = createPromptVersion as jest.Mock;
const mockedUpdateVersion = updatePromptVersion as jest.Mock;
const mockedListCompiled = listCompiledPrompts as jest.Mock;

const now = "2026-01-01T00:00:00.000Z";

const spec = {
  id: "p1",
  project_id: "p1",
  name: "Research synthesis",
  purpose: "Summarise evidence",
  description: "Synthesise findings",
  status: "active",
  versions_count: 2,
  created_at: now,
  updated_at: now,
};

const versions = [
  {
    id: "v2",
    prompt_id: "p1",
    version: 2,
    status: "frozen",
    content: "Frozen content",
    system_prompt: "Frozen system",
    variables: {},
    structured_spec: null,
    notes: null,
    blocks: [],
    content_hash: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    created_at: now,
    updated_at: now,
    frozen_at: now,
  },
  {
    id: "v1",
    prompt_id: "p1",
    version: 1,
    status: "draft",
    content: "Raw draft",
    system_prompt: "Draft system",
    variables: {},
    structured_spec: {
      objective: "Old objective",
      blocks: ["pb-1"],
      policies: ["pol-1"],
      variables: {},
      phases: ["Plan"],
      validation: ["Review"],
      output_contract: { format: "json", required_sections: ["Summary"] },
    },
    notes: "initial",
    blocks: ["pb-1"],
    content_hash: null,
    created_at: now,
    updated_at: now,
  },
];

const blocks = [
  { id: "pb-1", project_id: "p1", name: "Role preamble", category: "role", status: "active", version: 1 },
];

const policies = [
  { id: "pol-1", project_id: "p1", name: "Safety policy", domain: "safety", status: "active", version: 1 },
  { id: "pol-2", project_id: "p1", name: "Archived policy", domain: "legacy", status: "archived", version: 1 },
];

const usage = {
  prompt_id: "p1",
  versions: [
    {
      id: "v1",
      version: 1,
      status: "draft",
      used_by: {
        experiments: [{ experiment_id: "exp-1", configuration_id: "cfg-1", label: "control" }],
        runs: [{ run_id: "run-1", experiment_id: "exp-1", status: "success", evaluations: [] }],
      },
    },
  ],
  totals: { experiments: 1, runs: 1, evaluations: 0 },
};

const catalog = {
  targets: ["openai.chat"],
  modes: ["chat"],
  trust_classes: ["TRUSTED_INSTRUCTION"],
  compiler_versions: ["pc-1.0"],
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedGetPrompt.mockResolvedValue(spec);
  mockedListVersions.mockResolvedValue(versions);
  mockedGetUsage.mockResolvedValue(usage);
  mockedListBlocks.mockResolvedValue(blocks);
  mockedListPolicies.mockResolvedValue(policies);
  mockedCreateVersion.mockResolvedValue({});
  mockedUpdateVersion.mockResolvedValue({});
  (getPromptCompilerCatalog as jest.Mock).mockResolvedValue(catalog);
  (previewPromptCompilation as jest.Mock).mockResolvedValue({});
  (compilePrompt as jest.Mock).mockResolvedValue({});
  mockedListCompiled.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  (getCompiledPrompt as jest.Mock).mockResolvedValue({});
});

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/prompt-lab/prompts/p1"]}>
        <Routes>
          <Route path="/prompt-lab/prompts/:id" element={<PromptDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function rowWithStatus(status: string): HTMLElement {
  const rows = screen.getAllByTestId("prompt-version-row");
  const row = rows.find((r) => within(r).getByTestId("prompt-version-status").textContent === status);
  if (!row) throw new Error(`no ${status} version row`);
  return row;
}

async function openNewDraft(user: ReturnType<typeof userEvent.setup>) {
  await screen.findAllByTestId("prompt-version-row");
  await user.click(screen.getByTestId("prompt-new-draft"));
  await screen.findByTestId("prompt-version-dialog");
}

test("a new draft opens in raw mode and warns that blocks/policies are not injected", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();
  await openNewDraft(user);

  expect(screen.getByTestId("prompt-version-content-input")).toBeInTheDocument();
  expect(screen.getByTestId("prompt-builder-mode-raw")).toHaveAttribute("data-state", "active");
  expect(screen.getByTestId("prompt-builder-raw-note")).toHaveTextContent("not injected");
});

test("simple mode deterministically assembles labelled content and persists role as system prompt", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();
  await openNewDraft(user);

  await user.click(screen.getByTestId("prompt-builder-mode-simple"));
  await user.type(await screen.findByTestId("prompt-builder-goal"), "Summarise the evidence");
  await user.type(screen.getByTestId("prompt-builder-role"), "Research analyst");
  const constraints = screen.getByTestId("prompt-builder-constraints");
  await user.type(constraints, "Be concise");
  await user.type(constraints, "{enter}Cite sources");
  await user.type(screen.getByTestId("prompt-builder-expected-output"), "A short brief");

  const preview = screen.getByTestId("prompt-builder-preview");
  expect(preview).toHaveTextContent("## Goal");
  expect(preview).toHaveTextContent("## Role");
  expect(preview).toHaveTextContent("## Constraints");
  expect(preview).toHaveTextContent("- Be concise");
  expect(preview).toHaveTextContent("- Cite sources");
  expect(preview).toHaveTextContent("## Expected output");

  await user.click(screen.getByTestId("prompt-new-draft-submit"));

  await waitFor(() => expect(mockedCreateVersion).toHaveBeenCalledTimes(1));
  const body = mockedCreateVersion.mock.calls[0][1];
  expect(body.system_prompt).toBe("Research analyst");
  expect(body.content).toContain("## Goal\nSummarise the evidence");
  expect(body.content).toContain("## Constraints\n- Be concise\n- Cite sources");
  expect(body.content).toContain("## Expected output\nA short brief");
  expect(body.structured_spec).toBeUndefined();
});

test("structured mode shows active policies and persists structured_spec, blocks and assembled content", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();
  await openNewDraft(user);

  await user.click(screen.getByTestId("prompt-builder-mode-structured"));
  await user.type(await screen.findByTestId("prompt-builder-objective"), "Explain the plan");

  const policyList = screen.getByTestId("prompt-builder-policies");
  await waitFor(() => expect(within(policyList).getAllByTestId("prompt-builder-policy")).toHaveLength(1));
  expect(within(policyList).getByTestId("prompt-builder-policy")).toHaveTextContent("Safety policy");
  expect(within(policyList).queryByText("Archived policy")).not.toBeInTheDocument();

  const phases = screen.getByTestId("prompt-builder-phases");
  await user.type(phases, "Draft");
  await user.type(phases, "{enter}Review");
  await user.type(screen.getByTestId("prompt-builder-validation"), "Check facts");
  await user.type(screen.getByTestId("prompt-builder-output-format"), "markdown");
  const sections = screen.getByTestId("prompt-builder-output-sections");
  await user.type(sections, "Summary");
  await user.type(sections, "{enter}Risks");
  await user.click(screen.getByTestId("prompt-version-block-pb-1"));

  const preview = screen.getByTestId("prompt-builder-preview");
  expect(preview).toHaveTextContent("## Objective");
  expect(preview).toHaveTextContent("## Execution phases");
  expect(preview).toHaveTextContent("## Validation");
  expect(preview).toHaveTextContent("## Output contract");
  expect(preview).toHaveTextContent("format: markdown");

  await user.click(screen.getByTestId("prompt-new-draft-submit"));
  await waitFor(() => expect(mockedCreateVersion).toHaveBeenCalledTimes(1));
  const body = mockedCreateVersion.mock.calls[0][1];

  expect(body.blocks).toEqual(["pb-1"]);
  expect(body.structured_spec.objective).toBe("Explain the plan");
  expect(body.structured_spec.phases).toEqual(["Draft", "Review"]);
  expect(body.structured_spec.validation).toEqual(["Check facts"]);
  expect(body.structured_spec.output_contract).toEqual({ format: "markdown", required_sections: ["Summary", "Risks"] });
  expect(body.structured_spec.policies).toEqual(["pol-1"]);
  expect(body.content).toContain("## Objective\nExplain the plan");
  expect(body.content).toContain("- Draft\n- Review");
});

test("editing a draft with a structured spec defaults to structured and round-trips it", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();
  await screen.findAllByTestId("prompt-version-row");

  await user.click(within(rowWithStatus("draft")).getByTestId("prompt-version-edit"));

  const objective = await screen.findByTestId("prompt-builder-objective");
  expect(screen.getByTestId("prompt-builder-mode-structured")).toHaveAttribute("data-state", "active");
  expect(objective).toHaveValue("Old objective");
  expect(screen.getByTestId("prompt-builder-preview")).toHaveTextContent("Old objective");

  await user.click(screen.getByTestId("prompt-new-draft-submit"));
  await waitFor(() => expect(mockedUpdateVersion).toHaveBeenCalledTimes(1));
  expect(mockedUpdateVersion.mock.calls[0][0]).toBe("p1");
  expect(mockedUpdateVersion.mock.calls[0][1]).toBe("v1");
  expect(mockedUpdateVersion.mock.calls[0][2].structured_spec.objective).toBe("Old objective");
});
