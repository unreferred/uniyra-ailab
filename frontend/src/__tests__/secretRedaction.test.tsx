import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ToolsTab } from "@/components/project/ToolsTab";
import { testTool } from "@/lib/api";

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
const mockedTest = testTool as jest.Mock;

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

// Deliberately planted secret material: the real backend never returns a
// `value` field, but this proves the UI never renders it even if present.
const LEAKED_VALUE = "supersecret-value-planted-123";
const credWithLeak = {
  id: "cred-1",
  name: "MY_API_KEY",
  credential_type: "API_KEY",
  has_value: true,
  value: LEAKED_VALUE,
};

function setupGet() {
  mockedGet.mockImplementation((url: string) => {
    if (url === "/tools") return Promise.resolve({ data: { items: [toolHttp], total: 1 } });
    if (url === "/tools/tool-http-1/versions")
      return Promise.resolve({ data: { builtin: false, items: [] } });
    if (url === "/tool-credentials") return Promise.resolve({ data: { items: [credWithLeak] } });
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

test("credential manager never renders secret values even when planted in payload", async () => {
  renderToolsTab();
  const row = await screen.findByTestId("credential-cred-1");
  expect(row).toHaveTextContent("MY_API_KEY");
  expect(row).toHaveTextContent("API_KEY");
  expect(within(row).getByTestId("credential-stored-cred-1")).toHaveTextContent("Stored");
  // The planted value field must not leak anywhere in the rendered DOM.
  expect(row.textContent || "").not.toContain(LEAKED_VALUE);
  expect(document.body.textContent || "").not.toContain(LEAKED_VALUE);
});

test("tool test result renders sanitized '[secret:' placeholder instead of the raw secret", async () => {
  const user = userEvent.setup({ delay: null });
  // Sanitized server payload: secret placeholder, no raw credential material.
  mockedTest.mockResolvedValueOnce({
    tool_call: { id: "tc1", execution_status: "success", duration_ms: 42 },
    output: { ok: true },
    echo: { authorization: "[secret:MY_API_KEY]" },
  });
  renderToolsTab();
  await user.click(await screen.findByTestId("project-tool-test-tool-http-1"));
  fireEvent.change(await screen.findByTestId("tool-test-args"), {
    target: { value: "{}" },
  });
  await user.click(screen.getByTestId("tool-test-submit"));
  expect(mockedTest).toHaveBeenCalledWith(
    "tool-http-1",
    expect.objectContaining({ project_id: "p1" })
  );
  const result = await screen.findByTestId("tool-test-result");
  expect(result).toHaveTextContent("[secret:MY_API_KEY]");
  expect(result).not.toHaveTextContent(LEAKED_VALUE);
  expect(document.body.textContent || "").not.toContain(LEAKED_VALUE);
});
