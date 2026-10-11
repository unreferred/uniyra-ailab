import React, { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  Loader2,
  Maximize2,
  Minimize2,
  Minus,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";
import {
  analyzePlanningImpact,
  apiError,
  clearPlanningGraphReview,
  getPlanningGraph,
  getPlanningGraphNode,
  syncPlanningGraph,
} from "@/lib/api";
import { PlanningGraphNode } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { nText } from "@/components/experiment/format";
import { cn } from "@/lib/utils";
import {
  NODE_HEIGHT,
  NODE_WIDTH,
  layoutPlanGraph,
  planGraphNodeId,
} from "./planMapLayout";

export interface PlanMapViewProps {
  projectId: string;
  conversationId?: string;
  className?: string;
}

const MIN_SCALE = 0.25;
const MAX_SCALE = 2.5;

function clampScale(value: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));
}

function normalizeStatus(status?: string | null): string {
  return String(status || "").toUpperCase();
}

function statusClass(status?: string | null): string {
  const s = normalizeStatus(status);
  if (s === "CONFIRMED_BY_USER" || s === "CONFIRMED" || s === "TECHNICALLY_VERIFIED" || s === "APPROVED") {
    return "fill-emerald-500/15 stroke-emerald-500";
  }
  if (s === "ASSUMED_BY_MODEL" || s === "ASSUMPTION" || s === "NEEDS_VERIFICATION" || s === "DRAFT") {
    return "fill-amber-500/15 stroke-amber-500";
  }
  if (s === "REJECTED" || s === "SUPERSEDED" || s === "CONFLICT" || s === "BLOCKED") {
    return "fill-destructive/15 stroke-destructive";
  }
  if (s === "OPEN" || s === "UNRESOLVED") {
    return "fill-sky-500/15 stroke-sky-500";
  }
  return "fill-muted stroke-border";
}

function statusBadgeVariant(status?: string | null): "default" | "secondary" | "destructive" | "outline" {
  const s = normalizeStatus(status);
  if (s === "REJECTED" || s === "SUPERSEDED" || s === "CONFLICT" || s === "BLOCKED") return "destructive";
  if (s === "CONFIRMED_BY_USER" || s === "TECHNICALLY_VERIFIED" || s === "APPROVED") return "default";
  if (s === "OPEN" || s === "UNRESOLVED") return "secondary";
  return "outline";
}

function statusDotClass(status?: string | null): string {
  const s = normalizeStatus(status);
  if (s === "CONFIRMED_BY_USER" || s === "CONFIRMED" || s === "TECHNICALLY_VERIFIED" || s === "APPROVED") return "bg-emerald-500";
  if (s === "ASSUMED_BY_MODEL" || s === "ASSUMPTION" || s === "NEEDS_VERIFICATION" || s === "DRAFT") return "bg-amber-500";
  if (s === "REJECTED" || s === "SUPERSEDED" || s === "CONFLICT" || s === "BLOCKED") return "bg-destructive";
  if (s === "OPEN" || s === "UNRESOLVED") return "bg-sky-500";
  return "bg-muted-foreground";
}

function truncate(value: unknown, max: number): string {
  const text = value == null ? "" : String(value);
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1))}…`;
}

function provenanceExcerpt(provenance?: Record<string, any> | null): string | null {
  if (!provenance || typeof provenance !== "object") return null;
  try {
    return truncate(JSON.stringify(provenance), 260);
  } catch {
    return null;
  }
}

function shortId(value?: string | null): string {
  if (!value) return "";
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

export function PlanMapView({ projectId, conversationId, className }: PlanMapViewProps): React.JSX.Element {
  const qc = useQueryClient();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const [view, setView] = useState<{ scale: number; x: number; y: number }>({ scale: 1, x: 0, y: 0 });
  const [isExpanded, setIsExpanded] = useState(false);

  // Handle Escape key to exit expanded mode
  useEffect(() => {
    if (!isExpanded) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setIsExpanded(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isExpanded]);

  const graphQuery = useQuery({
    queryKey: ["planning-graph", projectId, conversationId],
    queryFn: () => getPlanningGraph(projectId, conversationId),
    retry: false,
    enabled: !!projectId,
  });

  const graph = graphQuery.data;
  const nodes = useMemo(() => graph?.nodes || [], [graph]);
  const edges = useMemo(() => graph?.edges || [], [graph]);
  const sections = useMemo(() => graph?.sections || [], [graph]);

  const detailQuery = useQuery({
    queryKey: ["planning-graph-node", selectedId],
    queryFn: () => getPlanningGraphNode(selectedId as string),
    retry: false,
    enabled: !!selectedId,
  });

  const invalidateGraph = () =>
    qc.invalidateQueries({ queryKey: ["planning-graph", projectId, conversationId] });

  const syncMutation = useMutation({
    mutationFn: () => syncPlanningGraph({ project_id: projectId, conversation_id: conversationId ?? null }),
    onSuccess: () => {
      invalidateGraph();
      toast.success("Plan graph synced");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const clearReview = useMutation({
    mutationFn: (nodeId: string) => clearPlanningGraphReview(nodeId, projectId),
    onSuccess: () => {
      invalidateGraph();
      qc.invalidateQueries({ queryKey: ["planning-graph-node", selectedId] });
      toast.success("Review cleared");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const impact = useMutation({
    mutationFn: (nodeId: string) => analyzePlanningImpact({ project_id: projectId, node_id: nodeId }),
    onSuccess: () => {
      invalidateGraph();
      qc.invalidateQueries({ queryKey: ["planning-graph-node", selectedId] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const resetImpact = impact.reset;
  useEffect(() => {
    resetImpact();
  }, [selectedId, resetImpact]);

  const entries = useMemo(
    () => nodes.map((node, index) => ({ node, id: planGraphNodeId(node, index) })),
    [nodes]
  );
  const entryById = useMemo(() => new Map(entries.map((entry) => [entry.id, entry])), [entries]);

  const layout = useMemo(
    () => layoutPlanGraph(nodes, edges, collapsed),
    [nodes, edges, collapsed]
  );

  const selectedEntry = selectedId ? entryById.get(selectedId) : undefined;
  const detailNode: PlanningGraphNode | undefined = detailQuery.data?.node || selectedEntry?.node;
  const isGraphEmpty = !graphQuery.isLoading && !!graph && nodes.length === 0;

  const zoomBy = (factor: number) =>
    setView((prev) => ({ ...prev, scale: clampScale(prev.scale * factor) }));

  const fitView = () => {
    const el = containerRef.current;
    const vw = el && el.clientWidth ? el.clientWidth : 900;
    const vh = el && el.clientHeight ? el.clientHeight : 420;
    const scale = clampScale(Math.min(vw / layout.width, vh / layout.height) * 0.92);
    setView({
      scale,
      x: (vw - layout.width * scale) / 2,
      y: (vh - layout.height * scale) / 2,
    });
  };

  const toggleCollapse = (key: string) =>
    setCollapsed((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));

  const collapseAll = () =>
    setCollapsed(sections.map((section) => section.key).filter((key): key is string => !!key));

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;

    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      zoomBy(event.deltaY < 0 ? 1.1 : 0.9);
    };

    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => {
      el.removeEventListener("wheel", handleWheel);
    };
  }, [graphQuery.isLoading, graphQuery.isError]);

  const onPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    (event.currentTarget as SVGSVGElement).setPointerCapture?.(event.pointerId);
    dragRef.current = { x: event.clientX, y: event.clientY, tx: view.x, ty: view.y };
    setDragging(true);
  };

  const onPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    setView((prev) => ({
      ...prev,
      x: drag.tx + (event.clientX - drag.x),
      y: drag.ty + (event.clientY - drag.y),
    }));
  };

  const endDrag = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!dragRef.current) return;
    (event.currentTarget as SVGSVGElement).releasePointerCapture?.(event.pointerId);
    dragRef.current = null;
    setDragging(false);
  };

  const countEntries = Object.entries(graph?.counts || {}).filter(([, value]) => typeof value === "number");

  const renderRelated = (label: string, testid: string, list?: PlanningGraphNode[] | null) => {
    const items = list || [];
    if (items.length === 0) return null;
    return (
      <div data-testid={testid} className="space-y-1">
        <h5 className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          {label} · {items.length}
        </h5>
        <div className="space-y-1">
          {items.map((item, index) => (
            <button
              key={item.id || item.node_key || `${label}-${index}`}
              type="button"
              className="block w-full truncate rounded border border-border bg-card px-2 py-1 text-left text-[11px] hover:bg-accent hover:text-accent-foreground"
              onClick={() => setSelectedId(item.id || item.node_key || null)}
            >
              {truncate(item.title || item.node_key || item.id, 40)}
            </button>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div
      data-testid="plan-map"
      className={cn(
        "flex flex-col gap-3",
        isExpanded
          ? "fixed inset-0 z-50 bg-background p-4 sm:p-6 overflow-hidden"
          : "h-full min-h-0 flex-1",
        className
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 className="font-display text-sm font-semibold tracking-tight">Plan Map</h3>
          {graph && (
            <Badge variant="outline" className="text-[10px]">
              {nodes.length} nodes · {edges.length} edges
            </Badge>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            data-testid="plan-map-sync"
            disabled={!projectId || syncMutation.isPending}
            onClick={() => syncMutation.mutate()}
            title="Sync planning graph"
          >
            {syncMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            Sync
          </Button>
          <Button size="icon" variant="outline" data-testid="plan-map-zoom-out" title="Zoom out" onClick={() => zoomBy(0.9)}>
            <Minus className="h-3.5 w-3.5" />
          </Button>
          <Button size="icon" variant="outline" data-testid="plan-map-zoom-in" title="Zoom in" onClick={() => zoomBy(1.1)}>
            <Plus className="h-3.5 w-3.5" />
          </Button>
          <Button size="icon" variant="outline" data-testid="plan-map-fit" title="Fit to view" onClick={fitView}>
            <Maximize2 className="h-3.5 w-3.5" />
          </Button>
          <Button
            size="icon"
            variant={isExpanded ? "secondary" : "outline"}
            data-testid="plan-map-expand-workspace"
            title={isExpanded ? "Exit expanded mode (Esc)" : "Expand workspace"}
            onClick={() => {
              setIsExpanded(!isExpanded);
              setTimeout(fitView, 50);
            }}
          >
            {isExpanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </Button>
          <Button size="icon" variant="outline" data-testid="plan-map-expand-all" title="Expand all sections" onClick={() => setCollapsed([])}>
            <ChevronsUpDown className="h-3.5 w-3.5" />
          </Button>
          <Button size="icon" variant="outline" data-testid="plan-map-collapse-all" title="Collapse all sections" onClick={collapseAll}>
            <ChevronsDownUp className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <div data-testid="plan-map-legend" className="flex flex-wrap items-center gap-1.5">
          {countEntries.length > 0 ? (
            countEntries.map(([key, value]) => (
              <Badge key={key} variant="outline" className="gap-1 text-[10px]">
                <span className={cn("h-2 w-2 rounded-full", statusDotClass(key))} />
                {key} {value}
              </Badge>
            ))
          ) : (
            <span className="text-[10px] text-muted-foreground">No counts available.</span>
          )}
        </div>
        {sections.filter((section) => !!section.key).length > 0 && <Separator orientation="vertical" className="mx-1 h-4" />}
        <div className="flex flex-wrap items-center gap-1">
          {sections
            .filter((section) => !!section.key)
            .map((section) => {
              const key = section.key as string;
              const isCollapsed = collapsed.includes(key);
              return (
                <Button
                  key={key}
                  size="sm"
                  variant={isCollapsed ? "secondary" : "outline"}
                  className="h-6 gap-1 px-2 text-[10px]"
                  data-testid={`plan-map-section-collapse-${key}`}
                  onClick={() => toggleCollapse(key)}
                >
                  {isCollapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                  {truncate(section.title || key, 24)}
                </Button>
              );
            })}
        </div>
      </div>

      {graphQuery.isLoading && <LoadingSpinner label="Loading plan map…" />}
      {graphQuery.isError && <ErrorNote message={apiError(graphQuery.error)} />}

      {!graphQuery.isLoading && !graphQuery.isError && (
        <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row">
          <div
            ref={containerRef}
            className="relative min-h-[420px] flex-1 overflow-hidden rounded-md border border-border bg-background"
          >
            <svg
              ref={svgRef}
              data-testid="plan-map-canvas"
              className={cn(
                "h-full min-h-[420px] w-full touch-none select-none",
                dragging ? "cursor-grabbing" : "cursor-grab"
              )}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={endDrag}
              onPointerLeave={endDrag}
            >
              <g transform={`translate(${view.x}, ${view.y}) scale(${view.scale})`}>
                {layout.visibleEdges.map((edge, index) => {
                  const from = layout.positions[edge.source_node_id as string];
                  const to = layout.positions[edge.target_node_id as string];
                  if (!from || !to) return null;
                  const x1 = from.x + NODE_WIDTH;
                  const y1 = from.y + NODE_HEIGHT / 2;
                  const x2 = to.x;
                  const y2 = to.y + NODE_HEIGHT / 2;
                  const mid = (x1 + x2) / 2;
                  return (
                    <path
                      key={edge.id || `${edge.source_node_id}-${edge.target_node_id}-${index}`}
                      d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                      className="fill-none stroke-border"
                      strokeWidth={1.2}
                    />
                  );
                })}
                {layout.visibleNodeIds.map((id) => {
                  const entry = entryById.get(id);
                  const position = layout.positions[id];
                  if (!entry || !position) return null;
                  const node = entry.node;
                  const isSelected = selectedId === id;
                  const testKey = node.node_key || node.id || id;
                  return (
                    <g
                      key={id}
                      data-testid={`plan-map-node-${testKey}`}
                      data-status={node.status || undefined}
                      transform={`translate(${position.x}, ${position.y})`}
                      className="cursor-pointer"
                      onClick={() => setSelectedId(id)}
                    >
                      <rect
                        width={NODE_WIDTH}
                        height={NODE_HEIGHT}
                        rx={8}
                        className={statusClass(node.status)}
                        strokeWidth={isSelected ? 2.5 : 1.2}
                      />
                      <text x={12} y={22} className="fill-foreground text-[11px] font-medium">
                        {truncate(node.title || node.node_key || node.id, 24)}
                      </text>
                      <text x={12} y={40} className="fill-muted-foreground text-[9px]">
                        {truncate(node.status, 30) || "n/a"}
                      </text>
                    </g>
                  );
                })}
              </g>
            </svg>

            {isGraphEmpty && (
              <div
                data-testid="plan-map-empty"
                className="absolute inset-0 flex flex-col items-center justify-center gap-1 px-6 text-center"
              >
                <p className="text-sm font-medium text-foreground">No planning graph yet</p>
                <p className="text-xs text-muted-foreground">
                  Sync this conversation to build the structured plan graph.
                </p>
              </div>
            )}
          </div>

          {selectedId && (
            <div
              data-testid="plan-map-detail"
              className="shrink-0 space-y-3 rounded-md border border-border bg-card p-3 lg:w-[340px] overflow-y-auto max-h-[70vh] lg:max-h-full"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h4 className="break-words font-display text-sm font-semibold">
                    {nText(detailNode?.title || detailNode?.node_key)}
                  </h4>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <span data-testid="plan-map-detail-status">
                      <Badge variant={statusBadgeVariant(detailNode?.status)} className="text-[10px]">
                        {nText(detailNode?.status)}
                      </Badge>
                    </span>
                    {detailNode?.type && (
                      <Badge variant="outline" className="text-[10px]">
                        {detailNode.type}
                      </Badge>
                    )}
                    {detailNode?.category && (
                      <span className="font-mono text-[9px] text-muted-foreground">{detailNode.category}</span>
                    )}
                  </div>
                </div>
                <Button size="icon" variant="ghost" className="h-6 w-6 shrink-0" title="Close detail" onClick={() => setSelectedId(null)}>
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>

              <p data-testid="plan-map-detail-content" className="whitespace-pre-wrap break-words text-xs text-foreground">
                {nText(detailNode?.content)}
              </p>

              {detailQuery.isLoading && <LoadingSpinner label="Loading node…" />}
              {detailQuery.isError && <ErrorNote message={apiError(detailQuery.error)} />}

              {(detailNode?.review_required || detailQuery.data?.node?.review_required) && (
                <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2">
                  <div className="flex items-center gap-1.5 text-[11px] text-amber-500">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    Review required
                  </div>
                  {detailNode?.review_reason && (
                    <p className="mt-0.5 text-[10px] text-muted-foreground">{detailNode.review_reason}</p>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    className="mt-1.5 h-6 text-[10px]"
                    data-testid="plan-map-clear-review"
                    disabled={clearReview.isPending}
                    onClick={() => detailNode?.id && clearReview.mutate(detailNode.id)}
                  >
                    Clear review
                  </Button>
                </div>
              )}

              <Separator />

              {detailNode?.decision_id && (
                <div className="space-y-1.5">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-[11px]"
                    data-testid="plan-map-analyze-impact"
                    disabled={impact.isPending}
                    onClick={() => detailNode?.id && impact.mutate(detailNode.id)}
                  >
                    {impact.isPending ? (
                      <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <AlertTriangle className="mr-1.5 h-3.5 w-3.5" />
                    )}
                    Analyze impact
                  </Button>
                  {impact.data && (
                    <div
                      data-testid="plan-map-impact"
                      className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-[11px]"
                    >
                      {impact.data.message && (
                        <p data-testid="plan-map-impact-message" className="text-foreground">
                          {nText(impact.data.message)}
                        </p>
                      )}
                      <h5 className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                        Affected areas · {(impact.data.affected_areas || []).length}
                      </h5>
                      {(impact.data.affected_areas || []).length > 0 ? (
                        <ul className="space-y-0.5">
                          {(impact.data.affected_areas || []).map((area, index) => (
                            <li
                              key={area.node_id || area.section_key || `impact-${index}`}
                              className="break-words"
                            >
                              <span className="font-medium text-foreground">
                                {nText(area.title || area.section_key)}
                              </span>
                              {area.reason && (
                                <span className="text-muted-foreground"> · {nText(area.reason)}</span>
                              )}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="text-muted-foreground">No affected areas reported.</p>
                      )}
                    </div>
                  )}
                </div>
              )}

              <div className="space-y-1 text-[10px]">
                <h5 className="font-mono uppercase tracking-wider text-muted-foreground">Source</h5>
                <div className="grid grid-cols-[88px_1fr] gap-x-2 gap-y-0.5 font-mono text-muted-foreground">
                  <span>decision</span>
                  <span className="break-all text-foreground">{nText(detailNode?.decision_id || detailQuery.data?.decision?.id)}</span>
                  <span>topic</span>
                  <span className="break-all text-foreground">{nText(detailNode?.topic_key)}</span>
                  <span>section</span>
                  <span className="break-all text-foreground">{nText(detailNode?.section_key)}</span>
                  <span>conversation</span>
                  <span className="break-all text-foreground">{nText(detailNode?.conversation_id || conversationId)}</span>
                  <span>message</span>
                  <span className="break-all text-foreground">{shortId(detailNode?.source_message_id) || "n/a"}</span>
                </div>
              </div>

              {provenanceExcerpt(detailQuery.data?.provenance || detailNode?.provenance) && (
                <div className="space-y-1">
                  <h5 className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Provenance</h5>
                  <p className="break-all rounded bg-muted/40 p-1.5 font-mono text-[9px] text-muted-foreground">
                    {provenanceExcerpt(detailQuery.data?.provenance || detailNode?.provenance)}
                  </p>
                </div>
              )}

              {renderRelated("Depends on", "plan-map-detail-dependencies", detailQuery.data?.dependencies)}
              {renderRelated("Dependents", "plan-map-detail-dependents", detailQuery.data?.dependents)}
              {renderRelated("Affected", "plan-map-detail-affected", detailQuery.data?.affected)}

              {(detailQuery.data?.history || []).length > 0 && (
                <div data-testid="plan-map-detail-history" className="space-y-1">
                  <h5 className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">History</h5>
                  <div className="space-y-1">
                    {(detailQuery.data?.history || []).map((entry, index) => (
                      <div key={`${entry.decision_id || "h"}-${index}`} className="rounded border border-border bg-muted/20 p-1.5 text-[10px]">
                        <div className="flex items-center gap-1.5">
                          {entry.status && (
                            <Badge variant={statusBadgeVariant(entry.status)} className="px-1 py-0 text-[9px]">
                              {entry.status}
                            </Badge>
                          )}
                          {entry.superseded_by_id && (
                            <span className="font-mono text-[9px] text-muted-foreground">
                              → {shortId(entry.superseded_by_id)}
                            </span>
                          )}
                        </div>
                        {entry.value && <p className="mt-0.5 break-words">{entry.value}</p>}
                        {entry.created_at && (
                          <p className="font-mono text-[9px] text-muted-foreground">{entry.created_at}</p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default PlanMapView;
