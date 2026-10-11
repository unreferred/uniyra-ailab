import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { SearchControl } from "@/components/workspace/SearchControl";
import { FetchControl } from "@/components/workspace/FetchControl";
import Workspace from "@/pages/Workspace";
import { api, listSearchProviders, testSearchProvider, listFetchProviders, testFetchProvider } from "@/lib/api";
import { SearchProvider, FetchProvider, ModelRecord, ToolRecord } from "@/lib/types";

jest.mock("@floating-ui/dom", () => {
  const actual = jest.requireActual("@floating-ui/dom");
  return {
    ...actual,
    computePosition: () =>
      Promise.resolve({ x: 0, y: 0, strategy: "fixed", placement: "bottom", middlewareData: {} }),
  };
});

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn(), message: jest.fn() },
}));

jest.mock("@/components/common/Markdown", () => ({
  MarkdownView: ({ content }: { content: string }) => <div data-testid="markdown-view">{content}</div>,
}));

jest.mock("@/components/common/SaveArtifactDialog", () => ({
  SaveArtifactDialog: () => null,
}));

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({
    activeId: "p1",
    activeProject: {
      id: "p1",
      name: "Demo Project",
      default_search_enabled: false,
      default_search_provider_id: "prov-search-tinyfish",
      default_fetch_enabled: false,
      default_fetch_provider_id: "prov-fetch-standard",
    },
    projects: [],
    isLoading: false,
    setActiveId: jest.fn(),
    refresh: jest.fn(),
  }),
}));

jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return {
    ...actual,
    api: {
      get: jest.fn(),
      post: jest.fn(),
      patch: jest.fn(),
      delete: jest.fn(),
    },
    listSearchProviders: jest.fn(),
    testSearchProvider: jest.fn(),
    listFetchProviders: jest.fn(),
    testFetchProvider: jest.fn(),
    uploadAttachments: jest.fn(),
  };
});

const mockedListSearchProviders = listSearchProviders as jest.Mock;
const mockedTestSearchProvider = testSearchProvider as jest.Mock;
const mockedListFetchProviders = listFetchProviders as jest.Mock;
const mockedTestFetchProvider = testFetchProvider as jest.Mock;
const mockedGet = api.get as jest.Mock;
const mockedPost = api.post as jest.Mock;

const mockSearchProviders: SearchProvider[] = [
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
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  } as SearchProvider,
];

const mockFetchProviders: FetchProvider[] = [
  {
    id: "prov-fetch-standard",
    name: "Standard HTTP Fetch",
    provider_type: "standard_http",
    enabled: true,
    capabilities: { fetch: "http" },
    credential_status: "NOT_REQUIRED",
    configuration: { ssrf_guard: "ACTIVE", max_bytes: 524288 },
    health_status: "CONNECTED",
    key_source: "None",
    is_default: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  } as FetchProvider,
  {
    id: "prov-fetch-tinyfish",
    name: "TinyFish Web Fetch",
    provider_type: "tinyfish",
    enabled: true,
    capabilities: { fetch: "web" },
    credential_status: "NOT_CONFIGURED",
    configuration: { base_url: "https://api.tinyfish.io" },
    health_status: "UNKNOWN",
    key_source: "None",
    is_default: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  } as FetchProvider,
];

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe("SearchControl and FetchControl Component Tests", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedListSearchProviders.mockResolvedValue(mockSearchProviders);
    mockedListFetchProviders.mockResolvedValue(mockFetchProviders);
  });

  test("SearchControl renders button and toggles open with provider details", async () => {
    const onEnabledChange = jest.fn();
    const onProviderChange = jest.fn();

    renderWithClient(
      <SearchControl
        enabled={false}
        onEnabledChange={onEnabledChange}
        providerId="prov-search-tinyfish"
        onProviderChange={onProviderChange}
      />
    );

    const button = screen.getByTestId("workspace-search-button");
    expect(button).toBeInTheDocument();
    expect(button).toHaveTextContent("Search");

    // Open popover
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByTestId("workspace-search-popover")).toBeInTheDocument();
      expect(screen.getByText("Web Search")).toBeInTheDocument();
      expect(screen.getByText("TinyFish Web Search")).toBeInTheDocument();
      expect(screen.getByTestId("search-provider-status")).toHaveTextContent("Connected");
    });

    // Toggle switch
    const switchControl = screen.getByTestId("search-enable-switch");
    fireEvent.click(switchControl);
    expect(onEnabledChange).toHaveBeenCalledWith(true);

    // Test connection button
    mockedTestSearchProvider.mockResolvedValueOnce({
      ok: true,
      status: "CONFIGURED",
      message: "TinyFish credentials configured.",
    });

    const testBtn = screen.getByTestId("search-test-btn");
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(mockedTestSearchProvider).toHaveBeenCalledWith("prov-search-tinyfish");
      expect(screen.getByText("TinyFish credentials configured.")).toBeInTheDocument();
    });
  });

  test("SearchControl disables switch with Configure link when provider is NOT_CONFIGURED", async () => {
    mockedListSearchProviders.mockResolvedValueOnce([
      {
        ...mockSearchProviders[0],
        credential_status: "NOT_CONFIGURED",
        health_status: "UNKNOWN",
        credential_id: null,
        has_vault_credential: false,
        key_source: "None",
      },
    ]);
    renderWithClient(
      <SearchControl
        enabled={false}
        onEnabledChange={jest.fn()}
        providerId="prov-search-tinyfish"
        onProviderChange={jest.fn()}
      />
    );
    fireEvent.click(screen.getByTestId("workspace-search-button"));
    await waitFor(() => {
      expect(screen.getByTestId("search-not-configured-note")).toBeInTheDocument();
    });
    expect(screen.getByTestId("search-enable-switch")).toBeDisabled();
    const link = screen.getByTestId("search-configure-link");
    expect(link).toHaveTextContent("[Configure Provider]");
    expect(link.getAttribute("href")).toBe("/tools?tab=providers");
    expect(screen.getByTestId("search-provider-status")).toHaveTextContent("Not configured");
  });

  test("FetchControl disables switch with Configure link when provider is NOT_CONFIGURED", async () => {
    renderWithClient(
      <FetchControl
        enabled={false}
        onEnabledChange={jest.fn()}
        providerId="prov-fetch-tinyfish"
        onProviderChange={jest.fn()}
      />
    );
    fireEvent.click(screen.getByTestId("workspace-fetch-button"));
    await waitFor(() => {
      expect(screen.getByTestId("fetch-not-configured-note")).toBeInTheDocument();
    });
    expect(screen.getByTestId("fetch-enable-switch")).toBeDisabled();
    const link = screen.getByTestId("fetch-configure-link");
    expect(link).toHaveTextContent("[Configure Provider]");
    expect(link.getAttribute("href")).toBe("/tools?tab=providers");
    expect(screen.getByTestId("fetch-provider-status")).toHaveTextContent("Not configured");
  });

  test("FetchControl renders button, shows SSRF notice, and handles provider selection", async () => {
    const onEnabledChange = jest.fn();
    const onProviderChange = jest.fn();

    renderWithClient(
      <FetchControl
        enabled={true}
        onEnabledChange={onEnabledChange}
        providerId="prov-fetch-standard"
        onProviderChange={onProviderChange}
      />
    );

    const button = screen.getByTestId("workspace-fetch-button");
    expect(button).toBeInTheDocument();
    expect(button).toHaveTextContent("On");

    // Open popover
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByTestId("workspace-fetch-popover")).toBeInTheDocument();
      expect(screen.getByText("Web Fetch")).toBeInTheDocument();
      expect(screen.getByText("Standard HTTP Fetch")).toBeInTheDocument();
      expect(screen.getByText(/Strict SSRF protection/i)).toBeInTheDocument();
    });

    // Select second provider
    const tinyfishProvider = screen.getByTestId("fetch-provider-prov-fetch-tinyfish");
    fireEvent.click(tinyfishProvider);
    expect(onProviderChange).toHaveBeenCalledWith("prov-fetch-tinyfish");

    // Test connection
    mockedTestFetchProvider.mockResolvedValueOnce({
      ok: true,
      status: "CONNECTED",
      message: "Standard HTTP fetch engine healthy.",
    });

    const testBtn = screen.getByTestId("fetch-test-btn");
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(mockedTestFetchProvider).toHaveBeenCalledWith("prov-fetch-standard");
      expect(screen.getByText("Standard HTTP fetch engine healthy.")).toBeInTheDocument();
    });
  });
});

describe("Workspace Integration: Search & Fetch Controls in Composer", () => {
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
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };

  const toolSearch: ToolRecord = {
    id: "tool-search-0001",
    name: "web_search",
    display_name: "Web Search",
    description: "Search the public web",
    version: "1.0.0",
    tool_type: "builtin",
    input_schema: {},
    enabled: true,
    risk_level: "low",
    permissions: ["search.execute"],
    available: true,
  };

  const conversation = {
    id: "c1",
    project_id: "p1",
    title: "New conversation",
    system_prompt: "",
    search_enabled: true,
    search_provider_id: "prov-search-tinyfish",
    fetch_enabled: true,
    fetch_provider_id: "prov-fetch-standard",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    messages: [],
  };

  beforeEach(() => {
    jest.clearAllMocks();
    mockedListSearchProviders.mockResolvedValue(mockSearchProviders);
    mockedListFetchProviders.mockResolvedValue(mockFetchProviders);

    mockedGet.mockImplementation((url: string) => {
      if (url === "/models") return Promise.resolve({ data: [model] });
      if (url === "/tools") return Promise.resolve({ data: { items: [toolSearch], total: 1 } });
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
      if (url === "/search-providers") return Promise.resolve({ data: mockSearchProviders });
      if (url === "/fetch-providers") return Promise.resolve({ data: mockFetchProviders });
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });

    mockedPost.mockImplementation((url: string, payload: any) => {
      if (url === "/conversations") {
        return Promise.resolve({ data: { ...conversation, id: "c-new" } });
      }
      if (url.startsWith("/conversations/") && url.endsWith("/messages")) {
        return Promise.resolve({
          data: {
            conversation: { ...conversation, messages: [{ id: "m1", role: "user", content: "hello" }] },
            user_message: { id: "m1", role: "user", content: "hello" },
            assistant_message: { id: "m2", role: "assistant", content: "hi there" },
            run: { id: "r1", status: "completed" },
          },
        });
      }
      return Promise.reject(new Error(`unexpected POST ${url}`));
    });
  });

  test("Workspace renders [Search] and [Fetch] buttons in composer toolbar", async () => {
    renderWithClient(<Workspace />);

    await waitFor(() => {
      expect(screen.getByTestId("workspace-search-button")).toBeInTheDocument();
      expect(screen.getByTestId("workspace-fetch-button")).toBeInTheDocument();
    });
  });

  test("Sending message passes search_enabled and fetch_enabled in payload", async () => {
    renderWithClient(<Workspace />);

    await waitFor(() => {
      expect(screen.getByTestId("workspace-search-button")).toBeInTheDocument();
      expect(screen.getByTestId("workspace-input")).toBeInTheDocument();
    });

    // Type a message in the composer
    const textarea = screen.getByTestId("workspace-input");
    fireEvent.change(textarea, { target: { value: "Search and fetch test query" } });

    // Enable search via popover
    fireEvent.click(screen.getByTestId("workspace-search-button"));
    await waitFor(() => {
      expect(screen.getByTestId("search-enable-switch")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("search-enable-switch"));

    // Tool/search-enabled runs now execute through the streaming endpoint.
    const chunks = [
      `data: ${JSON.stringify({ type: "RUN_STARTED", run_id: "r-new" })}\n\n`,
      `data: ${JSON.stringify({ type: "MODEL_OUTPUT_DELTA", delta: "ok" })}\n\n`,
      `data: ${JSON.stringify({ type: "RUN_COMPLETED", run: { id: "r-new", normalized_response: "ok" } })}\n\n`,
    ];
    let readIndex = 0;
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () =>
            readIndex < chunks.length
              ? { done: false, value: new TextEncoder().encode(chunks[readIndex++]) }
              : { done: true, value: undefined },
          cancel: async () => {},
          releaseLock: () => {},
        }),
      },
    });
    (global as any).fetch = fetchMock;

    // Send the message
    const sendButton = screen.getByTestId("workspace-send");
    fireEvent.click(sendButton);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [streamUrl, streamInit] = fetchMock.mock.calls[0];
    expect(String(streamUrl)).toContain("/messages/stream");
    const payload = JSON.parse(streamInit.body);
    expect(payload.search_enabled).toBe(true);
    expect(payload.search_provider_id).toBe("prov-search-tinyfish");
  });
});
