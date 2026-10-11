import React from "react";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Workspace from "@/pages/Workspace";
import { api, uploadAttachments } from "@/lib/api";
import { Attachment, ModelRecord, ToolRecord } from "@/lib/types";

jest.mock("@floating-ui/dom", () => {
  const actual = jest.requireActual("@floating-ui/dom");
  return {
    ...actual,
    // The real computePosition loops under jsdom (zero-size rects); positioning
    // is not under test, so return a stable placement.
    computePosition: () =>
      Promise.resolve({ x: 0, y: 0, strategy: "fixed", placement: "bottom", middlewareData: {} }),
  };
});

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn(), message: jest.fn() },
}));

// react-markdown / remark-gfm are ESM-only; the composer tests only need the text.
jest.mock("@/components/common/Markdown", () => ({
  MarkdownView: ({ content }: { content: string }) => <div data-testid="markdown-view">{content}</div>,
}));

// Avoid pulling in AuthContext/models for the artifact dialog.
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
  uploadAttachments: jest.fn(),
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

const attachment: Attachment = {
  id: "a1",
  owner_id: "u1",
  project_id: "p1",
  filename: "stored.txt",
  original_filename: "notes.txt",
  mime_type: "text/plain",
  kind: "text",
  size_bytes: 12,
  sha256: "deadbeef",
  processing_status: "ready",
  extraction_status: "done",
  created_at: now,
};

const conversation = {
  id: "c1",
  project_id: "p1",
  title: "New conversation",
  system_prompt: "",
  created_at: now,
  updated_at: now,
  messages: [],
};

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
    if (url.startsWith("/conversations?")) return Promise.resolve({ data: [] });
    if (url === "/conversations/c1") return Promise.resolve({ data: conversation });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });

  mockedPost.mockImplementation((url: string) => {
    if (url === "/conversations") return Promise.resolve({ data: conversation });
    if (url === "/conversations/c1/messages") return Promise.resolve({ data: { id: "msg-1" } });
    return Promise.resolve({ data: {} });
  });

  mockedPatch.mockResolvedValue({ data: {} });
  mockedUpload.mockResolvedValue([attachment]);
});

function renderWorkspace() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={["/workspace"]}>
      <QueryClientProvider client={qc}>
        <Workspace />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

test("composer exposes Attach, Tools, Context and Parameters controls", async () => {
  renderWorkspace();

  expect(await screen.findByTestId("workspace-attach-button")).toHaveTextContent("Attach");
  expect(screen.getByTestId("workspace-tools-button")).toHaveTextContent("Tools: 0");
  expect(screen.getByTestId("workspace-context-button")).toHaveTextContent("Context");
  expect(screen.getByTestId("workspace-params-button")).toHaveTextContent("Parameters");
});

test("picking a file uploads it and renders an attachment chip", async () => {
  renderWorkspace();
  await screen.findByTestId("workspace-file-input");

  const file = new File(["hello"], "notes.txt", { type: "text/plain" });
  fireEvent.change(screen.getByTestId("workspace-file-input"), { target: { files: [file] } });

  await waitFor(() => expect(mockedUpload).toHaveBeenCalledWith("p1", [file], undefined));
  expect(await screen.findByTestId("attachment-chip-a1")).toHaveTextContent("notes.txt");
});

test("send posts attachment_ids and enabled_tool_ids for the new conversation", async () => {
  const user = userEvent.setup({ delay: null });
  renderWorkspace();

  // The default enabled model is auto-selected.
  await waitFor(() =>
    expect(screen.getByTestId("workspace-model-select")).toHaveTextContent("Model One")
  );

  const file = new File(["hello"], "notes.txt", { type: "text/plain" });
  fireEvent.change(screen.getByTestId("workspace-file-input"), { target: { files: [file] } });
  await screen.findByTestId("attachment-chip-a1");

  await user.click(screen.getByTestId("workspace-tools-button"));
  await user.click(within(await screen.findByTestId("tool-t1")).getByRole("checkbox"));
  await waitFor(() =>
    expect(screen.getByTestId("workspace-tools-button")).toHaveTextContent("Tools: 1")
  );

  await user.type(screen.getByTestId("workspace-input"), "Hello world");
  await user.click(screen.getByTestId("workspace-send"));

  await waitFor(() => {
    expect(mockedPost).toHaveBeenCalledWith(
      "/conversations/c1/messages",
      expect.objectContaining({
        content: "Hello world",
        model_id: "m1",
        attachment_ids: ["a1"],
        enabled_tool_ids: ["t1"],
        knowledge_enabled: false,
        knowledge_collection_ids: [],
        streaming: false,
      })
    );
  });
});

test("keyboard: Enter sends prompt without modifier keys", async () => {
  renderWorkspace();
  await waitFor(() =>
    expect(screen.getByTestId("workspace-model-select")).toHaveTextContent("Model One")
  );
  const input = screen.getByTestId("workspace-input");
  fireEvent.change(input, { target: { value: "Enter test" } });
  fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

  await waitFor(() => {
    expect(mockedPost).toHaveBeenCalledWith(
      "/conversations/c1/messages",
      expect.objectContaining({
        content: "Enter test",
        model_id: "m1",
      })
    );
  });
});

test("keyboard: Shift+Enter does NOT send prompt", async () => {
  renderWorkspace();
  await waitFor(() =>
    expect(screen.getByTestId("workspace-model-select")).toHaveTextContent("Model One")
  );
  const input = screen.getByTestId("workspace-input");
  fireEvent.change(input, { target: { value: "Line 1" } });
  fireEvent.keyDown(input, { key: "Enter", shiftKey: true });

  // Post should NOT have been called
  expect(mockedPost).not.toHaveBeenCalledWith(
    "/conversations/c1/messages",
    expect.anything()
  );
});

test("keyboard: IME composition Enter does NOT send prematurely", async () => {
  renderWorkspace();
  await waitFor(() =>
    expect(screen.getByTestId("workspace-model-select")).toHaveTextContent("Model One")
  );
  const input = screen.getByTestId("workspace-input");
  fireEvent.change(input, { target: { value: "nihon" } });
  fireEvent.keyDown(input, { key: "Enter", shiftKey: false, nativeEvent: { isComposing: true } });

  expect(mockedPost).not.toHaveBeenCalledWith(
    "/conversations/c1/messages",
    expect.anything()
  );
});

test("keyboard: whitespace-only Enter does NOT send", async () => {
  renderWorkspace();
  await waitFor(() =>
    expect(screen.getByTestId("workspace-model-select")).toHaveTextContent("Model One")
  );
  const input = screen.getByTestId("workspace-input");
  fireEvent.change(input, { target: { value: "   \n\t  " } });
  fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

  expect(mockedPost).not.toHaveBeenCalledWith(
    "/conversations/c1/messages",
    expect.anything()
  );
});
