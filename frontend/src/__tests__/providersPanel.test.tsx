import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ProvidersPanel } from "@/components/tools/ProvidersPanel";
import ToolsPage from "@/pages/Tools";
import {
  listFetchProviders,
  listSearchProviders,
  listToolValves,
  testFetchProvider,
  testSearchProvider,
  updateFetchProvider,
  updateSearchProvider,
} from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return {
    ...actual,
    api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
    listSearchProviders: jest.fn(),
    testSearchProvider: jest.fn(),
    updateSearchProvider: jest.fn(),
    listFetchProviders: jest.fn(),
    testFetchProvider: jest.fn(),
    updateFetchProvider: jest.fn(),
    listToolValves: jest.fn().mockResolvedValue({ items: [], total: 0 }),
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

import { api } from "@/lib/api";
const mockedGet = api.get as jest.Mock;

const mockedListSearch = listSearchProviders as jest.Mock;
const mockedTestSearch = testSearchProvider as jest.Mock;
const mockedUpdateSearch = updateSearchProvider as jest.Mock;
const mockedListFetch = listFetchProviders as jest.Mock;
const mockedTestFetch = testFetchProvider as jest.Mock;
const mockedUpdateFetch = updateFetchProvider as jest.Mock;

const searchProviders = [
  {
    id: "prov-search-tinyfish",
    name: "TinyFish Web Search",
    provider_type: "tinyfish",
    enabled: true,
    capabilities: { search: "web" },
    credential_status: "CONFIGURED",
    configuration: { base_url: "https://api.tinyfish.io" },
    health_status: "CONNECTED",
    credential_id: "cred-1",
    has_vault_credential: true,
    key_source: "Vault",
    is_default: true,
    last_checked_at: "2026-02-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
  },
];

const fetchProviders = [
  {
    id: "prov-fetch-standard",
    name: "Standard HTTP Fetch",
    provider_type: "standard_http",
    enabled: true,
    capabilities: { fetch: "http" },
    credential_status: "NOT_REQUIRED",
    configuration: {},
    health_status: "CONNECTED",
    key_source: "None",
    is_default: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "prov-fetch-tinyfish",
    name: "TinyFish Web Fetch",
    provider_type: "tinyfish",
    enabled: true,
    capabilities: { fetch: "web" },
    credential_status: "NOT_CONFIGURED",
    configuration: {},
    health_status: "UNKNOWN",
    key_source: "None",
    is_default: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ProvidersPanel />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockedListSearch.mockResolvedValue({ items: searchProviders });
  mockedListFetch.mockResolvedValue({ items: fetchProviders });
  (listToolValves as jest.Mock).mockResolvedValue({ items: [], total: 0 });
});

test("renders SEARCH + FETCH sections with status badges, key source and default marker", async () => {
  renderPanel();
  expect(await screen.findByTestId("providers-section-search")).toBeInTheDocument();
  expect(screen.getByTestId("providers-section-fetch")).toBeInTheDocument();
  expect(screen.getByTestId("providers-explainer")).toHaveTextContent("A PROVIDER is the backend");
  const card = await screen.findByTestId("provider-card-search-prov-search-tinyfish");
  expect(within(card).getByTestId("provider-capability-search-prov-search-tinyfish")).toHaveTextContent("Search");
  expect(within(card).getByTestId("provider-status-search-prov-search-tinyfish")).toHaveTextContent("CONNECTED");
  expect(within(card).getByTestId("provider-key-source-search-prov-search-tinyfish")).toHaveTextContent("Vault");
  expect(within(card).getByTestId("provider-default-search-prov-search-tinyfish")).toHaveTextContent("default");
  const unconfigured = await screen.findByTestId("provider-card-fetch-prov-fetch-tinyfish");
  expect(within(unconfigured).getByTestId("provider-status-fetch-prov-fetch-tinyfish")).toHaveTextContent("NOT_CONFIGURED");
});

test("configure dialog opens, never renders the existing key, saves and auto-tests", async () => {
  const user = userEvent.setup({ delay: null });
  mockedUpdateSearch.mockResolvedValueOnce({ ...searchProviders[0] });
  mockedTestSearch.mockResolvedValueOnce({ ok: true, status: "CONNECTED", message: "TinyFish reachable." });
  renderPanel();
  await user.click(await screen.findByTestId("provider-configure-search-prov-search-tinyfish"));
  const dialog = await screen.findByTestId("provider-configure-dialog-search");
  expect(dialog).toBeInTheDocument();
  const keyInput = within(dialog).getByTestId("provider-configure-key-search") as HTMLInputElement;
  // Existing key is never displayed: empty value + replace placeholder.
  expect(keyInput.value).toBe("");
  expect(keyInput.placeholder).toMatch(/Stored — enter new value to replace/);
  expect(keyInput.getAttribute("type")).toBe("password");
  expect(document.body.textContent || "").not.toMatch(/sk-|supersecret/i);
  fireEvent.change(keyInput, { target: { value: "sk-new-secret" } });
  await user.click(within(dialog).getByTestId("provider-configure-save-search"));
  await waitFor(() => {
    expect(mockedUpdateSearch).toHaveBeenCalledWith(
      "prov-search-tinyfish",
      expect.objectContaining({ api_key: "sk-new-secret" })
    );
  });
  // After save, Test Connection runs automatically.
  await waitFor(() => {
    expect(mockedTestSearch).toHaveBeenCalledWith("prov-search-tinyfish");
  });
  expect(await screen.findByTestId("provider-configure-test-result-search")).toHaveTextContent("TinyFish reachable.");
});

test("test connection button calls POST test and shows ok/status/message", async () => {
  const user = userEvent.setup({ delay: null });
  mockedTestFetch.mockResolvedValueOnce({ ok: true, status: "CONNECTED", message: "Fetch engine healthy." });
  renderPanel();
  await user.click(await screen.findByTestId("provider-test-fetch-prov-fetch-standard"));
  await waitFor(() => {
    expect(mockedTestFetch).toHaveBeenCalledWith("prov-fetch-standard");
  });
  expect(await screen.findByTestId("provider-test-result-fetch-prov-fetch-standard")).toHaveTextContent(
    "Fetch engine healthy."
  );
});

test("Set Default visible only when >1 provider and PATCHes is_default", async () => {
  const user = userEvent.setup({ delay: null });
  mockedUpdateFetch.mockResolvedValueOnce({ ...fetchProviders[1], is_default: true });
  renderPanel();
  // Single-provider search section offers no Set Default button.
  await screen.findByTestId("provider-card-search-prov-search-tinyfish");
  expect(screen.queryByTestId("provider-set-default-search-prov-search-tinyfish")).not.toBeInTheDocument();
  // Multi-provider fetch section does for the non-default provider.
  const setDefault = await screen.findByTestId("provider-set-default-fetch-prov-fetch-tinyfish");
  await user.click(setDefault);
  await waitFor(() => {
    expect(mockedUpdateFetch).toHaveBeenCalledWith("prov-fetch-tinyfish", { is_default: true });
  });
});

test("TinyFish group is listed above the sections and shares one key with both providers", async () => {
  const user = userEvent.setup({ delay: null });
  mockedUpdateSearch.mockResolvedValueOnce({ ...searchProviders[0] });
  mockedUpdateFetch.mockResolvedValueOnce({ ...fetchProviders[1] });
  renderPanel();

  const group = await screen.findByTestId("providers-tinyfish-group");
  // The grouped card lists both TinyFish providers with their statuses.
  expect(await within(group).findByTestId("tinyfish-status-search")).toHaveTextContent("CONNECTED");
  expect(await within(group).findByTestId("tinyfish-status-fetch")).toHaveTextContent("NOT_CONFIGURED");
  expect(screen.getByTestId("providers-tinyfish-hint")).toHaveTextContent(
    "One TinyFish configuration powers Web Search, Web Fetch and Web Research. The secret is entered once."
  );

  // The shared key is a password field and is never pre-populated.
  const keyInput = screen.getByTestId("tinyfish-shared-key") as HTMLInputElement;
  expect(keyInput.getAttribute("type")).toBe("password");
  expect(keyInput.value).toBe("");

  fireEvent.change(keyInput, { target: { value: "tf-shared-secret" } });
  await user.click(screen.getByTestId("tinyfish-shared-save"));

  // One secret, written to BOTH the search and the fetch provider.
  await waitFor(() => {
    expect(mockedUpdateSearch).toHaveBeenCalledWith("prov-search-tinyfish", {
      api_key: "tf-shared-secret",
    });
    expect(mockedUpdateFetch).toHaveBeenCalledWith("prov-fetch-tinyfish", {
      api_key: "tf-shared-secret",
    });
  });

  // The existing search/fetch sections are untouched.
  expect(screen.getByTestId("providers-section-search")).toBeInTheDocument();
  expect(screen.getByTestId("providers-section-fetch")).toBeInTheDocument();
});

test("Tools page exposes a Providers tab honoring ?tab=providers with TOOL legend", async () => {
  const user = userEvent.setup({ delay: null });
  mockedGet.mockImplementation((url: string) => {
    if (url === "/tools") return Promise.resolve({ data: { items: [], total: 0 } });
    if (url === "/code-interpreter/status") return Promise.resolve({ data: { available: true, reason: null } });
    if (url === "/tool-credentials") return Promise.resolve({ data: { items: [] } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={["/tools?tab=providers"]}>
      <QueryClientProvider client={qc}>
        <ToolsPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
  expect(await screen.findByTestId("tools-tab-providers")).toBeInTheDocument();
  // Query param selects the Providers tab initially.
  expect(await screen.findByTestId("providers-panel")).toBeInTheDocument();
  await user.click(screen.getByTestId("tools-tab-registry"));
  expect(await screen.findByTestId("tools-legend")).toHaveTextContent("TOOL = capability exposed to the model");
});
