import React, { useMemo, useState } from "react";
import { useQuery, useQueries } from "@tanstack/react-query";
import { useNavigate, Link } from "react-router-dom";
import { FlaskConical, Plus, Swords, ShieldQuestion } from "lucide-react";
import { api } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { Dataset, DatasetVersion } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Switch } from "@/components/ui/switch";
import { EmptyState, SectionHeader, LoadingSpinner } from "@/components/common/Common";
import { fmtDate } from "@/lib/format";
import { BenchmarkResponse, ExperimentListItem } from "@/components/experiment/types";
import { cn } from "@/lib/utils";

function isProtocol(e: ExperimentListItem): boolean {
  return e.experiment_type === "benchmark" || Boolean(e.protocol) || (e.configurations_count ?? 0) > 0;
}

function statusVariant(status?: string): "default" | "secondary" | "destructive" | "outline" {
  const s = (status || "").toLowerCase();
  if (s === "completed" || s === "success") return "default";
  if (s === "running" || s === "ready") return "secondary";
  if (s === "failed" || s === "error") return "destructive";
  return "outline";
}

export default function Experiments() {
  const { activeId, activeProject } = useProjects();
  const navigate = useNavigate();
  const [onlyProject, setOnlyProject] = useState(true);

  const { data: experiments = [], isLoading } = useQuery<ExperimentListItem[]>({
    queryKey: ["experiments", onlyProject ? activeId : "all"],
    queryFn: async () => (await api.get(`/experiments${onlyProject && activeId ? `?project_id=${activeId}` : ""}`)).data,
  });

  const { data: datasetMap } = useQuery<{ datasets: Dataset[]; versions: Record<string, DatasetVersion[]> }>({
    queryKey: ["experiment-dataset-map", activeId],
    queryFn: async () => {
      const datasets: Dataset[] = (await api.get(`/datasets?project_id=${activeId}`)).data;
      const versions: Record<string, DatasetVersion[]> = {};
      await Promise.all(datasets.map(async (d) => {
        try {
          versions[d.id] = (await api.get(`/datasets/${d.id}/versions`)).data;
        } catch {
          versions[d.id] = [];
        }
      }));
      return { datasets, versions };
    },
    enabled: Boolean(activeId),
  });

  const protocolExperiments = useMemo(() => experiments.filter(isProtocol), [experiments]);
  const benchmarkQueries = useQueries({
    queries: protocolExperiments.map((e) => ({
      queryKey: ["experiments", "benchmark", e.id],
      queryFn: async () => (await api.get(`/experiments/${e.id}/benchmark`)).data as BenchmarkResponse,
      enabled: Boolean(e.id),
    })),
  });
  const benchmarkById = useMemo(() => {
    const map = new Map<string, BenchmarkResponse>();
    protocolExperiments.forEach((e, i) => {
      const data = benchmarkQueries[i]?.data;
      if (data) map.set(e.id, data);
    });
    return map;
  }, [protocolExperiments, benchmarkQueries]);

  const describeDataset = (e: ExperimentListItem) => {
    const datasetId = e.protocol?.dataset_id || e.dataset_id;
    const versionId = e.protocol?.dataset_version_id || e.dataset_version_id;
    if (!datasetId || !datasetMap) return "n/a";
    const dataset = datasetMap.datasets.find((d) => d.id === datasetId);
    const version = (datasetMap.versions[datasetId] || []).find((v) => v.id === versionId);
    if (!dataset) return "n/a";
    if (version) return `${dataset.name} · v${version.version}`;
    return dataset.name;
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6">
      <SectionHeader
        title="Experiments"
        caption="Protocol experiments are reproducible, frozen benchmarks. Arena is a quick, ad-hoc model comparison — start there to explore, then promote the idea into an experiment."
        action={
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>Current project only</span>
              <Switch data-testid="experiments-project-filter" checked={onlyProject} onCheckedChange={setOnlyProject} />
            </label>
            <Button size="sm" data-testid="experiment-create" onClick={() => navigate("/experiments/new")}>
              <Plus className="mr-1.5 h-4 w-4" /> New experiment
            </Button>
          </div>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-muted/30 px-4 py-3" data-testid="arena-vs-experiment">
        <div className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">Arena</span> = quick ad-hoc comparison ·{" "}
          <span className="font-medium text-foreground">Experiment</span> = reproducible protocol
        </div>
        <Button size="sm" variant="outline" asChild data-testid="go-to-arena">
          <Link to="/arena"><Swords className="mr-1.5 h-4 w-4" /> Open Arena</Link>
        </Button>
      </div>

      {isLoading ? <LoadingSpinner label="Loading experiments…" /> : experiments.length === 0 ? (
        <EmptyState
          icon={<FlaskConical className="h-6 w-6" />}
          title="No experiments yet"
          description={onlyProject ? `Create a protocol experiment in "${activeProject?.name}" or run an Arena for a quick comparison.` : "Create a protocol experiment or run an Arena."}
          action={<Button size="sm" data-testid="experiment-create-empty" onClick={() => navigate("/experiments/new")}><Plus className="mr-1.5 h-4 w-4" /> New experiment</Button>}
          testId="experiments-empty"
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5">Title</th>
                <th className="px-4 py-2.5">Type</th>
                <th className="px-4 py-2.5 text-center">Configs</th>
                <th className="px-4 py-2.5">Dataset / version</th>
                <th className="px-4 py-2.5">Benchmark</th>
                <th className="px-4 py-2.5 text-center">Status</th>
                <th className="px-4 py-2.5">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {experiments.map((e) => {
                const protocol = isProtocol(e);
                const benchmark = benchmarkById.get(e.id)?.plan || null;
                const configCount = e.configurations_count ?? e.configurations?.length ?? e.model_ids?.length ?? 0;
                const progress = benchmark?.progress;
                const settled = progress ? progress.completed + progress.failed : 0;
                const pct = progress && progress.total > 0 ? Math.round((settled / progress.total) * 100) : 0;
                return (
                  <tr key={e.id} data-testid={`experiment-row-${e.id}`} onClick={() => navigate(`/experiments/${e.id}`)}
                    className="cursor-pointer transition-colors hover:bg-accent/40">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2 font-medium">
                        {e.title}
                        {e.blind && <ShieldQuestion className="h-3.5 w-3.5 text-muted-foreground" />}
                        {e.cloned_from && <Badge variant="outline" className="text-[10px]">clone</Badge>}
                        {e.parent_experiment_id && <Badge variant="outline" className="text-[10px]">re-run</Badge>}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={protocol ? "secondary" : "outline"} className="font-mono text-[10px]">
                        {protocol ? "Protocol" : "Arena"}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-center font-mono">{configCount}</td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">{protocol ? describeDataset(e) : "n/a"}</td>
                    <td className="px-4 py-3">
                      {benchmark && progress ? (
                        <div className="w-40 space-y-1" data-testid={`experiment-progress-${e.id}`}>
                          <Progress value={pct} className="h-1.5" />
                          <div className="text-[10px] text-muted-foreground">
                            {settled}/{progress.total} · {benchmark.status}
                          </div>
                        </div>
                      ) : (
                        <span className="text-xs text-muted-foreground">n/a</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <Badge variant={statusVariant(e.status)} className="text-[10px] uppercase">{e.status}</Badge>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{fmtDate(e.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className={cn("text-[11px] text-muted-foreground", experiments.length === 0 && "hidden")}>
        Benchmark columns show a plan's settled calls; “n/a” means no plan exists yet.
      </p>
    </div>
  );
}
