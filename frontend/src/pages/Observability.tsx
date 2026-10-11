import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid,
} from "recharts";
import {
  LineChart as LineIcon, Activity, DollarSign, AlertTriangle, Cpu, Zap,
  ArrowDownToLine, ArrowUpFromLine, Percent, ArrowUpRight,
} from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import {
  Observability as ObsType,
  ObsPoint,
  PricingSnapshot,
  TokenUsage,
  CacheTelemetry,
  ReasoningTelemetry,
  CostBreakdown,
  ObsProviderQuotaRow,
} from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { EmptyState, ErrorNote, SectionHeader, LoadingSpinner } from "@/components/common/Common";
import { ReasoningPanel } from "@/components/workspace/ReasoningPanel";
import { fmtCost, fmtDate, fmtLatency, fmtTokens } from "@/lib/format";

const CHART = "hsl(var(--brand-indigo))";
const CHART2 = "hsl(var(--brand-cyan))";
const ROSE = "hsl(var(--brand-rose))";
const EMERALD = "hsl(var(--brand-emerald))";

const CONTEXTS = ["workspace", "arena", "judge", "council"];
const STATUSES = ["success", "error"];
const MAX_TABLE_ROWS = 50;

function providerResourceLabel(provider: string, resource: string): string {
  const p = (provider || "").toLowerCase();
  const r = (resource || "").toLowerCase();
  if (p === "tinyfish" && r === "search") return "TinyFish Search";
  if (p === "tinyfish" && r === "fetch") return "TinyFish Fetch";
  const label = (resource || "")
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
  return label || "Unknown";
}

function quotaWindowLabel(windowSeconds: number): string {
  if (windowSeconds === 60) return "this minute";
  if (windowSeconds === 3600) return "this hour";
  if (windowSeconds === 86400) return "today";
  return `last ${windowSeconds}s`;
}

function quotaUsageText(row: ObsProviderQuotaRow): string {
  if (row.window_limit != null) {
    return `${row.units_consumed} / ${row.window_limit} ${quotaWindowLabel(row.window_seconds)}`;
  }
  return `${row.units_consumed} (unlimited)`;
}

interface ObsModelUsageRow {
  model_id: string | null;
  model_name: string;
  provider_id?: string | null;
  provider_name?: string | null;
  requests: number;
  success: number;
  failures: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens?: number;
  reasoning_tokens?: number;
  avg_latency_ms: number | null;
  estimated_cost: number | null;
  known_cost_total?: number | null;
  unknown_cost_run_count?: number;
  cost_available: boolean;
}

interface ObsToolRow {
  tool_id?: string | null;
  tool_name?: string | null;
  calls: number;
  failures: number;
  avg_latency_ms: number | null;
}

interface ObsToolMetrics {
  total_calls: number | null;
  success_rate: number | null;
  failures: number | null;
  avg_latency_ms: number | null;
  by_tool?: ObsToolRow[] | null;
  code_interpreter?: { executions: number; timeouts: number; errors: number } | null;
}

interface ObsKnowledgeMetrics {
  retrievals: number | null;
  avg_latency_ms: number | null;
  empty_retrievals: number | null;
  indexing_failures: number | null;
  chunks_indexed: number | null;
}

interface ObservabilityData extends ObsType {
  model_usage?: ObsModelUsageRow[];
  provider_quota?: ObsProviderQuotaRow[] | null;
  tool_metrics?: ObsToolMetrics | null;
  knowledge_metrics?: ObsKnowledgeMetrics | null;
  scope?: string;
  project_id?: string | null;
}

interface RunDetail {
  id: string | null;
  run_id: string | null;
  experiment_id: string | null;
  project_id: string | null;
  context: string | null;
  execution_kind: string | null;
  provider_id: string | null;
  provider_name: string | null;
  model_id: string | null;
  model_name: string | null;
  model_identifier: string | null;
  status: string | null;
  latency_ms: number | null;
  ttft_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  reasoning_tokens: number | null;
  cache_read_tokens?: number | null;
  cache_write_tokens?: number | null;
  cache_creation_tokens?: number | null;
  uncached_input_tokens?: number | null;
  total_tokens?: number | null;
  reasoning_content?: string | null;
  reasoning_summary?: string | null;
  token_usage?: TokenUsage | null;
  cache_telemetry?: CacheTelemetry | null;
  reasoning_telemetry?: ReasoningTelemetry | null;
  cost_breakdown?: CostBreakdown | null;
  pricing_snapshot: PricingSnapshot | null;
  estimated_cost: number | null;
  cost_available: boolean;
  finish_reason: string | null;
  streaming: boolean | null;
  error: unknown;
  error_message: string | null;
  created_at: string;
  trace_id: string | null;
}

const fmtAxisTime = (v: number) =>
  new Date(v).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

const fmtTokNull = (n: number | null | undefined) => (n == null ? "n/a" : fmtTokens(n));
const fmtLatNull = (n: number | null | undefined) => (n == null ? "n/a" : fmtLatency(n));

const formatTooltipValue = (dataKey: string | number | undefined, value: any, nullLabel: string): string => {
  if (value == null) return nullLabel;
  switch (dataKey) {
    case "latency":
    case "ttft":
    case "avg_latency":
      return fmtLatency(value);
    case "cost":
      return fmtCost(value, true);
    default:
      return String(value);
  }
};

const ChartTooltip: React.FC<any> = ({ active, payload, nullLabel = "unknown" }) => {
  if (!active || !payload || payload.length === 0) return null;
  const point = payload[0]?.payload ?? {};
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      {(point.model_name || point.model) && <div className="font-medium">{point.model_name || point.model}</div>}
      {point.created_at && <div className={point.model_name || point.model ? "mb-1 text-muted-foreground" : "font-medium"}>{fmtDate(point.created_at)}</div>}
      <div className="space-y-0.5">
        {payload.map((item: any) => (
          <div key={String(item.dataKey)} className="flex items-center justify-between gap-4">
            <span className="text-muted-foreground">{item.name ?? item.dataKey}</span>
            <span className="font-mono tabular">{formatTooltipValue(item.dataKey, item.value, nullLabel)}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

const Detail: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className="flex flex-col gap-0.5">
    <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <span className="break-words font-mono text-sm">{value}</span>
  </div>
);

export default function Observability() {
  const navigate = useNavigate();
  const { projects, activeId } = useProjects();
  const [modelId, setModelId] = useState("all");
  const [projectId, setProjectId] = useState<string>(activeId ?? "all");
  const [providerId, setProviderId] = useState("all");
  const [context, setContext] = useState("all");
  const [status, setStatus] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  // Preselect the active project from ProjectContext until the user picks a scope.
  useEffect(() => {
    setProjectId(activeId ?? "all");
  }, [activeId]);

  const params = useMemo(() => {
    const p = new URLSearchParams();
    if (modelId !== "all") p.set("model_id", modelId);
    if (projectId !== "all") p.set("project_id", projectId);
    if (providerId !== "all") p.set("provider_id", providerId);
    if (context !== "all") p.set("context", context);
    if (status !== "all") p.set("status", status);
    if (dateFrom) p.set("date_from", new Date(dateFrom).toISOString());
    if (dateTo) p.set("date_to", new Date(dateTo + "T23:59:59.999Z").toISOString());
    return p.toString();
  }, [modelId, projectId, providerId, context, status, dateFrom, dateTo]);

  const { data, isLoading, isError, error } = useQuery<ObservabilityData>({
    queryKey: ["observability", params],
    queryFn: async () => (await api.get(`/observability${params ? `?${params}` : ""}`)).data,
  });

  const { data: runDetail, isLoading: detailLoading, isError: detailIsError, error: detailError } = useQuery<RunDetail>({
    queryKey: ["observability-run", selectedRunId],
    queryFn: async () => (await api.get(`/observability/runs/${selectedRunId}`)).data,
    enabled: !!selectedRunId,
  });

  const clearFilters = () => {
    setModelId("all"); setProjectId(activeId ?? "all"); setProviderId("all");
    setContext("all"); setStatus("all"); setDateFrom(""); setDateTo("");
  };

  // Real timestamps only; nulls are preserved as null so recharts renders gaps.
  const ordered = useMemo<ObsPoint[]>(() => {
    return (data?.series ?? [])
      .filter((p) => !!p.created_at)
      .slice()
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  }, [data]);

  const timeSeries = useMemo(
    () =>
      ordered.map((p) => ({
        ts: new Date(p.created_at).getTime(),
        created_at: p.created_at,
        run_id: p.run_id ?? null,
        model_name: p.model_name,
        latency: p.latency_ms ?? null,
        ttft: p.ttft_ms ?? null,
        cost: p.cost_available === false ? null : p.estimated_cost ?? null,
      })),
    [ordered]
  );

  const modelUsage = useMemo(() => data?.model_usage ?? [], [data]);
  const toolMetrics: ObsToolMetrics | null = (data?.tool_metrics as ObsToolMetrics | null | undefined) ?? null;
  const knowledgeMetrics: ObsKnowledgeMetrics | null = (data?.knowledge_metrics as ObsKnowledgeMetrics | null | undefined) ?? null;
  const toolRows: ObsToolRow[] = Array.isArray(toolMetrics?.by_tool) ? (toolMetrics?.by_tool as ObsToolRow[]) : [];
  const ci = toolMetrics?.code_interpreter ?? null;
  const emptyRate =
    knowledgeMetrics && knowledgeMetrics.retrievals != null && knowledgeMetrics.retrievals > 0 && knowledgeMetrics.empty_retrievals != null
      ? knowledgeMetrics.empty_retrievals / knowledgeMetrics.retrievals
      : null;

  const providerQuota = useMemo<ObsProviderQuotaRow[]>(
    () => (Array.isArray(data?.provider_quota) ? (data?.provider_quota as ObsProviderQuotaRow[]) : []),
    [data]
  );

  const providerQuotaGroups = useMemo(() => {
    const groups: { key: string; provider: string; resource: string; rows: ObsProviderQuotaRow[] }[] = [];
    const index = new Map<string, number>();
    providerQuota.forEach((row) => {
      const key = `${row.provider}::${row.resource}`;
      const existing = index.get(key);
      if (existing != null) {
        groups[existing].rows.push(row);
      } else {
        index.set(key, groups.length);
        groups.push({ key, provider: row.provider, resource: row.resource, rows: [row] });
      }
    });
    return groups;
  }, [providerQuota]);

  const perModel = useMemo(
    () =>
      modelUsage.map((m) => ({
        model: m.model_name,
        requests: m.requests,
        failures: m.failures,
        avg_latency: m.avg_latency_ms,
      })),
    [modelUsage]
  );

  const modelsUnknownLatency = useMemo(
    () => modelUsage.filter((m) => m.avg_latency_ms == null).map((m) => m.model_name),
    [modelUsage]
  );

  const tableRuns = useMemo(() => ordered.slice().reverse().slice(0, MAX_TABLE_ROWS), [ordered]);

  const hasCost = useMemo(
    () => ordered.some((p) => p.cost_available !== false && p.estimated_cost != null),
    [ordered]
  );

  const openRun = useCallback((runId?: string | null) => {
    if (runId) setSelectedRunId(runId);
  }, []);

  const onChartClick = useCallback(
    (state: any) => {
      const raw = state?.activeIndex ?? state?.activeTooltipIndex;
      const idx = typeof raw === "string" ? Number(raw) : raw;
      if (typeof idx === "number" && Number.isInteger(idx) && idx >= 0 && idx < timeSeries.length) {
        openRun(timeSeries[idx]?.run_id);
      }
    },
    [timeSeries, openRun]
  );

  if (isLoading) return <div className="p-6"><LoadingSpinner label="Loading telemetry…" /></div>;
  if (isError || !data) return <div className="p-6"><ErrorNote message={isError ? apiError(error) : "Telemetry unavailable."} /></div>;

  const scopedProjectId = data.scope === "project" ? data.project_id ?? (projectId !== "all" ? projectId : null) : null;
  const scopeName = scopedProjectId
    ? projects.find((p) => p.id === scopedProjectId)?.name ?? scopedProjectId
    : null;
  const scopeLabel = scopeName ? `Project: ${scopeName}` : "All projects";
  const detailErrorMessage = detailIsError ? apiError(detailError) : null;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6">
      <SectionHeader
        title="Observability"
        caption="Real execution telemetry across every run. All filters are server-side query params."
        action={
          <div className="flex items-center gap-2">
            <Badge variant={scopedProjectId ? "default" : "secondary"} data-testid="obs-scope-badge">{scopeLabel}</Badge>
            <Button size="sm" variant="outline" data-testid="obs-clear-filters" onClick={clearFilters}>Clear filters</Button>
          </div>
        }
      />

      <Card className="grid gap-2 p-3 md:grid-cols-4 lg:grid-cols-7" data-testid="obs-filters">
        <Select value={projectId} onValueChange={setProjectId}>
          <SelectTrigger data-testid="obs-project-filter"><SelectValue placeholder="Project" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All projects</SelectItem>
            {projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={modelId} onValueChange={setModelId}>
          <SelectTrigger data-testid="obs-model-filter"><SelectValue placeholder="Model" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All models</SelectItem>
            {(data.models || []).map((m) => <SelectItem key={m.model_id} value={m.model_id}>{m.model_name}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={providerId} onValueChange={setProviderId}>
          <SelectTrigger data-testid="obs-provider-filter"><SelectValue placeholder="Provider" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All providers</SelectItem>
            {(data.providers || []).map((p) => <SelectItem key={p.provider_id} value={p.provider_id}>{p.provider_name || p.provider_id.slice(0, 8)}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={context} onValueChange={setContext}>
          <SelectTrigger data-testid="obs-context-filter"><SelectValue placeholder="Context" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All contexts</SelectItem>
            {CONTEXTS.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger data-testid="obs-status-filter"><SelectValue placeholder="Status" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
          </SelectContent>
        </Select>
        <Input type="date" aria-label="From date" data-testid="obs-date-from" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="h-9" />
        <Input type="date" aria-label="To date" data-testid="obs-date-to" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="h-9" />
      </Card>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Card className="p-4" data-testid="obs-total-requests"><div className="flex items-center justify-between"><span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Requests</span><Activity className="h-4 w-4 text-muted-foreground" /></div><div className="mt-2 font-display text-2xl font-semibold tabular">{data.totals.requests}</div></Card>
        <Card className="p-4" data-testid="obs-failures"><div className="flex items-center justify-between"><span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Failures</span><AlertTriangle className="h-4 w-4 text-muted-foreground" /></div><div className="mt-2 font-display text-2xl font-semibold tabular">{data.totals.failures}</div></Card>
        <Card className="p-4" data-testid="obs-success-rate"><div className="flex items-center justify-between"><span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Success rate</span><Percent className="h-4 w-4 text-muted-foreground" /></div><div className="mt-2 font-display text-2xl font-semibold tabular">{data.totals.success_rate != null ? `${(data.totals.success_rate * 100).toFixed(1)}%` : "n/a"}</div></Card>
        <Card className="p-4" data-testid="obs-avg-latency"><div className="flex items-center justify-between"><span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Avg latency</span><Cpu className="h-4 w-4 text-muted-foreground" /></div><div className="mt-2 font-display text-2xl font-semibold tabular">{data.totals.avg_latency_ms != null ? fmtLatency(data.totals.avg_latency_ms) : "n/a"}</div></Card>
        <Card className="p-4" data-testid="obs-avg-ttft"><div className="flex items-center justify-between"><span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Avg TTFT</span><Zap className="h-4 w-4 text-muted-foreground" /></div><div className="mt-2 font-display text-2xl font-semibold tabular">{data.totals.avg_ttft_ms != null ? fmtLatency(data.totals.avg_ttft_ms) : "n/a"}</div></Card>
        <Card className="p-4" data-testid="obs-input-tokens"><div className="flex items-center justify-between"><span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Input tokens</span><ArrowDownToLine className="h-4 w-4 text-muted-foreground" /></div><div className="mt-2 font-display text-2xl font-semibold tabular">{fmtTokens(data.totals.input_tokens ?? null)}</div></Card>
        <Card className="p-4" data-testid="obs-output-tokens"><div className="flex items-center justify-between"><span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Output tokens</span><ArrowUpFromLine className="h-4 w-4 text-muted-foreground" /></div><div className="mt-2 font-display text-2xl font-semibold tabular">{fmtTokens(data.totals.output_tokens ?? null)}</div></Card>
        <Card className="p-4" data-testid="obs-total-cost"><div className="flex items-center justify-between"><span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Est. cost</span><DollarSign className="h-4 w-4 text-muted-foreground" /></div><div className="mt-2 font-display text-2xl font-semibold tabular">{fmtCost(data.totals.total_cost, data.totals.cost_available)}</div></Card>
      </div>

      {data.totals.requests === 0 ? (
        <EmptyState icon={<LineIcon className="h-6 w-6" />} title="No telemetry yet" description="Run prompts in the Workspace, Arena, or Council to populate observability." testId="obs-empty" />
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card className="p-4" data-testid="obs-latency-chart">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="font-display font-semibold">Latency over time (ms)</h3>
                <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                  <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: CHART }} /> Latency</span>
                  <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: CHART2 }} /> TTFT</span>
                </div>
              </div>
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={timeSeries} margin={{ left: -10, right: 10, top: 5 }} onClick={onChartClick}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={fmtAxisTime} tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" minTickGap={24} />
                  <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                  <Tooltip filterNull={false} content={<ChartTooltip nullLabel="unknown" />} />
                  <Line type="monotone" dataKey="latency" name="Latency" stroke={CHART} strokeWidth={2} dot={false} activeDot={{ r: 4 }} connectNulls={false} />
                  <Line type="monotone" dataKey="ttft" name="TTFT" stroke={CHART2} strokeWidth={2} dot={false} activeDot={{ r: 4 }} connectNulls={false} />
                </LineChart>
              </ResponsiveContainer>
              <p className="mt-2 text-[10px] text-muted-foreground">Gaps mark runs where latency was not reported. Click a point to inspect the run.</p>
            </Card>

            <Card className="p-4" data-testid="obs-requests-chart">
              <h3 className="mb-3 font-display font-semibold">Requests &amp; failures by model</h3>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={perModel} margin={{ left: -10, right: 10, top: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis dataKey="model" tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))" interval={0} angle={-12} textAnchor="end" height={50} />
                  <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                  <Tooltip filterNull={false} content={<ChartTooltip nullLabel="n/a" />} />
                  <Bar dataKey="requests" name="Requests" fill={CHART2} radius={[4, 4, 0, 0]} />
                  <Bar dataKey="failures" name="Failures" fill={ROSE} radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>

            <Card className="p-4" data-testid="obs-avglatency-chart">
              <h3 className="mb-3 font-display font-semibold">Avg latency by model (ms)</h3>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={perModel} margin={{ left: -10, right: 10, top: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis dataKey="model" tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))" interval={0} angle={-12} textAnchor="end" height={50} />
                  <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                  <Tooltip filterNull={false} content={<ChartTooltip nullLabel="n/a" />} />
                  <Bar dataKey="avg_latency" name="Avg latency (ms)" fill={CHART} radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
              {modelsUnknownLatency.length > 0 && (
                <p className="mt-2 text-[10px] text-muted-foreground">n/a (no known latency): {modelsUnknownLatency.join(", ")}</p>
              )}
            </Card>

            <Card className="p-4" data-testid="obs-cost-chart">
              <h3 className="mb-3 font-display font-semibold">Cost per run</h3>
              {hasCost ? (
                <>
                  <ResponsiveContainer width="100%" height={240}>
                    <LineChart data={timeSeries} margin={{ left: -10, right: 10, top: 5 }} onClick={onChartClick}>
                      <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                      <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={fmtAxisTime} tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" minTickGap={24} />
                      <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                      <Tooltip filterNull={false} content={<ChartTooltip nullLabel="n/a" />} />
                      <Line type="monotone" dataKey="cost" name="Cost" stroke={EMERALD} strokeWidth={2} dot={false} activeDot={{ r: 4 }} connectNulls={false} />
                    </LineChart>
                  </ResponsiveContainer>
                  <p className="mt-2 text-[10px] text-muted-foreground">Gaps mark runs where cost is unknown. Click a point to inspect the run.</p>
                </>
              ) : (
                <div className="flex h-[240px] items-center justify-center text-center text-sm text-muted-foreground">
                  Cost data unavailable for these models.<br />Set input/output pricing on BYOK models to track cost.
                </div>
              )}
            </Card>
          </div>

          <Card className="p-4" data-testid="obs-runs-table">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="font-display font-semibold">Runs</h3>
              <span className="text-xs text-muted-foreground">
                {ordered.length > MAX_TABLE_ROWS ? `Showing latest ${MAX_TABLE_ROWS} of ${ordered.length}` : `${ordered.length} run${ordered.length === 1 ? "" : "s"}`}
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                  <tr>
                    <th className="py-2">Time</th>
                    <th className="py-2">Model</th>
                    <th className="py-2">Status</th>
                    <th className="py-2 text-right">Latency</th>
                    <th className="py-2 text-right">In / out tok</th>
                    <th className="py-2 text-right">Cost</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {tableRuns.map((p, i) => (
                    <tr
                      key={p.run_id ?? `${p.created_at}-${i}`}
                      className={p.run_id ? "cursor-pointer transition-colors hover:bg-accent/50" : ""}
                      onClick={() => openRun(p.run_id)}
                      tabIndex={p.run_id ? 0 : undefined}
                      role={p.run_id ? "button" : undefined}
                      onKeyDown={(e) => {
                        if (p.run_id && (e.key === "Enter" || e.key === " ")) {
                          e.preventDefault();
                          openRun(p.run_id);
                        }
                      }}
                      data-testid="obs-run-row"
                    >
                      <td className="whitespace-nowrap py-2">{fmtDate(p.created_at)}</td>
                      <td className="py-2 font-medium">{p.model_name ?? "unknown"}</td>
                      <td className="py-2"><Badge variant={p.status === "success" ? "secondary" : "destructive"} className="text-[10px]">{p.status ?? "unknown"}</Badge></td>
                      <td className="py-2 text-right font-mono">{fmtLatNull(p.latency_ms)}</td>
                      <td className="py-2 text-right font-mono">{fmtTokNull(p.input_tokens)} / {fmtTokNull(p.output_tokens)}</td>
                      <td className="py-2 text-right font-mono">{fmtCost(p.cost_available === false ? null : p.estimated_cost, p.cost_available)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      <div className="space-y-4" data-testid="obs-tool-usage">
            <Card className="p-4" data-testid="obs-tool-cards">
              <h3 className="mb-3 font-display font-semibold">Tool usage</h3>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <div data-testid="obs-tool-total-calls">
                  <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Tool calls</div>
                  <div className="mt-1 font-display text-2xl font-semibold tabular">{toolMetrics?.total_calls ?? "Unknown"}</div>
                </div>
                <div data-testid="obs-tool-success-rate">
                  <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Tool success rate</div>
                  <div className="mt-1 font-display text-2xl font-semibold tabular">
                    {toolMetrics?.success_rate != null ? `${(toolMetrics.success_rate * 100).toFixed(1)}%` : "Unknown"}
                  </div>
                </div>
                <div data-testid="obs-tool-failures">
                  <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Tool failures</div>
                  <div className="mt-1 font-display text-2xl font-semibold tabular">{toolMetrics?.failures ?? "Unknown"}</div>
                </div>
                <div data-testid="obs-tool-avg-latency">
                  <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Tool avg latency</div>
                  <div className="mt-1 font-display text-2xl font-semibold tabular">
                    {toolMetrics?.avg_latency_ms != null ? fmtLatency(toolMetrics.avg_latency_ms) : "Unknown"}
                  </div>
                </div>
                <div data-testid="obs-tool-ci-executions">
                  <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Code interpreter executions</div>
                  <div className="mt-1 font-display text-2xl font-semibold tabular">{ci != null ? ci.executions : "Unknown"}</div>
                </div>
                <div data-testid="obs-tool-ci-timeouts">
                  <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Code interpreter timeouts</div>
                  <div className="mt-1 font-display text-2xl font-semibold tabular">{ci != null ? ci.timeouts : "Unknown"}</div>
                </div>
                <div data-testid="obs-tool-ci-errors">
                  <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Code interpreter errors</div>
                  <div className="mt-1 font-display text-2xl font-semibold tabular">{ci != null ? ci.errors : "Unknown"}</div>
                </div>
              </div>
              {toolMetrics == null && (
                <p className="mt-2 text-xs text-muted-foreground">Unknown — no tool telemetry reported for this scope.</p>
              )}
            </Card>
            <Card className="p-4" data-testid="obs-tool-table">
              <h3 className="mb-3 font-display font-semibold">Tool calls by tool</h3>
              {!toolMetrics || toolRows.length === 0 ? (
                <p className="text-xs text-muted-foreground">Unknown — no per-tool telemetry reported for this scope.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                      <tr>
                        <th className="py-2">Tool</th>
                        <th className="py-2 text-right">Calls</th>
                        <th className="py-2 text-right">Failures</th>
                        <th className="py-2 text-right">Avg latency</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {toolRows.map((t, i) => (
                        <tr key={t.tool_id || t.tool_name || `tool-${i}`} data-testid="obs-tool-row">
                          <td className="py-2 font-medium">{t.tool_name || "Unknown"}</td>
                          <td className="py-2 text-right font-mono">{t.calls}</td>
                          <td className="py-2 text-right font-mono">{t.failures}</td>
                          <td className="py-2 text-right font-mono">{t.avg_latency_ms != null ? fmtLatency(t.avg_latency_ms) : "Unknown"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
            <Card className="p-4" data-testid="obs-provider-quota">
              <h3 className="mb-3 font-display font-semibold">Provider quota</h3>
              {providerQuotaGroups.length === 0 ? (
                <p className="text-xs text-muted-foreground" data-testid="obs-provider-quota-empty">
                  No provider quota telemetry yet.
                </p>
              ) : (
                <div className="space-y-2">
                  {providerQuotaGroups.map((g) => (
                    <div
                      key={g.key}
                      className="flex flex-wrap items-baseline justify-between gap-2"
                      data-testid="obs-provider-quota-row"
                    >
                      <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                        {providerResourceLabel(g.provider, g.resource)}
                      </div>
                      <div className="font-mono text-sm tabular">
                        {g.rows.map((row, i) => (
                          <span key={`${row.window}-${i}`}>
                            {i > 0 ? " · " : ""}
                            {quotaUsageText(row)}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>

          <div className="space-y-4" data-testid="obs-knowledge">
            <Card className="grid grid-cols-2 gap-3 p-4 md:grid-cols-4" data-testid="obs-knowledge-cards">
              <div data-testid="obs-knowledge-retrievals">
                <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Retrievals</div>
                <div className="mt-1 font-display text-2xl font-semibold tabular">{knowledgeMetrics?.retrievals ?? "Unknown"}</div>
              </div>
              <div data-testid="obs-knowledge-empty">
                <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Empty retrievals</div>
                <div className="mt-1 font-display text-2xl font-semibold tabular">{knowledgeMetrics?.empty_retrievals ?? "Unknown"}</div>
              </div>
              <div data-testid="obs-knowledge-avg-latency">
                <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Retrieval avg latency</div>
                <div className="mt-1 font-display text-2xl font-semibold tabular">
                  {knowledgeMetrics?.avg_latency_ms != null ? fmtLatency(knowledgeMetrics.avg_latency_ms) : "Unknown"}
                </div>
              </div>
              <div data-testid="obs-knowledge-chunks">
                <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Chunks indexed</div>
                <div className="mt-1 font-display text-2xl font-semibold tabular">{knowledgeMetrics?.chunks_indexed ?? "Unknown"}</div>
              </div>
              <div data-testid="obs-knowledge-indexing-failures">
                <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Indexing failures</div>
                <div className="mt-1 font-display text-2xl font-semibold tabular">{knowledgeMetrics?.indexing_failures ?? "Unknown"}</div>
              </div>
            </Card>
            <p className="text-[11px] text-muted-foreground" data-testid="obs-knowledge-empty-note">
              {emptyRate != null
                ? `Empty-retrieval share is ${(emptyRate * 100).toFixed(1)}% of retrievals — informational only, not a quality score.`
                : "Empty-retrieval share is informational only, not a quality score."}
            </p>
            {knowledgeMetrics == null && (
              <p className="text-xs text-muted-foreground">Unknown — no knowledge telemetry reported for this scope.</p>
            )}
          </div>

      <Dialog open={selectedRunId != null} onOpenChange={(open) => { if (!open) setSelectedRunId(null); }}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl" data-testid="obs-run-drawer">
          <DialogHeader>
            <DialogTitle className="font-display">{runDetail?.model_name || "Run detail"}</DialogTitle>
            <DialogDescription>
              {runDetail
                ? `${runDetail.run_id ?? ""}${runDetail.created_at ? ` · ${fmtDate(runDetail.created_at)}` : ""}`
                : "Fetching safe run metadata…"}
            </DialogDescription>
          </DialogHeader>
          <div className="mt-4 space-y-5">
            {detailLoading && <LoadingSpinner label="Loading run…" />}
            {detailErrorMessage && <ErrorNote message={detailErrorMessage} />}
            {runDetail && (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={runDetail.status === "success" ? "secondary" : "destructive"}>{runDetail.status ?? "unknown"}</Badge>
                  {runDetail.streaming != null && <Badge variant="outline">{runDetail.streaming ? "streaming" : "non-streaming"}</Badge>}
                  {runDetail.execution_kind && <Badge variant="outline">{runDetail.execution_kind}</Badge>}
                  {runDetail.context && <Badge variant="outline">{runDetail.context}</Badge>}
                </div>
                {runDetail.error_message && <ErrorNote message={runDetail.error_message} />}
                <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                  <Detail label="Run ID" value={runDetail.run_id ?? "—"} />
                  <Detail label="Model" value={runDetail.model_name ?? "—"} />
                  <Detail label="Model identifier" value={runDetail.model_identifier ?? "—"} />
                  <Detail label="Provider" value={runDetail.provider_name ?? runDetail.provider_id ?? "—"} />
                  <Detail label="Created" value={fmtDate(runDetail.created_at)} />
                  <Detail label="Finish reason" value={runDetail.finish_reason ?? "—"} />
                  <Detail label="Latency" value={fmtLatNull(runDetail.latency_ms)} />
                  <Detail label="TTFT" value={fmtLatNull(runDetail.ttft_ms)} />
                  <Detail label="Input tokens" value={fmtTokNull(runDetail.input_tokens)} />
                  <Detail label="Uncached input" value={fmtTokNull(runDetail.uncached_input_tokens)} />
                  <Detail label="Cached tokens" value={fmtTokNull(runDetail.cache_read_tokens ?? runDetail.cached_tokens)} />
                  <Detail
                    label="Cache hit rate"
                    value={(() => {
                      if (runDetail.cache_telemetry?.cache_hit_rate != null) {
                        return `${(runDetail.cache_telemetry.cache_hit_rate * 100).toFixed(1)}%`;
                      }
                      const c = runDetail.cache_read_tokens ?? runDetail.cached_tokens;
                      const inp = runDetail.input_tokens;
                      if (c != null && inp && inp > 0) {
                        return `${((c / inp) * 100).toFixed(1)}%`;
                      }
                      return "—";
                    })()}
                  />
                  <Detail label="Output tokens" value={fmtTokNull(runDetail.output_tokens)} />
                  <Detail label="Reasoning tokens" value={fmtTokNull(runDetail.reasoning_tokens ?? runDetail.token_usage?.reasoning_tokens)} />
                  <Detail label="Total tokens" value={fmtTokNull(runDetail.total_tokens ?? (runDetail.input_tokens != null && runDetail.output_tokens != null ? runDetail.input_tokens + runDetail.output_tokens : null))} />
                  <Detail label="Est. cost" value={fmtCost(runDetail.estimated_cost, runDetail.cost_available)} />
                  <Detail label="Project" value={runDetail.project_id ?? "—"} />
                  <Detail label="Trace ID" value={runDetail.trace_id ?? "—"} />
                </div>
                {(runDetail.reasoning_content || runDetail.reasoning_telemetry?.available) && (
                  <div>
                    <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Provider reasoning</h4>
                    <ReasoningPanel
                      reasoningTelemetry={runDetail.reasoning_telemetry}
                      reasoningContent={runDetail.reasoning_content}
                      reasoningTokens={runDetail.reasoning_tokens}
                      defaultExpanded={false}
                    />
                  </div>
                )}
                {runDetail.cost_breakdown && runDetail.cost_breakdown.cost_available && (
                  <div data-testid="obs-run-cost-breakdown">
                    <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Cost breakdown</h4>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                      <Detail label="Total cost" value={fmtCost(runDetail.cost_breakdown.total_cost, true)} />
                      <Detail label="Uncached input" value={runDetail.cost_breakdown.uncached_input_cost != null ? `$${runDetail.cost_breakdown.uncached_input_cost.toFixed(6)}` : "—"} />
                      <Detail label="Cache read" value={runDetail.cost_breakdown.cache_read_cost != null ? `$${runDetail.cost_breakdown.cache_read_cost.toFixed(6)}` : "—"} />
                      <Detail label="Output" value={runDetail.cost_breakdown.output_cost != null ? `$${runDetail.cost_breakdown.output_cost.toFixed(6)}` : "—"} />
                      <Detail label="Reasoning" value={runDetail.cost_breakdown.reasoning_cost === 0 ? "Included in output" : (runDetail.cost_breakdown.reasoning_cost != null ? `$${runDetail.cost_breakdown.reasoning_cost.toFixed(6)}` : "—")} />
                    </div>
                  </div>
                )}
                {runDetail.pricing_snapshot && (
                  <div>
                    <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Pricing snapshot</h4>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                      <Detail label="Input / 1M" value={runDetail.pricing_snapshot.input_price_per_1m != null ? `$${runDetail.pricing_snapshot.input_price_per_1m}` : "n/a"} />
                      <Detail label="Output / 1M" value={runDetail.pricing_snapshot.output_price_per_1m != null ? `$${runDetail.pricing_snapshot.output_price_per_1m}` : "n/a"} />
                      <Detail label="Cache read / 1M" value={runDetail.pricing_snapshot.cache_read_price_per_1m != null ? `$${runDetail.pricing_snapshot.cache_read_price_per_1m}` : "—"} />
                      <Detail label="Reasoning / 1M" value={runDetail.pricing_snapshot.reasoning_price_per_1m != null ? `$${runDetail.pricing_snapshot.reasoning_price_per_1m}` : "—"} />
                      <Detail label="Source" value={runDetail.pricing_snapshot.pricing_source ?? "—"} />
                      <Detail label="Captured" value={runDetail.pricing_snapshot.captured_at ? fmtDate(runDetail.pricing_snapshot.captured_at) : "—"} />
                    </div>
                  </div>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  {runDetail.experiment_id && (
                    <Button
                      variant="outline"
                      size="sm"
                      data-testid="obs-run-experiment-link"
                      onClick={() => {
                        const experimentId = runDetail.experiment_id;
                        setSelectedRunId(null);
                        if (experimentId) navigate(`/experiments/${experimentId}`);
                      }}
                    >
                      <ArrowUpRight className="mr-1.5 h-4 w-4" /> View experiment
                    </Button>
                  )}
                  {(() => {
                    const rid = runDetail.run_id ?? runDetail.id;
                    if (!rid) return null;
                    const go = (to: string) => () => { setSelectedRunId(null); navigate(to); };
                    return (
                      <>
                        <Button variant="outline" size="sm" data-testid="obs-run-trace-link" onClick={go(`/trace?run=${rid}`)}>
                          Trace
                        </Button>
                        <Button variant="outline" size="sm" data-testid="obs-run-context-link" onClick={go(`/context-lab?run=${rid}`)}>
                          Context
                        </Button>
                        <Button variant="outline" size="sm" data-testid="obs-run-failures-link" onClick={go(`/failures?run_id=${rid}`)}>
                          Failures
                        </Button>
                        <Button variant="outline" size="sm" data-testid="obs-run-compare-link" onClick={go(`/run-compare?run_a=${rid}`)}>
                          Compare
                        </Button>
                      </>
                    );
                  })()}
                </div>
              </>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
