import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Play, Plus, ShieldAlert, Snowflake } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import {
  Dataset,
  DatasetVersion,
  ModelRecord,
  RedTeamCase,
  RedTeamCatalogEntry,
  RedTeamExperimentSummary,
  RedTeamSummary,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState, ErrorNote, LoadingSpinner, SectionHeader } from "@/components/common/Common";
import { nNumber, nText } from "@/components/experiment/format";

const NONE = "__none__";

interface RedTeamSummaryResponse {
  experiment?: RedTeamExperimentSummary | null;
  cases?: RedTeamCase[] | null;
  summary?: RedTeamSummary | null;
  red_team_version?: string | null;
}

const statusVariant = (status?: string | null): "default" | "secondary" | "destructive" | "outline" => {
  const s = (status || "").toLowerCase();
  if (s === "completed" || s === "success" || s === "passed") return "default";
  if (s === "running" || s === "ready" || s === "executing") return "secondary";
  if (s === "failed" || s === "error") return "destructive";
  return "outline";
};

const severityVariant = (sev?: string | null): "destructive" | "secondary" | "outline" => {
  if (sev === "critical" || sev === "high") return "destructive";
  if (sev === "low") return "outline";
  return "secondary";
};

const runCount = (c: RedTeamCase): number | null => (Array.isArray(c.runs) ? c.runs.length : null);
const findingsCount = (c: RedTeamCase): number | null => (Array.isArray(c.failures) ? c.failures.length : null);

const BoundaryResult: React.FC<{ held?: boolean | null }> = ({ held }) => {
  if (held === true)
    return (
      <Badge variant="outline" className="border-emerald-500/40 bg-emerald-500/15 text-emerald-600" data-testid="red-team-boundary-result">
        boundary held
      </Badge>
    );
  if (held === false) return <Badge variant="destructive" data-testid="red-team-boundary-result">boundary violated</Badge>;
  return <Badge variant="secondary" className="bg-muted text-muted-foreground" data-testid="red-team-boundary-result">n/a</Badge>;
};

const Metric: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="flex flex-col rounded-md border border-border px-3 py-2">
    <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <span className="font-mono text-sm font-semibold tabular">{value}</span>
  </div>
);

const NewExperimentDialog: React.FC<{
  projectId: string;
  onOpenChange: (open: boolean) => void;
  onCreated: (id: string) => void;
}> = ({ projectId, onOpenChange, onCreated }) => {
  const qc = useQueryClient();
  const [datasetId, setDatasetId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [modelId, setModelId] = useState(NONE);
  const [title, setTitle] = useState("");

  const { data: datasets = [] } = useQuery<Dataset[]>({
    queryKey: ["red-team-datasets", projectId],
    queryFn: async () => (await api.get("/datasets", { params: { project_id: projectId } })).data,
  });
  const { data: versions = [] } = useQuery<DatasetVersion[]>({
    queryKey: ["red-team-versions", datasetId],
    queryFn: async () => (await api.get(`/datasets/${datasetId}/versions`)).data,
    enabled: !!datasetId,
  });
  const { data: models = [] } = useQuery<ModelRecord[]>({
    queryKey: ["red-team-models"],
    queryFn: async () => (await api.get("/models")).data,
  });

  const frozenVersions = versions.filter((v) => v.status === "frozen");

  useEffect(() => {
    if (datasetId && versionId && !frozenVersions.some((v) => v.id === versionId)) setVersionId("");
  }, [datasetId, versions, versionId]); // eslint-disable-line react-hooks/exhaustive-deps

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post("/red-team/experiments", {
          project_id: projectId,
          dataset_id: datasetId,
          dataset_version_id: versionId,
          model_ids: modelId === NONE ? [] : [modelId],
          title: title.trim() || undefined,
          objective: "",
          repetitions: 1,
        })
      ).data,
    onSuccess: (experiment: RedTeamExperimentSummary) => {
      toast.success("Red-team experiment created");
      qc.invalidateQueries({ queryKey: ["red-team-experiments"] });
      if (experiment?.id) onCreated(experiment.id);
      onOpenChange(false);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <Dialog open onOpenChange={(o) => onOpenChange(o)}>
      <DialogContent data-testid="red-team-new-experiment-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">New red-team experiment</DialogTitle>
          <DialogDescription>
            Pin a frozen dataset version and optionally a model. Fixture datasets appear here after “Load fixtures”.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Dataset</label>
            <Select value={datasetId || NONE} onValueChange={(v) => { setDatasetId(v === NONE ? "" : v); setVersionId(""); }}>
              <SelectTrigger className="mt-1" data-testid="red-team-dataset-select"><SelectValue placeholder="Select dataset" /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>Select…</SelectItem>
                {datasets.map((d) => <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>)}
              </SelectContent>
            </Select>
            {datasets.length === 0 && <p className="mt-1 text-xs text-muted-foreground">No datasets in this project. Load fixtures first.</p>}
          </div>
          {datasetId && (
            <div>
              <label className="text-xs font-medium text-muted-foreground">Frozen dataset version</label>
              {frozenVersions.length === 0 ? (
                <p className="mt-1 text-xs text-muted-foreground" data-testid="red-team-no-frozen-version">No frozen version for this dataset.</p>
              ) : (
                <Select value={versionId || NONE} onValueChange={(v) => setVersionId(v === NONE ? "" : v)}>
                  <SelectTrigger className="mt-1" data-testid="red-team-version-select"><SelectValue placeholder="Select frozen version" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>Select…</SelectItem>
                    {frozenVersions.map((v) => <SelectItem key={v.id} value={v.id}>v{v.version} · {nNumber(v.case_count)} case(s)</SelectItem>)}
                  </SelectContent>
                </Select>
              )}
            </div>
          )}
          <div>
            <label className="text-xs font-medium text-muted-foreground">Model (optional)</label>
            <Select value={modelId} onValueChange={setModelId}>
              <SelectTrigger className="mt-1" data-testid="red-team-model-select"><SelectValue placeholder="No model" /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>No model</SelectItem>
                {models.filter((m) => m.enabled).map((m) => <SelectItem key={m.id} value={m.id}>{m.display_name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Title (optional)</label>
            <Input data-testid="red-team-title-input" value={title} onChange={(e) => setTitle(e.target.value)} className="mt-1" placeholder="Red team: …" />
          </div>
          <ErrorNote message={create.isError ? apiError(create.error) : null} />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button data-testid="red-team-create-submit" disabled={!datasetId || !versionId || create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? "Creating…" : "Create experiment"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default function RedTeam() {
  const { activeId, activeProject } = useProjects();
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(false);

  const { data: catalogData, isLoading: catalogLoading } = useQuery<Record<string, any> | RedTeamCatalogEntry[]>({
    queryKey: ["red-team-catalog"],
    queryFn: async () => (await api.get("/red-team/catalog")).data,
  });
  const catalogEntries = useMemo<RedTeamCatalogEntry[]>(() => {
    const raw = catalogData as any;
    if (Array.isArray(raw)) return raw as RedTeamCatalogEntry[];
    if (raw && Array.isArray(raw.categories)) return raw.categories as RedTeamCatalogEntry[];
    return [];
  }, [catalogData]);

  const { data: listData, isLoading: listLoading, isError: listError, error: listErr } = useQuery({
    queryKey: ["red-team-experiments", activeId],
    queryFn: async () => (await api.get("/red-team", { params: activeId ? { project_id: activeId } : {} })).data,
    enabled: !!activeId,
  });
  const experiments = useMemo<RedTeamExperimentSummary[]>(() => {
    const raw = listData as any;
    if (Array.isArray(raw)) return raw as RedTeamExperimentSummary[];
    return (raw?.items ?? []) as RedTeamExperimentSummary[];
  }, [listData]);

  useEffect(() => {
    setSelectedId((current) => {
      if (current && experiments.some((e) => e.id === current)) return current;
      return experiments[0]?.id ?? null;
    });
  }, [experiments]);

  const { data: detail, isLoading: detailLoading, isError: detailError, error: detailErr } = useQuery<RedTeamSummaryResponse>({
    queryKey: ["red-team-summary", selectedId],
    queryFn: async () => (await api.get(`/red-team/${selectedId}`)).data,
    enabled: !!selectedId,
  });

  const loadFixtures = useMutation({
    mutationFn: async () => (await api.post("/red-team/fixtures", { project_id: activeId })).data,
    onSuccess: (data: any) => {
      toast.success("Red-team fixtures materialized");
      qc.invalidateQueries({ queryKey: ["red-team-experiments"] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      if (data?.experiment?.id) setSelectedId(data.experiment.id);
      else if (data?.dataset?.id) setNewOpen(true);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const execute = useMutation({
    mutationFn: async () => (await api.post(`/red-team/experiments/${selectedId}/execute`)).data,
    onSuccess: () => {
      toast.success("Red-team experiment executed");
      qc.invalidateQueries({ queryKey: ["red-team-summary", selectedId] });
      qc.invalidateQueries({ queryKey: ["red-team-experiments"] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const cases = detail?.cases ?? [];
  const summary = detail?.summary;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="red-team-page">
      <SectionHeader
        title="Red Team"
        caption={`Adversarial probes for "${activeProject?.name || "this project"}". Materialize fixtures, run them against models, and inspect whether each safety boundary held.`}
        action={
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" data-testid="red-team-load-fixtures" disabled={!activeId || loadFixtures.isPending} onClick={() => loadFixtures.mutate()}>
              <Snowflake className="mr-1.5 h-4 w-4" /> {loadFixtures.isPending ? "Loading…" : "Load fixtures"}
            </Button>
            <Button size="sm" data-testid="red-team-new-experiment" disabled={!activeId} onClick={() => setNewOpen(true)}>
              <Plus className="mr-1.5 h-4 w-4" /> New red-team experiment
            </Button>
          </div>
        }
      />

      {!activeId ? (
        <EmptyState
          icon={<ShieldAlert className="h-6 w-6" />}
          title="No project selected"
          description="Select a project to materialize red-team fixtures and run probes."
          testId="red-team-no-project"
        />
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="p-4 lg:col-span-1" data-testid="red-team-experiment-list">
              <h3 className="mb-3 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Red-team experiments</h3>
              {listLoading ? (
                <LoadingSpinner label="Loading experiments…" />
              ) : listError ? (
                <ErrorNote message={apiError(listErr)} />
              ) : experiments.length === 0 ? (
                <p className="text-xs text-muted-foreground" data-testid="red-team-experiments-empty">No red-team experiments yet.</p>
              ) : (
                <ul className="divide-y divide-border">
                  {experiments.map((e) => (
                    <li key={e.id}>
                      <button
                        type="button"
                        data-testid="red-team-experiment-row"
                        onClick={() => setSelectedId(e.id ?? null)}
                        className={"flex w-full items-center gap-2 py-2 text-left text-sm transition-colors hover:bg-accent/40 " + (selectedId === e.id ? "bg-accent/40" : "")}
                      >
                        <span className="min-w-0 flex-1 truncate font-medium">{nText(e.title)}</span>
                        <Badge variant={statusVariant(e.status)} className="shrink-0 text-[9px] uppercase">{nText(e.status)}</Badge>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card className="p-4 lg:col-span-2" data-testid="red-team-catalog">
              <h3 className="mb-3 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Fixture catalog</h3>
              {catalogLoading ? (
                <LoadingSpinner label="Loading catalog…" />
              ) : catalogEntries.length === 0 ? (
                <p className="text-xs text-muted-foreground" data-testid="red-team-catalog-unavailable">Catalog unavailable.</p>
              ) : (
                <div className="grid gap-2 sm:grid-cols-2">
                  {catalogEntries.map((c, i) => (
                    <div key={c.category || i} className="rounded-md border border-border p-3" data-testid="red-team-catalog-entry">
                      <div className="flex flex-wrap items-center gap-2">
                        <ShieldAlert className="h-3.5 w-3.5 text-muted-foreground" />
                        <span className="text-sm font-medium">{nText(c.category)}</span>
                        {c.severity && <Badge variant={severityVariant(c.severity)} className="text-[9px]">{nText(c.severity)}</Badge>}
                        {(c.tags || []).length > 0 && <Badge variant="secondary" className="text-[9px]">{nText(c.tags?.[0])}</Badge>}
                      </div>
                      {c.description && <p className="mt-1 text-xs text-muted-foreground">{nText(c.description)}</p>}
                      {c.expected_behavior && <p className="mt-1 text-xs text-muted-foreground">{nText(c.expected_behavior)}</p>}
                      <p className="mt-1 text-[11px]" data-testid="red-team-catalog-boundary">
                        <span className="text-muted-foreground">Boundary: </span>{nText(c.expected_boundary)}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>

          <Card className="p-4" data-testid="red-team-summary">
            {!selectedId ? (
              <p className="text-xs text-muted-foreground" data-testid="red-team-summary-empty">Select a red-team experiment to inspect its cases.</p>
            ) : detailLoading ? (
              <LoadingSpinner label="Loading experiment summary…" />
            ) : detailError ? (
              <ErrorNote message={apiError(detailErr)} />
            ) : (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-display text-base font-semibold">{nText(detail?.experiment?.title)}</h3>
                      <Badge variant={statusVariant(detail?.experiment?.status)} className="text-[9px] uppercase">{nText(detail?.experiment?.status)}</Badge>
                      {detail?.red_team_version && <Badge variant="outline" className="font-mono text-[9px]">{nText(detail.red_team_version)}</Badge>}
                    </div>
                    <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">{nText(detail?.experiment?.id)}</p>
                  </div>
                  <Button size="sm" data-testid="red-team-execute" disabled={execute.isPending} onClick={() => execute.mutate()}>
                    <Play className="mr-1.5 h-4 w-4" /> {execute.isPending ? "Running…" : "Run"}
                  </Button>
                </div>
                <ErrorNote className="mt-3" message={execute.isError ? apiError(execute.error) : null} />

                <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
                  <Metric label="Cases" value={nNumber(summary?.cases_total)} />
                  <Metric label="Held" value={nNumber(summary?.boundaries_held)} />
                  <Metric label="Violated" value={nNumber(summary?.boundaries_violated)} />
                  <Metric label="Not evaluated" value={nNumber(summary?.not_evaluated)} />
                  <Metric label="Evaluations" value={nNumber(summary?.case_evaluations)} />
                </div>

                {cases.length === 0 ? (
                  <p className="mt-4 text-xs text-muted-foreground" data-testid="red-team-cases-empty">No cases for this experiment.</p>
                ) : (
                  <div className="mt-4 overflow-x-auto rounded-lg border border-border" data-testid="red-team-case-table">
                    <table className="w-full text-sm">
                      <thead className="bg-muted/50 text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                        <tr>
                          <th className="px-3 py-2.5">Category</th>
                          <th className="px-3 py-2.5">Expected boundary</th>
                          <th className="px-3 py-2.5">Severity</th>
                          <th className="px-3 py-2.5 text-center">Runs</th>
                          <th className="px-3 py-2.5">Result</th>
                          <th className="px-3 py-2.5">Findings</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {cases.map((c, i) => {
                          const findings = findingsCount(c);
                          return (
                            <tr key={c.case_id || i} data-testid="red-team-case-row">
                              <td className="max-w-[220px] truncate px-3 py-2.5 font-medium" title={c.category || undefined}>{nText(c.category)}</td>
                              <td className="max-w-[280px] px-3 py-2.5 text-xs text-muted-foreground">{nText(c.expected_boundary)}</td>
                              <td className="px-3 py-2.5"><Badge variant={severityVariant(c.severity)} className="text-[9px]">{nText(c.severity)}</Badge></td>
                              <td className="px-3 py-2.5 text-center font-mono text-xs" data-testid="red-team-case-runs">{nNumber(runCount(c))}</td>
                              <td className="px-3 py-2.5"><BoundaryResult held={c.boundary_held} /></td>
                              <td className="px-3 py-2.5" data-testid="red-team-case-findings">
                                {findings === null ? (
                                  <span className="text-xs text-muted-foreground">n/a</span>
                                ) : findings === 0 ? (
                                  <span className="text-xs text-muted-foreground">none</span>
                                ) : (
                                  <Badge variant="destructive" className="text-[9px]">{findings} finding(s)</Badge>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
          </Card>
        </>
      )}

      {newOpen && activeId && (
        <NewExperimentDialog projectId={activeId} onOpenChange={setNewOpen} onCreated={(id) => setSelectedId(id)} />
      )}
    </div>
  );
}
