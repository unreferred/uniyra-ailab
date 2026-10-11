import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ProvidersPanel } from "@/components/tools/ProvidersPanel";
import { ReasoningSegmentList } from "@/components/workspace/RunActivityTimeline";
import {
  testFetchProvider,
  testSearchProvider,
  updateFetchProvider,
  updateSearchProvider,
} from "@/lib/api";
import { useFetchProviders, useSearchProviders } from "@/lib/hooks";

jest.mock("@floating-ui/dom", () => {
  const actual = jest.requireActual("@floating-ui/dom");
  return {
    ...actual,
    // jsdom gives every node a zero-size rect; positioning is not under test.
    computePosition: () =>
      Promise.resolve({ x: 0, y: 0, strategy: "fixed", placement: "bottom", middlewareData: {} }),
  };
});

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn(), message: jest.fn() },
}));

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({
    activeId: "p1",
    activeProject: { id: "p1", name: "Demo" },
    projects: [],
    isLoading: false,
    setActiveId: jest.fn(),
    refresh: jest.fn(),
  }),
}));

jest.mock("@/lib/hooks", () => ({
  useSearchProviders: jest.fn(),
  useFetchProviders: jest.fn(),
}));

jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return {
    ...actual,
    api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
    apiError: (e: any) => (e && e.message) || "error",
    testSearchProvider: jest.fn(),
    updateSearchProvider: jest.fn(),
    testFetchProvider: jest.fn(),
    updateFetchProvider: jest.fn(),
  };
});

const mockedUseSearch = useSearchProviders as jest.Mock;
const mockedUseFetch = useFetchProviders as jest.Mock;
const mockedUpdateSearch = updateSearchProvider as jest.Mock;
const mockedUpdateFetch = updateFetchProvider as jest.Mock;
const mockedTestSearch = testSearchProvider as jest.Mock;
const mockedTestFetch = testFetchProvider as jest.Mock;

const tinyfishSearch = {
  id: "prov-search-tinyfish",
  name: "TinyFish Web Search",
  provider_type: "tinyfish",
  enabled: true,
  capabilities: { search: "web" },
  credential_status: "CONFIGURED",
  configuration: { base_url: "https://api.tinyfish.io" },
  health_status: "CONNECTED",
  is_default: true,
  valve_values: { location: "us", language: "en", max_results: 5 },
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const tinyfishFetch = {
  id: "prov-fetch-tinyfish",
  name: "TinyFish Web Fetch",
  provider_type: "tinyfish",
  enabled: true,
  capabilities: { fetch: "web", supports_batch_fetch: true },
  credential_status: "NOT_CONFIGURED",
  configuration: {},
  health_status: "UNKNOWN",
  is_default: false,
  valve_values: {
    format: "markdown",
    timeout_seconds: 30,
    max_urls: 10,
    fetch_urls_per_minute_limit: 150,
    fetch_urls_per_day_limit: 1000,
  },
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function renderProvidersPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ProvidersPanel />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe("TinyFish web research provider configuration", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedUpdateSearch.mockResolvedValue({});
    mockedUpdateFetch.mockResolvedValue({});
    mockedTestSearch.mockResolvedValue({ ok: true, status: "CONNECTED" });
    mockedTestFetch.mockResolvedValue({ ok: true, status: "CONNECTED" });
    mockedUseSearch.mockReturnValue({
      data: { items: [tinyfishSearch] },
      isLoading: false,
      isError: false,
      error: null,
    });
    mockedUseFetch.mockReturnValue({
      data: { items: [tinyfishFetch] },
      isLoading: false,
      isError: false,
      error: null,
    });
  });

  test("grouped TinyFish card lists both providers, shared valves and batch capability", async () => {
    renderProvidersPanel();
    const group = await screen.findByTestId("providers-tinyfish-group");
    expect(within(group).getByTestId("tinyfish-status-search")).toHaveTextContent("CONNECTED");
    expect(within(group).getByTestId("tinyfish-status-fetch")).toHaveTextContent("NOT_CONFIGURED");
    expect(within(group).getByText("Batch fetch supported")).toBeInTheDocument();
    // Read-only shared valves from both halves of the configuration.
    expect(within(group).getByText("location")).toBeInTheDocument();
    expect(within(group).getByText("max_results")).toBeInTheDocument();
    expect(within(group).getByText("timeout_seconds")).toBeInTheDocument();
  });

  test("fetch tile shows the provider batch cap and quotas, separate from search breadth", async () => {
    renderProvidersPanel();
    const fetchTile = await screen.findByTestId("tinyfish-provider-fetch");

    // Provider batching cap and quota valves are surfaced read-only.
    expect(within(fetchTile).getByText("batch size")).toBeInTheDocument();
    expect(within(fetchTile).getByText("10")).toBeInTheDocument();
    expect(within(fetchTile).getByText("URLs/min")).toBeInTheDocument();
    expect(within(fetchTile).getByText("150")).toBeInTheDocument();
    expect(within(fetchTile).getByText("URLs/day")).toBeInTheDocument();
    expect(within(fetchTile).getByText("1000")).toBeInTheDocument();

    // Search breadth stays on the search tile; the fetch tile never shows it.
    expect(within(fetchTile).queryByText("max_results")).toBeNull();
  });

  test("the shared secret saves once to both the search and fetch endpoints", async () => {
    const user = userEvent.setup({ delay: null });
    renderProvidersPanel();

    const keyInput = await screen.findByTestId("tinyfish-shared-key");
    expect((keyInput as HTMLInputElement).type).toBe("password");
    fireEvent.change(keyInput, { target: { value: "tf-one-secret" } });
    await user.click(screen.getByTestId("tinyfish-shared-save"));

    await waitFor(() => {
      expect(mockedUpdateSearch).toHaveBeenCalledWith("prov-search-tinyfish", {
        api_key: "tf-one-secret",
      });
      expect(mockedUpdateFetch).toHaveBeenCalledWith("prov-fetch-tinyfish", {
        api_key: "tf-one-secret",
      });
    });

    // The legacy search/fetch sections survive grouping.
    expect(screen.getByTestId("providers-section-search")).toBeInTheDocument();
    expect(screen.getByTestId("providers-section-fetch")).toBeInTheDocument();
  });

  test("a missing TinyFish provider shows a note and suppresses the shared save", async () => {
    mockedUseFetch.mockReturnValue({
      data: { items: [] },
      isLoading: false,
      isError: false,
      error: null,
    });
    renderProvidersPanel();
    expect(await screen.findByTestId("tinyfish-missing-note")).toBeInTheDocument();
    expect(screen.queryByTestId("tinyfish-shared-save")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// (b) Segmented reasoning rows
// ---------------------------------------------------------------------------

describe("ReasoningSegmentList", () => {
  const segments = [
    {
      model_call_id: "mc-1",
      round_index: 0,
      phase: "thinking",
      content: "First pass reasoning content.",
      duration_ms: 1200,
    },
    {
      model_call_id: "mc-2",
      round_index: 1,
      phase: "analyzing",
      content: "Second pass reasoning content.",
      duration_ms: 800,
    },
  ];

  test("renders one collapsed thought row per segment without raw event names", () => {
    const { container } = render(<ReasoningSegmentList segments={segments} />);

    // One collapsed thought row per model call.
    const rows = screen.getAllByTestId("run-thought");
    expect(rows).toHaveLength(2);
    expect(screen.queryByTestId("run-thought-content")).toBeNull();

    // Human labels only; raw protocol event names never leak into the UI.
    expect(container.textContent || "").toMatch(/Thought for/);
    expect(container.textContent || "").not.toMatch(
      /MODEL_REASONING_DELTA|MODEL_OUTPUT_DELTA|FINALIZATION_DELTA|MODEL_COMPLETED|TOOL_CALL_/
    );

    // Segment bodies are collapsed by default and expand on click.
    expect(screen.queryByText("First pass reasoning content.")).toBeNull();
    fireEvent.click(screen.getAllByTestId("run-thought-toggle")[0]);
    expect(screen.getByTestId("run-thought-content")).toHaveTextContent(
      "First pass reasoning content."
    );
  });
});
