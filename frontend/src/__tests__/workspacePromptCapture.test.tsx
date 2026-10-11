import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import Workspace from "@/pages/Workspace";
import {
  api,
  analyzePromptCapture,
  savePromptCapture,
  uploadAttachments,
} from "@/lib/api";
import { ModelRecord, ToolRecord } from "@/lib/types";

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
}));

const mockedGet = api.get as jest.Mock;
const mockedPost = api.post as jest.Mock;
const mockedPatch = api.patch as jest.Mock;
const mockedUpload = uploadAttachments as jest.Mock;
const mockedAnalyze = analyzePromptCapture as jest.Mock;
const mockedSave = savePromptCapture as jest.Mock;
const mockedToastSuccess = toast.success as jest.Mock;

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
  title: "Capture source",
  system_prompt: "",
  workspace_mode: "plan",
  created_at: now,
  updated_at: now,
  messages: [
    {
      id: "m1",
      conversation_id: "c1",
      role: "user",
      content: "Please review this change and tell me what to do.",
      created_at: now,
    },
    {
      id: "m2",
      conversation_id: "c1",
      role: "assistant",
      content: "## Objective\nDo the thing",
      model_id: "m1",
      model_name: "Model One",
      created_at: now,
    },
  ],
};

const RENDERED_TEXT = "## Objective\nDo the thing\n\n## Security\nNever leak secrets";

const capturePreview: any = {
  project_id: "p1",
  conversation_id: "c1",
  message_ids: ["m2"],
  mode: "conversation",
  title: "Capture source",
  purpose: "Do the thing",
  source: { conversation_id: "c1", message_ids: ["m1", "m2"], message_count: 2 },
  structure: {
    role: "You are a reviewer.",
    objective: "Do the thing",
    scope: ["Only the workspace"],
    constraints: ["Must stay owner-scoped"],
    architecture_rules: ["Reuse existing services"],
    design_rules: [],
    security_rules: ["Never leak secrets"],
    phases: ["Inspect", "Implement"],
    validation: ["Add tests"],
    expected_output: ["A report"],
  },
  content: "## Objective\nDo the thing",
  system_prompt: "You are a reviewer.",
  variables: {},
  structured_spec: { objective: "Do the thing" },
  blocks: {
    linked: [
      {
        id: "b1",
        name: "Bounded scope",
        category: "bounded_scope",
        version: 1,
        matched_by: "category_match:bounded_scope",
      },
    ],
    suggested_created: [],
    candidates: [
      { id: "b1", name: "Bounded scope", category: "bounded_scope", version: 1 },
      { id: "b2", name: "Tests are required", category: "tests_required", version: 1 },
    ],
  },
  policies: {
    linked: [
      {
        id: "pol1",
        name: "Arch policy",
        domain: "ARCHITECTURE",
        version: 1,
        matched_by: "domain_match:ARCHITECTURE",
      },
    ],
    candidates: [{ id: "pol1", name: "Arch policy", domain: "ARCHITECTURE", version: 1 }],
  },
  invariants: {
    linked: [
      {
        id: "inv1",
        code: "SEC_NO_LEAK",
        domain: "SECURITY",
        severity: "high",
        title: "No leaks",
        matched_by: "domain_match",
      },
    ],
    candidates: [
      { id: "inv1", code: "SEC_NO_LEAK", domain: "SECURITY", severity: "high", title: "No leaks" },
    ],
  },
  redaction: { redacted: true, matches: 1 },
  preview: {
    id: null,
    rendered_text: RENDERED_TEXT,
    checksum: "abc123",
    token_estimate: 42,
    persisted: false,
  },
  preview_error: null,
};

const saveResult = {
  capture: { id: "cap1" },
  prompt: { id: "pr1" },
  version: { id: "v1", status: "draft" },
  blocks: [],
  policies: [],
  invariants: [],
  preview: null,
};

function AgentLabStub() {
  const location = useLocation();
  return <div data-testid="agent-lab-stub">{location.search}</div>;
}

beforeEach(() => {
  jest.clearAllMocks();

  mockedAnalyze.mockResolvedValue(capturePreview);
  mockedSave.mockResolvedValue(saveResult);

  mockedGet.mockImplementation((url: string) => {
    if (url === "/models") return Promise.resolve({ data: [model] });
    if (url === "/tools") return Promise.resolve({ data: { items: [tool], total: 1 } });
    if (url === "/code-interpreter/status")
      return Promise.resolve({ data: { available: true, reason: null } });
    if (url === "/attachments") {
      return Promise.resolve({ data: { items: [], total: 0, limit: 50, offset: 0 } });
    }
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
          <Route path="/agents" element={<AgentLabStub />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

async function openCaptureDialog(user: ReturnType<typeof userEvent.setup>, triggerTestId: string) {
  await screen.findByTestId(triggerTestId);
  await waitFor(() => expect(screen.getByTestId(triggerTestId)).not.toBeDisabled());
  await user.click(screen.getByTestId(triggerTestId));
  await screen.findByTestId("prompt-capture-dialog");
  await screen.findByTestId("prompt-capture-preview");
  await waitFor(() =>
    expect(screen.getByTestId("prompt-capture-save-prompt")).not.toBeDisabled()
  );
}

test("opens conversation capture dialog and previews the capture", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();

  await openCaptureDialog(user, "capture-conversation");

  expect(mockedAnalyze).toHaveBeenCalledWith({
    project_id: "p1",
    conversation_id: "c1",
    message_ids: [],
    mode: "conversation",
  });

  expect(screen.getByTestId("prompt-capture-dialog")).toBeInTheDocument();
  expect(screen.getByTestId("prompt-capture-structure")).toHaveTextContent("You are a reviewer.");
  expect(screen.getByTestId("prompt-capture-preview")).toHaveTextContent("## Objective");
  expect(screen.getByTestId("capture-block-b1")).toBeInTheDocument();
  expect(screen.getByTestId("capture-policy-pol1")).toBeInTheDocument();
  expect(screen.getByTestId("capture-invariant-inv1")).toBeInTheDocument();
  // Candidate-only asset: present in blocks.candidates but not blocks.linked.
  const b2 = screen.getByTestId("capture-block-b2");
  expect(b2).toBeInTheDocument();
  expect(b2).toHaveAttribute("data-state", "unchecked");
});

test("capture action on an assistant message uses response mode", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();

  await openCaptureDialog(user, "capture-prompt-m2");

  expect(mockedAnalyze).toHaveBeenCalledWith({
    project_id: "p1",
    conversation_id: "c1",
    message_ids: ["m2"],
    mode: "response",
  });
});

test("Save as Prompt creates a draft prompt (no freeze)", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();

  await openCaptureDialog(user, "capture-conversation");
  await waitFor(() =>
    expect(screen.getByTestId("capture-block-b1")).toHaveAttribute("data-state", "checked")
  );

  await user.click(screen.getByTestId("prompt-capture-save-prompt"));

  await waitFor(() => expect(mockedSave).toHaveBeenCalledTimes(1));
  expect(mockedSave).toHaveBeenCalledWith(
    expect.objectContaining({
      kind: "prompt",
      freeze_version: false,
      project_id: "p1",
      conversation_id: "c1",
    })
  );
  const body = mockedSave.mock.calls[0][0];
  expect(body.include_block_ids).toContain("b1");

  await waitFor(() =>
    expect(screen.queryByTestId("prompt-capture-dialog")).not.toBeInTheDocument()
  );
  expect(mockedToastSuccess).toHaveBeenCalled();
});

test("Save as Planning Template passes the create-blocks confirmation", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();

  await openCaptureDialog(user, "capture-conversation");
  await user.click(screen.getByTestId("prompt-capture-create-blocks"));
  await user.click(screen.getByTestId("prompt-capture-save-template"));

  await waitFor(() => expect(mockedSave).toHaveBeenCalledTimes(1));
  expect(mockedSave).toHaveBeenCalledWith(
    expect.objectContaining({
      kind: "planning_template",
      freeze_version: false,
      create_missing_blocks: true,
    })
  );
});

test("Create Agent saves a FROZEN version then opens Agent Lab prefilled", async () => {
  const user = userEvent.setup({ delay: null });
  mockedSave.mockResolvedValue({ prompt: { id: "pr1" }, version: { id: "v1" } });
  renderWorkspace();

  await openCaptureDialog(user, "capture-conversation");
  await user.click(screen.getByTestId("prompt-capture-create-agent"));

  await waitFor(() => expect(mockedSave).toHaveBeenCalledTimes(1));
  expect(mockedSave).toHaveBeenCalledWith(
    expect.objectContaining({ kind: "prompt", freeze_version: true })
  );

  const stub = await screen.findByTestId("agent-lab-stub");
  expect(stub.textContent).toContain("capturePromptId=pr1");
  expect(stub.textContent).toContain("captureVersionId=v1");
});

test("Use once inserts the composed prompt into the composer", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();

  await openCaptureDialog(user, "capture-conversation");
  await user.click(screen.getByTestId("prompt-capture-use-once"));

  await waitFor(() =>
    expect(screen.getByTestId("workspace-input")).toHaveValue(RENDERED_TEXT)
  );
});
