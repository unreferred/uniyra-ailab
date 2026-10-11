import React from "react";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Sidebar } from "@/components/layout/Sidebar";

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({
    projects: [{ id: "p1", name: "Demo" }],
    activeProject: { id: "p1", name: "Demo" },
    activeId: "p1",
    setActiveId: jest.fn(),
    isLoading: false,
    refresh: jest.fn(),
  }),
  ProjectProvider: ({ children }: any) => <>{children}</>,
}));

// Route paths declared by the protected <Shell> in src/App.tsx.
const APP_ROUTES = new Set<string>([
  "/",
  "/workspace",
  "/arena",
  "/council",
  "/experiments",
  "/experiments/new",
  "/experiments/:id",
  "/research",
  "/research/:id",
  "/findings",
  "/datasets",
  "/datasets/:id",
  "/artifacts",
  "/observability",
  "/failures",
  "/red-team",
  "/scorecards",
  "/certification",
  "/tools",
  "/run-compare",
  "/context-lab",
  "/prompt-lab",
  "/prompt-lab/prompts/:id",
  "/agents",
  "/agents/:id",
  "/trace",
  "/models",
  "/projects/:id",
  "/settings",
]);

function matchesAppRoute(href: string): boolean {
  if (APP_ROUTES.has(href)) return true;
  return [...APP_ROUTES].some((route) => {
    if (!route.includes(":")) return false;
    const routeParts = route.split("/");
    const hrefParts = href.split("/");
    if (routeParts.length !== hrefParts.length) return false;
    return routeParts.every((part, i) => part.startsWith(":") || part === hrefParts[i]);
  });
}

function renderSidebar() {
  return render(
    <MemoryRouter>
      <Sidebar collapsed={false} onToggle={() => {}} />
    </MemoryRouter>
  );
}

test("sidebar renders grouped section headers", () => {
  renderSidebar();
  const sections: Array<[string, string]> = [
    ["nav-section-overview", "Overview"],
    ["nav-section-build", "Build & Configure"],
    ["nav-section-experiment", "Experiment"],
    ["nav-section-safety", "Evaluate & Safety"],
    ["nav-section-operations", "Operations"],
  ];
  for (const [testid, title] of sections) {
    const header = screen.getByTestId(testid);
    expect(header).toHaveTextContent(title);
  }
});

test("sidebar surfaces every lab entry", () => {
  renderSidebar();
  const labs: Array<[string, string]> = [
    ["nav-arena", "Arena"],
    ["nav-prompt-lab", "Prompt Lab"],
    ["nav-context-lab", "Context Lab"],
    ["nav-agent-lab", "Agent Lab"],
    ["nav-tool-lab", "Tool Lab"],
  ];
  for (const [testid, label] of labs) {
    expect(screen.getByTestId(testid)).toHaveTextContent(label);
  }
});

test("every sidebar nav link points at a route declared in App.tsx", () => {
  renderSidebar();
  const nav = screen.getByTestId("navigation-sidebar");
  const links = within(nav).getAllByRole("link");
  expect(links.length).toBeGreaterThan(0);
  for (const link of links) {
    const href = link.getAttribute("href");
    expect(href).toBeTruthy();
    expect(matchesAppRoute(href as string)).toBe(true);
  }
});

test("sidebar exposes only real modules (no Routing Lab or Coming Soon)", () => {
  renderSidebar();
  expect(screen.queryByText(/Routing Lab/i)).toBeNull();
  expect(screen.queryByText(/Coming Soon/i)).toBeNull();
});
