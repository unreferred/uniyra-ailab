import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToolsTab } from "@/components/project/ToolsTab";
import {
  createCustomTool,
  deleteCustomTool,
  testTool,
  importOpenAPI,
  generateFromOpenAPI,
  createToolCredential,
  updateToolCredential,
  deleteToolCredential,
} from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  createCustomTool: jest.fn(),
  deleteCustomTool: jest.fn(),
  testTool: jest.fn(),
  importOpenAPI: jest.fn(),
  generateFromOpenAPI: jest.fn(),
  createToolCredential: jest.fn(),
  updateToolCredential: jest.fn(),
  deleteToolCredential: jest.fn(),
}));

import { api } from "@/lib/api";
const mockedGet = api.get as jest.Mock;

const toolHttp = {
  id: "tool-http-1",
  name: "my_api",
  display_name: "My API",
  description: "demo",
  version: 2,
  tool_type: "http",
  current_version_id: "ver-http-abcdef123456",
  builtin: false,
  scope: "project",
  project_id: "p1",
  enabled: true,
  risk_level: "low",
  permissions: ["http.api.get"],
};

const versionsPayload = {
  builtin: false,
  items: [
    {
      id: "ver-http-v1",
      tool_id: "tool-http-1",
      version: 1,
      version_label: null,
      tool_type: "http",
      risk_level: "low",
      permissions: ["http.api.get"],
      implementation_config: { kind: "http", method: "GET", url_template: "https://api.example.com/v1" },
      source: "manual",
      created_at: "2026-01-01T00:00:00Z",
      immutable: true,
    },
    {
      id: "ver-http-v2",
      tool_id: "tool-http-1",
      version: 2,
      version_label: null,
      tool_type: "http",
      risk_level: "low",
      permissions: ["http.api.get"],
      implementation_config: { kind: "http", method: "GET", url_template: "https://api.example.com/v2" },
      source: "manual",
      created_at: "2026-02-01T00:00:00Z",
      immutable: true,
    },
  ],
};

const cred = {
  id: "cred-1",
  name: "MY_API_KEY",
  credential_type: "API_KEY",
  // Backend never returns values — public view only.
  has_value: true,
};

const openapiPreview = {
  title: "Demo API",
  version: "1.0.0",
  servers: ["https://api.example.com"],
  base_url: "https://api.example.com",
  operation_count: 2,
  operations: [
    { operation_id: "listThings", method: "GET", path: "/things", summary: "List things" },
    { operation_id: "createThing", method: "POST", path: "/things", summary: "Create a thing" },
  ],
};

function setupGet() {
  mockedGet.mockReset();
  mockedGet.mockImplementation((url: string) => {
    if (url === "/tools") return Promise.resolve({ data: { items: [toolHttp], total: 1 } });
    if (url === "/tools/tool-http-1/versions") return Promise.resolve({ data: versionsPayload });
    if (url === "/tool-credentials") return Promise.resolve({ data: { items: [cred] } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

function renderToolsTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToolsTab projectId="p1" />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  setupGet();
});

test("version dialog renders immutable versions with created_at, risk and permissions", async () => {
  const user = userEvent.setup({ delay: null });
  renderToolsTab();
  await user.click(await screen.findByTestId("project-tool-versions-tool-http-1"));
  expect(await screen.findByTestId("tool-versions-dialog")).toBeInTheDocument();
  const v1 = await screen.findByTestId("tool-version-ver-http-v1");
  expect(v1).toHaveTextContent("v1");
  expect(v1).toHaveTextContent("Immutable");
  expect(v1).toHaveTextContent("2026-01-01");
  expect(v1).toHaveTextContent("http.api.get");
  expect(await screen.findByTestId("tool-version-ver-http-v2")).toBeInTheDocument();
  expect(mockedGet).toHaveBeenCalledWith("/tools/tool-http-1/versions");
});

test("credential row shows Stored marker and type, never the secret value", async () => {
  renderToolsTab();
  const row = await screen.findByTestId("credential-cred-1");
  expect(row).toHaveTextContent("MY_API_KEY");
  expect(row).toHaveTextContent("API_KEY");
  expect(within(row).getByTestId("credential-stored-cred-1")).toHaveTextContent("Stored");
  // No secret material anywhere in the DOM.
  expect(document.body.textContent || "").not.toMatch(/supersecret|sk-|hunter2/i);
  expect(row.textContent || "").not.toContain("supersecret-value-123");
});

test("credential delete surfaces the 409 referenced message", async () => {
  const user = userEvent.setup({ delay: null });
  (deleteToolCredential as jest.Mock).mockRejectedValueOnce(
    new Error("Credential is referenced by tool versions and cannot be deleted")
  );
  renderToolsTab();
  await user.click(await screen.findByTestId("credential-delete-cred-1"));
  expect(await screen.findByTestId("credential-error")).toHaveTextContent(
    "Credential is referenced by tool versions and cannot be deleted"
  );
});

test("OpenAPI preview selection generates only selected operations", async () => {
  const user = userEvent.setup({ delay: null });
  (importOpenAPI as jest.Mock).mockResolvedValueOnce(openapiPreview);
  (generateFromOpenAPI as jest.Mock).mockResolvedValueOnce({ items: [{ id: "t-new" }], errors: [] });
  renderToolsTab();
  await user.click(await screen.findByTestId("tools-import-openapi"));
  fireEvent.change(screen.getByTestId("openapi-spec-input"), { target: { value: '{"openapi":"3.0.0"}' } });
  await user.click(screen.getByTestId("openapi-preview-submit"));
  expect(await screen.findByTestId("openapi-op-listThings")).toBeInTheDocument();
  expect(screen.getByTestId("openapi-op-createThing")).toBeInTheDocument();
  // Deselect createThing; generate must include only listThings.
  await user.click(within(screen.getByTestId("openapi-op-createThing")).getByRole("checkbox"));
  await user.click(screen.getByTestId("openapi-generate-submit"));
  expect(generateFromOpenAPI).toHaveBeenCalledWith(
    expect.objectContaining({
      project_id: "p1",
      operations: [{ operation_id: "listThings", include: true }],
    })
  );
});

test("OpenAPI generate shows per-operation errors without closing", async () => {
  const user = userEvent.setup({ delay: null });
  (importOpenAPI as jest.Mock).mockResolvedValueOnce(openapiPreview);
  (generateFromOpenAPI as jest.Mock).mockResolvedValueOnce({
    items: [],
    errors: [{ name: "createThing", message: "Tool 'createThing' already exists" }],
  });
  renderToolsTab();
  await user.click(await screen.findByTestId("tools-import-openapi"));
  fireEvent.change(screen.getByTestId("openapi-spec-input"), { target: { value: '{"openapi":"3.0.0"}' } });
  await user.click(screen.getByTestId("openapi-preview-submit"));
  await screen.findByTestId("openapi-op-listThings");
  await user.click(screen.getByTestId("openapi-generate-submit"));
  expect(await screen.findByTestId("openapi-generate-errors")).toHaveTextContent("createThing");
  // Dialog stays open so the user can adjust the selection.
  expect(screen.getByTestId("openapi-import-dialog")).toBeInTheDocument();
});

test("tool test dialog posts args and renders latency/status result", async () => {
  const user = userEvent.setup({ delay: null });
  (testTool as jest.Mock).mockResolvedValueOnce({
    tool_call: { id: "tc1", execution_status: "success", duration_ms: 123 },
    output: { ok: true },
  });
  renderToolsTab();
  await user.click(await screen.findByTestId("project-tool-test-tool-http-1"));
  const argsBox = await screen.findByTestId("tool-test-args");
  fireEvent.change(argsBox, { target: { value: '{"q":"x"}' } });
  await user.click(screen.getByTestId("tool-test-submit"));
  expect(testTool).toHaveBeenCalledWith("tool-http-1", {
    arguments: { q: "x" },
    project_id: "p1",
  });
  const result = await screen.findByTestId("tool-test-result");
  expect(result).toHaveTextContent("123");
  expect(result).toHaveTextContent("success");
});
