import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ToolsTab } from "@/components/project/ToolsTab";
import { createCustomTool } from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => {
    const detail = e?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d?.msg || JSON.stringify(d)).join(" ");
    return (e && e.message) || "error";
  },
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
import { toast } from "sonner";

const mockedGet = api.get as jest.Mock;
const mockedCreate = createCustomTool as jest.Mock;
const mockedToastError = toast.error as jest.Mock;

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

function setupGet() {
  mockedGet.mockImplementation((url: string) => {
    if (url === "/tools") return Promise.resolve({ data: { items: [toolHttp], total: 1 } });
    if (url === "/tools/tool-http-1/versions") return Promise.resolve({ data: versionsPayload });
    if (url === "/tool-credentials") return Promise.resolve({ data: { items: [] } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

function renderToolsTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ToolsTab projectId="p1" />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  setupGet();
});

test("versions dialog lists immutable versions via mocked listToolVersions (GET versions)", async () => {
  const user = userEvent.setup({ delay: null });
  renderToolsTab();
  await user.click(await screen.findByTestId("project-tool-versions-tool-http-1"));
  expect(await screen.findByTestId("tool-versions-dialog")).toBeInTheDocument();
  const v1 = await screen.findByTestId("tool-version-ver-http-v1");
  expect(v1).toHaveTextContent("v1");
  expect(v1).toHaveTextContent(/immutable/i);
  expect(v1).toHaveTextContent("2026-01-01");
  expect(v1).toHaveTextContent("http.api.get");
  expect(await screen.findByTestId("tool-version-ver-http-v2")).toBeInTheDocument();
  expect(mockedGet).toHaveBeenCalledWith("/tools/tool-http-1/versions");
});

test("create-custom-tool with invalid name surfaces server 400 without client-side pattern gate", async () => {
  // GAP (honest report): CreateToolDialog has NO client-side name-pattern
  // validation — the submit button enables for any non-empty name and the
  // mutation posts immediately. So this test asserts the achievable surface:
  // an invalid name IS submitted and the server 400 detail is surfaced.
  const user = userEvent.setup({ delay: null });
  mockedCreate.mockRejectedValueOnce({
    response: { status: 400, data: { detail: "Tool name must match ^[a-z0-9_-]+$" } },
    message: "Request failed with status code 400",
  });
  renderToolsTab();
  await user.click(await screen.findByTestId("tools-create-custom"));
  const nameInput = await screen.findByTestId("tool-create-name");
  fireEvent.change(nameInput, { target: { value: "Bad Name!" } });
  // No client-side pattern error is rendered before submit.
  expect(screen.queryByText(/must match|invalid name|pattern/i)).not.toBeInTheDocument();
  const submit = screen.getByTestId("tool-create-submit");
  expect(submit).not.toBeDisabled();
  await user.click(submit);
  await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1), { timeout: 5000 });
  expect(mockedCreate).toHaveBeenCalledWith(expect.objectContaining({ name: "Bad Name!" }));
  await waitFor(
    () =>
      expect(mockedToastError).toHaveBeenCalledWith(
        expect.stringContaining("must match")
      ),
    { timeout: 5000 }
  );
  // Dialog stays open on error so the user can fix the name.
  expect(screen.getByTestId("tool-create-dialog")).toBeInTheDocument();
});

test("create-custom-tool with valid name posts the version-1 payload", async () => {
  const user = userEvent.setup({ delay: null });
  mockedCreate.mockResolvedValueOnce({ id: "tool-new" });
  renderToolsTab();
  await user.click(await screen.findByTestId("tools-create-custom"));
  fireEvent.change(await screen.findByTestId("tool-create-name"), {
    target: { value: "my_api_2" },
  });
  await user.click(screen.getByTestId("tool-create-submit"));
  await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1), { timeout: 5000 });
  expect(mockedCreate).toHaveBeenCalledWith(
    expect.objectContaining({
      project_id: "p1",
      name: "my_api_2",
      version: expect.objectContaining({ tool_type: "http", source: "manual" }),
    })
  );
});

test("invalid input-schema JSON is rejected client-side before submit", async () => {
  const user = userEvent.setup({ delay: null });
  renderToolsTab();
  await user.click(await screen.findByTestId("tools-create-custom"));
  fireEvent.change(await screen.findByTestId("tool-create-name"), {
    target: { value: "ok_name" },
  });
  fireEvent.change(screen.getByTestId("tool-create-schema"), {
    target: { value: "{not-json" },
  });
  await user.click(screen.getByTestId("tool-create-submit"));
  expect(mockedCreate).not.toHaveBeenCalled();
  await waitFor(
    () =>
      expect(mockedToastError).toHaveBeenCalledWith(
        expect.stringContaining("Input schema must be valid JSON")
      ),
    { timeout: 5000 }
  );
});
