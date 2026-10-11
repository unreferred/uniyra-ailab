import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Findings from "@/pages/Findings";
import {
  api,
  linkFinding,
  promoteFindingToRedTeam,
  promoteFindingToRegression,
} from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
}));

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({
    activeId: "p1",
    activeProject: { id: "p1", name: "Project one" },
    projects: [{ id: "p1", name: "Project one" }],
    isLoading: false,
    setActiveId: jest.fn(),
    refresh: jest.fn(),
  }),
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  promoteFindingToRegression: jest.fn(),
  promoteFindingToRedTeam: jest.fn(),
  linkFinding: jest.fn(),
}));

const mockedGet = api.get as jest.Mock;
const mockedRegression = promoteFindingToRegression as jest.Mock;
const mockedRedTeam = promoteFindingToRedTeam as jest.Mock;
const mockedLink = linkFinding as jest.Mock;

const findingSummary = {
  id: "f1",
  project_id: "p1",
  title: "Hallucinated citation",
  statement: "The model invented sources.",
  description: "",
  confidence: "high",
  status: "provisional",
  notes: "",
  tags: ["research"],
  evidence: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const findingDetail = {
  ...findingSummary,
  resolved_evidence: [],
  promotions: [
    {
      promotion_id: "p-old",
      kind: "regression",
      dataset_id: "d-1",
      dataset_version_id: "dv-0",
      case_id: "case-0",
    },
  ],
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedGet.mockImplementation((url: string) => {
    if (url === "/findings?project_id=p1") return Promise.resolve({ data: [findingSummary] });
    if (url === "/findings/f1") return Promise.resolve({ data: findingDetail });
    if (url === "/experiments?project_id=p1") return Promise.resolve({ data: [] });
    if (url === "/research?project_id=p1") return Promise.resolve({ data: [] });
    if (url === "/artifacts?project_id=p1") return Promise.resolve({ data: [] });
    if (url === "/conversations?project_id=p1") return Promise.resolve({ data: [] });
    if (url === "/councils?project_id=p1") return Promise.resolve({ data: [] });
    if (url === "/datasets?project_id=p1") return Promise.resolve({ data: [] });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  mockedRegression.mockResolvedValue({ dataset_id: "d-9", dataset_version_id: "dv-9", case_id: "case-9" });
  mockedRedTeam.mockResolvedValue({ dataset_id: "d-8", dataset_version_id: "dv-8", case_id: "case-8" });
  mockedLink.mockResolvedValue({ ok: true });
});

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Findings />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

async function openDetail(user: ReturnType<typeof userEvent.setup>) {
  renderPage();
  await user.click(await screen.findByTestId("open-finding-f1"));
  await screen.findByTestId("finding-detail-dialog");
}

test("promote to regression calls the api helper with the finding id and renders returned ids", async () => {
  const user = userEvent.setup();
  await openDetail(user);

  // Existing promotions from GET /findings/{id} are rendered with a dataset link.
  const existing = await screen.findByTestId("finding-promotion-row");
  expect(existing).toHaveTextContent("dv-0");
  expect(existing).toHaveTextContent("case-0");
  expect(screen.getByTestId("finding-promotion-dataset-link")).toHaveAttribute("href", "/datasets/d-1");

  await user.click(screen.getByTestId("finding-promote-regression"));

  await waitFor(() => expect(mockedRegression).toHaveBeenCalledWith("f1"));
  const result = await screen.findByTestId("finding-promotion-result");
  expect(result).toHaveTextContent("dv-9");
  expect(within(result).getByTestId("promotion-case-id")).toHaveTextContent("case-9");
});

test("promote to red-team calls the api helper with the finding id and renders returned ids", async () => {
  const user = userEvent.setup();
  await openDetail(user);

  await user.click(screen.getByTestId("finding-promote-red-team"));

  await waitFor(() => expect(mockedRedTeam).toHaveBeenCalledWith("f1"));
  const result = await screen.findByTestId("finding-promotion-result");
  expect(result).toHaveTextContent("dv-8");
  expect(within(result).getByTestId("promotion-case-id")).toHaveTextContent("case-8");
});
