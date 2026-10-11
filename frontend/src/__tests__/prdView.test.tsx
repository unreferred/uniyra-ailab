import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PrdView } from "@/components/workspace/PrdView";
import {
  approvePrdVersion,
  comparePrdVersions,
  generatePrd,
  getPrdReadiness,
  getPrdVersion,
  listPrdVersions,
} from "@/lib/api";
import { PrdDiff, PrdReadiness, PrdVersion, PrdVersionSummary } from "@/lib/types";

jest.mock("@floating-ui/dom", () => {
  const actual = jest.requireActual("@floating-ui/dom");
  return {
    ...actual,
    computePosition: () =>
      Promise.resolve({ x: 0, y: 0, strategy: "fixed", placement: "bottom", middlewareData: {} }),
  };
});

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn(), message: jest.fn() },
}));

jest.mock("@/components/common/Markdown", () => ({
  MarkdownView: ({ content }: { content: string }) => <div data-testid="markdown-view">{content}</div>,
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  getPrdReadiness: jest.fn(),
  generatePrd: jest.fn(),
  listPrdVersions: jest.fn(),
  getPrdVersion: jest.fn(),
  comparePrdVersions: jest.fn(),
  approvePrdVersion: jest.fn(),
}));

const mockedReadiness = getPrdReadiness as jest.Mock;
const mockedGenerate = generatePrd as jest.Mock;
const mockedList = listPrdVersions as jest.Mock;
const mockedGetVersion = getPrdVersion as jest.Mock;
const mockedCompare = comparePrdVersions as jest.Mock;
const mockedApprove = approvePrdVersion as jest.Mock;

const now = "2026-01-02T00:00:00.000Z";

const readiness: PrdReadiness = {
  project_id: "p1",
  conversation_id: "c1",
  dimensions: [
    { key: "decisions", label: "Confirmed decisions", state: "READY", detail: "3 confirmed decisions", item_count: 3 },
    { key: "verification", label: "Verification items", state: "NEEDS_VERIFICATION", detail: "2 items awaiting verification", item_count: 2 },
    { key: "conflicts", label: "Unresolved conflicts", state: "BLOCKED", detail: "1 conflict blocks generation", item_count: 1 },
    { key: "sections", label: "Required sections", state: "INCOMPLETE", detail: "No sections compiled yet", item_count: 0 },
  ],
  blockers: [{ key: "conflicts", detail: "1 conflict blocks generation" }],
  blocker_count: 1,
  ready: false,
  summary: "Resolve the open conflict before generating.",
};

const planningState = {
  assumptions: [
    { id: "a1", topic_key: "platform.ios", value: "iOS may be required later", status: "ASSUMED_BY_MODEL" },
  ],
  verification_items: [
    { id: "vf1", topic_key: "security.fingerprint", value: "Multi-fingerprint mapping", status: "NEEDS_VERIFICATION" },
  ],
  conflicts: [
    { id: "cf1", topic_key: "storage.engine", previous_value: "Use Postgres", new_value: "Use SQLite", status: "OPEN" },
  ],
  open_questions: [
    { id: "oq1", topic_key: "data.retention", value: "What is the retention window?", status: "OPEN" },
  ],
};

const fullV1: PrdVersion = {
  id: "v1",
  project_id: "p1",
  version: 1,
  status: "APPROVED",
  title: "Field App PRD",
  markdown: "# Field App PRD\n\n## Storage\nUse SQLite.",
  content: { sections: [], planning_state: { assumptions: [], verification_items: [], conflicts: [], open_questions: [] } },
  compiler_version: "prd-compiler@1",
  content_hash: "aaa111",
  source_revision: { decisions: 3 },
  created_at: "2026-01-01T00:00:00.000Z",
  approved_at: "2026-01-01T12:00:00.000Z",
};

const fullV2: PrdVersion = {
  ...fullV1,
  id: "v2",
  version: 2,
  status: "DRAFT",
  markdown: "# Field App PRD v2\n\n## Storage\nUse SQLite with WAL.",
  content: { sections: [], planning_state: planningState },
  content_hash: "bbb222",
  created_at: now,
  approved_at: null,
};

const fullV3: PrdVersion = {
  ...fullV2,
  id: "v3",
  version: 3,
  status: "DRAFT",
  markdown: "# Field App PRD v3\n\n## Storage\nUse SQLite with WAL and backups.",
  content_hash: "ccc333",
  created_at: "2026-01-03T00:00:00.000Z",
};

const summary1: PrdVersionSummary = { id: "v1", project_id: "p1", version: 1, status: "APPROVED", title: "Field App PRD", created_at: "2026-01-01T00:00:00.000Z", content_hash: "aaa111" };
const summary2: PrdVersionSummary = { id: "v2", project_id: "p1", version: 2, status: "DRAFT", title: "Field App PRD", created_at: now, content_hash: "bbb222" };

const diff: PrdDiff = {
  from: { id: "v1", version: 1, status: "APPROVED" },
  to: { id: "v2", version: 2, status: "DRAFT" },
  sections: [
    {
      key: "storage",
      title: "Storage",
      status: "changed",
      line_diff: [
        { op: "unchanged", text: "## Storage" },
        { op: "removed", text: "Use SQLite." },
        { op: "added", text: "Use SQLite with WAL." },
      ],
    },
    { key: "scope", title: "Scope", status: "unchanged", line_diff: [] },
  ],
  summary: { added: 1, removed: 1, changed: 1, unchanged: 2 },
  affected_sections: [{ key: "storage", title: "Storage" }],
};

let versionsState: PrdVersionSummary[];
let fullVersions: Record<string, PrdVersion>;

beforeEach(() => {
  jest.clearAllMocks();
  versionsState = [summary1, summary2];
  fullVersions = { v1: fullV1, v2: fullV2 };

  mockedReadiness.mockResolvedValue(readiness);
  mockedList.mockImplementation(() =>
    Promise.resolve({
      versions: versionsState,
      latest_version: versionsState.reduce((max, v) => Math.max(max, v.version || 0), 0) || null,
    })
  );
  mockedGetVersion.mockImplementation((id: string) => Promise.resolve(fullVersions[id]));
  mockedGenerate.mockImplementation(() => {
    fullVersions[fullV3.id as string] = fullV3;
    versionsState = [
      { id: fullV3.id, project_id: "p1", version: fullV3.version, status: fullV3.status, title: fullV3.title, created_at: fullV3.created_at, content_hash: fullV3.content_hash },
      ...versionsState,
    ];
    return Promise.resolve(fullV3);
  });
  mockedCompare.mockResolvedValue(diff);
  mockedApprove.mockImplementation((id: string) => {
    const updated: PrdVersion = { ...fullVersions[id], status: "APPROVED", approved_at: "2026-01-04T00:00:00.000Z" };
    fullVersions[id] = updated;
    versionsState = versionsState.map((v) => (v.id === id ? { ...v, status: "APPROVED" } : v));
    return Promise.resolve(updated);
  });
});

function renderView() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <PrdView projectId="p1" conversationId="c1" />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

test("readiness renders every dimension with explicit states and blockers, without percentages", async () => {
  renderView();

  const section = await screen.findByTestId("prd-readiness");
  for (const key of ["decisions", "verification", "conflicts", "sections"]) {
    expect(within(section).getByTestId(`prd-readiness-dim-${key}`)).toBeInTheDocument();
    expect(within(section).getByTestId(`prd-readiness-state-${key}`)).toBeInTheDocument();
  }

  expect(screen.getByTestId("prd-readiness-state-decisions")).toHaveTextContent("READY");
  expect(screen.getByTestId("prd-readiness-state-verification")).toHaveTextContent("NEEDS_VERIFICATION");
  expect(screen.getByTestId("prd-readiness-state-conflicts")).toHaveTextContent("BLOCKED");
  expect(screen.getByTestId("prd-readiness-state-sections")).toHaveTextContent("INCOMPLETE");
  expect(screen.getByTestId("prd-readiness-dim-conflicts")).toHaveTextContent("1 conflict blocks generation");

  expect(screen.getByTestId("prd-blockers")).toHaveTextContent("Blockers: 1");
  expect(screen.getByTestId("prd-ready-summary")).toHaveTextContent("Not ready");
  expect(screen.getByTestId("prd-ready-summary")).toHaveTextContent("Resolve the open conflict before generating.");
  expect(mockedReadiness).toHaveBeenCalledWith("p1", "c1");

  expect(screen.getByTestId("prd-view")).not.toHaveTextContent("%");
});

test("generate flow calls the API and refreshes versions", async () => {
  const user = userEvent.setup({ delay: null });
  renderView();

  await screen.findByTestId("prd-version-list");
  const before = mockedList.mock.calls.length;

  await user.click(screen.getByTestId("prd-generate"));

  await waitFor(() => expect(mockedGenerate).toHaveBeenCalledWith({ project_id: "p1", conversation_id: "c1" }));
  await waitFor(() => expect(mockedList.mock.calls.length).toBeGreaterThan(before));
  expect(await screen.findByTestId("prd-version-3")).toBeInTheDocument();
});

test("switching versions fetches the selected version and renders its markdown", async () => {
  const user = userEvent.setup({ delay: null });
  renderView();

  await waitFor(() => expect(mockedGetVersion).toHaveBeenCalledWith("v2", "p1"));
  const markdown = await screen.findByTestId("prd-markdown");
  expect(markdown).toHaveTextContent("Use SQLite with WAL");
  expect(mockedGetVersion).toHaveBeenCalledWith("v2", "p1");

  await user.click(await screen.findByTestId("prd-version-1"));

  await waitFor(() => expect(mockedGetVersion).toHaveBeenCalledWith("v1", "p1"));
  await waitFor(() => expect(screen.getByTestId("prd-markdown")).toHaveTextContent("Use SQLite."));
  expect(within(screen.getByTestId("prd-version-list")).getByTestId("prd-version-status-v1")).toHaveTextContent("APPROVED");
  expect(screen.queryByTestId("prd-approve-v1")).toBeNull();
});

test("diff flow calls comparePrdVersions and renders section-level diff and affected sections", async () => {
  const user = userEvent.setup({ delay: null });
  renderView();

  const compareButton = await screen.findByTestId("prd-compare-run");
  await user.click(compareButton);

  await waitFor(() => expect(mockedCompare).toHaveBeenCalledWith("p1", "v1", "v2"));

  const diffEl = await screen.findByTestId("prd-diff");
  const storage = within(diffEl).getByTestId("prd-diff-section-storage");
  expect(storage).toHaveTextContent("changed");
  expect(storage).toHaveTextContent("Use SQLite with WAL.");
  expect(storage).toHaveTextContent("Use SQLite.");
  expect(storage.querySelectorAll(".text-emerald-500").length).toBeGreaterThan(0);
  expect(storage.querySelectorAll(".text-destructive").length).toBeGreaterThan(0);
  expect(within(diffEl).getByTestId("prd-diff-section-scope")).toHaveTextContent("unchanged");

  const affected = within(diffEl).getByTestId("prd-diff-affected");
  expect(affected).toHaveTextContent("Storage");
  expect(affected).toHaveTextContent("changed: 1");
});

test("approval flow calls approvePrdVersion and refreshes versions", async () => {
  const user = userEvent.setup({ delay: null });
  renderView();

  const approve = await screen.findByTestId("prd-approve-v2");
  const before = mockedList.mock.calls.length;

  await user.click(approve);

  await waitFor(() => expect(mockedApprove).toHaveBeenCalledWith("v2", "p1"));
  await waitFor(() => expect(mockedList.mock.calls.length).toBeGreaterThan(before));
  await waitFor(() => expect(screen.queryByTestId("prd-approve-v2")).toBeNull());
});

test("assumptions and verification items are clearly presented as non-confirmed", async () => {
  renderView();

  const assumptions = await screen.findByTestId("prd-assumptions");
  expect(assumptions).toHaveTextContent("Assumptions (not confirmed facts)");
  expect(assumptions).toHaveTextContent("iOS may be required later");

  const verification = await screen.findByTestId("prd-verification");
  expect(verification).toHaveTextContent("Needs verification (not confirmed)");
  expect(verification).toHaveTextContent("Multi-fingerprint mapping");

  expect(within(assumptions).queryByText("Multi-fingerprint mapping")).toBeNull();
  expect(within(verification).queryByText("iOS may be required later")).toBeNull();
});

test("old project with no versions renders prd-empty without errors and disables compare", async () => {
  versionsState = [];
  renderView();

  expect(await screen.findByTestId("prd-empty")).toBeInTheDocument();
  expect(screen.queryByTestId("prd-version-list")).toBeNull();
  expect(screen.queryByTestId("prd-markdown")).toBeNull();
  expect(screen.getByTestId("prd-compare-hint")).toBeInTheDocument();
  expect(screen.getByTestId("prd-compare-run")).toBeDisabled();
  expect(screen.getByTestId("prd-compare-from")).toBeDisabled();
});
