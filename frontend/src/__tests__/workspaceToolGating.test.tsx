import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToolsPopover } from "@/components/workspace/ToolsPopover";
import { CompatibilityInspector } from "@/components/workspace/CompatibilityInspector";
import { api } from "@/lib/api";

jest.mock("@floating-ui/dom", () => {
  const actual = jest.requireActual("@floating-ui/dom");
  return {
    ...actual,
    computePosition: () =>
      Promise.resolve({ x: 0, y: 0, strategy: "fixed", placement: "bottom", middlewareData: {} }),
  };
});

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  API_BASE: "/api",
}));

const mockedGet = api.get as jest.Mock;

const toolCapableModel: any = {
  id: "m-capable",
  owner_id: "u1",
  provider_id: "prov1",
  display_name: "DeepSeek V4.1",
  model_identifier: "deepseek-v4.1",
  family: "openai",
  enabled: true,
  context_window: 64000,
  input_cost: null,
  output_cost: null,
  capabilities: { text: true, streaming: true, tools: true },
  capability_source: "PROVIDER",
  tags: [],
  notes: "",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const unknownModel: any = {
  ...toolCapableModel,
  id: "m-unknown",
  display_name: "Mystery Model",
  model_identifier: "mystery-1",
  capabilities: {},
  capability_source: "UNKNOWN",
};

const unsupportedModel: any = {
  ...toolCapableModel,
  id: "m-no-tools",
  display_name: "Chat Only",
  model_identifier: "chat-only-1",
  capabilities: { text: true, tools: false },
  capability_source: "PROVIDER",
};

const calcTool: any = {
  id: "tool-calculator-0001",
  name: "calculator",
  display_name: "Calculator",
  description: "Evaluate arithmetic",
  version: "1.0.0",
  tool_type: "function",
  input_schema: {},
  enabled: true,
  risk_level: "low",
  permissions: ["calculator.execute"],
  scope: "global",
  project_id: null,
  builtin: true,
  available: true,
  availability_reason: null,
};

const ciTool: any = {
  id: "tool-code-interpreter-0001",
  name: "code_interpreter",
  display_name: "Code Interpreter",
  description: "Sandboxed python",
  version: 1,
  tool_type: "code_interpreter",
  current_version_id: "tool-code-interpreter-0001-v1",
  input_schema: {},
  enabled: true,
  risk_level: "high",
  permissions: ["code.execute"],
  scope: "global",
  project_id: null,
  builtin: false,
  available: true,
  availability_reason: null,
  permission_effective: "AVAILABLE_TO_SELECT",
  required_permissions: ["code.execute"],
};

const otherProjectTool: any = {
  id: "tool-other-1",
  name: "other_api",
  display_name: "Other API",
  description: "",
  version: "1",
  tool_type: "http",
  input_schema: {},
  enabled: true,
  risk_level: "medium",
  permissions: ["http.other.get"],
  scope: "project",
  project_id: "p-other",
  builtin: false,
  available: true,
  availability_reason: null,
};

const deniedTool: any = {
  id: "tool-denied-1",
  name: "denied_api",
  display_name: "Denied API",
  description: "",
  version: "1",
  tool_type: "http",
  input_schema: {},
  enabled: true,
  risk_level: "high",
  permissions: ["http.denied.get"],
  scope: "project",
  project_id: "p1",
  builtin: false,
  available: false,
  availability_reason: "Permission denied: http.denied.get not granted",
};

const tools = [calcTool, ciTool, otherProjectTool, deniedTool];

function setupApi(opts: { sandboxAvailable: boolean; sandboxReason?: string | null }) {
  mockedGet.mockReset();
  mockedGet.mockImplementation((url: string) => {
    if (url === "/tools") return Promise.resolve({ data: { items: tools, total: tools.length } });
    if (url === "/models") return Promise.resolve({ data: [toolCapableModel, unknownModel, unsupportedModel] });
    if (url === "/code-interpreter/status")
      return Promise.resolve({
        data: {
          available: opts.sandboxAvailable,
          reason: opts.sandboxReason ?? null,
          image: "omnilab-code-interpreter:latest",
          runtime: "docker",
          limits: { timeout_s: 30 },
          policy: { network: "off", packages: ["python"] },
        },
      });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

function renderPopover(modelId?: string | null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ToolsPopover projectId="p1" value={[]} onChange={() => {}} modelId={modelId} />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
});

test("BUILT-IN vs PROJECT TOOLS groups with explicit gates for a tool-capable model", async () => {
  const user = userEvent.setup({ delay: null });
  setupApi({ sandboxAvailable: true });
  renderPopover("m-capable");
  await user.click(screen.getByTestId("workspace-tools-button"));

  expect(await screen.findByTestId("tools-group-BUILT-IN")).toBeInTheDocument();
  expect(screen.getByTestId("tools-group-PROJECT TOOLS")).toBeInTheDocument();

  // Calculator available.
  expect(screen.getByTestId("tool-tool-calculator-0001")).toHaveAttribute("data-gate", "AVAILABLE");
  // Code interpreter selectable when sandbox online + model tool-capable.
  expect(screen.getByTestId("tool-tool-code-interpreter-0001")).toHaveAttribute("data-gate", "AVAILABLE");
  expect(within(screen.getByTestId("tool-tool-code-interpreter-0001")).getByRole("checkbox")).not.toBeDisabled();
  // Other-project tool explains project scoping.
  const other = screen.getByTestId("tool-tool-other-1");
  expect(other).toHaveAttribute("data-gate", "DISABLED_BY_PROJECT");
  expect(screen.getByTestId("tool-reason-tool-other-1")).toHaveTextContent("DISABLED_BY_PROJECT");
  // Permission denial is distinguished.
  const denied = screen.getByTestId("tool-tool-denied-1");
  expect(denied).toHaveAttribute("data-gate", "PERMISSION_DENIED");
  expect(screen.getByTestId("tool-reason-tool-denied-1")).toHaveTextContent("PERMISSION_DENIED");
});

test("live sandbox ONLINE overrides stale registry snapshot (cold cache)", async () => {
  const user = userEvent.setup({ delay: null });
  setupApi({ sandboxAvailable: true });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const staleCiTool = {
    ...ciTool,
    available: false,
    availability_reason: "status unknown — provider not yet checked",
  };
  mockedGet.mockImplementation((url: string) => {
    if (url === "/tools")
      return Promise.resolve({ data: { items: [calcTool, staleCiTool], total: 2 } });
    if (url === "/models") return Promise.resolve({ data: [toolCapableModel] });
    if (url === "/code-interpreter/status")
      return Promise.resolve({
        data: { available: true, reason: null, image: "omnilab-code-interpreter:latest" },
      });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ToolsPopover projectId="p1" value={[]} onChange={() => {}} modelId="m-capable" />
      </QueryClientProvider>
    </MemoryRouter>
  );
  await user.click(screen.getByTestId("workspace-tools-button"));
  const ci = await screen.findByTestId("tool-tool-code-interpreter-0001");
  expect(ci).toHaveAttribute("data-gate", "AVAILABLE");
  expect(within(ci).getByRole("checkbox")).not.toBeDisabled();
});

test("'status unknown' never maps to PERMISSION_DENIED", async () => {
  const user = userEvent.setup({ delay: null });
  setupApi({ sandboxAvailable: true });
  const unknownTool = {
    ...calcTool,
    id: "tool-unknown-1",
    name: "mystery_api",
    display_name: "Mystery API",
    available: false,
    availability_reason: "status unknown",
  };
  mockedGet.mockImplementation((url: string) => {
    if (url === "/tools")
      return Promise.resolve({ data: { items: [unknownTool], total: 1 } });
    if (url === "/models") return Promise.resolve({ data: [toolCapableModel] });
    if (url === "/code-interpreter/status")
      return Promise.resolve({ data: { available: true, reason: null } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ToolsPopover projectId="p1" value={[]} onChange={() => {}} modelId="m-capable" />
      </QueryClientProvider>
    </MemoryRouter>
  );
  await user.click(screen.getByTestId("workspace-tools-button"));
  const row = await screen.findByTestId("tool-tool-unknown-1");
  expect(row).toHaveAttribute("data-gate", "UNAVAILABLE");
  expect(row).not.toHaveAttribute("data-gate", "PERMISSION_DENIED");
  expect(screen.getByTestId("tool-reason-tool-unknown-1")).toHaveTextContent("UNAVAILABLE");
});

test("genuine permission reasons still map to PERMISSION_DENIED", async () => {
  const user = userEvent.setup({ delay: null });
  setupApi({ sandboxAvailable: true });
  const forbiddenTool = {
    ...calcTool,
    id: "tool-forbidden-1",
    name: "forbidden_api",
    display_name: "Forbidden API",
    available: false,
    availability_reason: "forbidden: credentials lack scope",
  };
  mockedGet.mockImplementation((url: string) => {
    if (url === "/tools")
      return Promise.resolve({ data: { items: [forbiddenTool], total: 1 } });
    if (url === "/models") return Promise.resolve({ data: [toolCapableModel] });
    if (url === "/code-interpreter/status")
      return Promise.resolve({ data: { available: true, reason: null } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ToolsPopover projectId="p1" value={[]} onChange={() => {}} modelId="m-capable" />
      </QueryClientProvider>
    </MemoryRouter>
  );
  await user.click(screen.getByTestId("workspace-tools-button"));
  const row = await screen.findByTestId("tool-tool-forbidden-1");
  expect(row).toHaveAttribute("data-gate", "PERMISSION_DENIED");
});

test("sandbox offline disables Code Interpreter with SANDBOX_UNAVAILABLE reason", async () => {
  const user = userEvent.setup({ delay: null });
  setupApi({ sandboxAvailable: false, sandboxReason: "sandbox image is not available" });
  renderPopover("m-capable");
  await user.click(screen.getByTestId("workspace-tools-button"));

  const ci = await screen.findByTestId("tool-tool-code-interpreter-0001");
  expect(ci).toHaveAttribute("data-gate", "SANDBOX_UNAVAILABLE");
  expect(within(ci).getByRole("checkbox")).toBeDisabled();
  expect(screen.getByTestId("tool-reason-tool-code-interpreter-0001")).toHaveTextContent("sandbox image is not available");
});

test("MODEL_UNSUPPORTED vs MODEL_CAPABILITY_UNKNOWN distinguished; unknown links to /models", async () => {
  const user = userEvent.setup({ delay: null });
  setupApi({ sandboxAvailable: true });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ToolsPopover projectId="p1" value={[]} onChange={() => {}} modelId="m-no-tools" />
      </QueryClientProvider>
    </MemoryRouter>
  );
  await user.click(screen.getByTestId("workspace-tools-button"));
  const calc = await screen.findByTestId("tool-tool-calculator-0001");
  expect(calc).toHaveAttribute("data-gate", "MODEL_UNSUPPORTED");
  expect(screen.getByTestId("tool-reason-tool-calculator-0001")).toHaveTextContent(
    "Model does not support tool/function calling",
  );
  expect(screen.queryByTestId("tool-inspect-tool-calculator-0001")).not.toBeInTheDocument();
});

test("capability unknown shows 'Tool calling capability unknown' + inspect link", async () => {
  const user = userEvent.setup({ delay: null });
  setupApi({ sandboxAvailable: true });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ToolsPopover projectId="p1" value={[]} onChange={() => {}} modelId="m-unknown" />
      </QueryClientProvider>
    </MemoryRouter>
  );
  await user.click(screen.getByTestId("workspace-tools-button"));
  const calc = await screen.findByTestId("tool-tool-calculator-0001");
  expect(calc).toHaveAttribute("data-gate", "MODEL_CAPABILITY_UNKNOWN");
  expect(screen.getByTestId("tool-reason-tool-calculator-0001")).toHaveTextContent(
    "Tool calling capability unknown",
  );
  const link = screen.getByTestId("tool-inspect-tool-calculator-0001");
  expect(link).toHaveTextContent("Inspect model capabilities");
  expect(link.getAttribute("href")).toBe("/models");
});

test("compatibility inspector shows MODEL panel + TOOLS gates + inspect action", async () => {
  const user = userEvent.setup({ delay: null });
  setupApi({ sandboxAvailable: true });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <CompatibilityInspector projectId="p1" model={unknownModel} enabledToolIds={[]} />
      </QueryClientProvider>
    </MemoryRouter>
  );
  await user.click(screen.getByTestId("workspace-compatibility-button"));
  const panel = await screen.findByTestId("workspace-compatibility");
  expect(within(panel).getByTestId("compatibility-model-name")).toHaveTextContent("Mystery Model");
  // DeepSeek-style example: Text ✓ Streaming ✓ Tool calling ? Vision ?
  expect(within(panel).getByTestId("compatibility-model-symbols")).toHaveTextContent("Tool calling ?");
  const calc = within(panel).getByTestId("compat-tool-tool-calculator-0001");
  expect(calc).toHaveAttribute("data-gate", "MODEL_CAPABILITY_UNKNOWN");
  expect(calc).toHaveTextContent("capability unknown");
  expect(within(panel).getByTestId("compatibility-capability-unknown-note")).toHaveTextContent(
    "Tool calling capability unknown",
  );
  const inspect = within(panel).getByTestId("compatibility-inspect-models");
  expect(inspect).toHaveTextContent("Inspect model capabilities");
  expect(inspect.getAttribute("href")).toBe("/models");
});
