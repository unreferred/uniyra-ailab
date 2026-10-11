import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PlanMapView } from "@/components/workspace/PlanMapView";
import { layoutPlanGraph } from "@/components/workspace/planMapLayout";
import {
  clearPlanningGraphReview,
  getPlanningGraph,
  getPlanningGraphNode,
  syncPlanningGraph,
} from "@/lib/api";
import { PlanningGraph, PlanningGraphEdge, PlanningGraphNode, PlanningGraphNodeDetail } from "@/lib/types";

jest.mock("@floating-ui/dom", () => {
  const actual = jest.requireActual("@floating-ui/dom");
  return {
    ...actual,
    computePosition: () =>
      Promise.resolve({ x: 0, y: 0, strategy: "fixed", placement: "bottom", middlewareData: {} }),
  };
});

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn(), message: jest.fn() },
}));

jest.mock("@/context/ProjectContext", () => ({
  useProjects: () => ({
    activeId: "p1",
    activeProject: { id: "p1", name: "Demo Project" },
    projects: [],
    isLoading: false,
    setActiveId: jest.fn(),
    refresh: jest.fn(),
  }),
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  getPlanningGraph: jest.fn(),
  syncPlanningGraph: jest.fn(),
  getPlanningGraphCatalog: jest.fn(),
  getPlanningGraphNode: jest.fn(),
  clearPlanningGraphReview: jest.fn(),
  analyzePlanningImpact: jest.fn(),
  getPrdReadiness: jest.fn(),
  generatePrd: jest.fn(),
  listPrdVersions: jest.fn(),
  getPrdVersion: jest.fn(),
  comparePrdVersions: jest.fn(),
  approvePrdVersion: jest.fn(),
}));

const mockedGetPlanningGraph = getPlanningGraph as jest.Mock;
const mockedSyncPlanningGraph = syncPlanningGraph as jest.Mock;
const mockedGetPlanningGraphNode = getPlanningGraphNode as jest.Mock;
const mockedClearReview = clearPlanningGraphReview as jest.Mock;

const now = new Date("2026-01-01T00:00:00.000Z").toISOString();

const sectionNode: PlanningGraphNode = {
  id: "n-sec",
  project_id: "p1",
  node_key: "section.persistence",
  type: "SECTION",
  title: "Persistence",
  content: "Storage decisions",
  order: 0,
};

const subdomainNode: PlanningGraphNode = {
  id: "n-sub",
  project_id: "p1",
  node_key: "subdomain.storage",
  type: "SUBDOMAIN",
  title: "Storage",
  order: 0,
};

const confirmedNode: PlanningGraphNode = {
  id: "n-conf",
  project_id: "p1",
  node_key: "decision.storage.engine",
  type: "DECISION",
  title: "Use SQLite",
  content: "SQLite for local storage",
  status: "CONFIRMED_BY_USER",
  decision_id: "dec1",
  topic_key: "storage.engine",
  category: "decision",
  section_key: "section.persistence",
  conversation_id: "c1",
  source_message_id: "m2",
  provenance: { message_id: "m2", quote: "we will use sqlite" },
  order: 0,
  review_required: false,
};

const assumedNode: PlanningGraphNode = {
  id: "n-asm",
  project_id: "p1",
  node_key: "decision.platform.ios",
  type: "DECISION",
  title: "Might need iOS",
  content: "iOS support later",
  status: "ASSUMED_BY_MODEL",
  decision_id: "dec2",
  topic_key: "platform.ios",
  category: "assumption",
  section_key: "section.persistence",
  conversation_id: "c1",
  order: 1,
  review_required: true,
  review_reason: "Unverified assumption",
};

const edges: PlanningGraphEdge[] = [
  { id: "e1", source_node_id: "n-sec", target_node_id: "n-sub", relation: "contains" },
  { id: "e2", source_node_id: "n-sub", target_node_id: "n-conf", relation: "contains" },
  { id: "e3", source_node_id: "n-sec", target_node_id: "n-asm", relation: "contains" },
];

const graph: PlanningGraph = {
  project_id: "p1",
  conversation_id: "c1",
  schema_version: "v1",
  synced_at: now,
  nodes: [sectionNode, subdomainNode, confirmedNode, assumedNode],
  edges,
  sections: [{ key: "section.persistence", title: "Persistence", node_id: "n-sec", counts: { DECISION: 2 }, review_required: false }],
  counts: { CONFIRMED_BY_USER: 1, ASSUMED_BY_MODEL: 1 },
};

const emptyGraph: PlanningGraph = {
  project_id: "p1",
  conversation_id: "c1",
  schema_version: "v1",
  nodes: [],
  edges: [],
  sections: [],
  counts: {},
};

const details: Record<string, PlanningGraphNodeDetail> = {
  "n-conf": {
    node: confirmedNode,
    decision: { id: "dec1", status: "CONFIRMED_BY_USER", value: "Use SQLite for local storage" },
    provenance: { message_id: "m2", quote: "we will use sqlite" },
    dependencies: [subdomainNode],
    dependents: [],
    affected: [assumedNode],
    history: [
      { decision_id: "dec0", status: "SUPERSEDED", value: "Use Postgres", created_at: now, superseded_by_id: "dec1" },
    ],
  },
  "n-asm": {
    node: assumedNode,
    decision: { id: "dec2", status: "ASSUMED_BY_MODEL", value: "iOS support later" },
    provenance: null,
    dependencies: [sectionNode],
    dependents: [],
    affected: [],
    history: [],
  },
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedGetPlanningGraph.mockResolvedValue(graph);
  mockedSyncPlanningGraph.mockResolvedValue(graph);
  mockedClearReview.mockResolvedValue({ ok: true });
  mockedGetPlanningGraphNode.mockImplementation((id: string) =>
    Promise.resolve(details[id] || { node: null })
  );
});

function renderPlanMap() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <PlanMapView projectId="p1" conversationId="c1" />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

test("loads the plan map and renders real graph nodes and counts", async () => {
  renderPlanMap();

  expect(await screen.findByTestId("plan-map")).toBeInTheDocument();
  expect(await screen.findByTestId("plan-map-canvas")).toBeInTheDocument();
  expect(await screen.findByTestId("plan-map-node-section.persistence")).toBeInTheDocument();
  expect(await screen.findByTestId("plan-map-node-decision.storage.engine")).toBeInTheDocument();
  expect(await screen.findByTestId("plan-map-node-decision.platform.ios")).toBeInTheDocument();
  expect(await screen.findByTestId("plan-map-legend")).toHaveTextContent("CONFIRMED_BY_USER 1");
  expect(mockedGetPlanningGraph).toHaveBeenCalledWith("p1", "c1");
});

test("selecting a node fetches detail and renders the detail panel", async () => {
  const user = userEvent.setup({ delay: null });
  renderPlanMap();

  const node = await screen.findByTestId("plan-map-node-decision.storage.engine");
  await user.click(node);

  const detail = await screen.findByTestId("plan-map-detail");
  expect(detail).toHaveTextContent("Use SQLite");
  expect(await screen.findByTestId("plan-map-detail-status")).toHaveTextContent("CONFIRMED_BY_USER");
  expect(await screen.findByTestId("plan-map-detail-content")).toHaveTextContent("SQLite for local storage");

  await waitFor(() => expect(mockedGetPlanningGraphNode).toHaveBeenCalledWith("n-conf"));
  expect(await screen.findByTestId("plan-map-detail-dependencies")).toHaveTextContent("Storage");
  expect(await screen.findByTestId("plan-map-detail-affected")).toHaveTextContent("Might need iOS");
  expect(await screen.findByTestId("plan-map-detail-history")).toHaveTextContent("Use Postgres");
});

test("confirmed and assumed nodes are visually distinguished", async () => {
  renderPlanMap();

  const confirmed = await screen.findByTestId("plan-map-node-decision.storage.engine");
  const assumed = await screen.findByTestId("plan-map-node-decision.platform.ios");

  expect(confirmed).toHaveTextContent("CONFIRMED_BY_USER");
  expect(assumed).toHaveTextContent("ASSUMED_BY_MODEL");

  const confirmedClass = confirmed.querySelector("rect")?.getAttribute("class") || "";
  const assumedClass = assumed.querySelector("rect")?.getAttribute("class") || "";
  expect(confirmedClass).toContain("emerald");
  expect(assumedClass).toContain("amber");
  expect(confirmedClass).not.toEqual(assumedClass);
});

test("collapsing a section hides its descendants and expand-all restores them", async () => {
  const user = userEvent.setup({ delay: null });
  renderPlanMap();

  expect(await screen.findByTestId("plan-map-node-decision.storage.engine")).toBeInTheDocument();

  await user.click(await screen.findByTestId("plan-map-section-collapse-section.persistence"));

  await waitFor(() =>
    expect(screen.queryByTestId("plan-map-node-decision.storage.engine")).toBeNull()
  );
  expect(screen.queryByTestId("plan-map-node-subdomain.storage")).toBeNull();
  expect(screen.queryByTestId("plan-map-node-decision.platform.ios")).toBeNull();
  expect(screen.getByTestId("plan-map-node-section.persistence")).toBeInTheDocument();

  await user.click(screen.getByTestId("plan-map-expand-all"));

  expect(await screen.findByTestId("plan-map-node-decision.storage.engine")).toBeInTheDocument();
  expect(await screen.findByTestId("plan-map-node-decision.platform.ios")).toBeInTheDocument();
});

test("sync calls the sync API and refetches the graph", async () => {
  const user = userEvent.setup({ delay: null });
  renderPlanMap();
  await screen.findByTestId("plan-map-node-decision.storage.engine");

  const before = mockedGetPlanningGraph.mock.calls.length;
  await user.click(screen.getByTestId("plan-map-sync"));

  await waitFor(() =>
    expect(mockedSyncPlanningGraph).toHaveBeenCalledWith({ project_id: "p1", conversation_id: "c1" })
  );
  await waitFor(() => expect(mockedGetPlanningGraph.mock.calls.length).toBeGreaterThan(before));
});

test("empty graph renders the honest empty state and no fake nodes", async () => {
  mockedGetPlanningGraph.mockResolvedValue(emptyGraph);
  renderPlanMap();

  expect(await screen.findByTestId("plan-map-empty")).toBeInTheDocument();
  expect(screen.queryByTestId("plan-map-node-section.persistence")).toBeNull();
  expect(screen.queryByTestId("plan-map-detail")).toBeNull();
});

test("clear-review action is offered and calls the API only when review is required", async () => {
  const user = userEvent.setup({ delay: null });
  renderPlanMap();

  await user.click(await screen.findByTestId("plan-map-node-decision.storage.engine"));
  await screen.findByTestId("plan-map-detail");
  await waitFor(() => expect(mockedGetPlanningGraphNode).toHaveBeenCalledWith("n-conf"));
  expect(screen.queryByTestId("plan-map-clear-review")).toBeNull();

  await user.click(screen.getByTestId("plan-map-node-decision.platform.ios"));
  await waitFor(() => expect(mockedGetPlanningGraphNode).toHaveBeenCalledWith("n-asm"));

  const clear = await screen.findByTestId("plan-map-clear-review");
  await user.click(clear);

  await waitFor(() => expect(mockedClearReview).toHaveBeenCalledWith("n-asm", "p1"));
});

test("layoutPlanGraph positions are deterministic and collapsing drops descendants", () => {
  const nodes: PlanningGraphNode[] = [
    { id: "r", node_key: "root", type: "ROOT", title: "Root", order: 0 },
    { id: "s", node_key: "sec", type: "SECTION", title: "Sec", order: 0 },
    { id: "d1", node_key: "d1", type: "DECISION", title: "D1", order: 0 },
    { id: "d2", node_key: "d2", type: "DECISION", title: "D2", order: 1 },
  ];
  const layoutEdges: PlanningGraphEdge[] = [
    { id: "e1", source_node_id: "r", target_node_id: "s" },
    { id: "e2", source_node_id: "s", target_node_id: "d1" },
    { id: "e3", source_node_id: "s", target_node_id: "d2" },
  ];

  const layout = layoutPlanGraph(nodes, layoutEdges, []);
  const again = layoutPlanGraph(nodes, layoutEdges, []);
  expect(layout.positions).toEqual(again.positions);
  expect(layout.visibleNodeIds).toEqual(["r", "s", "d1", "d2"]);
  expect(layout.positions["r"]).toEqual({ x: 40, y: 40 });
  expect(layout.positions["s"]).toEqual({ x: 300, y: 40 });
  expect(layout.positions["d1"]).toEqual({ x: 560, y: 40 });
  expect(layout.positions["d2"]).toEqual({ x: 560, y: 126 });
  expect(layout.visibleEdges).toHaveLength(3);
  expect(layout.width).toBe(784);
  expect(layout.height).toBe(224);

  const collapsed = layoutPlanGraph(nodes, layoutEdges, ["sec"]);
  expect(collapsed.visibleNodeIds).toEqual(["r", "s"]);
  expect(collapsed.visibleNodeIds).not.toContain("d1");
  expect(collapsed.visibleNodeIds).not.toContain("d2");
  expect(collapsed.positions["d1"]).toBeUndefined();
  expect(collapsed.positions["s"]).toEqual({ x: 300, y: 40 });
  expect(collapsed.visibleEdges).toHaveLength(1);
});

test("expand workspace button toggles expanded fullscreen layout and Escape exits it", async () => {
  const user = userEvent.setup({ delay: null });
  renderPlanMap();

  const planMapRoot = await screen.findByTestId("plan-map");
  expect(planMapRoot).not.toHaveClass("fixed");

  const expandBtn = screen.getByTestId("plan-map-expand-workspace");
  expect(expandBtn).toHaveAttribute("title", "Expand workspace");

  // Click to expand
  await user.click(expandBtn);

  expect(planMapRoot).toHaveClass("fixed");
  expect(planMapRoot).toHaveClass("inset-0");
  expect(expandBtn).toHaveAttribute("title", "Exit expanded mode (Esc)");

  // Press Escape to exit expanded mode
  fireEvent.keyDown(window, { key: "Escape" });

  expect(planMapRoot).not.toHaveClass("fixed");
  expect(expandBtn).toHaveAttribute("title", "Expand workspace");

  // Click again to expand, then click toggle button to exit
  await user.click(expandBtn);
  expect(planMapRoot).toHaveClass("fixed");
  await user.click(expandBtn);
  expect(planMapRoot).not.toHaveClass("fixed");
});

test("fit-view and node detail remain fully functional in normal and expanded mode", async () => {
  const user = userEvent.setup({ delay: null });
  renderPlanMap();

  const fitBtn = screen.getByTestId("plan-map-fit");
  await user.click(fitBtn);

  // Click node to open detail
  const node = await screen.findByTestId("plan-map-node-decision.storage.engine");
  await user.click(node);
  expect(await screen.findByTestId("plan-map-detail")).toBeInTheDocument();

  // Enter expand mode with detail open
  const expandBtn = screen.getByTestId("plan-map-expand-workspace");
  await user.click(expandBtn);
  expect(screen.getByTestId("plan-map-detail")).toBeInTheDocument();

  // Fit view still works in expand mode
  await user.click(fitBtn);
  expect(screen.getByTestId("plan-map-canvas")).toBeInTheDocument();
});

test("wheel interaction updates zoom and cleans up native listener on unmount", async () => {
  const addSpy = jest.spyOn(SVGSVGElement.prototype, "addEventListener");
  const removeSpy = jest.spyOn(SVGSVGElement.prototype, "removeEventListener");

  const { unmount } = renderPlanMap();
  const canvas = await screen.findByTestId("plan-map-canvas");
  const initialGroup = canvas.querySelector("g");
  const initialTransform = initialGroup?.getAttribute("transform") || "";

  // Expect native wheel listener registered with passive: false
  expect(addSpy).toHaveBeenCalledWith("wheel", expect.any(Function), { passive: false });

  // Dispatch wheel event to zoom in
  fireEvent.wheel(canvas, { deltaY: -100 });

  await waitFor(() => {
    const zoomedGroup = canvas.querySelector("g");
    const zoomedTransform = zoomedGroup?.getAttribute("transform") || "";
    expect(zoomedTransform).not.toEqual(initialTransform);
    expect(zoomedTransform).toContain("scale(1.1)");
  });

  // Dispatch wheel event to zoom out
  fireEvent.wheel(canvas, { deltaY: 100 });

  await waitFor(() => {
    const zoomedOutTransform = canvas.querySelector("g")?.getAttribute("transform") || "";
    expect(zoomedOutTransform).toContain("scale(0.99");
  });

  // Unmount cleans up listener
  unmount();
  expect(removeSpy).toHaveBeenCalledWith("wheel", expect.any(Function));

  addSpy.mockRestore();
  removeSpy.mockRestore();
});

test("expand and collapse does not duplicate wheel event listeners or increments", async () => {
  const user = userEvent.setup({ delay: null });
  renderPlanMap();

  const canvas = await screen.findByTestId("plan-map-canvas");
  const expandBtn = screen.getByTestId("plan-map-expand-workspace");

  // Expand and collapse
  await user.click(expandBtn);
  await user.click(expandBtn);

  // Wait for any timeout(fitView) to settle within waitFor
  await waitFor(() => {
    expect(canvas.querySelector("g")).toBeInTheDocument();
  });

  const initialGroup = canvas.querySelector("g");
  const initialTransform = initialGroup?.getAttribute("transform") || "";
  const match = initialTransform.match(/scale\(([^)]+)\)/);
  const currentScale = match ? parseFloat(match[1]) : 1;
  const expectedScale = currentScale * 1.1;

  // Wheel event should apply zoom exactly once
  fireEvent.wheel(canvas, { deltaY: -100 });

  await waitFor(() => {
    const group = canvas.querySelector("g");
    const newMatch = group?.getAttribute("transform")?.match(/scale\(([^)]+)\)/);
    const newScale = newMatch ? parseFloat(newMatch[1]) : 0;
    expect(newScale).toBeCloseTo(expectedScale, 4);
    // Ensure it didn't apply twice (which would be currentScale * 1.21)
    expect(newScale).not.toBeCloseTo(currentScale * 1.21, 2);
  });
});
