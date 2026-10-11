import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Workspace from "@/pages/Workspace";
import { api, uploadAttachments } from "@/lib/api";
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
  title: "New conversation",
  system_prompt: "",
  workspace_mode: "plan",
  created_at: now,
  updated_at: now,
  messages: [],
};

function makeReader(chunks: string[]) {
  let i = 0;
  return {
    read: async () =>
      i < chunks.length
        ? { done: false, value: new TextEncoder().encode(chunks[i++]) }
        : { done: true, value: undefined },
    cancel: async () => {},
    releaseLock: () => {},
  };
}

function sseResponse(chunks: string[]) {
  return { ok: true, status: 200, body: { getReader: () => makeReader(chunks) } } as any;
}

const frame = (evt: any) => `data: ${JSON.stringify(evt)}\n\n`;

beforeEach(() => {
  jest.clearAllMocks();

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
});

function renderWorkspace() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={["/workspace?conv=c1"]}>
      <QueryClientProvider client={qc}>
        <Workspace />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

async function openModeSelect(user: ReturnType<typeof userEvent.setup>) {
  const trigger = await screen.findByTestId("workspace-mode-select");
  await waitFor(() => expect(trigger).toHaveTextContent("Plan"));
  await user.click(trigger);
  await screen.findByTestId("workspace-mode-option-audit");
}

test("workspace mode selector hydrates from the conversation", async () => {
  renderWorkspace();

  const trigger = await screen.findByTestId("workspace-mode-select");
  await waitFor(() => expect(trigger).toHaveTextContent("Plan"));
  expect(trigger).toHaveTextContent("Plan");
});

test("changing mode persists it via PATCH /conversations/{id}", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();

  await openModeSelect(user);
  await user.click(screen.getByTestId("workspace-mode-option-audit"));

  await waitFor(() =>
    expect(mockedPatch).toHaveBeenCalledWith("/conversations/c1", { workspace_mode: "audit" })
  );
  expect(await screen.findByTestId("workspace-mode-select")).toHaveTextContent("Audit");
});

test("mode is included in outbound send payload", async () => {
  const user = userEvent.setup({ delay: null });
  const fetchMock = jest.fn().mockResolvedValue(
    sseResponse([
      frame({ type: "RUN_STARTED", run_id: "r1", trace_id: "t1" }),
      frame({ type: "MODEL_OUTPUT_DELTA", delta: "ok" }),
      frame({ type: "RUN_COMPLETED", run: { id: "r1", normalized_response: "ok" } }),
    ])
  );
  (global as any).fetch = fetchMock;

  renderWorkspace();
  await waitFor(() =>
    expect(screen.getByTestId("workspace-model-select")).toHaveTextContent("Model One")
  );

  await user.type(screen.getByTestId("workspace-input"), "do the plan");
  await user.click(screen.getByTestId("workspace-send"));

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const [url, init] = fetchMock.mock.calls[0];
  expect(String(url)).toContain("/conversations/c1/messages/stream");
  expect(JSON.parse(init.body)).toEqual(
    expect.objectContaining({ content: "do the plan", workspace_mode: "plan" })
  );
});

test("all four modes are offered", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();

  await openModeSelect(user);

  for (const mode of ["chat", "plan", "research", "audit"]) {
    expect(screen.getByTestId(`workspace-mode-option-${mode}`)).toBeInTheDocument();
  }
});
