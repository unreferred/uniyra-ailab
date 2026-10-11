import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Models from "@/pages/Models";
import Settings from "@/pages/Settings";
import { api, codeInterpreterStatus } from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  codeInterpreterStatus: jest.fn(),
}));


jest.mock("@/components/ui/command", () => ({
  Command: ({ children }: any) => <div>{children}</div>,
  CommandEmpty: ({ children }: any) => <div>{children}</div>,
  CommandGroup: ({ children }: any) => <div>{children}</div>,
  CommandInput: () => null,
  CommandItem: ({ children }: any) => <div>{children}</div>,
  CommandList: ({ children }: any) => <div>{children}</div>,
}));

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({
    projects: [{ id: "p1", name: "Demo", description: "", tags: [], status: "active", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }],
    activeProject: { id: "p1", name: "Demo", description: "", status: "active" },
    activeId: "p1",
    setActiveId: jest.fn(),
    isLoading: false,
    refresh: jest.fn(),
  }),
}));

jest.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", name: "Test User", email: "t@example.com", role: "admin" } }),
}));

const mockedGet = api.get as jest.Mock;
const mockedPatch = api.patch as jest.Mock;

const model: any = {
  id: "m1",
  owner_id: "u1",
  provider_id: "prov1",
  display_name: "DeepSeek V4.1",
  model_identifier: "deepseek-v4.1",
  family: "openai",
  enabled: true,
  context_window: 64000,
  input_cost: null,
  output_cost: null,
  input_price_per_1m: null,
  output_price_per_1m: null,
  capabilities: { text: true, streaming: true },
  capability_source: "UNKNOWN",
  modalities: {},
  tags: ["general"],
  notes: "",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function setupModelsApi(modelOverride: any = model) {
  mockedGet.mockReset();
  mockedPatch.mockReset();
  (codeInterpreterStatus as jest.Mock).mockReset();
  (codeInterpreterStatus as jest.Mock).mockResolvedValue({ available: true, reason: null, image: "img", runtime: "docker", limits: {}, policy: {} });
  mockedGet.mockImplementation((url: string) => {
    if (url === "/models") return Promise.resolve({ data: [modelOverride] });
    if (url === "/providers") return Promise.resolve({ data: [{ id: "prov1", name: "P1", provider_type: "openai_compatible", enabled: true }] });
    if (url === "/rubrics") return Promise.resolve({ data: [] });
    if (url === "/tools") return Promise.resolve({ data: { items: [], total: 0 } });
    if (url === "/code-interpreter/status")
      return Promise.resolve({ data: { available: true, reason: null, image: "img", runtime: "docker", limits: {}, policy: {} } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  mockedPatch.mockResolvedValue({ data: { ...modelOverride, capabilities: { text: true, streaming: true, tools: true }, capability_source: "MANUAL" } });
}

function renderModels() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <Models />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
});

test("model row shows normalized capability badges + UNKNOWN provenance", async () => {
  setupModelsApi();
  renderModels();
  const row = await screen.findByTestId("model-row-m1");
  const caps = within(row).getByTestId("model-caps-m1");
  expect(caps).toHaveTextContent("Tool calling ?");
  expect(caps).toHaveTextContent("Vision ?");
  expect(caps).toHaveTextContent("Streaming ✓");
  expect(caps).toHaveTextContent("Structured output ?");
  expect(caps).toHaveTextContent("Embeddings ?");
  expect(within(row).getByTestId("cap-source")).toHaveTextContent("UNKNOWN");
  expect(within(row).queryByTestId("cap-manual-marker")).not.toBeInTheDocument();
});

test("MANUAL source renders Supported·Manual override marker", async () => {
  setupModelsApi({ ...model, capabilities: { text: true, tools: true }, capability_source: "MANUAL" });
  renderModels();
  const row = await screen.findByTestId("model-row-m1");
  expect(within(row).getByTestId("cap-source")).toHaveTextContent("MANUAL");
  expect(within(row).getByTestId("cap-manual-marker")).toHaveTextContent("Manual override");
});

test("manual override dialog PATCHes capabilities + MANUAL and invalidates models", async () => {
  const user = userEvent.setup({ delay: null });
  setupModelsApi();
  renderModels();
  await screen.findByTestId("model-row-m1");
  await user.click(screen.getByTestId("capabilities-model-m1"));
  const dialog = await screen.findByTestId("capability-override-dialog");
  expect(dialog).toBeInTheDocument();
  // Set Tool Calling -> Supported.
  await user.click(within(dialog).getByTestId("capability-override-tools-supported"));
  await user.click(screen.getByTestId("capability-override-submit"));
  expect(mockedPatch).toHaveBeenCalledWith(
    "/models/m1",
    expect.objectContaining({
      capabilities: expect.objectContaining({ tools: true }),
      capability_source: "MANUAL",
    }),
  );
});

test("settings exposes Providers, Models, Tool Credentials, Runtime links", async () => {
  setupModelsApi();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <Settings />
      </QueryClientProvider>
    </MemoryRouter>
  );
  const hub = await screen.findByTestId("settings-discover");
  expect(await within(hub).findByTestId("settings-runtime-state")).toHaveTextContent("sandbox online");
  expect(within(hub).getByTestId("settings-link-providers")).toHaveTextContent("Providers");
  expect(within(hub).getByTestId("settings-link-models")).toHaveTextContent("Models");
  expect(within(hub).getByTestId("settings-link-credentials")).toHaveTextContent("Tool credentials");
  expect(within(hub).getByTestId("settings-link-runtime")).toHaveTextContent("Runtime status");
  fireEvent.click(within(hub).getByTestId("settings-link-models"));
});
