import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronRight, Clock, ExternalLink, EyeOff, GitCompare, Layers } from "lucide-react";
import { API_BASE, api, apiError } from "@/lib/api";
import { useTrace } from "@/lib/hooks";
import { ObsPoint, Observability, TraceSpan } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState, ErrorNote, LoadingSpinner, SectionHeader } from "@/components/common/Common";
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

const spanFailed = (status?: string | null) => status === "error" || status === "failed" || status === "failure";

const KNOWLEDGE_SPAN_TYPES = new Set(["KNOWLEDGE_QUERY", "KNOWLEDGE_RETRIEVAL", "KNOWLEDGE_CONTEXT_INJECTION"]);
const SANDBOX_SPAN_TYPES = new Set(["SANDBOX_CREATE", "CODE_EXECUTION", "SANDBOX_OUTPUT", "ARTIFACT_CAPTURE"]);

const SECRET_KEY_RE = /(api[_-]?key|secret|token|password|authorization|cookie|credential)/i;

function safeMetadataValue(value: any, depth = 0): any {
  if (value == null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
  if (depth > 3) return "…";
  if (Array.isArray(value)) return value.slice(0, 20).map((v) => safeMetadataValue(v, depth + 1));
  if (typeof value === "object") {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(value)) {
      if (SECRET_KEY_RE.test(k)) {
        out[k] = "[redacted]";
        continue;
      }
      out[k] = safeMetadataValue(v, depth + 1);
    }
    return out;
  }
  return String(value);
}

function safeMetadata(metadata: Record<string, any> | null | undefined): Record<string, any> {
  if (!metadata || typeof metadata !== "object") return {};
  return safeMetadataValue(metadata) as Record<string, any>;
}

function fileNameOf(f: any): string | null {
  if (typeof f === "string") {
    const base = f.split("/").pop() || f;
    return base || null;
  }
  if (f && typeof f === "object") {
    const raw = f.filename || f.name || f.path;
    if (typeof raw === "string" && raw) {
      const base = raw.split("/").pop() || raw;
      return base || null;
    }
  }
  return null;
}

const RunPicker: React.FC<{ value: string; onChange: (v: string) => void }> = ({ value, onChange }) => {
  const { data, isLoading } = useQuery<Observability>({
    queryKey: ["observability", "trace-run-picker"],
    queryFn: async () => (await api.get("/observability")).data,
  });
  const options = useMemo<ObsPoint[]>(() => {
    const seen = new Set<string>();
    const list: ObsPoint[] = [];
    [...(data?.series || [])]
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
      .forEach((p) => {
        if (!p.run_id || seen.has(p.run_id)) return;
        seen.add(p.run_id);
        list.push(p);
      });
    return list.slice(0, 100);
  }, [data]);

  return (
    <Select value={value || ""} onValueChange={onChange}>
      <SelectTrigger data-testid="trace-run-picker"><SelectValue placeholder={isLoading ? "Loading runs…" : "Select a run…"} /></SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o.run_id as string} value={o.run_id as string}>
            {o.model_name} · {String(o.run_id).slice(0, 8)} · {fmtDate(o.created_at)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
};

export default function TraceViewer() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run") || "";
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const setRun = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("run", value); else next.delete("run");
    setSearchParams(next, { replace: true });
  };

  const { data, isLoading, isError, error } = useTrace(runId || null);

  const spans = useMemo<TraceSpan[]>(() => {
    const list = [...(data?.spans || [])];
    list.sort(
      (a, b) => (a.ordering ?? 0) - (b.ordering ?? 0) || new Date(a.started_at).getTime() - new Date(b.started_at).getTime()
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
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="trace-viewer-page">
      <SectionHeader
        title="Trace Viewer"
        caption="Chronological spans for a run, with tool calls and related runs."
        action={
          runId ? (
            <div className="flex items-center gap-2">
              <Link to={`/context-lab?run=${runId}`} className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline" data-testid="trace-context-link">
                View context <ExternalLink className="h-3 w-3" />
              </Link>
              <Link to={`/run-compare?run_a=${runId}`} className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline" data-testid="trace-compare-link">
                Compare <GitCompare className="h-3 w-3" />
              </Link>
            </div>
          ) : undefined
        }
      />

      <Card className="flex flex-col gap-2 p-4 sm:flex-row sm:items-center">
        <div className="flex-1">
          <RunPicker value={runId} onChange={setRun} />
        </div>
        {runId && <div className="break-all font-mono text-[11px] text-muted-foreground">run {runId}</div>}
      </Card>

      {!runId ? (
        <EmptyState icon={<Layers className="h-6 w-6" />} title="Pick a run" description="Select a run to inspect its trace." testId="trace-viewer-empty" />
      ) : isLoading ? (
        <LoadingSpinner label="Loading trace…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : data && !available ? (
        <ErrorNote message={data.message || "Legacy run — trace unavailable"} />
      ) : (
        <div className="space-y-4">
          {data?.blind && (
            <div className="flex items-center gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-600" data-testid="trace-blind-badge">
              <EyeOff className="h-3.5 w-3.5" /> Identity hidden until reveal
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {trace.status && <Badge variant={spanFailed(trace.status) ? "destructive" : "secondary"}>{trace.status}</Badge>}
            {trace.trace_type && <Badge variant="outline">{trace.trace_type}</Badge>}
            {typeof trace.span_count === "number" && <Badge variant="outline">{trace.span_count} spans</Badge>}
            {trace.total_duration_ms != null && <Badge variant="outline">{fmtLatency(trace.total_duration_ms)}</Badge>}
          </div>

          {trace.started_at && (
            <div className="text-[11px] text-muted-foreground">
              Started {fmtDate(trace.started_at)}
              {trace.completed_at ? ` · completed ${fmtDate(trace.completed_at)}` : ""}
            </div>
          )}

          <Card className="p-4" data-testid="trace-timeline">
            <h3 className="mb-3 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Timeline</h3>
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
                        {isOpen ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                        <Clock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate font-medium">{s.name || s.span_type}</span>
                        {(KNOWLEDGE_SPAN_TYPES.has(s.span_type) || SANDBOX_SPAN_TYPES.has(s.span_type)) && (
                          <Badge variant="outline" className="shrink-0 px-1 py-0 text-[9px]" data-testid={`trace-span-kind-${s.id}`}>{s.span_type}</Badge>
                        )}
                        <span className="hidden shrink-0 font-mono text-[10px] text-muted-foreground sm:inline">{s.started_at ? fmtDate(s.started_at) : "—"}</span>
                        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{fmtLatency(s.duration_ms)}</span>
                        <Badge variant={spanFailed(s.status) ? "destructive" : "secondary"} className="shrink-0 px-1 py-0 text-[9px]">{s.status}</Badge>
                      </button>
                      {isOpen && (
                        <div className="space-y-2 border-t border-border px-3 py-2 text-[11px]">
                          <div className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[10px] text-muted-foreground">
                            <span>type: {s.span_type}</span>
                            <span>started: {s.started_at ? fmtDate(s.started_at) : "—"}</span>
                            <span>completed: {s.completed_at ? fmtDate(s.completed_at) : "—"}</span>
                            {s.run_id && <span className="col-span-2 break-all">run: {s.run_id}</span>}
                          </div>
                          {s.structured_error && (
                            <ErrorNote message={s.structured_error.message || s.structured_error.code || "Span error"} />
                          )}
                          {KNOWLEDGE_SPAN_TYPES.has(s.span_type) && s.metadata && (
                            <div className="rounded bg-muted/50 p-2" data-testid={`trace-knowledge-${s.id}`}>
                              <div className="font-mono text-[10px] text-muted-foreground">
                                {[
                                  s.metadata.result_count != null ? `${s.metadata.result_count} chunks` : null,
                                  s.metadata.top_k != null ? `top_k ${s.metadata.top_k}` : null,
                                  s.metadata.duration_ms != null ? `${s.metadata.duration_ms} ms` : null,
                                ].filter(Boolean).join(" · ") || "knowledge span"}
                              </div>
                              {Array.isArray((s.metadata as any).collection_ids) && (s.metadata as any).collection_ids.length > 0 && (
                                <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                                  Collections: {(s.metadata as any).collection_ids.join(", ")}
                                </div>
                              )}
                              {Array.isArray((s.metadata as any).sources) && (s.metadata as any).sources.length > 0 && (
                                <ul className="mt-1 space-y-0.5">
                                  {(s.metadata as any).sources.slice(0, 10).map((sr: any, i: number) => (
                                    <li key={i} className="font-mono text-[10px]">
                                      {sr.display_name || sr.collection_name || sr.document_id || sr.chunk_id || `source ${i + 1}`}
                                      {sr.score != null && Number.isFinite(Number(sr.score)) ? ` · ${Number(sr.score).toFixed(4)}` : ""}
                                    </li>
                                  ))}
                                </ul>
                              )}
                              {Array.isArray((s.metadata as any).scores) && (s.metadata as any).scores.length > 0 && (
                                <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                                  Scores: {(s.metadata as any).scores.slice(0, 10).map((v: any) => (Number.isFinite(Number(v)) ? Number(v).toFixed(4) : "n/a")).join(", ")}
                                </div>
                              )}
                            </div>
                          )}
                          {(s.span_type === "CODE_EXECUTION" || (s.name || "").toLowerCase().includes("code")) &&
                            s.metadata &&
                            (((s.metadata as any).stdout ?? (s.metadata as any).stderr) != null ||
                              Array.isArray((s.metadata as any).files)) && (
                              <div className="rounded bg-muted/50 p-2" data-testid={`trace-code-output-${s.id}`}>
                                {(s.metadata as any).stdout != null && String((s.metadata as any).stdout) !== "" && (
                                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words p-1 text-[10px] scrollbar-thin">
                                    {String((s.metadata as any).stdout).slice(0, 4000)}
                                  </pre>
                                )}
                                {(s.metadata as any).stderr != null && String((s.metadata as any).stderr) !== "" && (
                                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words p-1 text-[10px] text-destructive scrollbar-thin">
                                    {String((s.metadata as any).stderr).slice(0, 4000)}
                                  </pre>
                                )}
                                {Array.isArray((s.metadata as any).files) && (s.metadata as any).files.length > 0 && (
                                  <ul className="mt-1 space-y-0.5">
                                    {(s.metadata as any).files.map((f: any, i: number) => {
                                      const name = fileNameOf(f);
                                      const execId = (s.metadata as any).execution_id || (s.metadata as any).executionId || null;
                                      return (
                                        <li key={i} className="font-mono text-[10px]">
                                          {execId && name ? (
                                            <a
                                              href={`${API_BASE}/code-interpreter/executions/${execId}/files/${encodeURIComponent(name)}`}
                                              className="text-primary hover:underline"
                                              data-testid={`trace-code-file-${s.id}-${i}`}
                                            >
                                              {name}
                                            </a>
                                          ) : (
                                            name || `file ${i + 1}`
                                          )}
                                        </li>
                                      );
                                    })}
                                  </ul>
                                )}
                              </div>
                            )}
                          {KNOWLEDGE_SPAN_TYPES.has(s.span_type) && <KnowledgeProvenance metadata={s.metadata} testId={"trace-knowledge-provenance-" + s.id} />}
                           {!KNOWLEDGE_SPAN_TYPES.has(s.span_type) && s.metadata && Object.keys(s.metadata).length > 0 && (
                            <pre className="max-h-48 overflow-auto rounded bg-muted/50 p-2 text-[10px] scrollbar-thin">{safeJson(safeMetadata(s.metadata))}</pre>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
          </Card>

          {toolCalls.length > 0 && (
            <Card className="p-4" data-testid="trace-tool-calls">
              <h3 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Tool calls</h3>
              <ul className="space-y-1">
                {toolCalls.map((c) => {
                  const output: any = (c as any).output;
                  const execId: string | null =
                    (output && typeof output === "object" && ((output as any).execution_id || (output as any).executionId)) || null;
                  const files: any[] = Array.isArray(output?.files) ? output.files : [];
                  const stdout = output && typeof output === "object" && typeof output.stdout === "string" ? output.stdout : null;
                  const stderr = output && typeof output === "object" && typeof output.stderr === "string" ? output.stderr : null;
                  const isCode = (c.tool_name || "") === "code_interpreter" || (c as any).tool_type === "code_interpreter";
                  return (
                    <li key={c.id} className="rounded-md border border-border px-3 py-1.5 text-xs" data-testid={`trace-tool-call-${c.id}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="min-w-0 flex-1 truncate font-medium">{c.tool_name || c.tool_id || "tool"}</span>
                        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{fmtLatency(c.duration_ms)}</span>
                        <Badge variant={spanFailed(c.execution_status) ? "destructive" : "secondary"} className="shrink-0 px-1 py-0 text-[9px]">
                          {c.execution_status || c.validation_status || "unknown"}
                        </Badge>
                      </div>
                      {isCode && (stdout || stderr || files.length > 0 || execId) && (
                        <div className="mt-1.5 rounded bg-muted/50 p-2" data-testid={`trace-code-tool-${c.id}`}>
                          {stdout && (
                            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-[10px] scrollbar-thin">{stdout.slice(0, 4000)}</pre>
                          )}
                          {stderr && (
                            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-[10px] text-destructive scrollbar-thin">{stderr.slice(0, 4000)}</pre>
                          )}
                          {files.length > 0 && (
                            <ul className="mt-1 space-y-0.5">
                              {files.map((f: any, i: number) => {
                                const name = fileNameOf(f);
                                return (
                                  <li key={i} className="font-mono text-[10px]">
                                    {execId && name ? (
                                      <a
                                        href={`${API_BASE}/code-interpreter/executions/${execId}/files/${encodeURIComponent(name)}`}
                                        className="text-primary hover:underline"
                                        data-testid={`trace-tool-file-${c.id}-${i}`}
                                      >
                                        {name}
                                      </a>
                                    ) : (
                                      name || `file ${i + 1}`
                                    )}
                                  </li>
                                );
                              })}
                            </ul>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}

          {primaryRun && <FinalizationDiagnostics run={primaryRun} />}

          {relatedRuns.length > 0 && (
            <Card className="p-4" data-testid="trace-related-runs">
              <h3 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Related runs</h3>
              <ul className="space-y-2">
                {relatedRuns.map((r) => (
                  <li key={r.id} className="rounded-md border border-border px-3 py-1.5 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 flex-1 truncate font-mono text-[10px]">{r.id}</span>
                      <span className="shrink-0 text-muted-foreground">{r.model_name || ""}</span>
                      <Badge variant={spanFailed(r.status) ? "destructive" : "secondary"} className="shrink-0 px-1 py-0 text-[9px]">{r.status}</Badge>
                    </div>
                    <FinalizationDiagnostics run={r} className="mt-2" />
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
