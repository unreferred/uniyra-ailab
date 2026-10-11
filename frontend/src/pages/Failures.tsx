import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  AlertTriangle, ArrowUpRight, Bug, CheckCircle2, ChevronLeft, ChevronRight,
  ExternalLink, GitCompare, History, Plus, ShieldAlert, Wrench,
} from "lucide-react";
import { api, apiError, analyzeDrift, getDriftAnalysis, materializeDriftFindings } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { useFailures, useFailureAggregate, useFailure, useDriftCatalog, useDriftAnalyses } from "@/lib/hooks";
import {
  Failure, DriftAnalysis, DriftAnalyzeRequest, DriftAnalysisSummary,
  DriftCanonicalService, DriftChangedFile, DriftContracts, DriftDuplicatedRule,
  DriftEnumContract, DriftEvidence, DriftFinding, DriftForbiddenPattern,
  DriftMaterializeResult, DriftPromptContext,
} from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState, ErrorNote, LoadingSpinner, Metric, SectionHeader } from "@/components/common/Common";
import { fmtCost, fmtDate, fmtLatency, fmtTokens } from "@/lib/format";
import { nText } from "@/components/experiment/format";

const PAGE_SIZE = 25;

const EXECUTION_CATEGORIES = [
  "AUTH", "RATE_LIMIT", "TIMEOUT", "PROVIDER", "MODEL_UNAVAILABLE",
  "VALIDATION", "NETWORK", "MALFORMED_RESPONSE", "INTERNAL", "CANCELLED",
];
// Additive Wave-3 execution categories: sandbox/code-interpreter outcomes and
// knowledge-pipeline errors surface here alongside the provider categories.
const CODE_CATEGORIES = [
  "CODE_TIMEOUT", "CODE_MEMORY_LIMIT", "CODE_RUNTIME_ERROR", "CODE_OUTPUT_LIMIT",
  "SANDBOX_CREATE_ERROR", "SANDBOX_POLICY_VIOLATION", "SANDBOX_UNAVAILABLE",
];
const KNOWLEDGE_CATEGORIES = [
  "KNOWLEDGE_INDEX_ERROR", "KNOWLEDGE_EMBEDDING_ERROR",
  "KNOWLEDGE_RETRIEVAL_ERROR", "KNOWLEDGE_EMPTY_RESULT",
];
const QUALITY_CATEGORIES = [
  "WRONG_ANSWER", "HALLUCINATION", "INSTRUCTION_VIOLATION", "INCOMPLETE",
  "CONTEXT_MISINTERPRETATION", "FORMAT_VIOLATION", "SECURITY_FAILURE",
  "TOOL_SELECTION_ERROR", "TOOL_EXECUTION_ERROR", "EXCESSIVE_COST",
  "EXCESSIVE_LATENCY", "OTHER",
];
const ALL_CATEGORIES = [...EXECUTION_CATEGORIES, ...CODE_CATEGORIES, ...KNOWLEDGE_CATEGORIES, ...QUALITY_CATEGORIES];

const CATEGORY_LABELS: Record<string, string> = {
  CODE_TIMEOUT: "Code timeout",
  CODE_MEMORY_LIMIT: "Code memory limit",
  CODE_RUNTIME_ERROR: "Code runtime error",
  CODE_OUTPUT_LIMIT: "Code output limit",
  SANDBOX_CREATE_ERROR: "Sandbox create error",
  SANDBOX_POLICY_VIOLATION: "Sandbox policy violation",
  SANDBOX_UNAVAILABLE: "Sandbox unavailable",
  KNOWLEDGE_INDEX_ERROR: "Knowledge index error",
  KNOWLEDGE_EMBEDDING_ERROR: "Knowledge embedding error",
  KNOWLEDGE_RETRIEVAL_ERROR: "Knowledge retrieval error",
  KNOWLEDGE_EMPTY_RESULT: "Knowledge empty result",
};

// Unknown category strings (e.g. newly added backend categories) render
// gracefully via the raw value — never crash, never blank.
const categoryLabel = (c?: string | null) => {
  if (!c) return "—";
  return CATEGORY_LABELS[c] || c;
};
const SOURCES = ["SYSTEM", "HUMAN", "JUDGE", "RULE"];
const STATUSES = ["OPEN", "CONFIRMED", "DISMISSED", "RESOLVED"];
const SEVERITIES = ["low", "medium", "high", "critical"];
const FAILURE_CLASSES = ["execution", "quality"];
const FAILURE_DOMAINS = ["RUNTIME", "QUALITY", "RESEARCH", "SECURITY"];

const classOf = (f?: any): string => {
  if (!f) return "quality";
  // The taxonomy failure_domain is authoritative when present: RUNTIME maps to
  // an execution failure, everything else (QUALITY/RESEARCH/SECURITY) is a
  // quality failure. This keeps instruction_following/tool_selection QUALITY.
  if (f.failure_domain) return f.failure_domain === "RUNTIME" ? "execution" : "quality";
  if (f.failure_class) return f.failure_class;
  if (EXECUTION_CATEGORIES.includes(f.category)) return "execution";
  if (CODE_CATEGORIES.includes(f.category) || KNOWLEDGE_CATEGORIES.includes(f.category)) return "execution";
  if (f.source === "SYSTEM" && EXECUTION_CATEGORIES.includes(f.category)) return "execution";
  return "quality";
};

const classVariant = (cls: string) => (cls === "execution" ? "destructive" : "secondary");

const domainOf = (f?: any): string | null => (f && f.failure_domain) || null;
const taxonomyCodeOf = (f?: any): string | null => (f && (f.taxonomy_code || f.taxonomy?.code)) || null;
const taxonomyLabelOf = (f?: any): string | null => (f && f.taxonomy?.label) || null;
const domainVariant = (domain?: string | null) => {
  if (domain === "RUNTIME") return "destructive";
  if (domain === "SECURITY") return "destructive";
  if (domain === "QUALITY") return "secondary";
  return "outline";
};

const severityVariant = (sev?: string | null) => {
  if (sev === "critical" || sev === "high") return "destructive";
  if (sev === "low") return "outline";
  return "secondary";
};

const statusVariant = (status?: string | null) => {
  if (status === "RESOLVED" || status === "CONFIRMED") return "secondary";
  if (status === "DISMISSED") return "outline";
  return "destructive";
};

const CountList: React.FC<{
  title: string;
  rows: { label: string; count: number }[];
  testId?: string;
  emptyLabel?: string;
}> = ({ title, rows, testId, emptyLabel = "None" }) => {
  const max = rows.reduce((m, r) => Math.max(m, r.count), 0) || 1;
  return (
    <Card className="p-4" data-testid={testId}>
      <h3 className="mb-3 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">{emptyLabel}</p>
      ) : (
        <ul className="space-y-2">
          {rows.slice(0, 8).map((r) => (
            <li key={r.label} className="space-y-1">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate font-medium">{r.label}</span>
                <span className="shrink-0 font-mono tabular text-muted-foreground">{r.count}</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-primary" style={{ width: `${(r.count / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
};

const Field: React.FC<{ label: string; value: React.ReactNode; mono?: boolean }> = ({ label, value, mono }) => (
  <div className="flex flex-col gap-0.5">
    <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <span className={mono ? "break-all font-mono text-xs" : "break-words text-sm"}>{value}</span>
  </div>
);

/* --------------------------------------------------------------- detail dialog */
const FailureDetailDialog: React.FC<{
  failureId: string | null;
  onOpenChange: (o: boolean) => void;
  onCreateForRun: (runId?: string) => void;
}> = ({ failureId, onOpenChange, onCreateForRun }) => {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data, isLoading, isError, error } = useFailure(failureId);

  const [category, setCategory] = useState("");
  const [subcategory, setSubcategory] = useState("");
  const [severity, setSeverity] = useState("medium");
  const [status, setStatus] = useState("OPEN");
  const [note, setNote] = useState("");

  useEffect(() => {
    if (data?.failure) {
      setCategory(data.failure.category || "");
      setSubcategory(data.failure.subcategory || "");
      setSeverity(data.failure.severity || "medium");
      setStatus(data.failure.status || "OPEN");
      setNote("");
    }
  }, [data]);

  const patch = useMutation({
    mutationFn: async () =>
      (
        await api.patch(`/failures/${failureId}`, {
          category: category || undefined,
          subcategory,
          severity,
          status,
          note,
        })
      ).data,
    onSuccess: () => {
      toast.success("Failure reclassified");
      qc.invalidateQueries({ queryKey: ["failures"] });
      qc.invalidateQueries({ queryKey: ["failure", failureId] });
      qc.invalidateQueries({ queryKey: ["failure-aggregate"] });
      setNote("");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const f = data?.failure;
  const run = data?.run;
  const trace = (data?.trace || null) as Record<string, any> | null;
  const evaluation = data?.evaluation;
  const toolCalls = data?.tool_calls || [];

  return (
    <Dialog open={failureId != null} onOpenChange={(o) => onOpenChange(o)}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl" data-testid="failure-detail-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">{f?.title || "Failure detail"}</DialogTitle>
          <DialogDescription className="break-all">
            {f ? `${f.id}${f.created_at ? ` · ${fmtDate(f.created_at)}` : ""}` : "Loading failure…"}
          </DialogDescription>
        </DialogHeader>

        {isLoading && <LoadingSpinner label="Loading failure…" />}
        {isError && <ErrorNote message={apiError(error)} />}

        {f && (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={classVariant(classOf(f))} className="text-[10px] uppercase">{classOf(f)}</Badge>
              <Badge variant={domainVariant(domainOf(f))} className="text-[10px]" data-testid="failure-domain-badge">{domainOf(f) || "n/a"}</Badge>
              <Badge variant="outline" className="text-[10px] font-mono" data-testid="failure-taxonomy-code" title={taxonomyLabelOf(f) || undefined}>{taxonomyCodeOf(f) || "n/a"}</Badge>
              <Badge variant="outline" className="text-[10px]" data-testid="failure-category-badge" title={f.category}>{categoryLabel(f.category)}</Badge>
              {f.subcategory && <Badge variant="outline" className="text-[10px] font-mono">{f.subcategory}</Badge>}
              <Badge variant="secondary" className="text-[10px]">{f.source}</Badge>
              <Badge variant={severityVariant(f.severity)} className="text-[10px]">{f.severity}</Badge>
              <Badge variant={statusVariant(f.status)} className="text-[10px]">{f.status}</Badge>
            </div>

            {f.description && <p className="text-sm text-muted-foreground">{f.description}</p>}

            <div className="grid grid-cols-2 gap-x-4 gap-y-3">
              <Field label="Run model" value={f.run_model_name || f.model_id || "—"} />
              <Field label="Confidence" value={f.confidence != null ? String(f.confidence) : "—"} />
              <Field label="Run ID" value={f.run_id || "—"} mono />
              <Field label="Trace ID" value={f.trace_id || "—"} mono />
              <Field label="Experiment" value={f.experiment_id || "—"} mono />
              <Field label="Tool" value={f.tool_id || "—"} mono />
              <Field label="Failure domain" value={domainOf(f) || "n/a"} />
              <Field label="Taxonomy" value={taxonomyCodeOf(f) ? (taxonomyLabelOf(f) ? `${taxonomyCodeOf(f)} — ${taxonomyLabelOf(f)}` : taxonomyCodeOf(f)!) : "n/a"} mono />
            </div>

            {(f.tags || []).length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {(f.tags || []).map((t) => <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>)}
              </div>
            )}

            {f.evidence && Object.keys(f.evidence).length > 0 && (
              <div>
                <h4 className="mb-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Evidence</h4>
                <pre className="max-h-48 overflow-auto rounded-md bg-muted/50 p-2 text-[10px] scrollbar-thin">{JSON.stringify(f.evidence, null, 2)}</pre>
              </div>
            )}

            {run && (
              <div>
                <h4 className="mb-2 flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                  <Bug className="h-3 w-3" /> Linked run
                </h4>
                <Card className="grid grid-cols-2 gap-x-4 gap-y-3 p-3 sm:grid-cols-3">
                  <Field label="Status" value={<Badge variant={run.status === "success" ? "secondary" : "destructive"} className="text-[10px]">{run.status}</Badge>} />
                  <Field label="Model" value={run.model_name || "—"} />
                  <Field label="Latency" value={fmtLatency(run.latency_ms)} />
                  <Field label="Input tokens" value={fmtTokens(run.input_tokens)} />
                  <Field label="Output tokens" value={fmtTokens(run.output_tokens)} />
                  <Field label="Cost" value={fmtCost(run.estimated_cost, run.cost_available)} />
                </Card>
              </div>
            )}

            <div>
              <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Trace</h4>
              {trace ? (
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    {trace.status && <Badge variant="outline" className="text-[10px]">{trace.status}</Badge>}
                    {typeof trace.span_count === "number" && <span className="text-muted-foreground">{trace.span_count} spans</span>}
                    {trace.total_duration_ms != null && <span className="font-mono text-muted-foreground">{fmtLatency(trace.total_duration_ms)}</span>}
                  </div>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">No trace linked to this failure.</p>
              )}
              <div className="mt-2 flex flex-wrap gap-2">
                {f.run_id && (
                  <Button size="sm" variant="outline" data-testid="failure-view-trace" onClick={() => navigate(`/trace?run=${f.run_id}`)}>
                    <ExternalLink className="mr-1.5 h-3.5 w-3.5" /> View trace
                  </Button>
                )}
                {f.run_id && data?.context_available && (
                  <Button size="sm" variant="outline" data-testid="failure-view-context" onClick={() => navigate(`/context-lab?run=${f.run_id}`)}>
                    <ExternalLink className="mr-1.5 h-3.5 w-3.5" /> View context
                  </Button>
                )}
                {f.run_id && (
                  <Button size="sm" variant="outline" data-testid="failure-compare-run" onClick={() => navigate(`/run-compare?run_a=${f.run_id}`)}>
                    <GitCompare className="mr-1.5 h-3.5 w-3.5" /> Compare run
                  </Button>
                )}
                {f.experiment_id && (
                  <Button size="sm" variant="outline" data-testid="failure-view-experiment" onClick={() => navigate(`/experiments/${f.experiment_id}`)}>
                    <ArrowUpRight className="mr-1.5 h-3.5 w-3.5" /> Experiment
                  </Button>
                )}
              </div>
            </div>

            {evaluation && (
              <div>
                <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Related evaluation</h4>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge variant="outline" className="text-[10px]">{evaluation.eval_type || "evaluation"}</Badge>
                  <Badge variant="secondary" className="text-[10px]">{evaluation.status || "—"}</Badge>
                  {evaluation.judge_model_name && <span className="text-muted-foreground">{evaluation.judge_model_name}</span>}
                </div>
                {evaluation.result?.summary && <p className="mt-1 text-xs text-muted-foreground">{evaluation.result.summary}</p>}
              </div>
            )}

            {toolCalls.length > 0 && (
              <div>
                <h4 className="mb-2 flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                  <Wrench className="h-3 w-3" /> Tool calls
                </h4>
                <ul className="space-y-1">
                  {toolCalls.map((c) => (
                    <li key={c.id} className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-1.5 text-xs">
                      <span className="min-w-0 flex-1 truncate font-medium">{c.tool_name || c.tool_id || "tool"}</span>
                      <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{fmtLatency(c.duration_ms)}</span>
                      <Badge variant={c.execution_status === "error" || c.execution_status === "failed" ? "destructive" : "secondary"} className="shrink-0 text-[9px]">
                        {c.execution_status || c.validation_status || "unknown"}
                      </Badge>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {(f.history || []).length > 0 && (
              <div>
                <h4 className="mb-2 flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                  <History className="h-3 w-3" /> History
                </h4>
                <ol className="space-y-2 border-l border-border pl-3">
                  {(f.history || []).map((h, i) => (
                    <li key={i} className="relative text-xs">
                      <span className="absolute -left-[17px] top-1 h-2 w-2 rounded-full bg-primary" />
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{h.action}</span>
                        <span className="text-muted-foreground">{h.actor || "system"}</span>
                        <span className="font-mono text-[10px] text-muted-foreground">{fmtDate(h.at)}</span>
                      </div>
                      <div className="mt-0.5 flex flex-wrap gap-1.5 text-[10px] text-muted-foreground">
                        {h.category && <span>{h.category}</span>}
                        {h.status && <span>· {h.status}</span>}
                        {h.severity && <span>· {h.severity}</span>}
                      </div>
                      {h.note && <p className="mt-0.5 text-[11px] italic text-muted-foreground">“{h.note}”</p>}
                    </li>
                  ))}
                </ol>
              </div>
            )}

            <Separator />

            <div data-testid="failure-reclassify">
              <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Reclassify</h4>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px] text-muted-foreground">Category</label>
                  <Select value={category} onValueChange={setCategory}>
                    <SelectTrigger data-testid="failure-reclassify-category"><SelectValue /></SelectTrigger>
                    <SelectContent>{ALL_CATEGORIES.map((c) => <SelectItem key={c} value={c}>{CATEGORY_LABELS[c] ? `${c} — ${CATEGORY_LABELS[c]}` : c}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-[11px] text-muted-foreground">Subcategory</label>
                  <Input data-testid="failure-reclassify-subcategory" value={subcategory} onChange={(e) => setSubcategory(e.target.value)} className="mt-0.5 h-9 font-mono text-xs" />
                </div>
                <div>
                  <label className="text-[11px] text-muted-foreground">Severity</label>
                  <Select value={severity} onValueChange={setSeverity}>
                    <SelectTrigger data-testid="failure-reclassify-severity"><SelectValue /></SelectTrigger>
                    <SelectContent>{SEVERITIES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-[11px] text-muted-foreground">Status</label>
                  <Select value={status} onValueChange={setStatus}>
                    <SelectTrigger data-testid="failure-reclassify-status"><SelectValue /></SelectTrigger>
                    <SelectContent>{STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div className="col-span-2">
                  <label className="text-[11px] text-muted-foreground">Note</label>
                  <Textarea data-testid="failure-reclassify-note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} className="mt-0.5 text-xs" placeholder="Reason for reclassification (kept in audit history)" />
                </div>
              </div>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                <Button size="sm" variant="outline" data-testid="failure-create-quality" onClick={() => onCreateForRun(f.run_id || undefined)}>
                  <Plus className="mr-1.5 h-3.5 w-3.5" /> Create quality failure
                </Button>
                <Button size="sm" data-testid="failure-reclassify-save" disabled={patch.isPending} onClick={() => patch.mutate()}>
                  {patch.isPending ? "Saving…" : "Save changes"}
                </Button>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};

/* --------------------------------------------------------------- create dialog */
const CreateFailureDialog: React.FC<{
  open: boolean;
  onOpenChange: (o: boolean) => void;
  defaultRunId?: string;
  projectId?: string | null;
}> = ({ open, onOpenChange, defaultRunId, projectId }) => {
  const qc = useQueryClient();
  const [runId, setRunId] = useState("");
  const [category, setCategory] = useState("WRONG_ANSWER");
  const [source, setSource] = useState("HUMAN");
  const [severity, setSeverity] = useState("medium");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");

  useEffect(() => {
    if (open) {
      setRunId(defaultRunId || "");
      setCategory("WRONG_ANSWER");
      setSource("HUMAN");
      setSeverity("medium");
      setTitle("");
      setDescription("");
      setTags("");
    }
  }, [open, defaultRunId]);

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post(
          "/failures",
          {
            run_id: runId.trim() || undefined,
            category,
            source,
            severity,
            title,
            description,
            tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
          },
          projectId && !runId.trim() ? { params: { project_id: projectId } } : undefined
        )
      ).data,
    onSuccess: () => {
      toast.success("Quality failure created");
      qc.invalidateQueries({ queryKey: ["failures"] });
      qc.invalidateQueries({ queryKey: ["failure-aggregate"] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="create-failure-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">Create quality failure</DialogTitle>
          <DialogDescription>Manually classify a run as a quality failure. Quality failures come from humans, judges or rules.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Run ID {projectId ? "" : "(required)"}</label>
            <Input data-testid="create-failure-run" value={runId} onChange={(e) => setRunId(e.target.value)} className="mt-1 font-mono text-xs" placeholder="run_…" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Category</label>
              <Select value={category} onValueChange={setCategory}>
                <SelectTrigger className="mt-1" data-testid="create-failure-category"><SelectValue /></SelectTrigger>
                <SelectContent>{QUALITY_CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Source</label>
              <Select value={source} onValueChange={setSource}>
                <SelectTrigger className="mt-1" data-testid="create-failure-source"><SelectValue /></SelectTrigger>
                <SelectContent>{["HUMAN", "JUDGE", "RULE"].map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Severity</label>
              <Select value={severity} onValueChange={setSeverity}>
                <SelectTrigger className="mt-1" data-testid="create-failure-severity"><SelectValue /></SelectTrigger>
                <SelectContent>{SEVERITIES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Tags (comma-separated)</label>
              <Input data-testid="create-failure-tags" value={tags} onChange={(e) => setTags(e.target.value)} className="mt-1 text-xs" />
            </div>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Title</label>
            <Input data-testid="create-failure-title" value={title} onChange={(e) => setTitle(e.target.value)} className="mt-1" placeholder="e.g. Hallucinated citation" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Description</label>
            <Textarea data-testid="create-failure-description" value={description} onChange={(e) => setDescription(e.target.value)} rows={3} className="mt-1 text-xs" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button data-testid="create-failure-submit" disabled={!title.trim() || create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? "Creating…" : "Create failure"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

/* --------------------------------------------------------------- overview tab */
const OverviewTab: React.FC<{ projectId: string | null; onOpenFailure: (id: string) => void }> = ({ projectId, onOpenFailure }) => {
  const { data, isLoading, isError, error } = useFailureAggregate(projectId);

  if (isLoading) return <LoadingSpinner label="Loading failure overview…" />;
  if (isError || !data) return <ErrorNote message={isError ? apiError(error) : "Failure overview unavailable."} />;

  const statusCount = (s: string) => data.by_status.find((x) => x.status === s)?.count ?? 0;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Card className="p-4" data-testid="failure-kpi-total">
          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Total</span>
          <div className="mt-2 font-display text-2xl font-semibold tabular">{data.total}</div>
        </Card>
        <Card className="border-destructive/40 p-4" data-testid="failure-kpi-execution">
          <span className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider text-destructive"><AlertTriangle className="h-3 w-3" /> Execution</span>
          <div className="mt-2 font-display text-2xl font-semibold tabular text-destructive">{data.execution_count}</div>
        </Card>
        <Card className="border-primary/40 p-4" data-testid="failure-kpi-quality">
          <span className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider text-primary"><ShieldAlert className="h-3 w-3" /> Quality</span>
          <div className="mt-2 font-display text-2xl font-semibold tabular text-primary">{data.quality_count}</div>
        </Card>
        <Card className="p-4" data-testid="failure-kpi-open">
          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Open</span>
          <div className="mt-2 font-display text-2xl font-semibold tabular">{statusCount("OPEN")}</div>
        </Card>
        <Card className="p-4" data-testid="failure-kpi-resolved">
          <span className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground"><CheckCircle2 className="h-3 w-3" /> Resolved</span>
          <div className="mt-2 font-display text-2xl font-semibold tabular">{statusCount("RESOLVED")}</div>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <CountList title="By category" testId="failure-by-category" rows={data.by_category.map((x) => ({ label: x.category, count: x.count }))} />
        <CountList title="By source" testId="failure-by-source" rows={data.by_source.map((x) => ({ label: x.source, count: x.count }))} />
        <CountList title="By status" testId="failure-by-status" rows={data.by_status.map((x) => ({ label: x.status, count: x.count }))} />
        <CountList title="By severity" testId="failure-by-severity" rows={data.by_severity.map((x) => ({ label: x.severity, count: x.count }))} />
        <CountList title="By model" testId="failure-by-model" rows={data.by_model.map((x) => ({ label: x.model, count: x.count }))} />
        <CountList title="By tool" testId="failure-by-tool" rows={data.by_tool.map((x) => ({ label: x.tool_id, count: x.count }))} />
      </div>

      <Card className="p-4" data-testid="failure-recent">
        <h3 className="mb-3 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Recent failures</h3>
        {data.recent.length === 0 ? (
          <p className="text-xs text-muted-foreground">No failures recorded.</p>
        ) : (
          <ul className="divide-y divide-border">
            {data.recent.map((f) => (
              <li key={f.id}>
                <button
                  type="button"
                  className="flex w-full items-center gap-3 py-2 text-left text-xs transition-colors hover:bg-accent/40"
                  onClick={() => onOpenFailure(f.id)}
                  data-testid="failure-recent-row"
                >
                  <Badge variant={classVariant(classOf(f))} className="shrink-0 text-[9px] uppercase">{classOf(f)}</Badge>
                  <span className="min-w-0 flex-1 truncate font-medium">{f.title}</span>
                  <span className="hidden shrink-0 text-muted-foreground sm:inline">{f.run_model_name || f.model_id || "—"}</span>
                  <Badge variant={severityVariant(f.severity)} className="shrink-0 text-[9px]">{f.severity}</Badge>
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{fmtDate(f.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
};

/* --------------------------------------------------------------- failures tab */
const FailuresTab: React.FC<{
  projectId: string | null;
  onOpenFailure: (id: string) => void;
  initialRunId: string;
}> = ({ projectId, onOpenFailure, initialRunId }) => {
  const [category, setCategory] = useState("all");
  const [source, setSource] = useState("all");
  const [status, setStatus] = useState("all");
  const [failureClass, setFailureClass] = useState("all");
  const [domain, setDomain] = useState("all");
  const [refField, setRefField] = useState<"run_id" | "trace_id">("run_id");
  const [refValue, setRefValue] = useState(initialRunId);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    setOffset(0);
  }, [projectId, category, source, status, failureClass, domain, refField, refValue]);

  const params = useMemo(() => {
    const p: Record<string, any> = { limit: PAGE_SIZE, offset };
    if (projectId) p.project_id = projectId;
    if (category !== "all") p.category = category;
    if (source !== "all") p.source = source;
    if (status !== "all") p.status = status;
    if (failureClass !== "all") p.failure_class = failureClass;
    if (domain !== "all") p.failure_domain = domain;
    if (refValue.trim()) p[refField] = refValue.trim();
    return p;
  }, [projectId, offset, category, source, status, failureClass, domain, refField, refValue]);

  const { data, isLoading, isError, error } = useFailures(params);

  const clearFilters = () => {
    setCategory("all"); setSource("all"); setStatus("all"); setFailureClass("all"); setDomain("all");
    setRefValue(""); setRefField("run_id");
  };

  const total = data?.total ?? 0;
  const items = data?.items ?? [];

  return (
    <div className="space-y-4">
      <Card className="grid gap-2 p-3 md:grid-cols-3 lg:grid-cols-4" data-testid="failure-filters">
        <Select value={failureClass} onValueChange={setFailureClass}>
          <SelectTrigger data-testid="failure-filter-class"><SelectValue placeholder="Class" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All classes</SelectItem>
            {FAILURE_CLASSES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={category} onValueChange={setCategory}>
          <SelectTrigger data-testid="failure-filter-category"><SelectValue placeholder="Category" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All categories</SelectItem>
            {ALL_CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={domain} onValueChange={setDomain}>
          <SelectTrigger data-testid="failure-filter-domain"><SelectValue placeholder="Domain" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All domains</SelectItem>
            {FAILURE_DOMAINS.map((d) => <SelectItem key={d} value={d}>{d}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={source} onValueChange={setSource}>
          <SelectTrigger data-testid="failure-filter-source"><SelectValue placeholder="Source" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All sources</SelectItem>
            {SOURCES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger data-testid="failure-filter-status"><SelectValue placeholder="Status" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
          </SelectContent>
        </Select>
        <div className="flex gap-2">
          <Select value={refField} onValueChange={(v) => setRefField(v as "run_id" | "trace_id")}>
            <SelectTrigger className="w-[110px]" data-testid="failure-filter-ref-field"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="run_id">Run ID</SelectItem>
              <SelectItem value="trace_id">Trace ID</SelectItem>
            </SelectContent>
          </Select>
          <Input
            data-testid="failure-filter-ref-value"
            value={refValue}
            onChange={(e) => setRefValue(e.target.value)}
            placeholder="ID…"
            className="h-9 font-mono text-xs"
          />
        </div>
        <Button size="sm" variant="outline" data-testid="failure-clear-filters" onClick={clearFilters}>Clear filters</Button>
      </Card>

      {isLoading ? (
        <LoadingSpinner label="Loading failures…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : items.length === 0 ? (
        <EmptyState icon={<AlertTriangle className="h-6 w-6" />} title="No failures" description="No failure records match the current filters." testId="failures-empty" />
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="px-3 py-2.5">Title</th>
                  <th className="px-3 py-2.5">Domain</th>
                  <th className="px-3 py-2.5">Taxonomy</th>
                  <th className="px-3 py-2.5">Category</th>
                  <th className="px-3 py-2.5">Class</th>
                  <th className="px-3 py-2.5">Source</th>
                  <th className="px-3 py-2.5">Severity</th>
                  <th className="px-3 py-2.5">Status</th>
                  <th className="px-3 py-2.5">Model</th>
                  <th className="px-3 py-2.5">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {items.map((f) => (
                  <tr
                    key={f.id}
                    className="cursor-pointer transition-colors hover:bg-accent/40"
                    onClick={() => onOpenFailure(f.id)}
                    data-testid={`failure-row-${f.id}`}
                  >
                    <td className="max-w-[260px] truncate px-3 py-2.5 font-medium">{f.title}</td>
                    <td className="px-3 py-2.5"><Badge variant={domainVariant(domainOf(f))} className="text-[9px]" data-testid={`failure-row-domain-${f.id}`}>{domainOf(f) || "n/a"}</Badge></td>
                    <td className="px-3 py-2.5 font-mono text-[10px] text-muted-foreground" data-testid={`failure-row-taxonomy-${f.id}`}>{taxonomyCodeOf(f) || "n/a"}</td>
                    <td className="px-3 py-2.5 text-xs text-muted-foreground" title={f.category}>{categoryLabel(f.category)}</td>
                    <td className="px-3 py-2.5"><Badge variant={classVariant(classOf(f))} className="text-[9px] uppercase">{classOf(f)}</Badge></td>
                    <td className="px-3 py-2.5 text-xs">{f.source}</td>
                    <td className="px-3 py-2.5"><Badge variant={severityVariant(f.severity)} className="text-[9px]">{f.severity}</Badge></td>
                    <td className="px-3 py-2.5"><Badge variant={statusVariant(f.status)} className="text-[9px]">{f.status}</Badge></td>
                    <td className="px-3 py-2.5 text-xs text-muted-foreground">{f.run_model_name || f.model_id || "—"}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 font-mono text-[10px] text-muted-foreground">{fmtDate(f.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between border-t border-border px-3 py-2 text-xs" data-testid="failure-pagination">
            <span className="text-muted-foreground">
              {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`} of {total}
            </span>
            <div className="flex items-center gap-1">
              <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))} data-testid="failure-prev">
                <ChevronLeft className="h-3.5 w-3.5" />
              </Button>
              <Button size="sm" variant="outline" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset((o) => o + PAGE_SIZE)} data-testid="failure-next">
                <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
};

/* --------------------------------------------------------------- drift tab */

const splitList = (s: string): string[] =>
  s.split(",").map((t) => t.trim()).filter(Boolean);

const evidenceText = (evidence?: DriftEvidence | Record<string, any> | null): string => {
  if (!evidence) return "n/a";
  const parts: string[] = [];
  if (evidence.path != null) parts.push(`path: ${evidence.path}`);
  if (evidence.pattern != null) parts.push(`pattern: ${evidence.pattern}`);
  if (evidence.value != null) {
    parts.push(`value: ${typeof evidence.value === "object" ? JSON.stringify(evidence.value) : evidence.value}`);
  }
  return parts.length ? parts.join(" · ") : "n/a";
};

// deterministic sources render slate; judge-derived sources render amber so a
// model/rubric judgement is never mistaken for an observable, rule-derived fact.
const sourceBadgeClass = (source?: string | null): string =>
  source === "judge_derived"
    ? "border-amber-300 bg-amber-100 text-amber-800"
    : "border-slate-300 bg-slate-100 text-slate-700";

const DEFAULT_DRIFT_SCOPES = ["all", "prompt", "design", "architecture", "logic"];

interface ChangedFileDraft {
  path: string;
  language: string;
  imports_added: string;
  components_added: string;
  services_added: string;
  hardcoded_colors: string;
  constants_added: string;
}
interface CanonicalServiceDraft { name: string; responsibility: string; paths: string }
interface ForbiddenPatternDraft { id: string; pattern: string; type: string; reason: string }
interface DuplicatedRuleDraft { id: string; backend_pattern: string; frontend_pattern: string }
interface EnumContractDraft { name: string; backend_values: string; frontend_values: string }

const emptyChangedFile = (): ChangedFileDraft => ({
  path: "", language: "", imports_added: "", components_added: "",
  services_added: "", hardcoded_colors: "", constants_added: "",
});
const emptyCanonicalService = (): CanonicalServiceDraft => ({ name: "", responsibility: "", paths: "" });
const emptyForbiddenPattern = (): ForbiddenPatternDraft => ({ id: "", pattern: "", type: "", reason: "" });
const emptyDuplicatedRule = (): DuplicatedRuleDraft => ({ id: "", backend_pattern: "", frontend_pattern: "" });
const emptyEnumContract = (): EnumContractDraft => ({ name: "", backend_values: "", frontend_values: "" });

const DriftTab: React.FC<{ projectId: string | null }> = ({ projectId }) => {
  const { data: catalog } = useDriftCatalog();
  const { data: history } = useDriftAnalyses(projectId);

  const [scope, setScope] = useState("all");
  const [changedFiles, setChangedFiles] = useState<ChangedFileDraft[]>([]);
  const [designTokens, setDesignTokens] = useState("");
  const [canonicalServices, setCanonicalServices] = useState<CanonicalServiceDraft[]>([]);
  const [forbiddenPatterns, setForbiddenPatterns] = useState<ForbiddenPatternDraft[]>([]);
  const [duplicatedRules, setDuplicatedRules] = useState<DuplicatedRuleDraft[]>([]);
  const [enumContracts, setEnumContracts] = useState<EnumContractDraft[]>([]);
  const [requiredInvariantCodes, setRequiredInvariantCodes] = useState("");
  const [requiredPhases, setRequiredPhases] = useState("");
  const [prohibitedScope, setProhibitedScope] = useState("");
  const [compiledInvariantsUsed, setCompiledInvariantsUsed] = useState("");
  const [outputText, setOutputText] = useState("");
  const [runId, setRunId] = useState("");
  const [rubricId, setRubricId] = useState("");
  const [judgeModelId, setJudgeModelId] = useState("");
  const [analysis, setAnalysis] = useState<DriftAnalysis | null>(null);
  const [materialized, setMaterialized] = useState<DriftMaterializeResult | null>(null);

  const scopes = catalog?.scopes && catalog.scopes.length > 0 ? catalog.scopes : DEFAULT_DRIFT_SCOPES;

  const analyze = useMutation({
    mutationFn: async (body: DriftAnalyzeRequest) => analyzeDrift(body),
    onSuccess: (data) => { setAnalysis(data); setMaterialized(null); toast.success("Drift analysis complete"); },
    onError: (e) => toast.error(apiError(e)),
  });

  const materialize = useMutation({
    mutationFn: async (id: string) => materializeDriftFindings(id, {}),
    onSuccess: (data) => { setMaterialized(data); toast.success("Findings materialized"); },
    onError: (e) => toast.error(apiError(e)),
  });

  const viewAnalysis = useMutation({
    mutationFn: async (id: string) => getDriftAnalysis(id),
    onSuccess: (data) => { setAnalysis(data); setMaterialized(null); },
    onError: (e) => toast.error(apiError(e)),
  });

  const updateDraft = <T,>(
    list: T[],
    setList: React.Dispatch<React.SetStateAction<T[]>>,
    index: number,
    patch: Partial<T>
  ) => setList(list.map((item, i) => (i === index ? { ...item, ...patch } : item)));

  const submit = () => {
    if (!projectId) return;
    const files: DriftChangedFile[] = changedFiles.filter((f) => f.path.trim()).map((f) => {
      const out: DriftChangedFile = { path: f.path.trim() };
      if (f.language.trim()) out.language = f.language.trim();
      const imports = splitList(f.imports_added); if (imports.length) out.imports_added = imports;
      const components = splitList(f.components_added); if (components.length) out.components_added = components;
      const services = splitList(f.services_added); if (services.length) out.services_added = services;
      const colors = splitList(f.hardcoded_colors); if (colors.length) out.hardcoded_colors = colors;
      const constants = splitList(f.constants_added); if (constants.length) out.constants_added = constants;
      return out;
    });

    const contracts: DriftContracts = {};
    const tokens = splitList(designTokens); if (tokens.length) contracts.design_tokens = tokens;
    const services = canonicalServices.filter((s) => s.name.trim()).map((s) => {
      const item: DriftCanonicalService = { name: s.name.trim() };
      if (s.responsibility.trim()) item.responsibility = s.responsibility.trim();
      const paths = splitList(s.paths); if (paths.length) item.paths = paths;
      return item;
    });
    if (services.length) contracts.canonical_services = services;
    const patterns = forbiddenPatterns.filter((p) => p.pattern.trim()).map((p) => {
      const item: DriftForbiddenPattern = { pattern: p.pattern.trim() };
      if (p.id.trim()) item.id = p.id.trim();
      if (p.type.trim()) item.type = p.type.trim();
      if (p.reason.trim()) item.reason = p.reason.trim();
      return item;
    });
    if (patterns.length) contracts.forbidden_patterns = patterns;
    const rules = duplicatedRules
      .filter((r) => r.backend_pattern.trim() || r.frontend_pattern.trim())
      .map((r) => {
        const item: DriftDuplicatedRule = {};
        if (r.id.trim()) item.id = r.id.trim();
        if (r.backend_pattern.trim()) item.backend_pattern = r.backend_pattern.trim();
        if (r.frontend_pattern.trim()) item.frontend_pattern = r.frontend_pattern.trim();
        return item;
      });
    if (rules.length) contracts.duplicated_rules = rules;
    const enums = enumContracts.filter((e) => e.name.trim()).map((e) => {
      const item: DriftEnumContract = { name: e.name.trim() };
      const backend = splitList(e.backend_values); if (backend.length) item.backend_values = backend;
      const frontend = splitList(e.frontend_values); if (frontend.length) item.frontend_values = frontend;
      return item;
    });
    if (enums.length) contracts.enums = enums;

    const promptContext: DriftPromptContext = {};
    const invariantCodes = splitList(requiredInvariantCodes); if (invariantCodes.length) promptContext.required_invariant_codes = invariantCodes;
    const phases = splitList(requiredPhases); if (phases.length) promptContext.required_phases = phases;
    if (prohibitedScope.trim()) promptContext.prohibited_scope = prohibitedScope.trim();
    const compiled = splitList(compiledInvariantsUsed); if (compiled.length) promptContext.compiled_invariants_used = compiled;
    if (outputText.trim()) promptContext.output_text = outputText.trim();

    const body: DriftAnalyzeRequest = { project_id: projectId, scope };
    if (files.length) body.changed_files = files;
    if (Object.keys(contracts).length) body.contracts = contracts;
    if (Object.keys(promptContext).length) body.prompt_context = promptContext;
    if (runId.trim()) body.run_id = runId.trim();
    if (rubricId.trim()) body.rubric_id = rubricId.trim();
    if (judgeModelId.trim()) body.judge_model_id = judgeModelId.trim();
    analyze.mutate(body);
  };

  const summary: DriftAnalysisSummary | null | undefined = analysis?.summary;
  const grouped = useMemo(() => {
    const findings = analysis?.findings ?? [];
    const map = new Map<string, DriftFinding[]>();
    findings.forEach((f) => {
      const key = f.drift_type || "UNKNOWN";
      const list = map.get(key) ?? [];
      list.push(f);
      map.set(key, list);
    });
    return Array.from(map.entries());
  }, [analysis]);

  const createdFindings = materialized?.created ?? [];
  const historyItems = history?.items ?? [];

  return (
    <div className="space-y-5">
      <Card className="space-y-5 p-4" data-testid="drift-analyze-form">
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <label className="text-[11px] text-muted-foreground">Scope</label>
            <Select value={scope} onValueChange={setScope}>
              <SelectTrigger className="mt-0.5" data-testid="drift-scope"><SelectValue /></SelectTrigger>
              <SelectContent>{scopes.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="text-[11px] text-muted-foreground">Run ID</label>
              <Input data-testid="drift-run-id" value={runId} onChange={(e) => setRunId(e.target.value)} className="mt-0.5 h-9 font-mono text-xs" placeholder="run_…" />
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">Rubric</label>
              <Input data-testid="drift-rubric-id" value={rubricId} onChange={(e) => setRubricId(e.target.value)} className="mt-0.5 h-9 font-mono text-xs" placeholder="rubric_…" />
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">Judge model</label>
              <Input data-testid="drift-judge-model-id" value={judgeModelId} onChange={(e) => setJudgeModelId(e.target.value)} className="mt-0.5 h-9 font-mono text-xs" placeholder="model_…" />
            </div>
          </div>
        </div>

        <Separator />

        <div>
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Changed files</h4>
            <Button size="sm" variant="outline" data-testid="drift-changed-file-add" onClick={() => setChangedFiles((l) => [...l, emptyChangedFile()])}>
              <Plus className="mr-1.5 h-3.5 w-3.5" /> Add file
            </Button>
          </div>
          {changedFiles.length === 0 ? (
            <p className="text-xs text-muted-foreground">No files added. Add a changed file to analyze.</p>
          ) : (
            <div className="space-y-2">
              {changedFiles.map((f, i) => (
                <div key={i} className="grid gap-2 rounded-md border border-border p-2 md:grid-cols-3">
                  <div className="md:col-span-3">
                    <label className="text-[10px] text-muted-foreground">Path</label>
                    <Input data-testid="drift-changed-file-path" value={f.path} onChange={(e) => updateDraft(changedFiles, setChangedFiles, i, { path: e.target.value })} className="mt-0.5 h-8 font-mono text-xs" placeholder="src/…" />
                  </div>
                  <div>
                    <label className="text-[10px] text-muted-foreground">Imports added (comma)</label>
                    <Input value={f.imports_added} onChange={(e) => updateDraft(changedFiles, setChangedFiles, i, { imports_added: e.target.value })} className="mt-0.5 h-8 font-mono text-xs" />
                  </div>
                  <div>
                    <label className="text-[10px] text-muted-foreground">Components added (comma)</label>
                    <Input value={f.components_added} onChange={(e) => updateDraft(changedFiles, setChangedFiles, i, { components_added: e.target.value })} className="mt-0.5 h-8 font-mono text-xs" />
                  </div>
                  <div>
                    <label className="text-[10px] text-muted-foreground">Services added (comma)</label>
                    <Input value={f.services_added} onChange={(e) => updateDraft(changedFiles, setChangedFiles, i, { services_added: e.target.value })} className="mt-0.5 h-8 font-mono text-xs" />
                  </div>
                  <div>
                    <label className="text-[10px] text-muted-foreground">Hardcoded colors (comma)</label>
                    <Input value={f.hardcoded_colors} onChange={(e) => updateDraft(changedFiles, setChangedFiles, i, { hardcoded_colors: e.target.value })} className="mt-0.5 h-8 font-mono text-xs" />
                  </div>
                  <div>
                    <label className="text-[10px] text-muted-foreground">Constants added (comma)</label>
                    <Input value={f.constants_added} onChange={(e) => updateDraft(changedFiles, setChangedFiles, i, { constants_added: e.target.value })} className="mt-0.5 h-8 font-mono text-xs" />
                  </div>
                  <div className="flex items-end">
                    <Button size="sm" variant="ghost" className="text-destructive" onClick={() => setChangedFiles((l) => l.filter((_, idx) => idx !== i))}>Remove</Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <Separator />

        <div className="space-y-4">
          <h4 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Contracts</h4>
          <div>
            <label className="text-[11px] text-muted-foreground">Design tokens (comma)</label>
            <Input data-testid="drift-design-tokens" value={designTokens} onChange={(e) => setDesignTokens(e.target.value)} className="mt-0.5 h-9 font-mono text-xs" placeholder="--color-bg, --color-fg" />
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">Canonical services</span>
              <Button size="sm" variant="ghost" data-testid="drift-canonical-service-add" onClick={() => setCanonicalServices((l) => [...l, emptyCanonicalService()])}><Plus className="h-3.5 w-3.5" /></Button>
            </div>
            <div className="space-y-2">
              {canonicalServices.map((s, i) => (
                <div key={i} className="grid gap-2 rounded-md border border-border p-2 md:grid-cols-3">
                  <Input value={s.name} onChange={(e) => updateDraft(canonicalServices, setCanonicalServices, i, { name: e.target.value })} className="h-8 text-xs" placeholder="name" />
                  <Input value={s.responsibility} onChange={(e) => updateDraft(canonicalServices, setCanonicalServices, i, { responsibility: e.target.value })} className="h-8 text-xs" placeholder="responsibility" />
                  <Input value={s.paths} onChange={(e) => updateDraft(canonicalServices, setCanonicalServices, i, { paths: e.target.value })} className="h-8 font-mono text-xs" placeholder="paths (comma)" />
                </div>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">Forbidden patterns</span>
              <Button size="sm" variant="ghost" data-testid="drift-forbidden-pattern-add" onClick={() => setForbiddenPatterns((l) => [...l, emptyForbiddenPattern()])}><Plus className="h-3.5 w-3.5" /></Button>
            </div>
            <div className="space-y-2">
              {forbiddenPatterns.map((p, i) => (
                <div key={i} className="grid gap-2 rounded-md border border-border p-2 md:grid-cols-4">
                  <Input value={p.id} onChange={(e) => updateDraft(forbiddenPatterns, setForbiddenPatterns, i, { id: e.target.value })} className="h-8 font-mono text-xs" placeholder="id" />
                  <Input value={p.pattern} onChange={(e) => updateDraft(forbiddenPatterns, setForbiddenPatterns, i, { pattern: e.target.value })} className="h-8 font-mono text-xs" placeholder="pattern" />
                  <Input value={p.type} onChange={(e) => updateDraft(forbiddenPatterns, setForbiddenPatterns, i, { type: e.target.value })} className="h-8 text-xs" placeholder="type" />
                  <Input value={p.reason} onChange={(e) => updateDraft(forbiddenPatterns, setForbiddenPatterns, i, { reason: e.target.value })} className="h-8 text-xs" placeholder="reason" />
                </div>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">Duplicated rules</span>
              <Button size="sm" variant="ghost" data-testid="drift-duplicated-rule-add" onClick={() => setDuplicatedRules((l) => [...l, emptyDuplicatedRule()])}><Plus className="h-3.5 w-3.5" /></Button>
            </div>
            <div className="space-y-2">
              {duplicatedRules.map((r, i) => (
                <div key={i} className="grid gap-2 rounded-md border border-border p-2 md:grid-cols-3">
                  <Input value={r.id} onChange={(e) => updateDraft(duplicatedRules, setDuplicatedRules, i, { id: e.target.value })} className="h-8 font-mono text-xs" placeholder="id" />
                  <Input value={r.backend_pattern} onChange={(e) => updateDraft(duplicatedRules, setDuplicatedRules, i, { backend_pattern: e.target.value })} className="h-8 font-mono text-xs" placeholder="backend pattern" />
                  <Input value={r.frontend_pattern} onChange={(e) => updateDraft(duplicatedRules, setDuplicatedRules, i, { frontend_pattern: e.target.value })} className="h-8 font-mono text-xs" placeholder="frontend pattern" />
                </div>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">Enums</span>
              <Button size="sm" variant="ghost" data-testid="drift-enum-add" onClick={() => setEnumContracts((l) => [...l, emptyEnumContract()])}><Plus className="h-3.5 w-3.5" /></Button>
            </div>
            <div className="space-y-2">
              {enumContracts.map((e, i) => (
                <div key={i} className="grid gap-2 rounded-md border border-border p-2 md:grid-cols-3">
                  <Input value={e.name} onChange={(ev) => updateDraft(enumContracts, setEnumContracts, i, { name: ev.target.value })} className="h-8 text-xs" placeholder="name" />
                  <Input value={e.backend_values} onChange={(ev) => updateDraft(enumContracts, setEnumContracts, i, { backend_values: ev.target.value })} className="h-8 font-mono text-xs" placeholder="backend values (comma)" />
                  <Input value={e.frontend_values} onChange={(ev) => updateDraft(enumContracts, setEnumContracts, i, { frontend_values: ev.target.value })} className="h-8 font-mono text-xs" placeholder="frontend values (comma)" />
                </div>
              ))}
            </div>
          </div>
        </div>

        <Separator />

        <div className="space-y-3">
          <h4 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Prompt context</h4>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <label className="text-[11px] text-muted-foreground">Required invariant codes (comma)</label>
              <Input data-testid="drift-required-invariant-codes" value={requiredInvariantCodes} onChange={(e) => setRequiredInvariantCodes(e.target.value)} className="mt-0.5 h-9 font-mono text-xs" />
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">Required phases (comma)</label>
              <Input data-testid="drift-required-phases" value={requiredPhases} onChange={(e) => setRequiredPhases(e.target.value)} className="mt-0.5 h-9 font-mono text-xs" />
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">Prohibited scope</label>
              <Input data-testid="drift-prohibited-scope" value={prohibitedScope} onChange={(e) => setProhibitedScope(e.target.value)} className="mt-0.5 h-9 text-xs" />
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">Compiled invariants used (comma)</label>
              <Input data-testid="drift-compiled-invariants-used" value={compiledInvariantsUsed} onChange={(e) => setCompiledInvariantsUsed(e.target.value)} className="mt-0.5 h-9 font-mono text-xs" />
            </div>
            <div className="md:col-span-2">
              <label className="text-[11px] text-muted-foreground">Output text</label>
              <Textarea data-testid="drift-output-text" value={outputText} onChange={(e) => setOutputText(e.target.value)} rows={3} className="mt-0.5 text-xs" placeholder="Compiled prompt/output to compare against the contracts" />
            </div>
          </div>
        </div>

        <div className="flex items-center justify-end">
          <Button size="sm" data-testid="drift-analyze-submit" disabled={!projectId || analyze.isPending} onClick={submit}>
            {analyze.isPending ? "Analyzing…" : "Analyze drift"}
          </Button>
        </div>
      </Card>

      {analysis && (
        <Card className="space-y-4 p-4" data-testid="drift-results">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5" data-testid="drift-summary">
            <Metric label="Total" value={nText(summary?.total)} testId="drift-summary-total" />
            <Metric label="Deterministic" value={nText(summary?.deterministic_count)} testId="drift-summary-deterministic" />
            <Metric label="Judge-derived" value={nText(summary?.judge_derived_count)} testId="drift-summary-judge-derived" />
            <Field label="Scope" value={nText(analysis.scope)} />
            <Field label="Version" value={nText(analysis.version)} />
          </div>

          {grouped.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {grouped.map(([type]) => (
                <Badge key={type} variant="outline" className="text-[10px] font-mono" data-testid="drift-summary-by-type">
                  {type}: {nText(summary?.by_drift_type?.[type])}
                </Badge>
              ))}
            </div>
          )}

          {grouped.length === 0 ? (
            <p className="text-xs text-muted-foreground">No drift findings reported for this analysis.</p>
          ) : (
            grouped.map(([type, list]) => (
              <div key={type} className="space-y-2">
                <h4 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                  {type} ({nText(summary?.by_drift_type?.[type])})
                </h4>
                <ul className="space-y-2">
                  {list.map((f, i) => (
                    <li key={f.id ?? `${type}-${i}`} className="rounded-md border border-border p-3" data-testid="drift-finding-row">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="outline" className="text-[10px] font-mono" data-testid="drift-finding-type">{nText(f.drift_type)}</Badge>
                        <Badge variant={severityVariant(f.severity)} className="text-[10px]">{nText(f.severity)}</Badge>
                        <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[10px] font-medium ${sourceBadgeClass(f.source)}`} data-testid="drift-finding-source">
                          {nText(f.source)}
                        </span>
                        {f.category && <Badge variant="secondary" className="text-[10px] font-mono">{f.category}</Badge>}
                      </div>
                      <p className="mt-1.5 text-sm">{nText(f.message)}</p>
                      {f.source === "judge_derived" && (
                        <p className="mt-1 text-[10px] font-mono text-amber-700" data-testid="drift-judge-provenance">
                          model: {nText(f.provenance?.model ?? f.provenance?.model_id)} · rubric: {nText(f.provenance?.rubric ?? f.provenance?.rubric_id)}
                        </p>
                      )}
                      <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground" data-testid="drift-finding-evidence">{evidenceText(f.evidence)}</p>
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}

          <Separator />
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" data-testid="drift-materialize" disabled={!analysis.id || materialize.isPending} onClick={() => { if (analysis.id) materialize.mutate(analysis.id); }}>
              {materialize.isPending ? "Materializing…" : "Materialize findings"}
            </Button>
            {analysis.run_id && <Badge variant="outline" className="text-[10px] font-mono">run: {analysis.run_id}</Badge>}
            {analysis.evaluation_id && <Badge variant="outline" className="text-[10px] font-mono">evaluation: {analysis.evaluation_id}</Badge>}
          </div>

          {materialized && (
            <div className="rounded-md border border-border p-3 text-xs" data-testid="drift-materialize-result">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-muted-foreground">Created findings:</span>
                {createdFindings.length === 0 ? (
                  <span>n/a</span>
                ) : (
                  createdFindings.map((c: any, i: number) => {
                    const id = c?.id ?? c?.finding_id;
                    return (
                      <Link key={id ?? i} to="/findings" className="font-mono text-primary underline-offset-4 hover:underline" data-testid="drift-materialize-created">
                        {nText(id)}
                      </Link>
                    );
                  })
                )}
              </div>
              {(materialized.existing?.length ?? 0) > 0 && (
                <span className="mt-1 block text-muted-foreground">Already materialized: {materialized.existing!.length}</span>
              )}
            </div>
          )}
        </Card>
      )}

      <Card className="p-4" data-testid="drift-analysis-history">
        <h3 className="mb-3 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Analysis history</h3>
        {historyItems.length === 0 ? (
          <p className="text-xs text-muted-foreground">No drift analyses recorded for this project.</p>
        ) : (
          <ul className="divide-y divide-border">
            {historyItems.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-3 py-2 text-xs" data-testid="drift-analysis-row">
                <span className="min-w-0 flex-1 truncate font-mono">{nText(a.id)}</span>
                <Badge variant="outline" className="text-[10px]">{nText(a.scope)}</Badge>
                <span className="text-muted-foreground">{a.created_at ? fmtDate(a.created_at) : "n/a"}</span>
                <Button size="sm" variant="outline" data-testid="drift-analysis-view" disabled={!a.id || viewAnalysis.isPending} onClick={() => { if (a.id) viewAnalysis.mutate(a.id); }}>View</Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
};

/* --------------------------------------------------------------- page */
export default function Failures() {
  const { activeId } = useProjects();
  const [searchParams] = useSearchParams();
  const initialRunId = searchParams.get("run_id") || "";
  const [section, setSection] = useState("explorer");
  const [tab, setTab] = useState(initialRunId ? "failures" : "overview");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createRunId, setCreateRunId] = useState("");

  const openCreate = (runId?: string) => {
    setCreateRunId(runId || initialRunId || "");
    setCreateOpen(true);
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="failures-page">
      <SectionHeader
        title="Failure Lab"
        caption="Inspect, classify and learn from execution and quality failures. Execution failures are captured from provider errors; quality failures are raised by humans, judges or rules."
        action={<Button size="sm" data-testid="failure-create-open" onClick={() => openCreate()}><Plus className="mr-1.5 h-4 w-4" /> Create quality failure</Button>}
      />

      <Tabs value={section} onValueChange={setSection}>
        <TabsList data-testid="failure-section-tabs">
          <TabsTrigger value="explorer" data-testid="failures-tab-explorer">Explorer</TabsTrigger>
          <TabsTrigger value="drift" data-testid="failures-tab-drift">Drift Analysis</TabsTrigger>
        </TabsList>
        <TabsContent value="explorer" className="mt-4">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList data-testid="failure-tabs">
              <TabsTrigger value="overview" data-testid="tab-overview">Overview</TabsTrigger>
              <TabsTrigger value="failures" data-testid="tab-failures">Failures</TabsTrigger>
            </TabsList>
            <TabsContent value="overview" className="mt-4">
              <OverviewTab projectId={activeId} onOpenFailure={setSelectedId} />
            </TabsContent>
            <TabsContent value="failures" className="mt-4">
              <FailuresTab projectId={activeId} onOpenFailure={setSelectedId} initialRunId={initialRunId} />
            </TabsContent>
          </Tabs>
        </TabsContent>
        <TabsContent value="drift" className="mt-4">
          <DriftTab projectId={activeId} />
        </TabsContent>
      </Tabs>

      <FailureDetailDialog
        failureId={selectedId}
        onOpenChange={(o) => { if (!o) setSelectedId(null); }}
        onCreateForRun={openCreate}
      />
      <CreateFailureDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        defaultRunId={createRunId}
        projectId={activeId}
      />
    </div>
  );
}
