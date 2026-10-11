import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { FilesTab } from "@/components/project/FilesTab";
import { uploadProjectFiles, deleteProjectFile, getProjectFile } from "@/lib/api";
import { toast } from "sonner";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  fetchAttachmentBlob: jest.fn(),
  listProjectFiles: jest.fn(),
  uploadProjectFiles: jest.fn(),
  updateProjectFile: jest.fn(),
  deleteProjectFile: jest.fn(),
  getProjectFile: jest.fn(),
}));

import { api } from "@/lib/api";

const mockedGet = api.get as jest.Mock;
const mockedUpload = uploadProjectFiles as jest.Mock;
const mockedDelete = deleteProjectFile as jest.Mock;
const mockedDetail = getProjectFile as jest.Mock;

const fileA = {
  id: "f1",
  owner_id: "u1",
  project_id: "p1",
  display_name: "spec.md",
  kind: "text",
  tags: ["docs"],
  size_bytes: 1024,
  mime_type: "text/markdown",
  attachment_id: "a1",
  processing_status: "ready",
  sha256: "abc",
  created_at: "2026-01-01T00:00:00Z",
};

const fileB = {
  id: "f2",
  owner_id: "u1",
  project_id: "p1",
  display_name: "notes.txt",
  kind: "text",
  tags: ["draft"],
  size_bytes: 512,
  mime_type: "text/plain",
  attachment_id: "a2",
  processing_status: "ready",
  sha256: "def",
  created_at: "2026-01-02T00:00:00Z",
};

const allFiles = [fileA, fileB];

function setupGet() {
  mockedGet.mockImplementation((url: string, config?: any) => {
    if (url === "/projects/p1/files") {
      // Emulate server-side search filtering (FilesTab search is a query param).
      const q = String(config?.params?.search || "").toLowerCase();
      const items = q
        ? allFiles.filter((f) => f.display_name.toLowerCase().includes(q))
        : allFiles;
      return Promise.resolve({ data: items });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

function renderFilesTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <FilesTab projectId="p1" />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  setupGet();
});

test("list renders files with names, kinds and tags", async () => {
  renderFilesTab();
  const rowA = await screen.findByTestId("project-file-f1");
  expect(rowA).toHaveTextContent("spec.md");
  expect(rowA).toHaveTextContent("text");
  expect(rowA).toHaveTextContent("docs");
  expect(await screen.findByTestId("project-file-f2")).toHaveTextContent("notes.txt");
  expect(mockedGet).toHaveBeenCalledWith(
    "/projects/p1/files",
    expect.objectContaining({ params: expect.anything() })
  );
});

test("search filters via debounced server-side query param", async () => {
  renderFilesTab();
  await screen.findByTestId("project-file-f2");
  mockedGet.mockClear();
  fireEvent.change(screen.getByTestId("project-files-search"), {
    target: { value: "spec" },
  });
  await waitFor(
    () => {
      expect(screen.getByTestId("project-file-f1")).toBeInTheDocument();
      expect(screen.queryByTestId("project-file-f2")).not.toBeInTheDocument();
    },
    { timeout: 5000 }
  );
  expect(mockedGet).toHaveBeenCalledWith(
    "/projects/p1/files",
    expect.objectContaining({ params: expect.objectContaining({ search: "spec" }) })
  );
});

test("upload calls uploadProjectFiles with the selected files", async () => {
  mockedUpload.mockResolvedValueOnce([{ ...fileA, id: "f3", display_name: "hello.txt" }]);
  renderFilesTab();
  await screen.findByTestId("project-file-f1");
  const input = screen.getByTestId("project-files-input") as HTMLInputElement;
  const picked = new File(["hello"], "hello.txt", { type: "text/plain" });
  fireEvent.change(input, { target: { files: [picked] } });
  await waitFor(() => expect(mockedUpload).toHaveBeenCalledTimes(1), { timeout: 5000 });
  expect(mockedUpload).toHaveBeenCalledWith("p1", expect.any(Array));
  const sent = mockedUpload.mock.calls[0][1] as File[];
  expect(sent).toHaveLength(1);
  expect(sent[0].name).toBe("hello.txt");
  await waitFor(() =>
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining("Uploaded"))
  );
});

test("delete calls deleteProjectFile with project and file id", async () => {
  const user = userEvent.setup({ delay: null });
  mockedDelete.mockResolvedValueOnce({});
  renderFilesTab();
  await user.click(await screen.findByTestId("delete-file-f1"));
  await waitFor(() => expect(mockedDelete).toHaveBeenCalledWith("p1", "f1"), {
    timeout: 5000,
  });
});

test("preview without attachment backing shows references instead of content", async () => {
  const user = userEvent.setup({ delay: null });
  // openPreview falls back to f.attachment_id when detail.attachment_id is
  // falsy, so the listed file itself must have no attachment backing either.
  mockedGet.mockImplementation((url: string) => {
    if (url === "/projects/p1/files")
      return Promise.resolve({ data: [{ ...fileA, attachment_id: null }] });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  mockedDetail.mockResolvedValueOnce({
    id: "f1",
    attachment_id: null,
    references: { knowledge_documents: [{ id: "d1" }], run_count: 3 },
  });
  renderFilesTab();
  await user.click(await screen.findByTestId("preview-file-f1"));
  await waitFor(
    () =>
      expect(toast.message).toHaveBeenCalledWith(
        expect.stringContaining("References: 1 knowledge doc(s), 3 run(s)")
      ),
    { timeout: 5000 }
  );
  expect(mockedDetail).toHaveBeenCalledWith("p1", "f1");
});
