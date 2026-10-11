import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import DatasetDetail from "@/pages/DatasetDetail";
import { api } from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
}));

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({ activeId: "p1", activeProject: null, projects: [], isLoading: false, setActiveId: jest.fn(), refresh: jest.fn() }),
}));

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  apiError: (e: any) => (e && e.message) || "error",
}));

const mockedGet = api.get as jest.Mock;

const now = new Date("2026-01-01T00:00:00.000Z").toISOString();

const dataset = {
  id: "d1",
  project_id: "p1",
  name: "Frozen dataset",
  description: "A frozen snapshot",
  tags: [],
  created_at: now,
  updated_at: now,
  case_count: 2,
};

const frozenVersion = {
  id: "v1",
  dataset_id: "d1",
  project_id: "p1",
  version: 1,
  status: "frozen" as const,
  case_count: 2,
  content_hash: "deadbeefdeadbeefdeadbeefdeadbeef",
  created_at: now,
  frozen_at: now,
};

const versionDoc = {
  ...frozenVersion,
  cases: [
    { id: "case-1", dataset_id: "d1", dataset_version_id: "v1", project_id: "p1", input_text: "Question one", expected_output: "Answer one", metadata: {}, tags: [], ordering: 0, created_at: now },
    { id: "case-2", dataset_id: "d1", dataset_version_id: "v1", project_id: "p1", input_text: "Question two", expected_output: "Answer two", metadata: {}, tags: [], ordering: 1, created_at: now },
  ],
  case_count: 2,
  limit: 50,
  offset: 0,
};

beforeEach(() => {
  mockedGet.mockImplementation((url: string) => {
    if (url === "/datasets/d1") return Promise.resolve({ data: dataset });
    if (url === "/datasets/d1/versions") return Promise.resolve({ data: [frozenVersion] });
    if (url.startsWith("/datasets/d1/versions/")) return Promise.resolve({ data: versionDoc });
    if (url.startsWith("/experiments")) return Promise.resolve({ data: [] });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

function renderDataset() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/datasets/d1"]}>
        <Routes>
          <Route path="/datasets/:id" element={<DatasetDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

test("frozen dataset version is read-only with no case mutation controls", async () => {
  renderDataset();

  const readonly = await screen.findByTestId("frozen-readonly");
  expect(readonly).toHaveTextContent(/read-only/i);

  // Cases are still shown...
  expect(screen.getAllByTestId("case-row")).toHaveLength(2);

  // ...but draft-only mutation controls are absent.
  expect(screen.queryByTestId("case-add")).not.toBeInTheDocument();
  expect(screen.queryByTestId("edit-case-case-1")).not.toBeInTheDocument();
  expect(screen.queryByTestId("delete-case-case-1")).not.toBeInTheDocument();
  expect(screen.queryByTestId("edit-case-case-2")).not.toBeInTheDocument();
  expect(screen.queryByTestId("delete-case-case-2")).not.toBeInTheDocument();
  expect(screen.queryByTestId("dataset-freeze")).not.toBeInTheDocument();
});
