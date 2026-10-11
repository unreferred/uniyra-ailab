import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { KnowledgePopover } from "@/components/workspace/KnowledgePopover";
import { api } from "@/lib/api";

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
}));

const mockedGet = api.get as jest.Mock;

const collections = [
  { id: "k1", project_id: "p1", name: "Product docs", description: "Guides", status: "active", doc_count: 3, chunk_count: 12 },
  { id: "k2", project_id: "p1", name: "Research notes", description: "", status: "active", doc_count: 1, chunk_count: 4 },
];

beforeEach(() => {
  mockedGet.mockReset();
  mockedGet.mockImplementation((url: string) => {
    if (url === "/knowledge") return Promise.resolve({ data: collections });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

function renderKnowledge(value: string[] = [], onChange: (ids: string[]) => void = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <KnowledgePopover projectId="p1" value={value} onChange={onChange} retrievalOptions={{}} onRetrievalChange={() => {}} />
    </QueryClientProvider>
  );
}

test("knowledge popover lists collections and reports selection", async () => {
  const user = userEvent.setup({ delay: null });
  const onChange = jest.fn();
  renderKnowledge([], onChange);

  expect(screen.getByTestId("workspace-knowledge-button")).toHaveTextContent("Knowledge: 0");
  await user.click(screen.getByTestId("workspace-knowledge-button"));

  expect(await screen.findByText("Product docs")).toBeInTheDocument();
  expect(screen.getByText("Research notes")).toBeInTheDocument();
  expect(mockedGet).toHaveBeenCalledWith("/knowledge", { params: { project_id: "p1" } });

  await user.click(screen.getByTestId("knowledge-k1"));
  expect(onChange).toHaveBeenCalledWith(["k1"]);
});

test("retrieval options appear when at least one collection is selected", async () => {
  const user = userEvent.setup({ delay: null });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <KnowledgePopover projectId="p1" value={["k1"]} onChange={() => {}} retrievalOptions={{}} onRetrievalChange={() => {}} />
    </QueryClientProvider>
  );

  expect(screen.getByTestId("workspace-knowledge-button")).toHaveTextContent("Knowledge: 1");
  await user.click(screen.getByTestId("workspace-knowledge-button"));
  expect(await screen.findByTestId("knowledge-retrieval-options")).toBeInTheDocument();
  expect(screen.getByTestId("knowledge-top-k")).toBeInTheDocument();
  expect(screen.getByTestId("knowledge-threshold")).toBeInTheDocument();
});
