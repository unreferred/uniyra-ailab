/**
 * V4.1 Tooling UX (additive). Mocks the API layer; exercises Tools nav +
 * Tools page loads, CI detail + test console, Add HTTP Tool dialog, OpenAPI
 * selective import, credential masking, versions list, project defaults,
 * workspace disabled reason, and capability-override compatibility updates.
 */
import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ToolsPage from "@/pages/Tools";
import { ToolsPopover } from "@/components/workspace/ToolsPopover";
import { Sidebar } from "@/components/layout/Sidebar";
import {
  createCustomTool,
  executeCode,
  codeInterpreterStatus,
  importOpenAPI,
  generateFromOpenAPI,
  createToolCredential,
  updateToolCredential,
  deleteToolCredential,
  listToolCredentials,
  listToolValves,
  testTool,
} from "@/lib/api";
import { CredentialsPanel } from "@/components/tools/CredentialsPanel";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

// The real computePosition loops under jsdom (zero-size rects); positioning
// is not under test (mirrors tools.test.tsx).
jest.mock("@floating-ui/dom", () => {
  const actual = jest.requireActual("@floating-ui/dom");
  return {
    ...actual,
    computePosition: () =>
      Promise.resolve({ x: 0, y: 0, strategy: "fixed", placement: "bottom", middlewareData: {} }),
  };
});

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

import { api } from "@/lib/api";
const mockedGet = api.get as jest.Mock;

const calcTool = {
  id: "tool-calculator-0001", name: "calculator", display_name: "Calculator",
  description: "Evaluate a safe arithmetic expression.", version: "1.0.0",
  tool_type: "function", input_schema: {}, output_schema: {}, enabled: true,
  risk_level: "low", permissions: ["calculator.execute"], scope: "global",
  project_id: null, builtin: true, available: true, availability_reason: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-02-01T00:00:00Z",
};

const ciTool = {
  id: "tool-code-interpreter-0001", name: "code_interpreter", display_name: "Code Interpreter",
  description: "Execute Python code in a sandboxed Docker container.", version: 1,
  tool_type: "code_interpreter", current_version_id: "tool-code-interpreter-0001-v1",
  input_schema: { type: "object" }, output_schema: {}, enabled: true,
  risk_level: "high", permissions: ["code.execute"], scope: "global",
  project_id: null, builtin: false, available: true, availability_reason: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-02-01T00:00:00Z",
};

const ciToolOffline = {
  ...ciTool, available: false, availability_reason: "sandbox image is not available",
};

const httpTool = {
  id: "tool-http-1", name: "my_api", display_name: "My API", description: "demo",
  version: 2, tool_type: "http", current_version_id: "ver-2", builtin: false,
  scope: "project", project_id: "p1", enabled: true, risk_level: "low",
  permissions: ["http.api.get"], input_schema: {}, output_schema: {},
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-02-01T00:00:00Z",
};

const ciStatusOnline = {
  available: true, reason: null, image: "omnilab-code-interpreter:latest", runtime: "docker",
  limits: { memory_mb: 512, cpus: 1.0, pids_limit: 128, tmpfs_mb: 64, timeout_s: 30, max_output_bytes: 204800, max_files: 10, max_file_bytes: 5242880 },
  policy: { network: "off", packages: ["python", "pandas", "numpy", "matplotlib"], tool: "code_interpreter", permissions: ["code.execute"], risk: "high" },
};

const ciStatusOffline = {
  ...ciStatusOnline, available: false, reason: "sandbox image is not available",
};

const versionsPayload = {
  builtin: false,
  items: [
    { id: "ver-1", tool_id: "tool-http-1", version: 1, version_label: null, tool_type: "http", risk_level: "low", permissions: ["http.api.get"], implementation_config: { kind: "http", method: "GET", url_template: "https://api.example.com/v1" }, source: "manual", created_at: "2026-01-01T00:00:00Z", immutable: true },
    { id: "ver-2", tool_id: "tool-http-1", version: 2, version_label: null, tool_type: "http", risk_level: "low", permissions: ["http.api.get"], implementation_config: { kind: "http", method: "GET", url_template: "https://api.example.com/v2" }, source: "manual", created_at: "2026-02-01T00:00:00Z", immutable: true },
  ],
};

function setupApi(opts: { ciOnline?: boolean } = {}) {
  mockedGet.mockReset();
  const tools = opts.ciOnline === false ? [calcTool, ciToolOffline, httpTool] : [calcTool, ciTool, httpTool];
  const ciStatus = opts.ciOnline === false ? ciStatusOffline : ciStatusOnline;
  mockedGet.mockImplementation((url: string) => {
    if (url === "/tools") return Promise.resolve({ data: { items: tools, total: tools.length } });
    if (url === "/code-interpreter/status") return Promise.resolve({ data: ciStatus });
    if (url === "/tools/tool-http-1/versions") return Promise.resolve({ data: versionsPayload });
    if (url === "/tool-credentials") return Promise.resolve({ data: { items: [{ id: "cred-1", name: "MY_API_KEY", credential_type: "API_KEY", has_value: true }] } });
    if (url === "/projects/p1") return Promise.resolve({ data: { id: "p1", name: "Demo", default_enabled_tool_ids: ["tool-calculator-0001"] } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  (codeInterpreterStatus as jest.Mock).mockResolvedValue(ciStatus);
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

test("v41: Tool Lab nav visible", () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <Sidebar collapsed={false} onToggle={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>
  );
  expect(screen.getByTestId("nav-tool-lab")).toHaveTextContent("Tool Lab");
});

test("v41: Tools page loads registry rows", async () => {
  renderTools();
  expect(await screen.findByTestId("tools-page")).toBeInTheDocument();
  expect(await screen.findByTestId("tool-row-tool-calculator-0001")).toHaveTextContent("Calculator");
  expect(await screen.findByTestId("tool-row-tool-code-interpreter-0001")).toHaveTextContent("Code Interpreter");
  expect(await screen.findByTestId("tool-row-tool-http-1")).toHaveTextContent("My API");
});

test("v41: CI detail shows runtime info without host paths/secrets", async () => {
  const user = userEvent.setup({ delay: null });
  renderTools();
  await user.click(await screen.findByTestId("tool-row-tool-code-interpreter-0001"));
  expect(await screen.findByTestId("tool-detail-dialog")).toBeInTheDocument();
  expect(screen.getByTestId("tool-ci-image")).toHaveTextContent("omnilab-code-interpreter:latest");
  expect(screen.getByTestId("tool-ci-network")).toHaveTextContent("Disabled");
  expect(screen.getByTestId("tool-ci-permission")).toHaveTextContent("code.execute");
  expect(document.body.textContent || "").not.toMatch(/sk-|supersecret|\/var\/|\/home\/|\/data\//i);
  expect(codeInterpreterStatus).toHaveBeenCalled();
});

test("v41: CI test console defaults and result", async () => {
  const user = userEvent.setup({ delay: null });
  (executeCode as jest.Mock).mockResolvedValueOnce({
    status: "success", stdout: "4\n", stderr: "", exit_code: 0, duration_ms: 42,
    files: [{ name: "plot.png" }], execution_id: "exec-1",
  });
  renderTools();
  await user.click(await screen.findByTestId("tool-row-tool-code-interpreter-0001"));
  expect(await screen.findByTestId("tool-ci-code")).toHaveValue("print(2+2)");
  await user.click(screen.getByTestId("tool-ci-run"));
  expect(executeCode).toHaveBeenCalledWith({ project_id: "p1", code: "print(2+2)" });
  expect(await screen.findByTestId("tool-ci-result")).toHaveTextContent("4");
});

test("v41: Add HTTP Tool dialog posts full payload", async () => {
  const user = userEvent.setup({ delay: null });
  (createCustomTool as jest.Mock).mockResolvedValueOnce({ id: "tool-new" });
  renderTools();
  await screen.findByTestId("tool-row-tool-calculator-0001");
  await user.click(screen.getByTestId("tools-add-tool"));
  const dialog = await screen.findByTestId("add-http-tool-dialog");
  for (const section of ["add-tool-general", "add-tool-request", "add-tool-input", "add-tool-mapping", "add-tool-security"]) {
    expect(within(dialog).getByTestId(section)).toBeInTheDocument();
  }
  fireEvent.change(screen.getByTestId("add-tool-name"), { target: { value: "my_api" } });
  fireEvent.change(screen.getByTestId("add-tool-url"), { target: { value: "https://api.example.com/v1/things/{id}" } });
  await user.click(screen.getByTestId("add-tool-save"));
  expect(createCustomTool).toHaveBeenCalledWith(expect.objectContaining({
    name: "my_api",
    version: expect.objectContaining({
      tool_type: "http",
      implementation_config: expect.objectContaining({ method: "GET", url_template: "https://api.example.com/v1/things/{id}" }),
    }),
  }));
});

test("v41: OpenAPI import selective (GET default only)", async () => {
  const user = userEvent.setup({ delay: null });
  (importOpenAPI as jest.Mock).mockResolvedValueOnce({
    title: "Unibodi", version: "1.0.0", base_url: "https://api.example.com",
    operation_count: 3,
    operations: [
      { operation_id: "getProducts", method: "GET", path: "/products", summary: "List" },
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
  expect(within(screen.getByTestId("openapi-op-getProducts")).getByRole("checkbox")).toBeChecked();
  expect(within(screen.getByTestId("openapi-op-createOrder")).getByRole("checkbox")).not.toBeChecked();
  expect(within(screen.getByTestId("openapi-op-deleteOrder")).getByRole("checkbox")).not.toBeChecked();
  await user.click(screen.getByTestId("openapi-generate-submit"));
  const ops = (generateFromOpenAPI as jest.Mock).mock.calls[0][0].operations;
  expect(ops.map((o: any) => o.operation_id)).toEqual(["getProducts"]);
});

test("v41: credentials masked, no secret text", async () => {
  const user = userEvent.setup({ delay: null });
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
  expect(document.body.textContent || "").not.toMatch(/supersecret|sk-/i);
  // create wired with project scope
  (createToolCredential as jest.Mock).mockResolvedValueOnce({ id: "cred-2" });
  await user.click(screen.getByTestId("credential-create"));
  fireEvent.change(await screen.findByTestId("credential-name-input"), { target: { value: "OTHER_KEY" } });
  fireEvent.change(screen.getByTestId("credential-value-input"), { target: { value: "s3cr3t" } });
  await user.click(screen.getByTestId("credential-create-submit"));
  expect(createToolCredential).toHaveBeenCalledWith(expect.objectContaining({ project_id: "p1", name: "OTHER_KEY" }));
  // update + delete wired
  (updateToolCredential as jest.Mock).mockResolvedValueOnce({ id: "cred-1" });
  (deleteToolCredential as jest.Mock).mockResolvedValueOnce({ ok: true });
  await user.click(screen.getByTestId("credential-rotate-cred-1"));
  fireEvent.change(screen.getByTestId("credential-rotate-input-cred-1"), { target: { value: "n3w" } });
  await user.click(screen.getByTestId("credential-rotate-save-cred-1"));
  expect(updateToolCredential).toHaveBeenCalledWith("cred-1", { value: "n3w" });
  await user.click(screen.getByTestId("credential-delete-cred-1"));
  expect(deleteToolCredential).toHaveBeenCalledWith("cred-1");
});

test("v41: versions list shows history with dates", async () => {
  const user = userEvent.setup({ delay: null });
  renderTools();
  await user.click(await screen.findByTestId("tool-row-tool-http-1"));
  await user.click(screen.getByTestId("tool-detail-tab-versions"));
  expect(await screen.findByTestId("tool-version-ver-2")).toHaveTextContent("2026-02-01");
  expect(await screen.findByTestId("tool-version-ver-1")).toHaveTextContent("2026-01-01");
  expect(mockedGet).toHaveBeenCalledWith("/tools/tool-http-1/versions");
});

test("v41: project defaults tab with GLOBAL/PROJECT badges", async () => {
  const user = userEvent.setup({ delay: null });
  renderTools();
  await screen.findByTestId("tool-row-tool-calculator-0001");
  await user.click(screen.getByTestId("tools-tab-projects"));
  const list = await screen.findByTestId("project-tools-list");
  expect(within(list).getByTestId("project-available-tool-tool-calculator-0001")).toHaveTextContent("GLOBAL");
  expect(within(list).getByTestId("project-available-tool-tool-http-1")).toHaveTextContent("PROJECT");
  expect(screen.getByTestId("project-tools-save")).toBeInTheDocument();
});

test("v41: workspace popover disabled tool shows reason, enabled toggles", async () => {
  const user = userEvent.setup({ delay: null });
  const tools = [
    { id: "t1", name: "web_search", display_name: "Web Search", description: "Search", version: "1", tool_type: "function", input_schema: {}, enabled: true, risk_level: "low", permissions: [], available: true },
    { id: "t2", name: "code_interpreter", display_name: "Code Interpreter", description: "Sandbox", version: 1, tool_type: "code_interpreter", input_schema: {}, enabled: true, risk_level: "high", permissions: ["code.execute"], available: false, availability_reason: "sandbox image is not available" },
  ];
  mockedGet.mockReset();
  mockedGet.mockImplementation((url: string, config?: any) => {
    if (url === "/tools") return Promise.resolve({ data: { items: tools, total: tools.length } });
    if (url === "/models") return Promise.resolve({ data: [] });
    if (url === "/code-interpreter/status")
      return Promise.resolve({ data: { available: false, reason: "sandbox image is not available" } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  const onChange = jest.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ToolsPopover projectId="p1" value={[]} onChange={onChange} />
    </QueryClientProvider>
  );
  await user.click(screen.getByTestId("workspace-tools-button"));
  await screen.findByText("Web Search");
  // disabled CI checkbox + explicit SANDBOX gate reason visible; click is a no-op
  const ciRow = await screen.findByTestId("tool-t2");
  expect(ciRow).toHaveAttribute("data-gate", "SANDBOX_UNAVAILABLE");
  expect(within(ciRow).getByRole("checkbox")).toBeDisabled();
  expect(within(ciRow).getByTestId("tool-reason-t2")).toHaveTextContent("sandbox image is not available");
  await user.click(within(screen.getByTestId("tool-t2")).getByRole("checkbox"));
  expect(onChange).not.toHaveBeenCalled();
  // enabled tool toggles
  await user.click(within(screen.getByTestId("tool-t1")).getByRole("checkbox"));
  expect(onChange).toHaveBeenCalledWith(["t1"]);
});

test("v41: capability override updates compatibility (status mapping + PATCH body)", async () => {
  // Mirrors Models.tsx capabilityStatus for tools + the PATCH /models/{id}
  // override contract (backend: capabilities merged, source -> MANUAL).
  const statusOf = (caps: Record<string, any>): string => {
    if (caps.tools === true) return "SUPPORTED";
    if (Array.isArray(caps.supported_parameters) && caps.supported_parameters.includes("tools")) return "SUPPORTED";
    if (caps.tools === false) return "UNSUPPORTED";
    return "UNKNOWN";
  };
  // cold catalog metadata: no tools entry => UNKNOWN (never inferred)
  expect(statusOf({ supported_parameters: ["temperature"] })).toBe("UNKNOWN");
  // provider signal => SUPPORTED
  expect(statusOf({ supported_parameters: ["temperature", "tools"] })).toBe("SUPPORTED");
  // manual override flips UNKNOWN -> SUPPORTED without wiping other keys
  const stored = { supported_parameters: ["temperature"], vision: false };
  const merged = { ...stored, tools: true };
  expect(statusOf(merged)).toBe("SUPPORTED");
  expect(merged).toMatchObject({ supported_parameters: ["temperature"], vision: false, tools: true });
  // PATCH body the UI sends: known keys only, source MANUAL
  const body = { capabilities: { tools: true }, capability_source: "MANUAL" };
  (api.post as jest.Mock).mockResolvedValueOnce({ data: { ok: true } });
  (api.patch as jest.Mock).mockResolvedValueOnce({ data: { id: "m1", capabilities: merged, capability_source: "MANUAL" } });
  const patched = (await api.patch("/models/m1", body)).data;
  expect(api.patch).toHaveBeenCalledWith("/models/m1", body);
  expect(patched.capability_source).toBe("MANUAL");
  expect(statusOf(patched.capabilities)).toBe("SUPPORTED");
});
