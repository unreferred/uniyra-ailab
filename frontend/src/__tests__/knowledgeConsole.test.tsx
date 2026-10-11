import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { KnowledgeTab } from "@/components/project/KnowledgeTab";
import { api, searchKnowledge, reindexKnowledgeDocument, detachKnowledgeDocument } from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  createKnowledgeCollection: jest.fn(),
  deleteKnowledgeCollection: jest.fn(),
  attachKnowledgeDocuments: jest.fn(),
  indexKnowledgeCollection: jest.fn(),
  reindexKnowledgeDocument: jest.fn(),
  detachKnowledgeDocument: jest.fn(),
  searchKnowledge: jest.fn(),
}));

const mockedGet = api.get as jest.Mock;
const mockedSearch = searchKnowledge as jest.Mock;
const mockedReindex = reindexKnowledgeDocument as jest.Mock;
const mockedDetach = detachKnowledgeDocument as jest.Mock;

const collections = [
  {
    id: "k1",
    owner_id: "u1",
    project_id: "p1",
    name: "Product docs",
    description: "Guides",
    status: "active",
    doc_count: 1,
    chunk_count: 2,
    embedding_config: { provider: "local", model: "local-hashing-v1", dimension: 256 },
    chunking_config: { strategy: "fixed", chunk_size: 800, chunk_overlap: 100 },
    created_at: "2026-01-01T00:00:00Z",
  },
];

beforeEach(() => {
  mockedGet.mockReset();
  mockedSearch.mockReset();
  mockedReindex.mockReset();
  mockedDetach.mockReset();
  mockedGet.mockImplementation((url: string) => {
    if (url === "/knowledge") return Promise.resolve({ data: collections });
    return Promise.resolve({ data: [] });
  });
});

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <KnowledgeTab projectId="p1" />
    </QueryClientProvider>
  );
}

test("retrieval console posts to /knowledge/search and renders scores and sources", async () => {
  const user = userEvent.setup({ delay: null });
  mockedSearch.mockResolvedValue({
    query: "pricing",
    results: [
      {
        collection_id: "k1",
        collection_name: "Product docs",
        document_id: "d1",
        chunk_id: "c1",
        chunk_index: 3,
        content: "Pricing lives in the billing guide.",
        score: 0.91234,
        source: { display_name: "billing.md" },
      },
    ],
    result_count: 1,
  });
  renderTab();

  expect(await screen.findByTestId("knowledge-retrieval-console")).toBeInTheDocument();
  await user.type(screen.getByTestId("knowledge-search-query"), "pricing");
  await user.clear(screen.getByTestId("knowledge-search-topk"));
  await user.type(screen.getByTestId("knowledge-search-topk"), "5");
  await user.click(screen.getByTestId("knowledge-search-run"));

  expect(mockedSearch).toHaveBeenCalledWith(
    expect.objectContaining({ query: "pricing", top_k: 5, project_id: "p1" })
  );
  expect(await screen.findByTestId("retrieval-result-0")).toBeInTheDocument();
  expect(screen.getByTestId("retrieval-score-0")).toHaveTextContent("0.9123");
  expect(screen.getByTestId("retrieval-result-0")).toHaveTextContent("Product docs");
  expect(screen.getByTestId("retrieval-result-0")).toHaveTextContent("billing.md");
});

test("empty retrieval renders an honest message", async () => {
  const user = userEvent.setup({ delay: null });
  mockedSearch.mockResolvedValue({ query: "nothing", results: [], result_count: 0 });
  renderTab();

  await screen.findByTestId("knowledge-retrieval-console");
  await user.type(screen.getByTestId("knowledge-search-query"), "nothing");
  await user.click(screen.getByTestId("knowledge-search-run"));

  expect(await screen.findByTestId("retrieval-empty")).toHaveTextContent("No chunks retrieved");
});


test("document lifecycle shows canonical statuses and uses reindex/detach endpoints", async () => {
  const user = userEvent.setup({ delay: null });
  mockedReindex.mockResolvedValue({ processed: 1, ready: 1, failed: 0 });
  mockedDetach.mockResolvedValue({ id: "d-ready", ingestion_status: "detached" });
  mockedGet.mockImplementation((url: string) => {
    if (url === "/knowledge") return Promise.resolve({ data: collections });
    if (url === "/knowledge/k1/documents") return Promise.resolve({ data: [
      { id: "d-pending", display_name: "pending.txt", ingestion_status: "PENDING", parser: { name: "attachment", version: "p1" }, index: { status: "not_indexed" }, chunk_count: 0 },
      { id: "d-processing", display_name: "processing.txt", ingestion_status: "PROCESSING", parser: { name: "attachment", version: "p1" }, index: { status: "building" }, chunk_count: 2 },
      { id: "d-ready", display_name: "ready.txt", ingestion_status: "READY", parser: { name: "text", version: "p2" }, index: { status: "ready" }, chunk_count: 4 },
      { id: "d-failed", display_name: "failed.png", ingestion_status: "FAILED", error_code: "NO_TEXT", error_message: "Attachment has no extractable text", parser: { name: "attachment", version: "p1" }, index: { status: "failed" }, chunk_count: 0 },
    ] });
    if (url === "/knowledge/k1") return Promise.resolve({ data: collections[0] });
    return Promise.resolve({ data: [] });
  });
  renderTab();
  await user.click(await screen.findByTestId("knowledge-expand-k1"));
  expect(mockedGet.mock.calls.map((call) => call[0])).toContain("/knowledge/k1/documents");
  expect(await screen.findByTestId("knowledge-doc-status-d-pending")).toHaveTextContent("PENDING");
  expect(screen.getByTestId("knowledge-doc-status-d-processing")).toHaveTextContent("PROCESSING");
  expect(screen.getByTestId("knowledge-doc-status-d-ready")).toHaveTextContent("READY");
  expect(screen.getByTestId("knowledge-doc-status-d-failed")).toHaveTextContent("FAILED");
  expect(screen.getByTestId("knowledge-doc-error-d-failed")).toHaveTextContent("NO_TEXT: Attachment has no extractable text");
  await user.click(screen.getByTestId("knowledge-doc-reindex-d-failed"));
  expect(mockedReindex).toHaveBeenCalledWith("k1", "d-failed");
  await user.click(screen.getByTestId("knowledge-doc-detach-d-ready"));
  expect(mockedDetach).toHaveBeenCalledWith("k1", "d-ready");
});
