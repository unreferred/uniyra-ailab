import { PlanningGraphEdge, PlanningGraphNode } from "@/lib/types";

export interface PlanMapPosition {
  x: number;
  y: number;
}

export interface PlanMapLayout {
  positions: Record<string, PlanMapPosition>;
  width: number;
  height: number;
  visibleNodeIds: string[];
  visibleEdges: PlanningGraphEdge[];
}

export const NODE_WIDTH = 184;
export const NODE_HEIGHT = 58;
export const COLUMN_GAP = 76;
export const ROW_GAP = 86;
export const PADDING = 40;

export const COLUMN_STEP = NODE_WIDTH + COLUMN_GAP;

interface NodeEntry {
  node: PlanningGraphNode;
  id: string;
  index: number;
}

export function planGraphNodeId(node: PlanningGraphNode, index: number): string {
  return node.id || node.node_key || `plan-node-${index}`;
}

function compareEntries(a: NodeEntry, b: NodeEntry): number {
  const ao = a.node.order;
  const bo = b.node.order;
  if (ao != null && bo != null && ao !== bo) return ao - bo;
  if (ao != null && bo == null) return -1;
  if (ao == null && bo != null) return 1;
  const ak = a.node.node_key || a.id;
  const bk = b.node.node_key || b.id;
  if (ak !== bk) return ak < bk ? -1 : 1;
  return a.index - b.index;
}

function computeDepths(entries: NodeEntry[], edges: PlanningGraphEdge[]): Map<string, number> {
  const ids = new Set(entries.map((e) => e.id));
  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, number>();
  for (const entry of entries) {
    outgoing.set(entry.id, []);
    incoming.set(entry.id, 0);
  }
  for (const edge of edges) {
    const source = edge.source_node_id;
    const target = edge.target_node_id;
    if (!source || !target) continue;
    if (!ids.has(source) || !ids.has(target)) continue;
    outgoing.get(source)!.push(target);
    incoming.set(target, (incoming.get(target) || 0) + 1);
  }

  const roots = entries
    .filter((entry) => (incoming.get(entry.id) || 0) === 0)
    .sort(compareEntries)
    .map((entry) => entry.id);

  const depths = new Map<string, number>();
  const queue: string[] = [];
  for (const root of roots) {
    depths.set(root, 0);
    queue.push(root);
  }
  let cursor = 0;
  while (cursor < queue.length) {
    const current = queue[cursor++];
    const depth = depths.get(current) ?? 0;
    for (const next of outgoing.get(current) || []) {
      if (!depths.has(next)) {
        depths.set(next, depth + 1);
        queue.push(next);
      }
    }
  }
  for (const entry of entries) {
    if (!depths.has(entry.id)) depths.set(entry.id, 0);
  }
  return depths;
}

function computeHidden(entries: NodeEntry[], edges: PlanningGraphEdge[], collapsedKeys: Iterable<string>): Set<string> {
  const collapsed = new Set(collapsedKeys);
  if (collapsed.size === 0) return new Set<string>();
  const ids = new Set(entries.map((e) => e.id));
  const outgoing = new Map<string, string[]>();
  for (const entry of entries) outgoing.set(entry.id, []);
  for (const edge of edges) {
    const source = edge.source_node_id;
    const target = edge.target_node_id;
    if (!source || !target) continue;
    if (!ids.has(source) || !ids.has(target)) continue;
    outgoing.get(source)!.push(target);
  }

  const collapsedIds = new Set<string>();
  for (const entry of entries) {
    const key = entry.node.node_key;
    if ((key && collapsed.has(key)) || collapsed.has(entry.id)) collapsedIds.add(entry.id);
  }

  const hidden = new Set<string>();
  const queue: string[] = [];
  for (const id of Array.from(collapsedIds).sort()) {
    for (const child of outgoing.get(id) || []) {
      if (!hidden.has(child)) {
        hidden.add(child);
        queue.push(child);
      }
    }
  }
  let cursor = 0;
  while (cursor < queue.length) {
    const current = queue[cursor++];
    for (const next of outgoing.get(current) || []) {
      if (!hidden.has(next)) {
        hidden.add(next);
        queue.push(next);
      }
    }
  }
  return hidden;
}

export function layoutPlanGraph(
  nodes: PlanningGraphNode[],
  edges: PlanningGraphEdge[],
  collapsedKeys: Iterable<string> = []
): PlanMapLayout {
  const entries: NodeEntry[] = (nodes || []).map((node, index) => ({
    node,
    id: planGraphNodeId(node, index),
    index,
  }));

  const edgeList = edges || [];
  const hidden = computeHidden(entries, edgeList, collapsedKeys);
  const visibleEntries = entries.filter((entry) => !hidden.has(entry.id));
  const visibleIds = new Set(visibleEntries.map((entry) => entry.id));

  if (visibleEntries.length === 0) {
    return {
      positions: {},
      width: PADDING * 2,
      height: PADDING * 2,
      visibleNodeIds: [],
      visibleEdges: [],
    };
  }

  const depths = computeDepths(visibleEntries, edgeList);
  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, number>();
  for (const entry of visibleEntries) {
    outgoing.set(entry.id, []);
    incoming.set(entry.id, 0);
  }
  for (const edge of edgeList) {
    const source = edge.source_node_id;
    const target = edge.target_node_id;
    if (!source || !target) continue;
    if (!visibleIds.has(source) || !visibleIds.has(target)) continue;
    outgoing.get(source)!.push(target);
    incoming.set(target, (incoming.get(target) || 0) + 1);
  }

  const entryById = new Map(visibleEntries.map((entry) => [entry.id, entry]));
  const childOrder = new Map<string, NodeEntry[]>();
  for (const entry of visibleEntries) {
    const children = (outgoing.get(entry.id) || [])
      .map((id) => entryById.get(id))
      .filter((child): child is NodeEntry => !!child);
    childOrder.set(entry.id, children.sort(compareEntries));
  }

  const roots = visibleEntries
    .filter((entry) => (incoming.get(entry.id) || 0) === 0)
    .sort(compareEntries);

  const order: NodeEntry[] = [];
  const visited = new Set<string>();
  const stack: NodeEntry[] = [];
  const pushChildren = (entry: NodeEntry) => {
    const children = childOrder.get(entry.id) || [];
    for (let i = children.length - 1; i >= 0; i -= 1) stack.push(children[i]);
  };
  for (let i = roots.length - 1; i >= 0; i -= 1) stack.push(roots[i]);
  while (stack.length > 0) {
    const entry = stack.pop() as NodeEntry;
    if (visited.has(entry.id)) continue;
    visited.add(entry.id);
    order.push(entry);
    pushChildren(entry);
  }
  const leftovers = visibleEntries.filter((entry) => !visited.has(entry.id)).sort(compareEntries);
  for (const entry of leftovers) {
    if (visited.has(entry.id)) continue;
    visited.add(entry.id);
    order.push(entry);
    pushChildren(entry);
    while (stack.length > 0) {
      const next = stack.pop() as NodeEntry;
      if (visited.has(next.id)) continue;
      visited.add(next.id);
      order.push(next);
      pushChildren(next);
    }
  }

  const positions: Record<string, PlanMapPosition> = {};
  const columnRows = new Map<number, number>();
  let maxDepth = 0;
  let maxRows = 0;
  for (const entry of order) {
    const depth = depths.get(entry.id) ?? 0;
    const row = columnRows.get(depth) || 0;
    columnRows.set(depth, row + 1);
    positions[entry.id] = {
      x: PADDING + depth * COLUMN_STEP,
      y: PADDING + row * ROW_GAP,
    };
    if (depth > maxDepth) maxDepth = depth;
    if (row + 1 > maxRows) maxRows = row + 1;
  }

  const visibleEdges = edgeList.filter(
    (edge) =>
      !!edge.source_node_id &&
      !!edge.target_node_id &&
      visibleIds.has(edge.source_node_id) &&
      visibleIds.has(edge.target_node_id)
  );

  return {
    positions,
    width: PADDING * 2 + maxDepth * COLUMN_STEP + NODE_WIDTH,
    height: PADDING * 2 + Math.max(0, maxRows - 1) * ROW_GAP + NODE_HEIGHT,
    visibleNodeIds: order.map((entry) => entry.id),
    visibleEdges,
  };
}
