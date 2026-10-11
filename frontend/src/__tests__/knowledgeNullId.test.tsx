import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import { KnowledgeTab } from "@/components/project/KnowledgeTab";
import { api, reindexKnowledgeDocument, detachKnowledgeDocument } from "@/lib/api";

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
const mockedReindex = reindexKnowledgeDocument as jest.Mock;
const mockedDetach = detachKnowledgeDocument as jest.Mock;
const mockedToast = toast as unknown as { success: jest.Mock; error: jest.Mock; message: jest.Mock };

const REAL_COLLECTION_ID = "k1";
const REAL_DOCUMENT_ID = "doc-1";

const makeCollections = (docCount: number, chunkCount: number) => [
  {
    id: REAL_COLLECTION_ID,
    owner_id: "u1",
    project_id: "p1",
    name: "Product docs",
    description: "Guides",
    status: "active",
    doc_count: docCount,
    chunk_count: chunkCount,
    embedding_config: { provider: "local", model: "local-hashing-v1", dimension: 256 },
    chunking_config: { strategy: "fixed", chunk_size: 800, chunk_overlap: 100 },
    created_at: "2026-01-01T00:00:00Z",
  },
];

const pendingDocument = {
  id: REAL_DOCUMENT_ID,
  display_name: "guide.md",
  ingestion_status: "PENDING",
  status: "PENDING",
  parser: { name: "text", version: "p2" },
  index: { status: "not_indexed" },
  chunk_count: 0,
};

const readyDocument = {
  id: REAL_DOCUMENT_ID,
  display_name: "guide.md",
  ingestion_status: "READY",
  status: "READY",
  parser: { name: "text", version: "p2" },
  index: { status: "ready" },
  chunk_count: 1,
};

let collectionCounts = { doc: 1, chunk: 1 };
let documentReady = false;
let lastDocsSnapshot: any = null;

function installApiMock() {
  collectionCounts = { doc: 1, chunk: 1 };
  documentReady = false;
  lastDocsSnapshot = null;
  mockedGet.mockImplementation((url: string) => {
    if (url === "/knowledge") {
      return Promise.resolve({ data: makeCollections(collectionCounts.doc, collectionCounts.chunk) });
    }
    if (url === `/knowledge/${REAL_COLLECTION_ID}/documents`) {
      const doc = documentReady ? readyDocument : pendingDocument;
      lastDocsSnapshot = doc;
      return Promise.resolve({ data: [doc] });
    }
    if (url === `/knowledge/${REAL_COLLECTION_ID}/chunks`) {
      return Promise.resolve({ data: [] });
    }
    if (url === `/knowledge/${REAL_COLLECTION_ID}`) {
      return Promise.resolve({ data: makeCollections(collectionCounts.doc, collectionCounts.chunk)[0] });
    }
    return Promise.resolve({ data: [] });
  });
}

beforeEach(() => {
  mockedGet.mockReset();
  mockedReindex.mockReset();
  mockedDetach.mockReset();
  mockedToast.success.mockReset();
  mockedToast.error.mockReset();
  mockedToast.message.mockReset();
  installApiMock();
});

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <KnowledgeTab projectId="p1" />
    </QueryClientProvider>
  );
}

const allGetUrls = () => mockedGet.mock.calls.map((call) => String(call[0]));
const chunkGetCalls = () =>
  mockedGet.mock.calls.filter((call) => String(call[0]).includes("/chunks"));

async function expandCollection(user: ReturnType<typeof userEvent.setup>) {
  renderTab();
  await user.click(await screen.findByTestId(`knowledge-expand-${REAL_COLLECTION_ID}`));
  await screen.findByTestId(`knowledge-doc-${REAL_DOCUMENT_ID}`);
}

test("C: a lifecycle mutation with the chunks panel hidden never requests /knowledge/null", async () => {
  const user = userEvent.setup({ delay: null });
  mockedReindex.mockResolvedValue({ processed: 1, ready: 1, failed: 0, errors: [] });
  await expandCollection(user);

  await user.click(screen.getByTestId(`knowledge-doc-reindex-${REAL_DOCUMENT_ID}`));
  expect(mockedReindex).toHaveBeenCalledWith(REAL_COLLECTION_ID, REAL_DOCUMENT_ID);

  await waitFor(() => expect(mockedToast.message).toHaveBeenCalledWith("Reindex finished: 1 ready, 0 failed"));

  const urls = allGetUrls();
  expect(urls.some((url) => url.includes("/knowledge/null"))).toBe(false);
  expect(urls.some((url) => url.includes("/null/chunks"))).toBe(false);
  expect(chunkGetCalls()).toHaveLength(0);
});

test("C2: opening the chunks panel requests the real collection id, never null", async () => {
  const user = userEvent.setup({ delay: null });
  await expandCollection(user);

  await user.click(screen.getByTestId(`knowledge-chunks-toggle-${REAL_COLLECTION_ID}`));
  await waitFor(() =>
    expect(mockedGet).toHaveBeenCalledWith(`/knowledge/${REAL_COLLECTION_ID}/chunks`, { params: {} })
  );

  await user.click(screen.getByTestId(`knowledge-doc-chunks-${REAL_DOCUMENT_ID}`));
  await waitFor(() =>
    expect(mockedGet).toHaveBeenCalledWith(`/knowledge/${REAL_COLLECTION_ID}/chunks`, {
      params: { document_id: REAL_DOCUMENT_ID },
    })
  );

  const urls = allGetUrls();
  expect(urls.some((url) => url.includes("/knowledge/null"))).toBe(false);
  expect(urls.some((url) => url.includes("/null/chunks"))).toBe(false);
  for (const call of chunkGetCalls()) {
    expect(String(call[0])).toBe(`/knowledge/${REAL_COLLECTION_ID}/chunks`);
  }
});

test("F: the reindex summary is not rendered as a document; the canonical READY row is kept", async () => {
  const user = userEvent.setup({ delay: null });
  mockedReindex.mockImplementation(async () => {
    documentReady = true;
    return { processed: 1, ready: 1, failed: 0, errors: [] };
  });
  await expandCollection(user);

  await user.click(screen.getByTestId(`knowledge-doc-reindex-${REAL_DOCUMENT_ID}`));

  const status = await screen.findByTestId(`knowledge-doc-status-${REAL_DOCUMENT_ID}`);
  await waitFor(() => expect(status).toHaveTextContent("READY"));
  expect(screen.getByTestId(`knowledge-doc-${REAL_DOCUMENT_ID}`)).toBeInTheDocument();
  expect(screen.queryByTestId("knowledge-doc-undefined")).not.toBeInTheDocument();
  expect(lastDocsSnapshot?.id).toBe(REAL_DOCUMENT_ID);
  expect(lastDocsSnapshot?.status).toBe("READY");
  expect(screen.queryByText("undefined")).not.toBeInTheDocument();
});

test("G: a lifecycle mutation refetches collections so the header counts update", async () => {
  const user = userEvent.setup({ delay: null });
  mockedDetach.mockImplementation(async () => {
    collectionCounts = { doc: 0, chunk: 0 };
    return { id: REAL_DOCUMENT_ID, ingestion_status: "DETACHED" };
  });
  await expandCollection(user);

  expect(screen.getByText("1 docs")).toBeInTheDocument();
  expect(screen.getByText("1 chunks")).toBeInTheDocument();

  await user.click(screen.getByTestId(`knowledge-doc-detach-${REAL_DOCUMENT_ID}`));
  expect(mockedDetach).toHaveBeenCalledWith(REAL_COLLECTION_ID, REAL_DOCUMENT_ID);

  expect(await screen.findByText("0 docs")).toBeInTheDocument();
  expect(await screen.findByText("0 chunks")).toBeInTheDocument();
  expect(mockedGet.mock.calls.filter((call) => String(call[0]) === "/knowledge").length).toBeGreaterThanOrEqual(2);
});
