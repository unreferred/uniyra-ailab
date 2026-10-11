import React, { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Clock, EyeOff } from "lucide-react";
import { useTrace } from "@/lib/hooks";
import { TraceSpan } from "@/lib/types";
import { apiError } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { FinalizationDiagnostics } from "@/components/workspace/FinalizationDiagnostics";
import { fmtDate, fmtLatency } from "@/lib/format";
import { KnowledgeProvenance } from "@/components/workspace/KnowledgeProvenance";

function safeJson(value: any): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

const spanFailed = (status?: string | null) =>
  status === "error" || status === "failed" || status === "failure";

const KNOWLEDGE_SPAN_TYPES = new Set(["KNOWLEDGE_QUERY", "KNOWLEDGE_RETRIEVAL", "KNOWLEDGE_CONTEXT_INJECTION"]);

export const TraceDrawer: React.FC<{
  runId: string | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}> = ({ runId, open, onOpenChange }) => {
  const { data, isLoading, isError, error } = useTrace(open ? runId : null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const spans = useMemo<TraceSpan[]>(() => {
    const list = [...(data?.spans || [])];
    list.sort(
      (a, b) =>
        (a.ordering ?? 0) - (b.ordering ?? 0) ||
        new Date(a.started_at).getTime() - new Date(b.started_at).getTime()
    );
    return list;
  }, [data]);

  const trace = (data?.trace || {}) as Record<string, any>;
  const available = data ? data.available !== false : true;
  const toolCalls = data?.tool_calls || [];
  const runs = data?.runs || [];
  const primaryRun = runs.find((r) => r.id === runId) || null;
  const relatedRuns = runs.filter((r) => r.id !== runId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto scrollbar-thin sm:max-w-2xl" data-testid="trace-drawer">
        <DialogHeader>
          <DialogTitle className="font-display">Trace</DialogTitle>
          <DialogDescription className="break-all">
            {runId ? `run ${runId}` : "Run trace"}
          </DialogDescription>
        </DialogHeader>

        <div className="mt-4 space-y-4">
          {isLoading && <LoadingSpinner label="Loading trace…" />}
          {isError && <ErrorNote message={apiError(error)} />}
          {!isLoading && data && !available && (
            <ErrorNote message={data.message || "Legacy run — trace unavailable"} />
          )}

          {!isLoading && available && data && (
            <>
              {data.blind && (
                <div className="flex items-center gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-600">
                  <EyeOff className="h-3.5 w-3.5" /> Identity hidden until reveal
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2">
                {trace.status && (
                  <Badge variant={spanFailed(trace.status) ? "destructive" : "secondary"}>{trace.status}</Badge>
                )}
                {trace.trace_type && <Badge variant="outline">{trace.trace_type}</Badge>}
                {typeof trace.span_count === "number" && (
                  <Badge variant="outline">{trace.span_count} spans</Badge>
                )}
                {trace.total_duration_ms != null && (
                  <Badge variant="outline">{fmtLatency(trace.total_duration_ms)}</Badge>
                )}
              </div>

              {trace.started_at && (
                <div className="text-[11px] text-muted-foreground">
                  Started {fmtDate(trace.started_at)}
                  {trace.completed_at ? ` · completed ${fmtDate(trace.completed_at)}` : ""}
                </div>
              )}

              <div>
                <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Timeline</h4>
                {spans.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No spans recorded.</p>
                ) : (
                  <ol className="space-y-1.5">
                    {spans.map((s) => {
                      const isOpen = !!expanded[s.id];
                      return (
                        <li key={s.id} className="rounded-md border border-border bg-card">
                          <button
                            type="button"
                            data-testid={`trace-span-${s.id}`}
                            className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs"
                            onClick={() => setExpanded((p) => ({ ...p, [s.id]: !p[s.id] }))}
                          >
                            {isOpen ? (
                              <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                            ) : (
                              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                            )}
                            <Clock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                            <span className="min-w-0 flex-1 truncate font-medium">{s.name || s.span_type}</span>
                            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                              {fmtLatency(s.duration_ms)}
                            </span>
                            <Badge
                              variant={spanFailed(s.status) ? "destructive" : "secondary"}
                              className="shrink-0 px-1 py-0 text-[9px]"
                            >
                              {s.status}
                            </Badge>
                          </button>
                          {isOpen && (
                            <div className="space-y-2 border-t border-border px-3 py-2 text-[11px]">
                              <div className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[10px] text-muted-foreground">
                                <span>type: {s.span_type}</span>
                                <span>started: {s.started_at ? fmtDate(s.started_at) : "—"}</span>
                                {s.run_id && <span className="col-span-2 break-all">run: {s.run_id}</span>}
                              </div>
                              {s.structured_error && (
                                <ErrorNote
                                  message={
                                    s.structured_error.message || s.structured_error.code || "Span error"
                                  }
                                />
                              )}
                              {KNOWLEDGE_SPAN_TYPES.has(s.span_type) && <KnowledgeProvenance metadata={s.metadata} testId={"trace-drawer-knowledge-provenance-" + s.id} />}
                               {!KNOWLEDGE_SPAN_TYPES.has(s.span_type) && s.metadata && Object.keys(s.metadata).length > 0 && (
                                <pre className="max-h-48 overflow-auto rounded bg-muted/50 p-2 text-[10px] scrollbar-thin">
                                  {safeJson(s.metadata)}
                                </pre>
                              )}
                            </div>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                )}
              </div>

              {toolCalls.length > 0 && (
                <div>
                  <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Tool calls</h4>
                  <ul className="space-y-1">
                    {toolCalls.map((c) => (
                      <li
                        key={c.id}
                        className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-1.5 text-xs"
                      >
                        <span className="min-w-0 flex-1 truncate font-medium">{c.tool_name || c.tool_id || "tool"}</span>
                        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                          {fmtLatency(c.duration_ms)}
                        </span>
                        <Badge
                          variant={spanFailed(c.execution_status) ? "destructive" : "secondary"}
                          className="shrink-0 px-1 py-0 text-[9px]"
                        >
                          {c.execution_status || c.validation_status || "unknown"}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {primaryRun && <FinalizationDiagnostics run={primaryRun} />}

              {relatedRuns.length > 0 && (
                <div>
                  <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Related runs</h4>
                  <ul className="space-y-2">
                    {relatedRuns.map((r) => (
                      <li key={r.id} className="rounded-md border border-border px-3 py-1.5 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <span className="min-w-0 flex-1 truncate font-mono text-[10px]">{r.id}</span>
                          <span className="shrink-0 text-muted-foreground">{r.model_name || ""}</span>
                          <Badge variant={spanFailed(r.status) ? "destructive" : "secondary"} className="shrink-0 px-1 py-0 text-[9px]">
                            {r.status}
                          </Badge>
                        </div>
                        <FinalizationDiagnostics run={r} className="mt-2" />
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};
