import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Workspace from "@/pages/Workspace";
import {
  api,
  analyzePromptCapture,
  generateConsolidatedPlan,
  getPlanningState,
  ingestPlanningConversation,
  resolvePlanningConflict,
  savePromptCapture,
  updatePlanningItem,
  uploadAttachments,
} from "@/lib/api";
import { ModelRecord, PlanningState, ToolRecord } from "@/lib/types";

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

jest.mock("@/components/common/SaveArtifactDialog", () => ({
  SaveArtifactDialog: () => null,
}));

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({
    activeId: "p1",
    activeProject: { id: "p1", name: "Demo Project" },
    projects: [],
    isLoading: false,
    setActiveId: jest.fn(),
    refresh: jest.fn(),
  }),
}));

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  apiError: (e: any) => (e && e.message) || "error",
  API_BASE: "/api",
  csrfHeader: () => ({}),
  uploadAttachments: jest.fn(),
  analyzePromptCapture: jest.fn(),
  savePromptCapture: jest.fn(),
  getWorkspaceModes: jest.fn(),
  listPromptCaptures: jest.fn(),
  getPlanningStateCatalog: jest.fn(),
  getPlanningState: jest.fn(),
  ingestPlanningConversation: jest.fn(),
  createPlanningItem: jest.fn(),
  updatePlanningItem: jest.fn(),
  reopenPlanningItem: jest.fn(),
  verifyPlanningItem: jest.fn(),
  listPlanningConflicts: jest.fn(),
  resolvePlanningConflict: jest.fn(),
  checkPlanningQuestions: jest.fn(),
  generateConsolidatedPlan: jest.fn(),
}));

const mockedGet = api.get as jest.Mock;
const mockedPost = api.post as jest.Mock;
const mockedPatch = api.patch as jest.Mock;
const mockedUpload = uploadAttachments as jest.Mock;
const mockedAnalyze = analyzePromptCapture as jest.Mock;
const mockedSave = savePromptCapture as jest.Mock;
const mockedGetPlanningState = getPlanningState as jest.Mock;
const mockedIngest = ingestPlanningConversation as jest.Mock;
const mockedUpdate = updatePlanningItem as jest.Mock;
const mockedResolve = resolvePlanningConflict as jest.Mock;
const mockedGenerate = generateConsolidatedPlan as jest.Mock;

const now = new Date("2026-01-01T00:00:00.000Z").toISOString();

const model: ModelRecord = {
  id: "m1",
  owner_id: "u1",
  provider_id: "prov1",
  display_name: "Model One",
  model_identifier: "gpt-x",
  family: "openai",
  enabled: true,
  context_window: 8192,
  input_cost: null,
  output_cost: null,
  capabilities: { text: true, streaming: true, tools: true },
  capability_source: "PROVIDER",
  tags: [],
  notes: "",
  created_at: now,
  updated_at: now,
};

const tool: ToolRecord = {
  id: "t1",
  name: "web_search",
  display_name: "Web Search",
  description: "Search the public web",
  version: "1",
  tool_type: "builtin",
  input_schema: {},
  enabled: true,
  risk_level: "low",
  permissions: [],
  available: true,
};

const conversation = {
  id: "c1",
  project_id: "p1",
  title: "Planning source",
  system_prompt: "",
  workspace_mode: "plan",
  created_at: now,
  updated_at: now,
  messages: [
    {
      id: "m1",
      conversation_id: "c1",
      role: "user",
      content: "We are building a field app.",
      created_at: now,
    },
    {
      id: "m2",
      conversation_id: "c1",
      role: "assistant",
      content: "## Plan\nUse SQLite",
      model_id: "m1",
      model_name: "Model One",
      created_at: now,
    },
  ],
};

const planningState: PlanningState = {
  project_id: "p1",
  conversation_id: "c1",
  confirmed_decisions: [
    {
      id: "dec1",
      topic_key: "storage.engine",
      category: "decision",
      kind: "decision",
      value: "Use SQLite for local storage",
      status: "CONFIRMED_BY_USER",
      source_type: "conversation",
      source_message_id: "m2",
      provenance: { message_id: "m2", quote: "we will use sqlite" },
      created_at: now,
      updated_at: now,
    },
  ],
  constraints: [
    {
      id: "con1",
      topic_key: "platform.scope",
      category: "constraint",
      value: "Android only",
      status: "CONFIRMED_BY_USER",
    },
  ],
  rejected_options: [
    {
      id: "rej1",
      topic_key: "location.tracking",
      category: "rejected_option",
      kind: "rejected_option",
      value: "GPS geofencing",
      status: "REJECTED",
    },
  ],
  assumptions: [
    {
      id: "asm1",
      topic_key: "platform.ios",
      category: "assumption",
      kind: "assumption",
      value: "Might need iOS later",
      status: "ASSUMED_BY_MODEL",
      verification_status: "UNVERIFIED",
      source_type: "model_inference",
      created_at: now,
      updated_at: now,
    },
  ],
  open_questions: [
    {
      id: "oq1",
      topic_key: "data.retention",
      kind: "open_question",
      value: "What is the retention window?",
      status: "OPEN",
    },
  ],
  verification_items: [
    {
      id: "ver1",
      topic_key: "security.fingerprint",
      kind: "verification",
      value: "Multi-fingerprint mapping",
      status: "NEEDS_VERIFICATION",
      verification_status: "NEEDS_VERIFICATION",
    },
  ],
  conflicts: [
    {
      id: "cf1",
      topic_key: "storage.engine",
      previous_value: "Use Postgres",
      new_value: "Use SQLite",
      status: "OPEN",
    },
  ],
  counts: { confirmed_decisions: 1, assumptions: 1, rejected_options: 1 },
};

const promptPreview: any = {
  project_id: "p1",
  conversation_id: "c1",
  message_ids: ["m2"],
  mode: "conversation",
  title: "Planning source",
  purpose: "Capture",
  structure: null,
  content: "",
  capture_fields: {
    confirmed_decisions: ["Android only"],
    assumptions: ["Might need iOS"],
    rejected_options: ["GPS/geofence"],
    open_questions: ["retention"],
    verification_items: ["multi-fingerprint mapping"],
  },
  preview: {
    id: null,
    rendered_text: "## Objective\nCapture the plan",
    checksum: "abc123",
    token_estimate: 12,
    persisted: false,
  },
};

beforeEach(() => {
  jest.clearAllMocks();

  mockedGetPlanningState.mockResolvedValue(planningState);
  mockedIngest.mockResolvedValue({ items: [], conflicts: [] });
  mockedUpdate.mockResolvedValue({ id: "asm1" });
  mockedResolve.mockResolvedValue({ conflict: { id: "cf1" }, items: [], state: planningState });
  mockedGenerate.mockResolvedValue({
    markdown: "# Consolidated Plan\n\n- Use SQLite\n- Android only",
    sections: {},
    policies: [],
    invariants: [],
  });
  mockedAnalyze.mockResolvedValue(promptPreview);
  mockedSave.mockResolvedValue({ prompt: { id: "pr1" }, version: { id: "v1" } });

  mockedGet.mockImplementation((url: string) => {
    if (url === "/models") return Promise.resolve({ data: [model] });
    if (url === "/tools") return Promise.resolve({ data: { items: [tool], total: 1 } });
    if (url === "/code-interpreter/status")
      return Promise.resolve({ data: { available: true, reason: null } });
    if (url === "/attachments") {
      return Promise.resolve({ data: { items: [], total: 0, limit: 50, offset: 0 } });
    }
    if (url === "/planning-state") return Promise.resolve({ data: planningState });
    if (url.startsWith("/conversations/") && url.includes("stream-support")) {
      return Promise.resolve({ data: { supports_stream: false } });
    }
    if (url.startsWith("/conversations?")) return Promise.resolve({ data: [conversation] });
    if (url === "/conversations/c1") return Promise.resolve({ data: conversation });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });

  mockedPost.mockResolvedValue({ data: {} });
  mockedPatch.mockResolvedValue({ data: {} });
  mockedUpload.mockResolvedValue([]);
  (global as any).fetch = jest.fn().mockRejectedValue(new Error("fetch not expected"));
});

function renderWorkspace() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={["/workspace?conv=c1"]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/workspace" element={<Workspace />} />
          <Route path="/agents" element={<div data-testid="agent-lab-stub">agents</div>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

function stateFetches(): number {
  return mockedGetPlanningState.mock.calls.length;
}

async function openPlanningPanel(user: ReturnType<typeof userEvent.setup>) {
  const trigger = await screen.findByTestId("workspace-planning-state");
  await waitFor(() => expect(trigger).not.toBeDisabled());
  await user.click(trigger);
  await screen.findByTestId("planning-state-panel");
  await screen.findByTestId("planning-group-confirmed");
}

async function openCaptureDialog(user: ReturnType<typeof userEvent.setup>) {
  const trigger = await screen.findByTestId("capture-conversation");
  await waitFor(() => expect(trigger).not.toBeDisabled());
  await user.click(trigger);
  await screen.findByTestId("prompt-capture-dialog");
  await screen.findByTestId("prompt-capture-preview");
  await waitFor(() =>
    expect(screen.getByTestId("prompt-capture-save-prompt")).not.toBeDisabled()
  );
}

test("workspace exposes the planning state button and opens the panel", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();

  const trigger = await screen.findByTestId("workspace-planning-state");
  await waitFor(() => expect(trigger).not.toBeDisabled());
  await user.click(trigger);

  expect(await screen.findByTestId("planning-state-panel")).toBeInTheDocument();
  expect(await screen.findByTestId("planning-group-confirmed")).toBeInTheDocument();
  expect(await screen.findByTestId("planning-group-assumptions")).toBeInTheDocument();
  expect(await screen.findByTestId("planning-group-rejected")).toBeInTheDocument();
  expect(mockedGetPlanningState).toHaveBeenCalledWith("p1", "c1");
});

test("panel shows provenance for a decided item", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();
  await openPlanningPanel(user);

  const row = screen.getByTestId("planning-item-dec1");
  expect(row).toHaveTextContent("storage.engine");

  const provenance = screen.getByTestId("planning-provenance-dec1");
  expect(provenance).toHaveTextContent("source: conversation");
  expect(provenance).toHaveTextContent("m2");
});

test("confirm action calls the update API and refreshes", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();
  await openPlanningPanel(user);

  const before = stateFetches();
  await user.click(screen.getByTestId("planning-confirm-asm1"));

  await waitFor(() =>
    expect(mockedUpdate).toHaveBeenCalledWith("asm1", { status: "CONFIRMED_BY_USER" })
  );
  await waitFor(() => expect(stateFetches()).toBeGreaterThan(before));
});

test("reject action marks the item rejected", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();
  await openPlanningPanel(user);

  await user.click(screen.getByTestId("planning-reject-asm1"));

  await waitFor(() =>
    expect(mockedUpdate).toHaveBeenCalledWith("asm1", {
      status: "REJECTED",
      kind: "rejected_option",
    })
  );
});

test("conflict resolution accept_new calls the resolve API", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();
  await openPlanningPanel(user);

  const conflict = await screen.findByTestId("planning-conflict-cf1");
  expect(conflict).toHaveTextContent("Use Postgres");
  expect(conflict).toHaveTextContent("Use SQLite");

  await user.click(within(conflict).getByRole("button", { name: "Accept new" }));

  await waitFor(() =>
    expect(mockedResolve).toHaveBeenCalledWith("cf1", "accept_new", "")
  );
});

test("generate consolidated plan shows the markdown", async () => {
  const user = userEvent.setup({ delay: null });
  mockedGenerate.mockResolvedValue({
    markdown: "# Consolidated Plan\n\n- Use SQLite\n- Android only",
    sections: {},
    policies: [],
    invariants: [],
  });
  renderWorkspace();
  await openPlanningPanel(user);

  await user.click(screen.getByTestId("planning-consolidated-plan"));

  const pre = await screen.findByTestId("planning-consolidated-markdown");
  expect(pre).toHaveTextContent("Consolidated Plan");
  expect(pre).toHaveTextContent("Use SQLite");
});

test("re-scan conversation calls the ingest API", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();
  await openPlanningPanel(user);

  await user.click(screen.getByTestId("planning-rescan"));

  await waitFor(() => expect(mockedIngest).toHaveBeenCalledWith("p1", "c1"));
});

test("prompt capture dialog shows confirmed decisions and keeps assumptions separate", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();

  await openCaptureDialog(user);

  const block = await screen.findByTestId("prompt-capture-decision-state");

  const confirmed = within(block).getByTestId("prompt-capture-confirmed-decisions");
  expect(confirmed).toHaveTextContent("Android only");
  expect(confirmed).not.toHaveTextContent("Might need iOS");

  const assumptionsLabel = within(block).getByText("Assumptions (not facts)");
  const assumptionsSection = assumptionsLabel.closest("div") as HTMLElement;
  expect(within(assumptionsSection).getByText("Might need iOS")).toBeInTheDocument();
});
