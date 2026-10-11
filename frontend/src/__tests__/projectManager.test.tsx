import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import ProjectManager from "@/pages/ProjectManager";
import { api } from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  projectSettings: jest.fn(),
  fetchAttachmentBlob: jest.fn(),
  listProjectFiles: jest.fn(),
  uploadProjectFiles: jest.fn(),
  updateProjectFile: jest.fn(),
  deleteProjectFile: jest.fn(),
  getProjectFile: jest.fn(),
  createKnowledgeCollection: jest.fn(),
  deleteKnowledgeCollection: jest.fn(),
  attachKnowledgeDocuments: jest.fn(),
  indexKnowledgeCollection: jest.fn(),
  createCustomTool: jest.fn(),
  deleteCustomTool: jest.fn(),
  testTool: jest.fn(),
  importOpenAPI: jest.fn(),
  generateFromOpenAPI: jest.fn(),
  createToolCredential: jest.fn(),
  updateToolCredential: jest.fn(),
  deleteToolCredential: jest.fn(),
}));

jest.mock("@/components/workspace/AttachmentViewer", () => ({
  AttachmentViewer: () => null,
}));

const mockedGet = api.get as jest.Mock;

const project = {
  id: "p1",
  owner_id: "u1",
  name: "Demo",
  description: "",
  tags: [],
  status: "active",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

beforeEach(() => {
  mockedGet.mockReset();
  mockedGet.mockImplementation((url: string) => {
    if (url === "/projects/p1") return Promise.resolve({ data: project });
    if (url === "/projects/p1/overview") {
      return Promise.resolve({
        data: {
          project,
          counts: { conversations: 1, experiments: 0 },
          known_cost: null,
          recent_activity: [],
          recent_conversations: [{ id: "c1", title: "Hello", updated_at: project.updated_at }],
          recent_experiments: [],
        },
      });
    }
    if (url === "/knowledge") return Promise.resolve({ data: [] });
    if (url === "/tools") return Promise.resolve({ data: { items: [], total: 0 } });
    if (url === "/models") return Promise.resolve({ data: [] });
    if (url === "/tool-credentials") return Promise.resolve({ data: { items: [] } });
    return Promise.resolve({ data: [] });
  });
});

function renderPM() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={["/projects/p1"]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/projects/:id" element={<ProjectManager />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

test("project manager renders all tabs and real overview counts", async () => {
  renderPM();
  expect(await screen.findByTestId("project-manager")).toBeInTheDocument();
  for (const t of ["overview", "conversations", "files", "knowledge", "prompts", "tools", "experiments", "datasets", "findings", "artifacts", "settings"]) {
    expect(screen.getByTestId(`project-tab-${t}`)).toBeInTheDocument();
  }
  expect(await screen.findByTestId("overview-count-conversations")).toHaveTextContent("1");
  expect(screen.getByTestId("overview-known-cost")).toHaveTextContent("Unknown");
});

test("knowledge tab shows a working empty state and create dialog", async () => {
  const user = userEvent.setup({ delay: null });
  renderPM();
  await screen.findByTestId("project-manager");
  await user.click(screen.getByTestId("project-tab-knowledge"));
  expect(await screen.findByTestId("project-knowledge-empty")).toBeInTheDocument();
  await user.click(screen.getByTestId("knowledge-create"));
  expect(await screen.findByTestId("knowledge-create-dialog")).toBeInTheDocument();
  expect(mockedGet).toHaveBeenCalledWith("/knowledge", expect.objectContaining({ params: { project_id: "p1" } }));
});

test("files tab shows empty state with upload control hitting the real endpoint shape", async () => {
  const user = userEvent.setup({ delay: null });
  renderPM();
  await screen.findByTestId("project-manager");
  await user.click(screen.getByTestId("project-tab-files"));
  expect(await screen.findByTestId("project-files-empty")).toBeInTheDocument();
  expect(screen.getByTestId("project-files-upload")).toBeInTheDocument();
  expect(screen.getByTestId("project-files-dropzone")).toBeInTheDocument();
  await waitFor(() =>
    expect(mockedGet).toHaveBeenCalledWith("/projects/p1/files", expect.objectContaining({ params: {} }))
  );
});
