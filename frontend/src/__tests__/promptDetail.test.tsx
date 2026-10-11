import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PromptDetail from "@/pages/PromptDetail";
import {
  comparePromptVersions,
  createPromptVersion,
  deprecatePromptVersion,
  clonePromptVersion,
  freezePromptVersion,
  getPrompt,
  getPromptUsage,
  listPromptBlocks,
  listPromptVersions,
  restorePromptVersion,
  updatePrompt,
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
}));

const mockedGetPrompt = getPrompt as jest.Mock;
const mockedListVersions = listPromptVersions as jest.Mock;
const mockedGetUsage = getPromptUsage as jest.Mock;
const mockedListBlocks = listPromptBlocks as jest.Mock;
const mockedFreeze = freezePromptVersion as jest.Mock;
const mockedDeprecate = deprecatePromptVersion as jest.Mock;
const mockedClone = clonePromptVersion as jest.Mock;
const mockedRestore = restorePromptVersion as jest.Mock;
const mockedCreateVersion = createPromptVersion as jest.Mock;
const mockedUpdateVersion = updatePromptVersion as jest.Mock;
const mockedUpdatePrompt = updatePrompt as jest.Mock;
const mockedCompare = comparePromptVersions as jest.Mock;

const now = "2026-01-01T00:00:00.000Z";

const spec = {
  id: "p1",
  project_id: "p1",
  name: "Research synthesis",
  purpose: "Summarise evidence",
  description: "Synthesise findings",
  status: "active",
  versions_count: null,
  created_at: now,
  updated_at: now,
};

const versions = [
  {
    id: "v2",
    prompt_id: "p1",
    version: 2,
    status: "frozen",
    content: "New content",
    system_prompt: "New system",
    variables: { topic: "x" },
    structured_spec: { sections: [] },
    notes: null,
    blocks: ["pb-1"],
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
    content: "Old content",
    system_prompt: "Old system",
    variables: {},
    structured_spec: null,
    notes: "initial",
    blocks: [],
    content_hash: null,
    created_at: now,
    updated_at: now,
  },
];

const blocks = [
  { id: "pb-1", project_id: "p1", name: "Role preamble", category: "role", status: "active", version: 1 },
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
        runs: [
          {
            run_id: "run-1",
            experiment_id: "exp-1",
            status: "success",
            evaluations: [
              { id: "ev-1", outcome: "PASS", evaluator_type: "rubric" },
              { id: "ev-2", outcome: null, evaluator_type: "judge" },
            ],
          },
        ],
      },
    },
  ],
  totals: { experiments: 1, runs: 1, evaluations: 2 },
};

const diff = {
  from: { id: "v1", version: 1 },
  to: { id: "v2", version: 2 },
  content_diff: [
    { op: "equal", from_line: 1, to_line: 1, text: "same" },
    { op: "delete", from_line: 2, to_line: null, text: "old" },
    { op: "insert", from_line: null, to_line: 2, text: "new" },
  ],
  fields: [{ field: "system_prompt", changed: true, from: "Old system", to: "New system" }],
  variables: { added: ["topic"], removed: [], changed: [] },
  blocks: { added: ["pb-1"], removed: [] },
  summary: { lines_added: 1, lines_removed: 1, fields_changed: 1 },
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedGetPrompt.mockResolvedValue(spec);
  mockedListVersions.mockResolvedValue(versions);
  mockedGetUsage.mockResolvedValue(usage);
  mockedListBlocks.mockResolvedValue(blocks);
  mockedFreeze.mockResolvedValue({ id: "v1", status: "frozen" });
  mockedDeprecate.mockResolvedValue({});
  mockedClone.mockResolvedValue({});
  mockedRestore.mockResolvedValue({});
  mockedCreateVersion.mockResolvedValue({});
  mockedUpdateVersion.mockResolvedValue({});
  mockedUpdatePrompt.mockResolvedValue({});
  mockedCompare.mockResolvedValue(diff);
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

test("renders the prompt spec and its version rows", async () => {
  renderPage();

  expect(await screen.findByTestId("prompt-detail-page")).toBeInTheDocument();
  expect(await screen.findByText("Research synthesis")).toBeInTheDocument();
  const rows = await screen.findAllByTestId("prompt-version-row");
  expect(rows).toHaveLength(2);
  expect(within(rowWithStatus("frozen")).getByText("v2")).toBeInTheDocument();
  expect(within(rowWithStatus("draft")).getByText("v1")).toBeInTheDocument();
  expect(mockedGetPrompt).toHaveBeenCalledWith("p1");
  expect(mockedListVersions).toHaveBeenCalledWith("p1");
});

test("a missing version count renders n/a and never a fabricated value", async () => {
  renderPage();
  const count = await screen.findByTestId("prompt-versions-count");
  expect(count).toHaveTextContent("n/a");
});

test("the freeze action calls the helper for a draft version", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();
  await screen.findAllByTestId("prompt-version-row");

  await user.click(within(rowWithStatus("draft")).getByTestId("prompt-version-freeze"));
  await waitFor(() => expect(mockedFreeze).toHaveBeenCalledWith("p1", "v1"));
  expect(mockedFreeze).toHaveBeenCalledTimes(1);
});

test("a draft row shows Edit while a frozen row does not", async () => {
  renderPage();
  await screen.findAllByTestId("prompt-version-row");

  const draftEdit = within(rowWithStatus("draft")).getByTestId("prompt-version-edit");
  expect(draftEdit).toBeInTheDocument();
  expect(draftEdit).not.toBeDisabled();

  expect(within(rowWithStatus("frozen")).queryByTestId("prompt-version-edit")).not.toBeInTheDocument();
});

test("compare renders the diff lines, fields and summary from the backend", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();
  await screen.findAllByTestId("prompt-version-row");

  await waitFor(() => expect(screen.getByTestId("prompt-compare")).not.toBeDisabled());
  await user.click(screen.getByTestId("prompt-compare"));

  await waitFor(() => expect(mockedCompare).toHaveBeenCalledWith("p1", "v1", "v2"));

  const lines = await screen.findAllByTestId("prompt-diff-line");
  expect(lines).toHaveLength(3);
  expect(lines[1]).toHaveTextContent("old");
  expect(lines[2]).toHaveTextContent("new");

  const result = screen.getByTestId("prompt-compare-result");
  expect(within(result).getByTestId("prompt-compare-summary")).toHaveTextContent("lines added: 1");
  expect(within(result).getByTestId("prompt-diff-field")).toHaveTextContent("system_prompt");
});

test("compare shows an explicit no-differences state", async () => {
  mockedCompare.mockResolvedValue({
    from: { id: "v1", version: 1 },
    to: { id: "v2", version: 2 },
    content_diff: [{ op: "equal", from_line: 1, to_line: 1, text: "same" }],
    fields: [{ field: "content", changed: false, from: "same", to: "same" }],
    variables: { added: [], removed: [], changed: [] },
    blocks: { added: [], removed: [] },
    summary: { lines_added: 0, lines_removed: 0, fields_changed: 0 },
  });
  const user = userEvent.setup({ delay: null });
  renderPage();
  await screen.findAllByTestId("prompt-version-row");

  await waitFor(() => expect(screen.getByTestId("prompt-compare")).not.toBeDisabled());
  await user.click(screen.getByTestId("prompt-compare"));

  expect(await screen.findByTestId("prompt-compare-no-diff")).toHaveTextContent("No differences");
});

test("the usage panel renders experiment and run counts with n/a for missing outcomes", async () => {
  renderPage();

  const panel = await screen.findByTestId("prompt-usage-panel");
  expect(within(panel).getByTestId("prompt-usage-totals")).toHaveTextContent("experiments: 1");

  const usageVersion = await within(panel).findByTestId("prompt-usage-version");
  const counts = within(usageVersion).getByTestId("prompt-usage-version-counts");
  expect(counts).toHaveTextContent("experiments: 1");
  expect(counts).toHaveTextContent("runs: 1");

  const experimentLink = within(usageVersion).getByRole("link", { name: "control" });
  expect(experimentLink).toHaveAttribute("href", "/experiments/exp-1");

  const runLink = within(usageVersion).getByRole("link", { name: "run-1" });
  expect(runLink).toHaveAttribute("href", "/trace?run=run-1");

  const outcomes = within(usageVersion).getAllByTestId("prompt-usage-evaluation");
  expect(outcomes).toHaveLength(2);
  expect(outcomes.some((node) => node.textContent?.includes("n/a"))).toBe(true);
  expect(outcomes.some((node) => node.textContent?.includes("PASS"))).toBe(true);
});

test("the new draft dialog posts createPromptVersion with the chosen content", async () => {
  const user = userEvent.setup({ delay: null });
  renderPage();
  await screen.findAllByTestId("prompt-version-row");

  await user.click(screen.getByTestId("prompt-new-draft"));
  const content = await screen.findByTestId("prompt-version-content-input");
  await user.type(content, "Fresh draft content");
  await user.click(screen.getByTestId("prompt-new-draft-submit"));

  await waitFor(() =>
    expect(mockedCreateVersion).toHaveBeenCalledWith(
      "p1",
      expect.objectContaining({ content: "Fresh draft content" })
    )
  );
});
