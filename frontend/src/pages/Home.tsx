import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  FolderKanban, FlaskConical, Activity, Boxes, FileText, DollarSign,
  Swords, MessageSquare, ArrowUpRight, AlertTriangle,
} from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { Overview, Run } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { fmtCost, fmtLatency, fmtDate, fmtTokens } from "@/lib/format";
import { useAuth } from "@/context/AuthContext";

type ModelUsageRow = Overview["model_usage"][number] & {
  known_usage_run_count?: number;
  unknown_usage_run_count?: number;
  known_cost_total?: number | null;
  unknown_cost_run_count?: number;
};

interface OverviewData extends Omit<Overview, "model_usage"> {
  model_usage: ModelUsageRow[];
}

const StatCard: React.FC<{ icon: React.ReactNode; label: string; value: string; testId: string }> = ({ icon, label, value, testId }) => (
  <Card className="p-4" data-testid={testId}>
    <div className="flex items-center justify-between">
      <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className="text-muted-foreground">{icon}</span>
    </div>
    <div className="mt-2 font-display text-2xl font-semibold tabular">{value}</div>
  </Card>
);

function errorText(run: Run): string {
  if (run.error_message) return run.error_message;
  if (typeof run.error === "string") return run.error;
  return run.error?.message || "Unknown error";
}

function usageValue(value: number, known?: number, unknown?: number): string {
  if ((unknown ?? 0) > 0 && (known ?? 0) === 0) return "n/a";
  const formatted = fmtTokens(value);
  return (unknown ?? 0) > 0 ? `${formatted}*` : formatted;
}

export default function Home() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { projects, activeProject, activeId } = useProjects();
  const [scopeMode, setScopeMode] = useState<"project" | "all">("project");

  // Default back to the active project whenever it changes.
  useEffect(() => { setScopeMode("project"); }, [activeId]);

  const effectiveProjectId = scopeMode === "project" ? activeId ?? null : null;

  const { data, isLoading, isError, error } = useQuery<OverviewData>({
    queryKey: ["overview", effectiveProjectId],
    queryFn: async () =>
      (await api.get(`/overview${effectiveProjectId ? `?project_id=${encodeURIComponent(effectiveProjectId)}` : ""}`)).data,
  });

  if (isLoading) return <div className="p-6"><LoadingSpinner label="Loading overview…" /></div>;
  if (isError || !data) return <div className="p-6"><ErrorNote message={isError ? apiError(error) : "Overview unavailable."} /></div>;

  const c = data.counts;
  const noActivity = (c.experiments ?? 0) === 0 && (c.runs ?? 0) === 0;
  const scopedProjectId = data.project_id ?? effectiveProjectId ?? null;
  const scoped = data.scope === "project" && !!scopedProjectId;
  const scopeName = scopedProjectId
    ? projects.find((p) => p.id === scopedProjectId)?.name ?? activeProject?.name ?? scopedProjectId
    : null;
  const scopeLabel = scoped ? `Project: ${scopeName}` : "All projects";
  const hasUnknownUsage = data.model_usage.some((m) => (m.unknown_usage_run_count ?? 0) > 0);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight lg:text-3xl">Welcome back, {user?.name?.split(" ")[0]}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <Badge variant={scoped ? "default" : "secondary"} data-testid="home-scope-badge">{scopeLabel}</Badge>
            <span>{scoped ? "Metrics scoped to this project." : "Metrics across all projects."}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setScopeMode((s) => (s === "project" ? "all" : "project"))}
            disabled={!activeId}
            data-testid="home-scope-toggle"
          >
            {scopeMode === "project" ? "View all projects" : "View active project"}
          </Button>
          <Button size="sm" variant="outline" onClick={() => navigate("/observability")} data-testid="home-goto-observability"><Activity className="mr-1.5 h-4 w-4" /> Observability</Button>
          <Button size="sm" variant="outline" onClick={() => navigate("/workspace")} data-testid="home-goto-workspace"><MessageSquare className="mr-1.5 h-4 w-4" /> Workspace</Button>
          <Button size="sm" onClick={() => navigate("/arena")} data-testid="home-goto-arena"><Swords className="mr-1.5 h-4 w-4" /> New Arena</Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <StatCard icon={<FolderKanban className="h-4 w-4" />} label="Projects" value={String(c.projects ?? 0)} testId="stat-projects" />
        <StatCard icon={<FlaskConical className="h-4 w-4" />} label="Experiments" value={String(c.experiments ?? 0)} testId="stat-experiments" />
        <StatCard icon={<Activity className="h-4 w-4" />} label="Runs" value={String(c.runs ?? 0)} testId="stat-runs" />
        <StatCard icon={<Boxes className="h-4 w-4" />} label="Models" value={String(c.models ?? 0)} testId="stat-models" />
        <StatCard icon={<FileText className="h-4 w-4" />} label="Artifacts" value={String(c.artifacts ?? 0)} testId="stat-artifacts" />
        <StatCard icon={<DollarSign className="h-4 w-4" />} label="Est. cost" value={fmtCost(data.cost_summary.total_estimated_cost, data.cost_summary.cost_available)} testId="stat-cost" />
      </div>

      {data.cost_summary.total_requests > 0 && !data.cost_summary.cost_available && (
        <p className="-mt-3 text-xs text-muted-foreground">
          Cost n/a: pricing unknown for {data.unknown_cost_run_count ?? 0} run(s).
        </p>
      )}

      {noActivity ? (
        <EmptyState icon={<FlaskConical className="h-6 w-6" />} title="Your lab is ready" description="Run a prompt in the Workspace or launch a multi-model Arena to generate your first real telemetry." testId="home-empty"
          action={<Button onClick={() => navigate("/arena")}><Swords className="mr-1.5 h-4 w-4" /> Launch Arena</Button>} />
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          {/* recent experiments */}
          <Card className="p-4 lg:col-span-2" data-testid="recent-experiments">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="font-display font-semibold">Recent experiments</h3>
              <button className="text-xs text-brand hover:underline" onClick={() => navigate("/experiments")}>View all</button>
            </div>
            <div className="space-y-1">
              {data.recent_experiments.length === 0 && <p className="text-sm text-muted-foreground">No experiments yet.</p>}
              {data.recent_experiments.map((e) => (
                <button key={e.id} onClick={() => navigate(`/experiments/${e.id}`)}
                  className="flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-sm transition-colors hover:bg-accent/50">
                  <span className="flex items-center gap-2 truncate"><FlaskConical className="h-3.5 w-3.5 text-muted-foreground" /><span className="truncate">{e.title}</span></span>
                  <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                    <Badge variant="secondary" className="text-[10px]">{e.model_ids.length} models</Badge>
                    {fmtDate(e.created_at)}<ArrowUpRight className="h-3.5 w-3.5" />
                  </span>
                </button>
              ))}
            </div>
          </Card>

          {/* failures */}
          <Card className="p-4" data-testid="recent-failures">
            <h3 className="mb-3 flex items-center gap-1.5 font-display font-semibold"><AlertTriangle className="h-4 w-4 text-[hsl(var(--brand-amber))]" /> Recent failures</h3>
            {data.failures.length === 0 ? <p className="text-sm text-muted-foreground">No failures. Clean runs ✨</p> : (
              <div className="space-y-2">
                {data.failures.map((f) => (
                  <div key={f.id} className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs">
                    <div className="font-medium">{f.model_name || "Unknown model"}</div>
                    <div className="mt-0.5 truncate text-muted-foreground">{errorText(f)}</div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* model usage */}
          <Card className="p-4 lg:col-span-3" data-testid="model-usage">
            <h3 className="mb-3 font-display font-semibold">Model usage</h3>
            {data.model_usage.length === 0 ? <p className="text-sm text-muted-foreground">No usage recorded yet.</p> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                    <tr><th className="py-2">Model</th><th className="py-2 text-right">Requests</th><th className="py-2 text-right">In tok</th><th className="py-2 text-right">Out tok</th><th className="py-2 text-right">Avg latency</th><th className="py-2 text-right">Cost</th><th className="py-2 text-right">Fail</th></tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {data.model_usage.map((m) => (
                      <tr key={m.model_name}>
                        <td className="py-2 font-medium">{m.model_name}</td>
                        <td className="py-2 text-right font-mono">{m.requests}</td>
                        <td className="py-2 text-right font-mono">{usageValue(m.input_tokens, m.known_usage_run_count, m.unknown_usage_run_count)}</td>
                        <td className="py-2 text-right font-mono">{usageValue(m.output_tokens, m.known_usage_run_count, m.unknown_usage_run_count)}</td>
                        <td className="py-2 text-right font-mono">{m.avg_latency_ms != null ? fmtLatency(m.avg_latency_ms) : "n/a"}</td>
                        <td className="py-2 text-right font-mono">{fmtCost(m.estimated_cost, m.cost_available)}</td>
                        <td className="py-2 text-right font-mono">{m.failures > 0 ? <span className="text-destructive">{m.failures}</span> : "0"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {hasUnknownUsage && <p className="mt-2 text-[10px] text-muted-foreground">* partial: some runs did not report token usage.</p>}
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
