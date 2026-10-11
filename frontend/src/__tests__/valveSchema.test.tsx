import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ValveForm } from "@/components/tools/ValveForm";
import { ToolValveDialog } from "@/components/tools/ToolValveDialog";
import ToolsPage from "@/pages/Tools";

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
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return {
    ...actual,
    api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn(), put: jest.fn() },
    getToolValves: jest.fn(),
    updateToolValves: jest.fn(),
    testToolValves: jest.fn(),
    listToolValves: jest.fn(),
  };
});

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

import { api, getToolValves, listToolValves, testToolValves, updateToolValves } from "@/lib/api";

const mockedGet = api.get as jest.Mock;
const mockedGetToolValves = getToolValves as jest.Mock;
const mockedUpdateToolValves = updateToolValves as jest.Mock;
const mockedTestToolValves = testToolValves as jest.Mock;
const mockedListToolValves = listToolValves as jest.Mock;

const tinyfishSchema = {
  api_key: { type: "SECRET", label: "API Key", required: true, secret: true, scope: "PROJECT" },
  max_results: { type: "INTEGER", label: "Max Results", default: 5, min: 1, max: 20 },
  timeout_seconds: { type: "INTEGER", label: "Timeout (seconds)", default: 15, min: 1, max: 60 },
} as const;

const webSearchValves = {
  tool_id: "tool-search-0001",
  name: "web_search",
  display_name: "Web Search",
  tool_type: "function",
  schema: {},
  values: {},
  provider_kind: "search",
  provider_id: "prov-search-tinyfish",
  providers: [
    {
      id: "prov-search-tinyfish",
      name: "TinyFish Search",
      provider_type: "tinyfish",
      enabled: true,
      health_status: null,
      valve_schema: {
        api_key: { type: "SECRET", label: "API Key", required: true, secret: true, scope: "PROJECT" },
        max_results: { type: "INTEGER", label: "Max Results", default: 5, min: 1, max: 20 },
        timeout_seconds: { type: "INTEGER", label: "Timeout (seconds)", default: 15, min: 1, max: 60 },
      },
      valve_values: { api_key: { stored: true }, max_results: 5, timeout_seconds: 15 },
      status: { enabled: true, configured: true, available: true, healthy: false, reason: null },
    },
    {
      id: "prov-search-tavily",
      name: "Tavily Search",
      provider_type: "tavily",
      enabled: true,
      health_status: null,
      valve_schema: {
        api_key: { type: "SECRET", label: "API Key", required: true, secret: true, scope: "PROJECT" },
        search_depth: { type: "SELECT", label: "Search Depth", default: "basic", options: [{ value: "basic", label: "Basic" }, { value: "advanced", label: "Advanced" }] },
      },
      valve_values: { api_key: { stored: false }, search_depth: "basic" },
      status: { enabled: true, configured: false, available: false, healthy: false, reason: "API Key required" },
    },
  ],
  provider_schema: {
    api_key: { type: "SECRET", label: "API Key", required: true, secret: true, scope: "PROJECT" },
    max_results: { type: "INTEGER", label: "Max Results", default: 5, min: 1, max: 20 },
    timeout_seconds: { type: "INTEGER", label: "Timeout (seconds)", default: 15, min: 1, max: 60 },
  },
  provider_values: { api_key: { stored: true }, max_results: 5, timeout_seconds: 15 },
  status: { enabled: true, configured: false, available: false, healthy: false, reason: "API Key required" },
};

const toolRecord = {
  id: "tool-search-0001",
  name: "web_search",
  display_name: "Web Search",
  tool_type: "function",
  enabled: true,
  permissions: ["search.execute"],
  scope: "global",
  builtin: true,
  available: true,
  availability_reason: null,
};

const webResearchValves = {
  tool_id: "tool-research-0001",
  name: "web_research",
  display_name: "Web Research",
  tool_type: "function",
  schema: {
    research_default_max_results: {
      type: "INTEGER",
      label: "Default search results",
      description: "Used when the model does not request max_results.",
      default: 5,
      min: 1,
      max: 10,
    },
    research_default_max_fetches: {
      type: "INTEGER",
      label: "Default fetch URLs",
      description: "Used when the model does not request max_fetches.",
      default: 3,
      min: 1,
      max: 10,
    },
    research_max_results_ceiling: {
      type: "INTEGER",
      label: "Maximum search results",
      description: "Maximum search breadth one research operation may request; cannot exceed the hard server ceiling of 10.",
      default: 10,
      min: 1,
      max: 10,
    },
    research_max_fetches_ceiling: {
      type: "INTEGER",
      label: "Maximum fetch URLs",
      description: "Maximum fetch breadth one research operation may request; cannot exceed the hard server ceiling of 10.",
      default: 10,
      min: 1,
      max: 10,
    },
  },
  values: {
    research_default_max_results: 5,
    research_default_max_fetches: 3,
    research_max_results_ceiling: 10,
    research_max_fetches_ceiling: 10,
  },
  provider_kind: null,
  provider_id: null,
  providers: [],
  status: { enabled: true, configured: true, available: true, healthy: true, reason: null },
};

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderDialog(tool: any = { id: "tool-search-0001", display_name: "Web Search" }) {
  const qc = newClient();
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ToolValveDialog tool={tool} projectId="p1" onClose={jest.fn()} />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockedListToolValves.mockResolvedValue({ items: [], total: 0 });
  mockedGetToolValves.mockResolvedValue(webSearchValves);
  mockedUpdateToolValves.mockResolvedValue(webSearchValves);
  mockedTestToolValves.mockResolvedValue({ ok: true, status: "CONNECTED", message: "TinyFish reachable" });
});

test("ValveForm renders SECRET as Stored with no plaintext", () => {
  render(
    <ValveForm
      prefix="vf"
      schema={tinyfishSchema as any}
      values={{ api_key: { stored: true }, max_results: 5, timeout_seconds: 15 }}
      onChange={jest.fn()}
    />
  );
  expect(screen.getByTestId("vf-valve-stored-api_key")).toHaveTextContent("Stored");
  const input = screen.getByTestId("vf-valve-input-api_key") as HTMLInputElement;
  expect(input.value).toBe("");
  expect(input.getAttribute("type")).toBe("password");
  expect(document.body.textContent || "").not.toMatch(/sk-[A-Za-z0-9]{4,}|supersecret/i);
});

test("ToolValveDialog shows Not Configured status and no credential ID field", async () => {
  renderDialog();
  const status = await screen.findByTestId("tool-valve-status");
  expect(status).toHaveTextContent(/Not configured/);
  expect(status).toHaveTextContent(/API Key required/);
  expect(await screen.findByTestId("tool-valve-provider-valve-input-api_key")).toBeInTheDocument();
  expect(document.body.textContent || "").not.toMatch(/credential id/i);
});

test("provider switch re-renders the provider Valve schema", async () => {
  const user = userEvent.setup({ delay: null });
  renderDialog();
  await screen.findByTestId("tool-valve-provider-valve-input-max_results");
  await user.click(screen.getByTestId("tool-valve-provider"));
  await user.click(await screen.findByRole("option", { name: "Tavily Search" }));
  expect(await screen.findByTestId("tool-valve-provider-valve-select-search_depth")).toBeInTheDocument();
  expect(screen.queryByTestId("tool-valve-provider-valve-input-max_results")).toBeNull();
});

test("provider Select is always controlled and shows the chosen provider", async () => {
  // React routes the "uncontrolled -> controlled" diagnostic through
  // console.error; Radix useControllableState emits it through console.warn.
  // Spy on both so this actually catches the regression.
  const errorSpy = jest.spyOn(console, "error").mockImplementation(() => {});
  const warnSpy = jest.spyOn(console, "warn").mockImplementation(() => {});
  try {
    const user = userEvent.setup({ delay: null });
    renderDialog();
    await screen.findByTestId("tool-valve-provider");
    await user.click(screen.getByTestId("tool-valve-provider"));
    await user.click(await screen.findByRole("option", { name: "Tavily Search" }));
    expect(await screen.findByTestId("tool-valve-provider-valve-select-search_depth")).toBeInTheDocument();
    expect(screen.getByTestId("tool-valve-provider")).toHaveTextContent("Tavily Search");
    const controlWarnings = [...errorSpy.mock.calls, ...warnSpy.mock.calls]
      .map((args) => args.map(String).join(" "))
      .filter((msg) => /uncontrolled|controlled/i.test(msg));
    expect(controlWarnings).toEqual([]);
  } finally {
    errorSpy.mockRestore();
    warnSpy.mockRestore();
  }
});

test("saving a new secret sends it once and never stores plaintext client-side", async () => {
  const user = userEvent.setup({ delay: null });
  renderDialog();
  const input = await screen.findByTestId("tool-valve-provider-valve-input-api_key");
  await user.type(input, "sk-new-secret");
  await user.click(screen.getByTestId("tool-valve-save"));
  await waitFor(() => {
    expect(mockedUpdateToolValves).toHaveBeenCalledWith(
      "tool-search-0001",
      expect.objectContaining({
        provider_id: "prov-search-tinyfish",
        provider_values: expect.objectContaining({ api_key: "sk-new-secret" }),
      })
    );
  });
  const saved = await mockedUpdateToolValves.mock.results[0].value;
  expect(JSON.stringify(saved)).not.toContain("sk-new-secret");
  expect(document.body.textContent || "").not.toContain("sk-new-secret");
});

test("Test Connection calls the valve test endpoint and shows the result", async () => {
  const user = userEvent.setup({ delay: null });
  renderDialog();
  await screen.findByTestId("tool-valve-status");
  await user.click(screen.getByTestId("tool-valve-test"));
  await waitFor(() => {
    expect(mockedTestToolValves).toHaveBeenCalledWith("tool-search-0001", "p1");
  });
  const result = await screen.findByTestId("tool-valve-test-result");
  expect(result).toHaveTextContent("CONNECTED");
  expect(result).toHaveTextContent("TinyFish reachable");
});

test("web_research ToolValveDialog exposes default research valves under Tool settings", async () => {
  mockedGetToolValves.mockResolvedValue(webResearchValves);
  renderDialog({ id: "tool-research-0001", name: "web_research", display_name: "Web Research" });

  const defaults = await screen.findByTestId("tool-valve-valve-input-research_default_max_results");
  expect((defaults as HTMLInputElement).value).toBe("5");
  expect(screen.getByTestId("tool-valve-valve-input-research_default_max_fetches")).toBeInTheDocument();
  expect(screen.getByText("Research settings")).toBeInTheDocument();

  // Ceiling keys live behind the collapsed Advanced subsection until toggled.
  expect(screen.queryByTestId("tool-valve-valve-input-research_max_results_ceiling")).toBeNull();
  expect(screen.queryByTestId("tool-valve-valve-input-research_max_fetches_ceiling")).toBeNull();
  expect(screen.getByTestId("tool-valve-advanced-toggle")).toHaveAttribute("aria-expanded", "false");
});

test("Advanced toggle reveals both research ceiling valves", async () => {
  const user = userEvent.setup({ delay: null });
  mockedGetToolValves.mockResolvedValue(webResearchValves);
  renderDialog({ id: "tool-research-0001", name: "web_research", display_name: "Web Research" });

  await screen.findByTestId("tool-valve-valve-input-research_default_max_results");
  await user.click(screen.getByTestId("tool-valve-advanced-toggle"));

  const resultsCeiling = await screen.findByTestId("tool-valve-valve-input-research_max_results_ceiling");
  expect((resultsCeiling as HTMLInputElement).value).toBe("10");
  const fetchesCeiling = screen.getByTestId("tool-valve-valve-input-research_max_fetches_ceiling");
  expect((fetchesCeiling as HTMLInputElement).value).toBe("10");
  expect(screen.getByTestId("tool-valve-advanced-toggle")).toHaveAttribute("aria-expanded", "true");
});

test("Advanced research valves save through the same tool values path", async () => {
  const user = userEvent.setup({ delay: null });
  mockedGetToolValves.mockResolvedValue(webResearchValves);
  renderDialog({ id: "tool-research-0001", name: "web_research", display_name: "Web Research" });

  await screen.findByTestId("tool-valve-valve-input-research_default_max_results");
  await user.click(screen.getByTestId("tool-valve-advanced-toggle"));
  const resultsCeiling = await screen.findByTestId("tool-valve-valve-input-research_max_results_ceiling");
  fireEvent.change(resultsCeiling, { target: { value: "8" } });
  await user.click(screen.getByTestId("tool-valve-save"));

  await waitFor(() => {
    expect(mockedUpdateToolValves).toHaveBeenCalledWith(
      "tool-research-0001",
      expect.objectContaining({
        values: expect.objectContaining({ research_max_results_ceiling: 8 }),
      })
    );
  });
});

test("Tools page exposes Configure and no Credentials tab", async () => {
  const user = userEvent.setup({ delay: null });
  mockedGet.mockImplementation((url: string) => {
    if (url === "/tools") return Promise.resolve({ data: { items: [toolRecord], total: 1 } });
    if (url === "/code-interpreter/status") return Promise.resolve({ data: { available: true, reason: null } });
    if (url === "/tool-valves") return Promise.resolve({ data: { items: [webSearchValves] } });
    if (url === "/tool-credentials") return Promise.resolve({ data: { items: [] } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  mockedListToolValves.mockResolvedValue({ items: [webSearchValves], total: 1 });
  const qc = newClient();
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ToolsPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
  const configure = await screen.findByTestId("tool-configure-tool-search-0001");
  expect(screen.queryByTestId("tools-tab-credentials")).toBeNull();
  await user.click(configure);
  expect(await screen.findByTestId("tool-valve-dialog")).toBeInTheDocument();
});
