import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Workspace from "@/pages/Workspace";
import { api } from "@/lib/api";

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({
    activeId: "p1",
    activeProject: { id: "p1", name: "Project One" },
    projects: [{ id: "p1", name: "Project One" }],
    isLoading: false,
    setActiveId: jest.fn(),
    refresh: jest.fn(),
  }),
}));

// Do not mock @/lib/hooks; mock api endpoints directly as done in workspaceComposer.test.tsx


jest.mock("@/components/common/Markdown", () => ({
  MarkdownView: ({ content }: { content: string }) => <div data-testid="markdown-view">{content}</div>,
}));

jest.mock("@/components/common/SaveArtifactDialog", () => ({
  SaveArtifactDialog: () => null,
}));
jest.mock("@/components/workspace/AttachmentViewer", () => ({
  AttachmentViewer: () => null,
}));
jest.mock("@/components/workspace/TraceDrawer", () => ({
  TraceDrawer: () => null,
}));
jest.mock("@/components/workspace/ContextDrawer", () => ({
  ContextDrawer: () => null,
}));
jest.mock("@/components/workspace/PlanningStatePanel", () => ({
  PlanningStatePanel: () => null,
  PlanningDecisionsView: () => null,
}));
jest.mock("@/components/workspace/PromptCaptureDialog", () => ({
  PromptCaptureDialog: () => null,
}));
jest.mock("@/components/workspace/PrdView", () => ({
  PrdView: () => null,
}));
jest.mock("@/components/workspace/PlanMapView", () => ({
  PlanMapView: () => <div data-testid="mocked-plan-map-view">Plan Map</div>,
}));

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  apiError: (e: any) => e?.message || "Error",
  uploadAttachments: jest.fn(),
}));

jest.mock("sonner", () => ({
  toast: {
    success: jest.fn(),
    error: jest.fn(),
    message: jest.fn(),
  },
}));

describe("Workspace Conversations Delete & Bulk Delete UX", () => {
  const mockedGet = api.get as jest.Mock;
  const mockedPost = api.post as jest.Mock;
  const mockedDelete = api.delete as jest.Mock;

  const convs = [
    { id: "c1", project_id: "p1", title: "Conversation Alpha", created_at: "2026-09-22T00:00:00Z", updated_at: "2026-09-22T00:00:00Z" },
    { id: "c2", project_id: "p1", title: "Conversation Beta", created_at: "2026-09-22T00:00:00Z", updated_at: "2026-09-22T00:00:00Z" },
    { id: "c3", project_id: "p1", title: "Conversation Gamma", created_at: "2026-09-22T00:00:00Z", updated_at: "2026-09-22T00:00:00Z" },
  ];

  beforeEach(() => {
    jest.clearAllMocks();
    mockedGet.mockImplementation((url: string) => {
      if (url === "/models") return Promise.resolve({ data: [{ id: "m1", name: "Model 1", enabled: true }] });
      if (url === "/tools") return Promise.resolve({ data: { items: [], total: 0 } });
      if (url === "/code-interpreter/status") return Promise.resolve({ data: { available: true } });
      if (url === "/search-providers") return Promise.resolve({ data: [] });
      if (url === "/fetch-providers") return Promise.resolve({ data: [] });
      if (url === "/attachments") return Promise.resolve({ data: { items: [], total: 0 } });
      if (url.includes("/conversations?project_id=p1")) {
        return Promise.resolve({ data: convs });
      }
      if (url === "/conversations/c1") {
        return Promise.resolve({ data: { ...convs[0], messages: [] } });
      }
      if (url.includes("/stream-support")) {
        return Promise.resolve({ data: { supports_stream: false } });
      }
      return Promise.resolve({ data: {} });
    });
    mockedDelete.mockResolvedValue({ data: { ok: true } });
    mockedPost.mockResolvedValue({ data: { ok: true, deleted_count: 2, deleted_ids: ["c1", "c2"] } });
  });

  function LocationSpy() {
    const location = useLocation();
    return <div data-testid="location-search">{location.search}</div>;
  }

  function renderWorkspace() {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/workspace?conv=c1"]}>
          <LocationSpy />
          <Routes>
            <Route path="/workspace" element={<Workspace />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
  }

  test("single delete opens confirmation dialog, cancel preserves it, confirm deletes conversation", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    expect(await screen.findByText("Conversation Alpha")).toBeInTheDocument();

    const deleteBtn = screen.getByTestId("delete-conversation-c1");
    await user.click(deleteBtn);

    // Dialog should open
    const dialog = await screen.findByTestId("conversation-delete-dialog");
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Delete conversation?");
    expect(dialog).toHaveTextContent("Conversation Alpha");

    // Cancel click
    const cancelBtn = screen.getByTestId("conversation-delete-cancel");
    await user.click(cancelBtn);

    await waitFor(() => {
      expect(screen.queryByTestId("conversation-delete-dialog")).toBeNull();
    });
    expect(mockedDelete).not.toHaveBeenCalled();

    // Open again and confirm delete
    await user.click(screen.getByTestId("delete-conversation-c1"));
    expect(await screen.findByTestId("conversation-delete-dialog")).toBeInTheDocument();

    const confirmBtn = screen.getByTestId("conversation-delete-confirm");
    await user.click(confirmBtn);

    await waitFor(() => {
      expect(mockedDelete).toHaveBeenCalledWith("/conversations/c1");
    });

    // Verify ?conv=c1 is cleaned up from search parameters upon deleting the active conversation
    await waitFor(() => {
      expect(screen.getByTestId("location-search")).toHaveTextContent("");
    });
  });

  test("bulk select mode toggles, selection count updates, select all works, and bulk delete confirms", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    expect(await screen.findByText("Conversation Alpha")).toBeInTheDocument();

    // Toggle bulk select mode
    const selectModeToggle = screen.getByTestId("conversation-select-mode-toggle");
    expect(selectModeToggle).toHaveTextContent("Select");
    await user.click(selectModeToggle);

    expect(selectModeToggle).toHaveTextContent("Cancel");
    expect(screen.getByTestId("conversation-selected-count")).toHaveTextContent("0 selected");

    // Delete button disabled when 0 selected
    const bulkDeleteBtn = screen.getByTestId("conversation-bulk-delete-btn");
    expect(bulkDeleteBtn).toBeDisabled();

    // Select individual conversation
    const c1Checkbox = screen.getByTestId("conversation-select-c1");
    await user.click(c1Checkbox);
    expect(screen.getByTestId("conversation-selected-count")).toHaveTextContent("1 selected");
    expect(bulkDeleteBtn).not.toBeDisabled();

    // Select another
    const c2Checkbox = screen.getByTestId("conversation-select-c2");
    await user.click(c2Checkbox);
    expect(screen.getByTestId("conversation-selected-count")).toHaveTextContent("2 selected");

    // Select all
    const selectAllBtn = screen.getByTestId("conversation-select-all");
    expect(selectAllBtn).toHaveTextContent("Select all");
    await user.click(selectAllBtn);
    expect(screen.getByTestId("conversation-selected-count")).toHaveTextContent("3 selected");
    expect(selectAllBtn).toHaveTextContent("Deselect all");

    // Click bulk delete
    await user.click(bulkDeleteBtn);

    // Confirmation dialog opens
    expect(await screen.findByTestId("conversation-bulk-delete-dialog")).toBeInTheDocument();
    expect(screen.getByText("Delete 3 conversations?")).toBeInTheDocument();

    // Cancel bulk delete
    await user.click(screen.getByTestId("conversation-bulk-delete-cancel"));
    await waitFor(() => {
      expect(screen.queryByTestId("conversation-bulk-delete-dialog")).toBeNull();
    });
    expect(mockedPost).not.toHaveBeenCalledWith("/conversations/bulk-delete", expect.anything());

    // Reopen and confirm bulk delete
    await user.click(bulkDeleteBtn);
    expect(await screen.findByTestId("conversation-bulk-delete-dialog")).toBeInTheDocument();
    await user.click(screen.getByTestId("conversation-bulk-delete-confirm"));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith("/conversations/bulk-delete", {
        conversation_ids: ["c1", "c2", "c3"],
        project_id: "p1",
      });
    });

    // Verify ?conv=c1 is cleaned up from search parameters upon bulk deleting the active conversation
    await waitFor(() => {
      expect(screen.getByTestId("location-search")).toHaveTextContent("");
    });
  });
});
