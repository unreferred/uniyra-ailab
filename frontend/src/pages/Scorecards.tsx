import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ChevronDown, ChevronRight, List, Plus } from "lucide-react";
import {
  apiError,
  buildScorecard,
  compareExperimentScorecards,
  getExperimentScorecard,
  getRunScorecard,
  getScorecard,
  listScorecards,
} from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { ScorecardComparisonRow, ScorecardDimension } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState, ErrorNote, LoadingSpinner, SectionHeader } from "@/components/common/Common";
import { nNumber, nText } from "@/components/experiment/format";
import { cn } from "@/lib/utils";

type Scope = "run" | "experiment" | "comparison";

const SCOPES: { value: Scope; label: string }[] = [
  { value: "run", label: "Run" },
  { value: "experiment", label: "Experiment" },
  { value: "comparison", label: "Comparison" },
];

function dimsOf(s: any): ScorecardDimension[] {
  if (!s) return [];
  if (Array.isArray(s.dimensions)) return s.dimensions;
  if (s.dimension_map && typeof s.dimension_map === "object") return Object.values(s.dimension_map) as ScorecardDimension[];
  return [];
}

function rowsOf(s: any): ScorecardComparisonRow[] {
  if (!s) return [];
  if (Array.isArray(s.rows)) return s.rows;
  if (Array.isArray(s.dimensions)) return s.dimensions;
  if (s.dimension_map && typeof s.dimension_map === "object") return Object.values(s.dimension_map) as ScorecardComparisonRow[];
  return [];
}

const isComparison = (s: any): boolean =>
  !!s && (s.scope === "comparison" || Array.isArray(s.rows) || (rowsOf(s).length > 0 && dimsOf(s).length === 0));

function stateOf(d: ScorecardDimension): string {
  if (d.state) return d.state;
  if (d.value === null || d.value === undefined) return "not_available";
  return "available";
}

const STATE_LABEL: Record<string, string> = {
  available: "available",
  not_evaluated: "NOT_EVALUATED",
  not_available: "not available",
};

function stateClasses(state: string): string {
  if (state === "available") return "border-emerald-500/40 bg-emerald-500/15 text-emerald-600";
  if (state === "not_evaluated") return "border-amber-500/40 bg-amber-500/15 text-amber-600";
  return "border-border bg-muted text-muted-foreground";
}

// A value is only ever rendered when the dimension is available AND the value
// is actually present. Everything else is "n/a" — never a fabricated 0.
function valueText(d: ScorecardDimension): string {
  if (stateOf(d) !== "available") return "n/a";
  if (d.value === null || d.value === undefined || Number.isNaN(d.value)) return "n/a";
  return nNumber(d.value);
}

function sourceKey(d: ScorecardDimension): string {
  const raw = (d.source || "").toLowerCase().replace(/-/g, "_");
  if (raw) return raw;
  if (d.deterministic) return "deterministic";
  if (d.judge_derived) return "judge_derived";
  return "telemetry";
}

const SOURCE_LABEL: Record<string, string> = {
  deterministic: "deterministic",
  judge_derived: "judge-derived",
  telemetry: "telemetry",
  policy_derived: "policy-derived",
  invariant_derived: "invariant-derived",
};

function sourceClasses(key: string): string {
  if (key === "deterministic") return "border-sky-500/40 bg-sky-500/15 text-sky-600";
  if (key === "judge_derived") return "border-violet-500/40 bg-violet-500/15 text-violet-600";
  if (key === "policy_derived" || key === "invariant_derived") return "border-amber-500/40 bg-amber-500/15 text-amber-600";
  return "border-border bg-muted text-muted-foreground";
}

// Canonical labels for the additive V6 dimensions so the new keys are readable
// even when the backend omits a label.
const DIMENSION_LABELS: Record<string, string> = {
  policy_compliance: "Policy compliance",
  invariant_compliance: "Invariant compliance",
  task_completion: "Task completion",
  context_usage: "Context usage",
  drift: "Drift",
};

function dimensionLabel(d: ScorecardDimension): string {
  const key = String(d?.key || "");
  return d?.label || DIMENSION_LABELS[key] || key;
}

const ClassificationBadge: React.FC<{ classification?: string | null }> = ({ classification }) => {
  const c = (classification || "unknown").toLowerCase();
  const cls =
    c === "improved"
      ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-600"
      : c === "degraded"
      ? "border-destructive/40 bg-destructive/15 text-destructive"
      : c === "unchanged"
      ? "border-border bg-muted text-muted-foreground"
      : "border-border bg-muted text-muted-foreground";
  return <Badge variant="outline" className={cn("text-[10px]", cls)}>{c}</Badge>;
};

const EvidenceCell: React.FC<{ dim: ScorecardDimension }> = ({ dim }) => {
  const evidence = Array.isArray(dim.evidence) ? dim.evidence : [];
  if (evidence.length === 0) return <span className="text-xs text-muted-foreground">n/a</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {evidence.map((ev: any, i: number) => {
        const type = (ev?.type || "").toLowerCase();
        const refId = ev?.ref_id || ev?.id;
        if (!refId) return <span key={i} className="text-xs text-muted-foreground">n/a</span>;
        if (type === "run") {
          return (
            <Link key={i} to={`/trace?run=${refId}`} data-testid="scorecard-evidence" className="text-xs text-primary underline-offset-4 hover:underline">
              run
            </Link>
          );
        }
        if (type === "experiment") {
          return (
            <Link key={i} to={`/experiments/${refId}`} data-testid="scorecard-evidence" className="text-xs text-primary underline-offset-4 hover:underline">
              experiment
            </Link>
          );
        }
        return (
          <span key={i} data-testid="scorecard-evidence" className="font-mono text-[10px] text-muted-foreground">
            {type ? `${type}:` : ""}{nText(refId)}
          </span>
        );
      })}
    </div>
  );
};

const DimensionsTable: React.FC<{ dimensions: ScorecardDimension[] }> = ({ dimensions }) => {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<string>("all");
  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  if (dimensions.length === 0) {
    return <p className="text-sm text-muted-foreground" data-testid="scorecard-dimensions-empty">No dimensions returned.</p>;
  }

  const visible = filter === "all" ? dimensions : dimensions.filter((d) => String(d.key) === filter);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Select value={filter} onValueChange={setFilter}>
          <SelectTrigger className="h-8 w-56" data-testid="scorecard-dimension-filter">
            <SelectValue placeholder="All dimensions" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All dimensions</SelectItem>
            {dimensions.map((d, i) => (
              <SelectItem key={String(d.key || i)} value={String(d.key || i)}>
                {dimensionLabel(d)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    <div className="overflow-x-auto rounded-lg border border-border">
      <Table data-testid="scorecard-dimensions">
        <TableHeader>
          <TableRow className="bg-muted/50">
            <TableHead className="text-[10px] font-mono uppercase tracking-wider">Dimension</TableHead>
            <TableHead className="text-[10px] font-mono uppercase tracking-wider">Value</TableHead>
            <TableHead className="text-[10px] font-mono uppercase tracking-wider">Unit</TableHead>
            <TableHead className="text-[10px] font-mono uppercase tracking-wider">State</TableHead>
            <TableHead className="text-[10px] font-mono uppercase tracking-wider">Source</TableHead>
            <TableHead className="text-[10px] font-mono uppercase tracking-wider">Explanation</TableHead>
            <TableHead className="text-[10px] font-mono uppercase tracking-wider">Evidence</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {visible.map((d, i) => {
            const key = String(d.key || i);
            const state = stateOf(d);
            const open = expanded.has(key);
            return (
              <TableRow key={key} data-testid="scorecard-dimension-row">
                <TableCell className="text-xs font-medium">{nText(dimensionLabel(d))}</TableCell>
                <TableCell className="font-mono text-xs tabular" data-testid="scorecard-dimension-value">{valueText(d)}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{nText(d.unit)}</TableCell>
                <TableCell>
                  <Badge variant="outline" className={cn("text-[10px]", stateClasses(state))} data-testid="scorecard-dimension-state">
                    {STATE_LABEL[state] || state}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge
                    variant="outline"
                    className={cn("text-[10px]", sourceClasses(sourceKey(d)))}
                    data-testid="scorecard-dimension-source"
                    title={d.explanation || undefined}
                  >
                    {SOURCE_LABEL[sourceKey(d)] || sourceKey(d)}
                  </Badge>
                </TableCell>
                <TableCell className="max-w-[280px]">
                  {d.explanation ? (
                    <div>
                      <button
                        type="button"
                        className="flex items-center gap-1 text-left text-xs text-primary underline-offset-4 hover:underline"
                        onClick={() => toggle(key)}
                        data-testid="scorecard-dimension-explanation-toggle"
                      >
                        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                        explanation
                      </button>
                      {open && <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">{d.explanation}</p>}
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">n/a</span>
                  )}
                </TableCell>
                <TableCell>
                  <EvidenceCell dim={d} />
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
    </div>
  );
};

const CompositeView: React.FC<{ composite: any }> = ({ composite }) => {
  const available = composite?.available === true;
  if (!available) {
    return (
      <Card className="p-4" data-testid="scorecard-composite">
        <h3 className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Composite</h3>
        <p className="text-sm text-muted-foreground">No composite score (no scoring policy)</p>
        {composite?.reason && <p className="mt-1 text-xs text-muted-foreground">{composite.reason}</p>}
      </Card>
    );
  }

  const policy = composite?.policy;
  const used: string[] = Array.isArray(composite?.dimensions_used) ? composite.dimensions_used : [];
  const missing: string[] = Array.isArray(composite?.missing_dimensions) ? composite.missing_dimensions : [];
  return (
    <Card className="p-4" data-testid="scorecard-composite">
      <h3 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Composite</h3>
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-display text-2xl font-semibold tabular" data-testid="scorecard-composite-value">
          {nNumber(composite?.value ?? null)}
        </span>
        {policy ? (
          <Badge variant="outline" className="font-mono text-[10px]" data-testid="scorecard-composite-policy">
            {nText(policy.id)}{policy.version ? ` · v${policy.version}` : ""}
          </Badge>
        ) : (
          <span className="text-xs text-muted-foreground">policy n/a</span>
        )}
      </div>
      <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Dimensions used</span>
          <p className="mt-0.5 text-muted-foreground">{used.length > 0 ? used.map((k) => nText(k)).join(", ") : "n/a"}</p>
        </div>
        <div>
          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Missing dimensions</span>
          <p className="mt-0.5 text-muted-foreground">{missing.length > 0 ? missing.map((k) => nText(k)).join(", ") : "n/a"}</p>
        </div>
      </div>
    </Card>
  );
};

const ScorecardView: React.FC<{ data: any; title?: string | null }> = ({ data, title }) => {
  const dims = dimsOf(data);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-display text-sm font-semibold">{nText(title || data?.scope || "Scorecard")}</h3>
        {data?.scope && <Badge variant="outline" className="text-[10px]">{nText(data.scope)}</Badge>}
        {data?.builder_version && <Badge variant="outline" className="font-mono text-[10px]">{nText(data.builder_version)}</Badge>}
        {data?.id && <span className="break-all font-mono text-[10px] text-muted-foreground">{nText(data.id)}</span>}
      </div>
      <DimensionsTable dimensions={dims} />
      <CompositeView composite={data?.composite} />
    </div>
  );
};

const ComparisonView: React.FC<{ data: any }> = ({ data }) => {
  const rows = rowsOf(data);
  const summary = data?.summary;
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto rounded-lg border border-border">
        <Table data-testid="scorecard-comparison">
          <TableHeader>
            <TableRow className="bg-muted/50">
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Dimension</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Baseline</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Candidate</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Delta</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Classification</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-xs text-muted-foreground">No comparison rows returned.</TableCell>
              </TableRow>
            ) : (
              rows.map((r, i) => (
                <TableRow key={String(r.key || i)} data-testid="scorecard-comparison-row">
                  <TableCell className="text-xs font-medium">{nText(r.label || DIMENSION_LABELS[String(r.key)] || r.key)}</TableCell>
                  <TableCell className="font-mono text-xs tabular">{nNumber(r.baseline ?? null)}</TableCell>
                  <TableCell className="font-mono text-xs tabular">{nNumber(r.candidate ?? null)}</TableCell>
                  <TableCell className="font-mono text-xs tabular">{nNumber(r.delta ?? null)}</TableCell>
                  <TableCell>
                    <ClassificationBadge classification={r.classification} />
                    {r.significance_note && <p className="mt-0.5 text-[10px] text-muted-foreground">{r.significance_note}</p>}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      {summary && (
        <div className="flex flex-wrap gap-2 text-xs" data-testid="scorecard-comparison-summary">
          <Badge variant="outline" className="border-emerald-500/40 bg-emerald-500/15 text-emerald-600">improved {nNumber(summary.improved ?? null)}</Badge>
          <Badge variant="outline" className="border-destructive/40 bg-destructive/15 text-destructive">degraded {nNumber(summary.degraded ?? null)}</Badge>
          <Badge variant="outline">unchanged {nNumber(summary.unchanged ?? null)}</Badge>
          <Badge variant="outline">unknown {nNumber(summary.unknown ?? null)}</Badge>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        No statistical significance is computed{data?.significance == null ? "" : ""}. {nText(data?.note || data?.significance_note || "")}
      </p>
    </div>
  );
};

export default function Scorecards() {
  const { activeId, activeProject, projects } = useProjects();
  const qc = useQueryClient();
  const [projectId, setProjectId] = useState<string>(activeId || "");
  const [scope, setScope] = useState<Scope>("run");
  const [runId, setRunId] = useState("");
  const [experimentId, setExperimentId] = useState("");
  const [configurationId, setConfigurationId] = useState("");
  const [baselineId, setBaselineId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [viewed, setViewed] = useState<any>(null);
  const [lookupId, setLookupId] = useState("");

  useEffect(() => {
    if (activeId && !projectId) setProjectId(activeId);
  }, [activeId, projectId]);

  useEffect(() => {
    setViewed(null);
  }, [scope, runId, experimentId, configurationId, baselineId, candidateId, projectId]);

  const ready =
    scope === "run"
      ? !!runId.trim()
      : scope === "experiment"
      ? !!experimentId.trim()
      : !!experimentId.trim() && !!baselineId.trim() && !!candidateId.trim();

  const { data: liveData, isLoading, isError, error } = useQuery<any>({
    queryKey: ["scorecard", scope, runId, experimentId, configurationId, baselineId, candidateId, projectId],
    queryFn: async () => {
      if (scope === "run") return getRunScorecard(runId.trim());
      if (scope === "experiment") return getExperimentScorecard(experimentId.trim(), configurationId.trim() || null);
      return compareExperimentScorecards(experimentId.trim(), baselineId.trim(), candidateId.trim());
    },
    enabled: ready,
  });

  const { data: listData, isLoading: listLoading } = useQuery<any>({
    queryKey: ["scorecards", projectId],
    queryFn: async () => listScorecards({ project_id: projectId || undefined, limit: 25 }),
    enabled: !!projectId,
  });
  const persisted = useMemo<any[]>(() => {
    const raw: any = listData;
    if (Array.isArray(raw)) return raw;
    return raw?.items ?? [];
  }, [listData]);

  const build = useMutation({
    mutationFn: async () =>
      buildScorecard({
        project_id: projectId,
        scope,
        run_id: scope === "run" ? runId.trim() : undefined,
        experiment_id: scope !== "run" ? experimentId.trim() : undefined,
        configuration_id: scope === "experiment" ? configurationId.trim() || undefined : undefined,
        baseline_configuration_id: scope === "comparison" ? baselineId.trim() : undefined,
        candidate_configuration_id: scope === "comparison" ? candidateId.trim() : undefined,
      }),
    onSuccess: (data: any) => {
      toast.success("Scorecard built");
      qc.invalidateQueries({ queryKey: ["scorecards"] });
      if (data && (dimsOf(data).length > 0 || rowsOf(data).length > 0)) setViewed(data);
      else if (data?.id) getScorecard(data.id).then(setViewed).catch(() => {});
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const lookup = useMutation({
    mutationFn: async (id: string) => getScorecard(id.trim()),
    onSuccess: (data: any) => setViewed(data),
    onError: (e) => toast.error(apiError(e)),
  });

  const displayData = viewed ?? liveData;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="scorecards-page">
      <SectionHeader
        title="Scorecards"
        caption={`Deterministic and judge-derived scoring evidence for "${activeProject?.name || "this project"}". Dimensions marked NOT_EVALUATED differ from unavailable (n/a) values.`}
      />

      <Card className="grid gap-3 p-4 md:grid-cols-2 lg:grid-cols-4" data-testid="scorecard-controls">
        <div>
          <label className="text-[11px] text-muted-foreground">Project</label>
          <Select value={projectId || ""} onValueChange={setProjectId}>
            <SelectTrigger data-testid="scorecard-project"><SelectValue placeholder="Select project" /></SelectTrigger>
            <SelectContent>
              {projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div>
          <label className="text-[11px] text-muted-foreground">Scope</label>
          <Select value={scope} onValueChange={(v) => setScope(v as Scope)}>
            <SelectTrigger data-testid="scorecard-scope"><SelectValue /></SelectTrigger>
            <SelectContent>
              {SCOPES.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>

        {(scope === "run" || scope === "experiment" || scope === "comparison") && (
          <div>
            <label className="text-[11px] text-muted-foreground">Run ID</label>
            <Input
              data-testid="scorecard-run-id"
              value={runId}
              onChange={(e) => setRunId(e.target.value)}
              disabled={scope !== "run"}
              placeholder="run_…"
              className="mt-0.5 font-mono text-xs"
            />
          </div>
        )}

        {scope !== "run" && (
          <div>
            <label className="text-[11px] text-muted-foreground">Experiment ID</label>
            <Input
              data-testid="scorecard-experiment-id"
              value={experimentId}
              onChange={(e) => setExperimentId(e.target.value)}
              placeholder="exp_…"
              className="mt-0.5 font-mono text-xs"
            />
          </div>
        )}

        {scope === "experiment" && (
          <div>
            <label className="text-[11px] text-muted-foreground">Configuration ID (optional)</label>
            <Input
              data-testid="scorecard-configuration-id"
              value={configurationId}
              onChange={(e) => setConfigurationId(e.target.value)}
              placeholder="config_…"
              className="mt-0.5 font-mono text-xs"
            />
          </div>
        )}

        {scope === "comparison" && (
          <>
            <div>
              <label className="text-[11px] text-muted-foreground">Baseline configuration</label>
              <Input
                data-testid="scorecard-baseline-config"
                value={baselineId}
                onChange={(e) => setBaselineId(e.target.value)}
                placeholder="config_baseline"
                className="mt-0.5 font-mono text-xs"
              />
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">Candidate configuration</label>
              <Input
                data-testid="scorecard-candidate-config"
                value={candidateId}
                onChange={(e) => setCandidateId(e.target.value)}
                placeholder="config_candidate"
                className="mt-0.5 font-mono text-xs"
              />
            </div>
          </>
        )}
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" data-testid="scorecard-build" disabled={!projectId || !ready || build.isPending} onClick={() => build.mutate()}>
          <Plus className="mr-1.5 h-4 w-4" /> {build.isPending ? "Building…" : "Build + persist"}
        </Button>
        {viewed && (
          <Button size="sm" variant="ghost" data-testid="scorecard-clear-viewed" onClick={() => setViewed(null)}>Back to live result</Button>
        )}
      </div>
      <ErrorNote message={build.isError ? apiError(build.error) : null} />

      <Card className="space-y-3 p-4" data-testid="scorecard-result">
        {!ready ? (
          <p className="text-sm text-muted-foreground" data-testid="scorecard-no-identifier">
            Enter the {scope === "comparison" ? "experiment and configuration" : scope} identifier to load a scorecard.
          </p>
        ) : isLoading ? (
          <LoadingSpinner label="Loading scorecard…" />
        ) : isError ? (
          <ErrorNote message={apiError(error)} />
        ) : displayData ? (
          isComparison(displayData) ? (
            <ComparisonView data={displayData} />
          ) : (
            <ScorecardView
              data={displayData}
              title={viewed && !liveData ? (displayData?.id ? `Persisted ${displayData.id}` : "Persisted scorecard") : displayData?.scope}
            />
          )
        ) : (
          <p className="text-sm text-muted-foreground">No scorecard returned.</p>
        )}
      </Card>

      <Card className="space-y-3 p-4" data-testid="scorecard-persisted">
        <div className="flex items-center justify-between gap-2">
          <h3 className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
            <List className="h-3 w-3" /> Persisted scorecards
          </h3>
          <div className="flex items-center gap-2">
            <Input
              data-testid="scorecard-lookup-id"
              value={lookupId}
              onChange={(e) => setLookupId(e.target.value)}
              placeholder="scorecard id…"
              className="h-8 w-44 font-mono text-xs"
            />
            <Button size="sm" variant="outline" data-testid="scorecard-lookup" disabled={!lookupId.trim() || lookup.isPending} onClick={() => lookup.mutate(lookupId)}>
              Look up
            </Button>
          </div>
        </div>
        {listLoading ? (
          <LoadingSpinner label="Loading scorecards…" />
        ) : persisted.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="scorecard-persisted-empty">No persisted scorecards for this project.</p>
        ) : (
          <ul className="divide-y divide-border">
            {persisted.map((s: any, i: number) => (
              <li key={s?.id || i}>
                <button
                  type="button"
                  data-testid="scorecard-persisted-row"
                  className="flex w-full items-center gap-3 py-2 text-left text-xs transition-colors hover:bg-accent/40"
                  onClick={() => s?.id && lookup.mutate(s.id)}
                >
                  <Badge variant="outline" className="shrink-0 text-[9px]">{nText(s?.scope)}</Badge>
                  <span className="min-w-0 flex-1 truncate font-mono">{nText(s?.id)}</span>
                  <span className="hidden shrink-0 text-muted-foreground sm:inline">{nText(s?.experiment_id || s?.run_id)}</span>
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{nText(s?.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
