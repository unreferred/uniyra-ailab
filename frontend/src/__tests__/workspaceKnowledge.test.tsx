import React, { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { KnowledgePopover, RetrievalOptions } from "@/components/workspace/KnowledgePopover";
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
}));

const mockedGet = api.get as jest.Mock;
const mockedPatch = api.patch as jest.Mock;

const collections = [
  { id: "k1", project_id: "p1", name: "Product docs", description: "Guides", status: "active", doc_count: 3, chunk_count: 12 },
  { id: "k2", project_id: "p1", name: "Research notes", description: "", status: "active", doc_count: 1, chunk_count: 4 },
];

beforeEach(() => {
  jest.clearAllMocks();
  mockedGet.mockImplementation((url: string) => {
    if (url === "/knowledge") return Promise.resolve({ data: collections });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

// Mirrors the Workspace page wiring: onChange/onRetrievalChange persist via
// PATCH /conversations/:id while updating local selection state.
function Host({ convId = "c1" }: { convId?: string }) {
  const qcHost = React.useMemo(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
    []
  );
  const [value, setValue] = useState<string[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [retrieval, setRetrieval] = useState<RetrievalOptions>({});
  const onChange = (ids: string[]) => {
    setValue(ids);
    setEnabled(ids.length > 0);
    void api.patch("/conversations/" + convId, { knowledge_enabled: ids.length > 0, knowledge_collection_ids: ids, retrieval_options: ids.length > 0 ? retrieval : {} });
  };
  const onRetrievalChange = (o: RetrievalOptions) => {
    setRetrieval(o);
    void api.patch(`/conversations/${convId}`, { retrieval_options: o });
  };
  return (
    <MemoryRouter>
      <QueryClientProvider client={qcHost}>
        <KnowledgePopover
          projectId="p1"
          value={value}
          knowledgeEnabled={enabled}
          onChange={onChange}
          onKnowledgeEnabledChange={(next) => { setEnabled(next); void api.patch("/conversations/" + convId, { knowledge_enabled: next, knowledge_collection_ids: next ? value : [], retrieval_options: next ? retrieval : {} }); }}
          retrievalOptions={retrieval}
          onRetrievalChange={onRetrievalChange}
        />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

function renderStandalone(value: string[] = [], onChange = jest.fn(), onRetrievalChange = jest.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <KnowledgePopover
          projectId="p1"
          value={value}
          onChange={onChange}
          retrievalOptions={{}}
          onRetrievalChange={onRetrievalChange}
        />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

test("renders 'Knowledge: N' and toggling a collection calls onChange", async () => {
  const user = userEvent.setup({ delay: null });
  const onChange = jest.fn();
  renderStandalone([], onChange, jest.fn());
  expect(screen.getByTestId("workspace-knowledge-button")).toHaveTextContent("Knowledge: 0");
  await user.click(screen.getByTestId("workspace-knowledge-button"));
  expect(await screen.findByText("Product docs")).toBeInTheDocument();
  await user.click(screen.getByTestId("knowledge-k1"));
  expect(onChange).toHaveBeenCalledWith(["k1"]);
});

test("toggling a collection through Workspace-style wiring issues PATCH", async () => {
  const user = userEvent.setup({ delay: null });
  mockedPatch.mockResolvedValueOnce({ data: {} });
  render(<Host />);
  await user.click(screen.getByTestId("workspace-knowledge-button"));
  await user.click(await screen.findByTestId("knowledge-k1"));
  await screen.findByTestId("workspace-knowledge-button");
  expect(mockedPatch).toHaveBeenCalledWith("/conversations/c1", {
    knowledge_enabled: true,
    knowledge_collection_ids: ["k1"],
    retrieval_options: {},
  });
});

test("retrieval persistence PATCHes retrieval_options (update wiring)", async () => {
  const user = userEvent.setup({ delay: null });
  mockedPatch.mockResolvedValue({ data: {} });
  render(<Host />);
  await user.click(screen.getByTestId("workspace-knowledge-button"));
  await user.click(await screen.findByTestId("knowledge-k1"));
  await screen.findByTestId("knowledge-retrieval-options");
  fireEvent.change(screen.getByTestId("knowledge-top-k"), { target: { value: "7" } });
  await user.click(await screen.findByTestId("knowledge-k1"));
  // Selection was cleared; reselect and assert the retrieval PATCH happened.
  await user.click(await screen.findByTestId("knowledge-k1"));
  expect(mockedPatch).toHaveBeenCalledWith(
    "/conversations/c1",
    expect.objectContaining({ retrieval_options: expect.objectContaining({ top_k: 7 }) })
  );
});

test("top_k/threshold inputs appear only when a collection is selected", async () => {
  const user = userEvent.setup({ delay: null });
  renderStandalone([], jest.fn(), jest.fn());
  await user.click(screen.getByTestId("workspace-knowledge-button"));
  await screen.findByText("Product docs");
  expect(screen.queryByTestId("knowledge-retrieval-options")).not.toBeInTheDocument();

  const qc2 = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc2}>
        <KnowledgePopover
          projectId="p1"
          value={["k1"]}
          onChange={() => {}}
          retrievalOptions={{}}
          onRetrievalChange={() => {}}
        />
      </QueryClientProvider>
    </MemoryRouter>
  );
  const buttons = screen.getAllByTestId("workspace-knowledge-button");
  await user.click(buttons[buttons.length - 1]);
  expect(await screen.findByTestId("knowledge-retrieval-options")).toBeInTheDocument();
  expect(screen.getByTestId("knowledge-top-k")).toBeInTheDocument();
  expect(screen.getByTestId("knowledge-threshold")).toBeInTheDocument();
});


test("Knowledge control exposes explicit OFF and clears retrieval payload", async () => {
  const user = userEvent.setup({ delay: null });
  const onChange = jest.fn();
  const onKnowledgeEnabledChange = jest.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <KnowledgePopover projectId="p1" value={["k1"]} knowledgeEnabled={true} onChange={onChange} onKnowledgeEnabledChange={onKnowledgeEnabledChange} retrievalOptions={{ top_k: 7 }} onRetrievalChange={jest.fn()} />
      </QueryClientProvider>
    </MemoryRouter>
  );
  await user.click(screen.getByTestId("workspace-knowledge-button"));
  expect(screen.getByTestId("knowledge-selected-ids")).toHaveTextContent("k1");
  expect(screen.getByTestId("knowledge-enabled-switch")).toHaveAttribute("data-state", "checked");
  await user.click(screen.getByTestId("knowledge-enabled-switch"));
  expect(onKnowledgeEnabledChange).toHaveBeenCalledWith(false);
  expect(onChange).toHaveBeenCalledWith([]);
});
