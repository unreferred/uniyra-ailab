import React from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Archive, ArrowLeft, Copy, FlaskConical, Gavel, Loader2, Pencil, Play, RefreshCw, Snowflake, Trash2, Wrench } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { BenchmarkPlan } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState, LoadingSpinner, SectionHeader } from "@/components/common/Common";
import { ArenaResults } from "@/components/arena/ArenaResults";
import { ConfigDraft, ConfigEditor, configFromRecord, configToPayload, validateConfigs } from "@/components/experiment/ConfigEditor";
import { PlanReview } from "@/components/experiment/PlanReview";
import { BenchmarkProgress } from "@/components/experiment/BenchmarkProgress";
import { ResultsTable } from "@/components/experiment/ResultsTable";
import { ComparePanel } from "@/components/experiment/ComparePanel";
import { ProvenancePanel } from "@/components/experiment/ProvenancePanel";
import { EvaluationList, RegressionPanel } from "@/components/evaluation";
import { FindingFromResultsDialog } from "@/components/experiment/FindingFromResultsDialog";
import { BenchmarkResponse, ConfigurationRecord, ExperimentDetailResponse } from "@/components/experiment/types";
import { nText } from "@/components/experiment/format";
import { useModels, useRubrics } from "@/lib/hooks";
import { fmtDate } from "@/lib/format";

function isProtocolExperiment(e: ExperimentDetailResponse): boolean {
  return e.experiment_type === "benchmark" || Boolean(e.protocol) || (e.configurations?.length ?? 0) > 0;
}

const norm = (s?: string) => (s || "").toUpperCase();

export default function ExperimentDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: models = [] } = useModels();
  const { data: rubrics = [] } = useRubrics();

  const [editOpen, setEditOpen] = React.useState(false);
  const [editTitle, setEditTitle] = React.useState("");
  const [editObjective, setEditObjective] = React.useState("");
  const [editHypothesis, setEditHypothesis] = React.useState("");
  const [configOpen, setConfigOpen] = React.useState(false);
  const [configDrafts, setConfigDrafts] = React.useState<ConfigDraft[]>([]);
  const [configError, setConfigError] = React.useState<string | null>(null);
  const [confirmed, setConfirmed] = React.useState(false);
  const [confirmAction, setConfirmAction] = React.useState<null | "archive" | "delete">(null);

  const { data: experiment, isLoading, refetch } = useQuery<ExperimentDetailResponse>({
    queryKey: ["experiment", id],
    queryFn: async () => (await api.get(`/experiments/${id}`)).data,
    enabled: !!id,
  });

  const protocol = experiment ? isProtocolExperiment(experiment) : false;

  const { data: benchmark } = useQuery<BenchmarkResponse>({
    queryKey: ["experiments", "benchmark", id],
    queryFn: async () => (await api.get(`/experiments/${id}/benchmark`)).data,
    enabled: !!id && protocol,
    refetchInterval: (query) => {
      const plan = (query.state.data as BenchmarkResponse | undefined)?.plan;
      return plan?.status?.toLowerCase() === "running" ? 2000 : false;
    },
  });

  const plan: BenchmarkPlan | null = benchmark?.plan ?? experiment?.benchmark_plan ?? null;
  const configs: ConfigurationRecord[] = (experiment?.configurations || []) as ConfigurationRecord[];

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ["experiment", id] });
    qc.invalidateQueries({ queryKey: ["experiments", "benchmark", id] });
    qc.invalidateQueries({ queryKey: ["experiments"] });
  };

  const updateMeta = useMutation({
    mutationFn: async () => (await api.patch(`/experiments/${id}`, {
      title: editTitle.trim(), objective: editObjective, hypothesis: editHypothesis,
    })).data,
    onSuccess: () => { toast.success("Experiment updated"); setEditOpen(false); invalidateAll(); refetch(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const updateConfigs = useMutation({
    mutationFn: async () => (await api.patch(`/experiments/${id}`, {
      configurations: configDrafts.map((c, i) => configToPayload(c, i)),
    })).data,
    onSuccess: () => { toast.success("Configurations updated"); setConfigOpen(false); invalidateAll(); refetch(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const readyMut = useMutation({
    mutationFn: async () => (await api.post(`/experiments/${id}/ready`)).data,
    onSuccess: () => { toast.success("Experiment marked READY — configurations frozen"); invalidateAll(); refetch(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const buildPlanMut = useMutation({
    mutationFn: async () => (await api.post(`/experiments/${id}/benchmark/plan`)).data as BenchmarkPlan,
    onSuccess: () => { toast.success("Benchmark plan built"); invalidateAll(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const startMut = useMutation({
    mutationFn: async () => {
      if (!plan) throw new Error("No plan");
      return (await api.post(`/experiments/${id}/benchmark/start`, { plan_id: plan.id, confirm: confirmed })).data;
    },
    onSuccess: () => { toast.success("Benchmark started"); invalidateAll(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const cancelMut = useMutation({
    mutationFn: async () => (await api.post(`/experiments/${id}/benchmark/cancel`)).data,
    onSuccess: () => { toast.success("Cancel requested"); invalidateAll(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const resumeMut = useMutation({
    mutationFn: async () => (await api.post(`/experiments/${id}/benchmark/resume`)).data,
    onSuccess: () => { toast.success("Benchmark resumed"); invalidateAll(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const archiveMut = useMutation({
    mutationFn: async () => (await api.patch(`/experiments/${id}`, { status: "ARCHIVED" })).data,
    onSuccess: () => { toast.success("Experiment archived"); setConfirmAction(null); invalidateAll(); refetch(); },
    onError: (e) => { toast.error(apiError(e)); setConfirmAction(null); },
  });

  // Legacy Arena actions (kept working for both experiment kinds).
  const rerun = useMutation({
    mutationFn: async () => (await api.post(`/experiments/${id}/rerun`)).data,
    onSuccess: (data) => { toast.success("Re-run complete — previous runs preserved"); navigate(`/experiments/${data.experiment.id}`); },
    onError: (e) => toast.error(apiError(e)),
  });
  const clone = useMutation({
    mutationFn: async () => (await api.post(`/experiments/${id}/clone`)).data,
    onSuccess: (data) => { toast.success("Experiment cloned"); navigate(`/experiments/${data.id}`); },
    onError: (e) => toast.error(apiError(e)),
  });
  const del = useMutation({
    mutationFn: async () => (await api.delete(`/experiments/${id}`)).data,
    onSuccess: () => { toast.success("Experiment deleted"); navigate("/experiments"); },
    onError: (e) => { toast.error(apiError(e)); setConfirmAction(null); },
  });

  if (isLoading || !experiment) return <div className="p-6"><LoadingSpinner label="Loading experiment…" /></div>;

  const status = norm(experiment.status);
  const isDraft = status === "DRAFT";
  const isReady = status === "READY";
  const isRunning = status === "RUNNING";
  const isArchived = status === "ARCHIVED";
  const planRunning = plan?.status?.toLowerCase() === "running";
  const planCompleted = plan?.status?.toLowerCase() === "completed";
  const planCancelled = plan?.status?.toLowerCase() === "cancelled";
  const canStart = isReady && Boolean(plan) && !planRunning && !planCompleted;

  const openEdit = () => {
    setEditTitle(experiment.title);
    setEditObjective(experiment.objective || experiment.protocol?.objective || "");
    setEditHypothesis(experiment.hypothesis || experiment.protocol?.hypothesis || "");
    setEditOpen(true);
  };
  const openConfigs = () => {
    setConfigDrafts(configs.map((c, i) => configFromRecord(c, i)));
    setConfigError(null);
    setConfigOpen(true);
  };

  const evalEvidence = (experiment.evaluations || []).map((ev: any) => ({
    id: ev.id,
    run_id: ev.run_id ?? null,
    evaluator_type: ev.evaluator_type ?? null,
    eval_type: ev.eval_type ?? null,
    parsed_score: ev.parsed_score ?? null,
  }));
  const datasetVersionId = experiment.dataset_version_id || experiment.protocol?.dataset_version_id || plan?.dataset_version_id || null;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6">
      <button onClick={() => navigate("/experiments")} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground" data-testid="back-to-experiments">
        <ArrowLeft className="h-4 w-4" /> Experiments
      </button>

      <SectionHeader
        title={experiment.title}
        caption={`${protocol ? "protocol" : "arena"} · ${fmtDate(experiment.created_at)}${experiment.protocol?.repetitions ? ` · ${experiment.protocol.repetitions} repetition(s)` : ""}`}
        action={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Badge variant={status === "COMPLETED" ? "default" : status === "FAILED" ? "destructive" : "outline"} className="text-[10px] uppercase" data-testid="experiment-status">
              {experiment.status}
            </Badge>

            {protocol && isDraft && (
              <>
                <Button size="sm" variant="outline" data-testid="edit-experiment" onClick={openEdit}><Pencil className="mr-1.5 h-4 w-4" /> Edit</Button>
                <Button size="sm" variant="outline" data-testid="edit-configurations" onClick={openConfigs}><Wrench className="mr-1.5 h-4 w-4" /> Configs</Button>
                <Button size="sm" data-testid="mark-ready" disabled={readyMut.isPending} onClick={() => readyMut.mutate()}>
                  {readyMut.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Snowflake className="mr-1.5 h-4 w-4" />} Mark ready
                </Button>
              </>
            )}

            {protocol && isReady && (
              <Button size="sm" variant="outline" data-testid="benchmark-plan-build-header" disabled={buildPlanMut.isPending} onClick={() => buildPlanMut.mutate()}>
                {buildPlanMut.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <FlaskConical className="mr-1.5 h-4 w-4" />} {plan ? "Rebuild plan" : "Plan benchmark"}
              </Button>
            )}

            {protocol && isReady && plan && !planRunning && !planCompleted && (
              <Button size="sm" data-testid="benchmark-start" disabled={startMut.isPending || (Boolean(plan.requires_confirmation) && !confirmed)} onClick={() => startMut.mutate()}>
                {startMut.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Play className="mr-1.5 h-4 w-4" />} Start
              </Button>
            )}

            {protocol && isRunning && (
              <Button size="sm" variant="outline" data-testid="benchmark-cancel-header" disabled={cancelMut.isPending} onClick={() => cancelMut.mutate()}>
                {cancelMut.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null} Cancel
              </Button>
            )}
            {protocol && planCancelled && (
              <Button size="sm" variant="outline" data-testid="benchmark-resume-header" disabled={resumeMut.isPending} onClick={() => resumeMut.mutate()}>
                {resumeMut.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null} Resume
              </Button>
            )}

            {!isArchived && (
              <Button size="sm" variant="ghost" data-testid="archive-experiment" onClick={() => setConfirmAction("archive")}>
                <Archive className="mr-1.5 h-4 w-4" /> Archive
              </Button>
            )}
            {!protocol && (
              <>
                <Button size="sm" variant="outline" data-testid="rerun-experiment" onClick={() => rerun.mutate()} disabled={rerun.isPending}>
                  {rerun.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1.5 h-4 w-4" />} Re-run
                </Button>
                <Button size="sm" variant="outline" data-testid="clone-experiment" onClick={() => clone.mutate()} disabled={clone.isPending}>
                  <Copy className="mr-1.5 h-4 w-4" /> Clone
                </Button>
              </>
            )}
            <Button size="sm" variant="ghost" className="text-destructive" data-testid="delete-experiment" onClick={() => setConfirmAction("delete")}>
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        }
      />

      {!protocol ? (
        <div className="space-y-4">
          {experiment.blind && experiment.revealed !== true && (
            <div className="flex items-center gap-2"><Badge variant="outline" className="gap-1">Blind arena · hidden until reveal</Badge></div>
          )}
          <ArenaResults experiment={experiment} onRefetch={() => refetch()} />
        </div>
      ) : (
        <div className="space-y-6">
          <ConfigTable configs={configs} onEdit={openConfigs} editable={isDraft} />

          {isDraft && (
            <Card className="p-4 text-sm text-muted-foreground" data-testid="draft-notice">
              This experiment is a DRAFT. Mark it ready to freeze configurations, the dataset snapshot, and reproducibility hashes before planning a benchmark.
            </Card>
          )}

          <PlanReview
            plan={plan}
            building={buildPlanMut.isPending}
            starting={startMut.isPending}
            confirmed={confirmed}
            onConfirmedChange={setConfirmed}
            onBuild={() => buildPlanMut.mutate()}
            onStart={() => startMut.mutate()}
            canBuild={isReady}
            canStart={canStart}
            disabledReason={
              isDraft ? "Mark the experiment ready before planning a benchmark."
              : isArchived ? "This experiment is archived."
              : isRunning ? "A benchmark is already running."
              : planCompleted ? "The current plan is complete. Rebuild the plan to run again."
              : null
            }
          />

          {plan && !["planned"].includes((plan.status || "").toLowerCase()) && (
            <BenchmarkProgress
              benchmark={{ plan, progress: benchmark?.progress || plan.progress, configurations: benchmark?.configurations }}
              onCancel={() => cancelMut.mutate()}
              onResume={() => resumeMut.mutate()}
              cancelling={cancelMut.isPending}
              resuming={resumeMut.isPending}
            />
          )}

          <ResultsTable experimentId={experiment.id} configs={configs} models={models} rubrics={rubrics} enabled />

          <ComparePanel experimentId={experiment.id} configs={configs} enabled={configs.length >= 2} />

          <div className="space-y-6" data-testid="evaluation-regression-section">
            <div className="flex items-center gap-2">
              <Gavel className="h-4 w-4 text-brand" />
              <span className="font-display font-semibold">Evaluation &amp; Regression</span>
            </div>
            <RegressionPanel
              experimentId={experiment.id}
              configurations={configs.filter((c) => !!c.id).map((c) => ({ id: c.id, label: c.label || c.id }))}
            />
            <EvaluationList experimentId={experiment.id} />
          </div>

          <div className="flex justify-end">
            <FindingFromResultsDialog
              experimentId={experiment.id}
              configs={configs}
              runs={(experiment.runs || []).map((r) => ({ id: r.id, model_name: r.model_name, status: r.status, dataset_case_id: r.dataset_case_id }))}
              evaluations={evalEvidence}
              datasetVersionId={datasetVersionId}
            />
          </div>

          <ProvenancePanel experimentId={experiment.id} enabled />
        </div>
      )}

      {/* Edit metadata dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent data-testid="edit-experiment-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Edit experiment</DialogTitle>
            <DialogDescription>Update the title, objective, and hypothesis while this experiment is a DRAFT.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Title</label>
              <input className="mt-1 flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm" data-testid="edit-title" value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Objective</label>
              <textarea className="mt-1 flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm" rows={3} data-testid="edit-objective" value={editObjective} onChange={(e) => setEditObjective(e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Hypothesis</label>
              <textarea className="mt-1 flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm" rows={3} data-testid="edit-hypothesis" value={editHypothesis} onChange={(e) => setEditHypothesis(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditOpen(false)}>Cancel</Button>
            <Button data-testid="edit-save" disabled={!editTitle.trim() || updateMeta.isPending} onClick={() => updateMeta.mutate()}>
              {updateMeta.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null} Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit configurations dialog */}
      <Dialog open={configOpen} onOpenChange={setConfigOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl" data-testid="config-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Edit configurations</DialogTitle>
            <DialogDescription>Add, remove, or tune configurations. Changes are only allowed while the experiment is a DRAFT.</DialogDescription>
          </DialogHeader>
          <ConfigEditor
            models={models}
            configs={configDrafts}
            onChange={(c) => { setConfigDrafts(c); setConfigError(null); }}
            error={configError}
          />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfigOpen(false)}>Cancel</Button>
            <Button
              data-testid="config-save"
              disabled={updateConfigs.isPending}
              onClick={() => {
                const err = validateConfigs(configDrafts);
                if (err) { setConfigError(err); return; }
                updateConfigs.mutate();
              }}
            >
              {updateConfigs.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null} Save configurations
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Archive / delete confirmation */}
      <AlertDialog open={confirmAction != null} onOpenChange={(open) => { if (!open) setConfirmAction(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmAction === "delete" ? "Delete this experiment?" : "Archive this experiment?"}</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmAction === "delete"
                ? "This permanently deletes the experiment, its configurations, plan, runs, and evaluations. This cannot be undone."
                : "Archiving freezes the experiment from further lifecycle actions. Existing runs and results are preserved."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid={confirmAction === "delete" ? "confirm-delete" : "confirm-archive"}
              onClick={() => (confirmAction === "delete" ? del.mutate() : archiveMut.mutate())}
            >
              {confirmAction === "delete" ? "Delete" : "Archive"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

const ConfigTable: React.FC<{ configs: ConfigurationRecord[]; editable: boolean; onEdit: () => void }> = ({ configs, editable, onEdit }) => (
  <Card className="space-y-3 p-4" data-testid="configurations-table">
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <Wrench className="h-4 w-4 text-brand" />
        <span className="font-display font-semibold">Configurations</span>
        <Badge variant="outline" className="text-[10px]">{configs.length}</Badge>
      </div>
      {editable && <Button size="sm" variant="outline" data-testid="configurations-edit" onClick={onEdit}><Pencil className="mr-1.5 h-4 w-4" /> Edit</Button>}
    </div>
    {configs.length === 0 ? (
      <EmptyState icon={<Wrench className="h-5 w-5" />} title="No configurations" description="This experiment has no frozen configurations yet." testId="configs-empty" />
    ) : (
      <div className="overflow-x-auto rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/50">
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Label</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Model</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Temperature</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Max tokens</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Top P</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">System prompt</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Control</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {configs.map((c) => (
              <TableRow key={c.id} data-testid={`config-row-${c.id}`}>
                <TableCell className="whitespace-nowrap font-medium">{c.label || "n/a"}</TableCell>
                <TableCell className="whitespace-nowrap text-xs">
                  {c.model_snapshot?.display_name || c.model_id || "n/a"}
                  <span className="ml-1 font-mono text-[10px] text-muted-foreground">{c.model_snapshot?.model_identifier || ""}</span>
                </TableCell>
                <TableCell className="font-mono text-xs">{nText(c.temperature)}</TableCell>
                <TableCell className="font-mono text-xs">{nText(c.max_tokens)}</TableCell>
                <TableCell className="font-mono text-xs">{nText(c.top_p)}</TableCell>
                <TableCell className="max-w-[240px] text-xs">
                  <span className="line-clamp-2 text-muted-foreground" title={c.system_prompt || ""}>{c.system_prompt || "n/a"}</span>
                </TableCell>
                <TableCell>{c.is_control ? <Badge variant="secondary" className="text-[10px]">control</Badge> : <span className="text-xs text-muted-foreground">n/a</span>}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    )}
  </Card>
);
