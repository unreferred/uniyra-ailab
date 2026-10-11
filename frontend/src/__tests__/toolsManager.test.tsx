import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ToolsPage from "@/pages/Tools";
import {
  createCustomTool,
  deleteCustomTool,
  executeCode,
  codeInterpreterStatus,
  importOpenAPI,
  generateFromOpenAPI,
  createToolCredential,
  updateToolCredential,
  deleteToolCredential,
  listToolCredentials,
  listToolValves,
} from "@/lib/api";
import { Sidebar } from "@/components/layout/Sidebar";
import { CredentialsPanel } from "@/components/tools/CredentialsPanel";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  createCustomTool: jest.fn(),
  updateCustomTool: jest.fn(),
  deleteCustomTool: jest.fn(),
  listToolVersions: jest.fn(),
  getToolVersion: jest.fn(),
  testTool: jest.fn(),
  importOpenAPI: jest.fn(),
  generateFromOpenAPI: jest.fn(),
  listToolCredentials: jest.fn(),
  createToolCredential: jest.fn(),
  getToolCredential: jest.fn(),
  updateToolCredential: jest.fn(),
  deleteToolCredential: jest.fn(),
  codeInterpreterStatus: jest.fn(),
  executeCode: jest.fn(),
  listCodeExecutions: jest.fn(),
  getCodeExecution: jest.fn(),
  downloadExecutionFile: jest.fn(),
  deleteCodeExecution: jest.fn(),
  projectSettings: jest.fn(),
  listToolValves: jest.fn(),
  getToolValves: jest.fn(),
  updateToolValves: jest.fn(),
  testToolValves: jest.fn(),
}));

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({
    projects: [{ id: "p1", name: "Demo", description: "", tags: [], status: "active", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }],
    activeProject: { id: "p1", name: "Demo" },
    activeId: "p1",
    setActiveId: jest.fn(),
    isLoading: false,
    refresh: jest.fn(),
  }),
  ProjectProvider: ({ children }: any) => <>{children}</>,
}));

import { api, testTool } from "@/lib/api";
const mockedGet = api.get as jest.Mock;

const calcTool = {
  id: "tool-calculator-0001",
  name: "calculator",
  display_name: "Calculator",
  description: "Evaluate a safe arithmetic expression.",
  version: "1.0.0",
  tool_type: "function",
  input_schema: {},
  output_schema: {},
  enabled: true,
  risk_level: "low",
  permissions: ["calculator.execute"],
  scope: "global",
  project_id: null,
  builtin: true,
  available: true,
  availability_reason: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-02-01T00:00:00Z",
};

const ciTool = {
  id: "tool-code-interpreter-0001",
  name: "code_interpreter",
  display_name: "Code Interpreter",
  description: "Execute Python code in a sandboxed Docker container.",
  version: 1,
  tool_type: "code_interpreter",
  current_version_id: "tool-code-interpreter-0001-v1",
  input_schema: { type: "object" },
  output_schema: {},
  enabled: true,
  risk_level: "high",
  permissions: ["code.execute"],
  scope: "global",
  project_id: null,
  builtin: false,
  available: true,
  availability_reason: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-02-01T00:00:00Z",
};

const openapiTool = {
  id: "tool-openapi-1",
  name: "unibodi_api",
  display_name: "Unibodi API",
  description: "Demo",
  version: 3,
  tool_type: "openapi",
  current_version_id: "ver-3",
  input_schema: {},
  output_schema: {},
  enabled: true,
  risk_level: "medium",
  permissions: ["http.unibodi.get"],
  scope: "project",
  project_id: "p1",
  builtin: false,
  available: true,
  availability_reason: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-03-01T00:00:00Z",
};

const ciStatusOnline = {
  available: true,
  reason: null,
  image: "omnilab-code-interpreter:latest",
  runtime: "docker",
  limits: {
    memory_mb: 512, cpus: 1.0, pids_limit: 128, tmpfs_mb: 64,
    timeout_s: 30, max_output_bytes: 204800, max_files: 10, max_file_bytes: 5242880,
  },
  policy: { network: "off", packages: ["python", "pandas", "numpy", "matplotlib"], tool: "code_interpreter", permissions: ["code.execute"], risk: "high" },
};

const versionsPayload = {
  builtin: false,
  items: [
    { id: "ver-1", tool_id: "tool-openapi-1", version: 1, version_label: null, tool_type: "openapi", risk_level: "low", permissions: ["http.unibodi.get"], implementation_config: { kind: "http", method: "GET", url_template: "https://api.example.com/v1" }, source: "openapi", created_at: "2026-01-01T00:00:00Z", immutable: true },
    { id: "ver-2", tool_id: "tool-openapi-1", version: 2, version_label: null, tool_type: "openapi", risk_level: "low", permissions: ["http.unibodi.get"], implementation_config: { kind: "http", method: "GET", url_template: "https://api.example.com/v2" }, source: "openapi", created_at: "2026-02-01T00:00:00Z", immutable: true },
    { id: "ver-3", tool_id: "tool-openapi-1", version: 3, version_label: null, tool_type: "openapi", risk_level: "medium", permissions: ["http.unibodi.get", "http.unibodi.post"], implementation_config: { kind: "http", method: "GET", url_template: "https://api.example.com/v3" }, source: "openapi", created_at: "2026-03-01T00:00:00Z", immutable: true },
  ],
};

function setupApi() {
  mockedGet.mockReset();
  mockedGet.mockImplementation((url: string) => {
    if (url === "/tools") return Promise.resolve({ data: { items: [calcTool, ciTool, openapiTool], total: 3 } });
    if (url === "/code-interpreter/status") return Promise.resolve({ data: ciStatusOnline });
    if (url === "/tool-openapi-1/versions" || url === "/tools/tool-openapi-1/versions")
      return Promise.resolve({ data: versionsPayload });
    if (url === "/tool-credentials") return Promise.resolve({ data: { items: [{ id: "cred-1", name: "MY_API_KEY", credential_type: "API_KEY", has_value: true }] } });
    if (url === "/projects/p1") return Promise.resolve({ data: { id: "p1", name: "Demo", default_enabled_tool_ids: ["tool-calculator-0001"] } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  (codeInterpreterStatus as jest.Mock).mockResolvedValue(ciStatusOnline);
  (listToolCredentials as jest.Mock).mockResolvedValue({ items: [{ id: "cred-1", name: "MY_API_KEY", credential_type: "API_KEY", has_value: true }] });
  (listToolValves as jest.Mock).mockResolvedValue({ items: [], total: 0 });
}

function renderTools() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ToolsPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  setupApi();
});

test("Tool Lab nav entry is visible in the Experiment section", () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <Sidebar collapsed={false} onToggle={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>
  );
  const nav = screen.getByTestId("navigation-sidebar");
  const labels = within(nav).getAllByRole("link").map((a) => a.textContent);
  expect(screen.getByTestId("nav-tool-lab")).toHaveTextContent("Tool Lab");
  const order = ["Arena", "Prompt Lab", "Context Lab", "Agent Lab", "Tool Lab", "Experiments", "Datasets", "Research", "Artifacts", "Findings", "Failures", "Red Team", "Scorecards", "Certification", "Observability", "Home", "Workspace", "Council", "Models"];
  let last = -1;
  for (const label of order) {
    const idx = labels.findIndex((t) => t === label);
    expect(idx).toBeGreaterThan(last);
    last = idx;
  }
});

test("Tools page loads registry rows with display name, type, scope, status, risk, version, permission, updated", async () => {
  renderTools();
  expect(await screen.findByTestId("tools-page")).toBeInTheDocument();
  const calc = await screen.findByTestId("tool-row-tool-calculator-0001");
  expect(calc).toHaveTextContent("Calculator");
  expect(calc).toHaveTextContent("Built-in");
  expect(calc).toHaveTextContent("GLOBAL");
  expect(within(calc).getByTestId("tool-status-tool-calculator-0001")).toHaveTextContent("Available");
  expect(within(calc).getByTestId("tool-risk-tool-calculator-0001")).toHaveTextContent("LOW");
  expect(within(calc).getByTestId("tool-version-tool-calculator-0001")).toHaveTextContent("v1.0.0");
  expect(calc).toHaveTextContent("calculator.execute");
  expect(within(calc).getByTestId("tool-updated-tool-calculator-0001")).not.toHaveTextContent("—");

  const ci = await screen.findByTestId("tool-row-tool-code-interpreter-0001");
  expect(ci).toHaveTextContent("Code Interpreter");
  await screen.findByTestId("tool-ci-image-tool-code-interpreter-0001");
  expect(within(ci).getByTestId("tool-status-tool-code-interpreter-0001")).toHaveTextContent("Sandbox online");
  expect(within(ci).getByTestId("tool-risk-tool-code-interpreter-0001")).toHaveTextContent("HIGH");

  const oa = await screen.findByTestId("tool-row-tool-openapi-1");
  expect(oa).toHaveTextContent("Unibodi API");
  expect(within(oa).getByTestId("tool-version-tool-openapi-1")).toHaveTextContent("v3");
  expect(within(oa).getByTestId("tool-risk-tool-openapi-1")).toHaveTextContent("MEDIUM");

  // Real GET /tools (no project filter for the global registry).
  expect(mockedGet).toHaveBeenCalledWith("/tools", { params: {} });
});

test("filters narrow the registry (Built-in / Code Interpreter)", async () => {
  const user = userEvent.setup({ delay: null });
  renderTools();
  await screen.findByTestId("tool-row-tool-calculator-0001");
  await user.click(screen.getByTestId("tools-filter-builtin"));
  expect(screen.getByTestId("tool-row-tool-calculator-0001")).toBeInTheDocument();
  expect(screen.queryByTestId("tool-row-tool-openapi-1")).not.toBeInTheDocument();
  await user.click(screen.getByTestId("tools-filter-code_interpreter"));
  expect(screen.getByTestId("tool-row-tool-code-interpreter-0001")).toBeInTheDocument();
  expect(screen.queryByTestId("tool-row-tool-calculator-0001")).not.toBeInTheDocument();
});

test("CI detail shows real runtime info without host paths or secrets", async () => {
  const user = userEvent.setup({ delay: null });
  renderTools();
  await user.click(await screen.findByTestId("tool-row-tool-code-interpreter-0001"));
  expect(await screen.findByTestId("tool-detail-dialog")).toBeInTheDocument();
  const runtime = await screen.findByTestId("tool-ci-runtime");
  expect(runtime).toHaveTextContent("Online");
  expect(screen.getByTestId("tool-ci-image")).toHaveTextContent("omnilab-code-interpreter:latest");
  expect(screen.getByTestId("tool-ci-network")).toHaveTextContent("Disabled");
  expect(screen.getByTestId("tool-ci-cpu")).toHaveTextContent("1");
  expect(screen.getByTestId("tool-ci-memory")).toHaveTextContent("512MB");
  expect(screen.getByTestId("tool-ci-timeout")).toHaveTextContent("30s");
  expect(screen.getByTestId("tool-ci-pids")).toHaveTextContent("128");
  expect(screen.getByTestId("tool-ci-output-limit")).toHaveTextContent("200KB");
  expect(screen.getByTestId("tool-ci-file-limit")).toHaveTextContent("10");
  expect(screen.getByTestId("tool-ci-packages")).toHaveTextContent("numpy");
  expect(screen.getByTestId("tool-ci-permission")).toHaveTextContent("code.execute");
  expect(document.body.textContent || "").not.toMatch(/sk-|supersecret|\/var\/|\/home\/|\/data\//i);
  expect(codeInterpreterStatus).toHaveBeenCalled();
});

test("CI test console defaults to print(2+2) and shows stdout/stderr/duration/exit/files", async () => {
  const user = userEvent.setup({ delay: null });
  (executeCode as jest.Mock).mockResolvedValueOnce({
    status: "success", stdout: "4\n", stderr: "", exit_code: 0, duration_ms: 42,
    files: [{ name: "plot.png" }], execution_id: "exec-1",
  });
  renderTools();
  await user.click(await screen.findByTestId("tool-row-tool-code-interpreter-0001"));
  const codeBox = await screen.findByTestId("tool-ci-code");
  expect(codeBox).toHaveValue("print(2+2)");
  await user.click(screen.getByTestId("tool-ci-run"));
  expect(executeCode).toHaveBeenCalledWith({ project_id: "p1", code: "print(2+2)" });
  const result = await screen.findByTestId("tool-ci-result");
  expect(result).toHaveTextContent("4");
  expect(screen.getByTestId("tool-ci-exit")).toHaveTextContent("exit 0");
  expect(screen.getByTestId("tool-ci-duration")).toHaveTextContent("42");
  expect(screen.getByTestId("tool-ci-files")).toHaveTextContent("plot.png");
});

test("Add HTTP Tool creates via POST /tools with full sections", async () => {
  const user = userEvent.setup({ delay: null });
  (createCustomTool as jest.Mock).mockResolvedValueOnce({ id: "tool-new" });
  renderTools();
  await screen.findByTestId("tool-row-tool-calculator-0001");
  await user.click(screen.getByTestId("tools-add-tool"));
  const dialog = await screen.findByTestId("add-http-tool-dialog");
  // All required sections present.
  for (const section of ["add-tool-general", "add-tool-request", "add-tool-input", "add-tool-mapping", "add-tool-security"]) {
    expect(within(dialog).getByTestId(section)).toBeInTheDocument();
  }
  fireEvent.change(screen.getByTestId("add-tool-name"), { target: { value: "my_api" } });
  fireEvent.change(screen.getByTestId("add-tool-display"), { target: { value: "My API" } });
  fireEvent.change(screen.getByTestId("add-tool-url"), { target: { value: "https://api.example.com/v1/things/{id}" } });
  fireEvent.change(screen.getByTestId("add-tool-permissions"), { target: { value: "http.api.get" } });
  await user.click(screen.getByTestId("add-tool-save"));
  expect(createCustomTool).toHaveBeenCalledWith(expect.objectContaining({
    name: "my_api",
    display_name: "My API",
    version: expect.objectContaining({
      tool_type: "http",
      implementation_config: expect.objectContaining({ method: "GET", url_template: "https://api.example.com/v1/things/{id}" }),
    }),
  }));
  // No arbitrary Python tool type is offered here.
  expect(screen.queryByTestId("add-tool-python")).not.toBeInTheDocument();
});

test("OpenAPI import checks only GET by default and generates only selected", async () => {
  const user = userEvent.setup({ delay: null });
  (importOpenAPI as jest.Mock).mockResolvedValueOnce({
    title: "Unibodi", version: "1.0.0", base_url: "https://api.example.com",
    operation_count: 4,
    operations: [
      { operation_id: "getProducts", method: "GET", path: "/products", summary: "List" },
      { operation_id: "getOrder", method: "GET", path: "/orders/{id}", summary: "Get" },
      { operation_id: "createOrder", method: "POST", path: "/orders", summary: "Create" },
      { operation_id: "deleteOrder", method: "DELETE", path: "/orders/{id}", summary: "Delete" },
    ],
  });
  (generateFromOpenAPI as jest.Mock).mockResolvedValueOnce({ items: [{ id: "t1" }], errors: [] });
  renderTools();
  await screen.findByTestId("tool-row-tool-calculator-0001");
  await user.click(screen.getByTestId("tools-import-openapi"));
  fireEvent.change(await screen.findByTestId("openapi-spec-input"), { target: { value: '{"openapi":"3.0.0"}' } });
  await user.click(screen.getByTestId("openapi-preview-submit"));
  await screen.findByTestId("openapi-op-getProducts");
  // Safe default: GETs checked, POST/DELETE unchecked (never auto-enable all).
  expect(within(screen.getByTestId("openapi-op-getProducts")).getByRole("checkbox")).toBeChecked();
  expect(within(screen.getByTestId("openapi-op-getOrder")).getByRole("checkbox")).toBeChecked();
  expect(within(screen.getByTestId("openapi-op-createOrder")).getByRole("checkbox")).not.toBeChecked();
  expect(within(screen.getByTestId("openapi-op-deleteOrder")).getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByTestId("openapi-selection-count")).toHaveTextContent("2 of 4 selected");
  // Preview + credentials/permissions/risk save options present.
  expect(screen.getByTestId("openapi-preview")).toHaveTextContent("2 tools will be created");
  expect(screen.getByTestId("openapi-save-options")).toBeInTheDocument();
  await user.click(screen.getByTestId("openapi-generate-submit"));
  expect(generateFromOpenAPI).toHaveBeenCalledWith(expect.objectContaining({
    operations: expect.arrayContaining([
      expect.objectContaining({ operation_id: "getProducts", include: true }),
      expect.objectContaining({ operation_id: "getOrder", include: true }),
    ]),
  }));
  const ops = (generateFromOpenAPI as jest.Mock).mock.calls[0][0].operations;
  expect(ops.map((o: any) => o.operation_id).sort()).toEqual(["getOrder", "getProducts"]);
});

test("Advanced credentials panel (moved to Settings) masks secrets and shows usage; create/update/delete wired", async () => {
  const user = userEvent.setup({ delay: null });
  (createToolCredential as jest.Mock).mockResolvedValueOnce({ id: "cred-2" });
  (updateToolCredential as jest.Mock).mockResolvedValueOnce({ id: "cred-1" });
  (deleteToolCredential as jest.Mock).mockResolvedValueOnce({ ok: true });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <CredentialsPanel
          projectId="p1"
          availableProjects={[{ id: "p1", name: "Demo" }]}
          credentials={[{ id: "cred-1", name: "MY_API_KEY", credential_type: "API_KEY", has_value: true }]}
          isLoading={false}
          isError={false}
          error={null}
          tools={[]}
        />
      </QueryClientProvider>
    </MemoryRouter>
  );
  const row = await screen.findByTestId("credential-cred-1");
  expect(row).toHaveTextContent("MY_API_KEY");
  expect(within(row).getByTestId("credential-stored-cred-1")).toHaveTextContent("Stored");
  expect(within(row).getByTestId("credential-masked-cred-1")).toHaveTextContent("••••••••");
  expect(within(row).getByTestId("credential-usage-cred-1")).toHaveTextContent("Used by");
  expect(document.body.textContent || "").not.toMatch(/supersecret|sk-/i);
  // Create flow.
  await user.click(screen.getByTestId("credential-create"));
  fireEvent.change(await screen.findByTestId("credential-name-input"), { target: { value: "OTHER_KEY" } });
  fireEvent.change(screen.getByTestId("credential-value-input"), { target: { value: "s3cr3t" } });
  await user.click(screen.getByTestId("credential-create-submit"));
  expect(createToolCredential).toHaveBeenCalledWith(expect.objectContaining({ project_id: "p1", name: "OTHER_KEY" }));
  // Update-replace flow.
  await user.click(screen.getByTestId("credential-rotate-cred-1"));
  fireEvent.change(screen.getByTestId("credential-rotate-input-cred-1"), { target: { value: "n3w" } });
  await user.click(screen.getByTestId("credential-rotate-save-cred-1"));
  expect(updateToolCredential).toHaveBeenCalledWith("cred-1", { value: "n3w" });
  // Delete flow.
  await user.click(screen.getByTestId("credential-delete-cred-1"));
  expect(deleteToolCredential).toHaveBeenCalledWith("cred-1");
});

test("Tools page no longer exposes a Credentials tab", async () => {
  renderTools();
  await screen.findByTestId("tools-page");
  expect(screen.queryByTestId("tools-tab-credentials")).not.toBeInTheDocument();
  expect(screen.getByTestId("tools-tab-connections")).toBeInTheDocument();
});

test("Tool versions show v3 CURRENT/v2/v1 with dates and change notes", async () => {
  const user = userEvent.setup({ delay: null });
  renderTools();
  await user.click(await screen.findByTestId("tool-row-tool-openapi-1"));
  expect(await screen.findByTestId("tool-detail-dialog")).toBeInTheDocument();
  await user.click(screen.getByTestId("tool-detail-tab-versions"));
  const v3 = await screen.findByTestId("tool-version-ver-3");
  expect(v3).toHaveTextContent("v3");
  expect(screen.getByTestId("tool-version-current-ver-3")).toHaveTextContent("CURRENT");
  expect(v3).toHaveTextContent("2026-03-01");
  expect(await screen.findByTestId("tool-version-ver-2")).toHaveTextContent("2026-02-01");
  expect(await screen.findByTestId("tool-version-ver-1")).toHaveTextContent("2026-01-01");
  expect(screen.getByTestId("tool-version-changes-ver-3")).toHaveTextContent("url");
  expect(mockedGet).toHaveBeenCalledWith("/tools/tool-openapi-1/versions");
});

test("Project defaults tab shows available checkboxes with GLOBAL/PROJECT badges", async () => {
  const user = userEvent.setup({ delay: null });
  renderTools();
  await screen.findByTestId("tool-row-tool-calculator-0001");
  await user.click(screen.getByTestId("tools-tab-projects"));
  const list = await screen.findByTestId("project-tools-list");
  expect(within(list).getByTestId("project-available-tool-tool-calculator-0001")).toHaveTextContent("GLOBAL");
  expect(within(list).getByTestId("project-available-tool-tool-openapi-1")).toHaveTextContent("PROJECT");
  expect(within(list).getByTestId("project-available-tool-tool-code-interpreter-0001")).toHaveTextContent("Code Interpreter");
  expect(screen.getByTestId("project-tools-save")).toBeInTheDocument();
});

test("Generic tool test posts args and renders latency/status", async () => {
  const user = userEvent.setup({ delay: null });
  (testTool as jest.Mock).mockResolvedValueOnce({
    tool_call: { id: "tc1", execution_status: "success", duration_ms: 77 },
    output: { ok: true },
  });
  renderTools();
  await user.click(await screen.findByTestId("tool-row-tool-openapi-1"));
  await user.click(await screen.findByTestId("tool-detail-tab-test"));
  fireEvent.change(await screen.findByTestId("tool-test-args"), { target: { value: '{"id":"1"}' } });
  await user.click(screen.getByTestId("tool-test-submit"));
  expect(testTool).toHaveBeenCalledWith("tool-openapi-1", { arguments: { id: "1" }, project_id: "p1" });
  expect(await screen.findByTestId("tool-test-result")).toHaveTextContent("77");
});
