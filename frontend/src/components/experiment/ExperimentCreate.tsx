import React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Check, Database, FlaskConical, Loader2, Snowflake } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { Dataset, DatasetVersion } from "@/lib/types";
import { useModels, useRubrics } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { FamilyDot, ErrorNote, SectionHeader } from "@/components/common/Common";
import { ConfigDraft, ConfigEditor, configToPayload, emptyConfig, validateConfigs } from "./ConfigEditor";
import { ExperimentDetailResponse } from "./types";
import { cn } from "@/lib/utils";

const STEPS = ["Dataset", "Hypothesis", "Configurations", "Protocol", "Review"];
const NONE = "__none__";

export default function ExperimentCreate() {
  const navigate = useNavigate();
  const { activeId, activeProject } = useProjects();
  const { data: models = [] } = useModels();
  const { data: rubrics = [] } = useRubrics();

  const [step, setStep] = React.useState(0);
  const [datasetId, setDatasetId] = React.useState("");
  const [versionId, setVersionId] = React.useState("");
  const [title, setTitle] = React.useState("");
  const [objective, setObjective] = React.useState("");
  const [hypothesis, setHypothesis] = React.useState("");
  const [configs, setConfigs] = React.useState<ConfigDraft[]>([emptyConfig(0), emptyConfig(1)]);
  const [repetitions, setRepetitions] = React.useState("1");
  const [rubricId, setRubricId] = React.useState(NONE);
  const [judgeModelId, setJudgeModelId] = React.useState(NONE);
  const [created, setCreated] = React.useState<ExperimentDetailResponse | null>(null);

  const { data: datasets = [] } = useQuery<Dataset[]>({
    queryKey: ["datasets", activeId],
    queryFn: async () => (await api.get(`/datasets?project_id=${activeId}`)).data,
    enabled: Boolean(activeId),
  });

  const { data: versions = [], isLoading: versionsLoading } = useQuery<DatasetVersion[]>({
    queryKey: ["dataset-versions", datasetId],
    queryFn: async () => (await api.get(`/datasets/${datasetId}/versions`)).data,
    enabled: Boolean(datasetId),
  });

  const frozenVersions = versions.filter((v) => v.status === "frozen");
  const selectedVersion = frozenVersions.find((v) => v.id === versionId) || null;

  React.useEffect(() => {
    if (datasetId && versionId && !frozenVersions.some((v) => v.id === versionId)) setVersionId("");
  }, [datasetId, versions]); // eslint-disable-line

  const configError = step === 2 ? validateConfigs(configs) : null;
  const repetitionNumber = Number(repetitions);
  const repetitionsValid = Number.isInteger(repetitionNumber) && repetitionNumber >= 1 && repetitionNumber <= 20;

  const create = useMutation({
    mutationFn: async () =>
      (await api.post("/experiments", {
        project_id: activeId,
        title: title.trim(),
        objective: objective.trim(),
        hypothesis: hypothesis.trim(),
        experiment_type: "benchmark",
        protocol: {
          objective: objective.trim(),
          hypothesis: hypothesis.trim(),
          dataset_id: datasetId,
          dataset_version_id: versionId,
          rubric_id: rubricId === NONE ? null : rubricId,
          judge_model_id: judgeModelId === NONE ? null : judgeModelId,
          judge_rubric_id: null,
          variables: [],
          constants: {},
          repetitions: repetitionNumber,
          success_criteria: [],
          max_planned_calls: null,
        },
        configurations: configs.map((c, i) => configToPayload(c, i)),
      })).data as ExperimentDetailResponse,
    onSuccess: (experiment) => {
      setCreated(experiment);
      toast.success("Experiment created as DRAFT");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const markReady = useMutation({
    mutationFn: async (id: string) => (await api.post(`/experiments/${id}/ready`)).data as ExperimentDetailResponse,
    onSuccess: (experiment) => {
      toast.success("Experiment marked READY");
      navigate(`/experiments/${experiment.id}`);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const canNext =
    step === 0 ? Boolean(datasetId && versionId)
    : step === 1 ? Boolean(title.trim() && objective.trim())
    : step === 2 ? !configError
    : step === 3 ? repetitionsValid
    : true;

  if (!activeId) {
    return (
      <div className="mx-auto max-w-5xl p-4 lg:p-6">
        <SectionHeader title="New experiment" caption="Select a project to create a reproducible protocol experiment." />
        <Card className="mt-4 p-6 text-sm text-muted-foreground">No active project. Choose one from the sidebar first.</Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 lg:p-6">
      <button onClick={() => navigate("/experiments")} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Experiments
      </button>

      <SectionHeader
        title="New protocol experiment"
        caption={`A reproducible benchmark in "${activeProject?.name || "project"}". Every step is saved to the backend — nothing is simulated.`}
      />

      {!created && (
        <div className="flex flex-wrap items-center gap-2" data-testid="create-stepper">
          {STEPS.map((label, index) => (
            <React.Fragment key={label}>
              <span className={cn("flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs", index === step ? "border-primary bg-accent" : "border-border text-muted-foreground")}>
                <span className="font-mono">{index + 1}</span> {label}
              </span>
              {index < STEPS.length - 1 && <span className="text-muted-foreground">→</span>}
            </React.Fragment>
          ))}
        </div>
      )}

      {created ? (
        <Card className="space-y-4 p-6" data-testid="create-success">
          <div className="flex items-center gap-2">
            <Check className="h-5 w-5 text-[hsl(var(--brand-emerald))]" />
            <h3 className="font-display text-lg font-semibold">Experiment created</h3>
            <Badge variant="outline" className="text-[10px] uppercase">{created.status}</Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            “{created.title}” is saved as a DRAFT. Mark it ready to freeze its configurations, dataset snapshot, and hashes.
          </p>
          <ErrorNote message={markReady.isError ? apiError(markReady.error) : null} />
          <div className="flex flex-wrap items-center gap-2">
            <Button data-testid="mark-ready-after-create" disabled={markReady.isPending} onClick={() => markReady.mutate(created.id)}>
              {markReady.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Snowflake className="mr-1.5 h-4 w-4" />} Mark ready
            </Button>
            <Button variant="outline" onClick={() => navigate(`/experiments/${created.id}`)} data-testid="open-created-experiment">
              Open experiment
            </Button>
          </div>
        </Card>
      ) : (
        <Card className="space-y-4 p-5">
          {step === 0 && (
            <div className="space-y-4" data-testid="create-step-dataset">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Dataset</label>
                <Select value={datasetId || NONE} onValueChange={(v) => { setDatasetId(v === NONE ? "" : v); setVersionId(""); }}>
                  <SelectTrigger className="mt-1 h-9" data-testid="create-dataset-select"><SelectValue placeholder="Select dataset" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>Select…</SelectItem>
                    {datasets.map((d) => <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>)}
                  </SelectContent>
                </Select>
                {datasets.length === 0 && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    No datasets in this project. <Link to="/datasets" className="text-primary underline underline-offset-4">Create a dataset</Link> first.
                  </p>
                )}
              </div>

              {datasetId && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Dataset version (frozen only)</label>
                  {versionsLoading ? (
                    <p className="mt-1 text-sm text-muted-foreground">Loading versions…</p>
                  ) : frozenVersions.length === 0 ? (
                    <div className="mt-1 rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground" data-testid="no-frozen-version">
                      <p className="flex items-center gap-1.5"><Database className="h-4 w-4" /> No frozen version for this dataset.</p>
                      <p className="mt-1 text-xs">A protocol experiment must pin a frozen dataset version. Freeze one from the dataset page.</p>
                      <Link to={`/datasets/${datasetId}`} className="mt-2 inline-block text-primary underline underline-offset-4">Open dataset to freeze a version →</Link>
                    </div>
                  ) : (
                    <Select value={versionId || NONE} onValueChange={(v) => setVersionId(v === NONE ? "" : v)}>
                      <SelectTrigger className="mt-1 h-9" data-testid="create-version-select"><SelectValue placeholder="Select frozen version" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>Select…</SelectItem>
                        {frozenVersions.map((v) => (
                          <SelectItem key={v.id} value={v.id}>v{v.version} · {v.case_count} case(s)</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </div>
              )}

              {selectedVersion && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground" data-testid="frozen-version-note">
                  <Snowflake className="h-3.5 w-3.5" /> Frozen v{selectedVersion.version} · {selectedVersion.case_count} case(s)
                  {selectedVersion.content_hash ? ` · ${selectedVersion.content_hash.slice(0, 12)}…` : ""}
                </div>
              )}
            </div>
          )}

          {step === 1 && (
            <div className="space-y-3" data-testid="create-step-hypothesis">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Title</label>
                <Input value={title} onChange={(e) => setTitle(e.target.value)} className="mt-1" data-testid="create-title" placeholder="Experiment title" />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Objective</label>
                <Textarea value={objective} onChange={(e) => setObjective(e.target.value)} rows={3} className="mt-1" data-testid="create-objective" placeholder="What are you trying to measure?" />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Hypothesis (optional)</label>
                <Textarea value={hypothesis} onChange={(e) => setHypothesis(e.target.value)} rows={3} className="mt-1" data-testid="create-hypothesis" placeholder="What do you expect, and why?" />
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-3" data-testid="create-step-configurations">
              <p className="text-sm text-muted-foreground">Add at least two configurations. Each is frozen with a model/pricing snapshot when you mark ready.</p>
              <ConfigEditor models={models} configs={configs} onChange={setConfigs} error={configError} />
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4" data-testid="create-step-protocol">
              <div className="w-56">
                <label className="text-xs font-medium text-muted-foreground">Repetitions (1–20)</label>
                <Input
                  type="number"
                  min="1"
                  max="20"
                  value={repetitions}
                  onChange={(e) => setRepetitions(e.target.value)}
                  className="mt-1 h-9"
                  data-testid="create-repetitions"
                />
                {!repetitionsValid && <p className="mt-1 text-xs text-destructive">Repetitions must be between 1 and 20.</p>}
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Rubric (optional)</label>
                  <Select value={rubricId} onValueChange={setRubricId}>
                    <SelectTrigger className="mt-1 h-9" data-testid="create-rubric"><SelectValue placeholder="No rubric" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NONE}>No rubric</SelectItem>
                      {rubrics.map((r) => <SelectItem key={r.id} value={r.id}>{r.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Judge model (optional)</label>
                  <Select value={judgeModelId} onValueChange={setJudgeModelId}>
                    <SelectTrigger className="mt-1 h-9" data-testid="create-judge-model"><SelectValue placeholder="No judge model" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NONE}>No judge model</SelectItem>
                      {models.filter((m) => m.enabled).map((m) => (
                        <SelectItem key={m.id} value={m.id}>
                          <span className="flex items-center gap-2"><FamilyDot family={m.family} /> {m.display_name}</span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="space-y-3 text-sm" data-testid="create-step-review">
              <div className="grid gap-2 sm:grid-cols-2">
                <Review label="Title" value={title.trim() || "n/a"} />
                <Review label="Dataset version" value={selectedVersion ? `v${selectedVersion.version} · ${selectedVersion.case_count} case(s)` : "n/a"} />
                <Review label="Repetitions" value={repetitionsValid ? repetitions : "n/a"} />
                <Review label="Configurations" value={`${configs.length}`} />
                <Review label="Rubric" value={rubricId === NONE ? "none" : rubrics.find((r) => r.id === rubricId)?.name || "n/a"} />
                <Review label="Judge model" value={judgeModelId === NONE ? "none" : models.find((m) => m.id === judgeModelId)?.display_name || "n/a"} />
              </div>
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Objective</h4>
                <p className="whitespace-pre-wrap text-sm">{objective.trim() || "n/a"}</p>
              </div>
              {hypothesis.trim() && (
                <div>
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Hypothesis</h4>
                  <p className="whitespace-pre-wrap text-sm">{hypothesis.trim()}</p>
                </div>
              )}
              <div className="space-y-1">
                {configs.map((c, i) => {
                  const model = models.find((m) => m.id === c.model_id);
                  return (
                    <div key={c.key} className="flex flex-wrap items-center gap-2 rounded-md border border-border px-3 py-2 text-xs">
                      <span className="font-medium">{c.label || `Configuration ${i + 1}`}</span>
                      {c.is_control && <Badge variant="secondary" className="text-[10px]">control</Badge>}
                      <span className="text-muted-foreground">{model?.display_name || "no model"}</span>
                      <span className="font-mono text-[10px] text-muted-foreground">temp {c.temperature || "default"} · max {c.max_tokens || "default"} · top_p {c.top_p || "default"}</span>
                    </div>
                  );
                })}
              </div>
              <ErrorNote message={create.isError ? apiError(create.error) : null} />
            </div>
          )}

          <div className="flex items-center justify-between border-t border-border pt-4">
            <Button variant="ghost" onClick={() => (step === 0 ? navigate("/experiments") : setStep(step - 1))} data-testid="create-back">
              {step === 0 ? "Cancel" : "Back"}
            </Button>
            {step < STEPS.length - 1 ? (
              <Button disabled={!canNext} onClick={() => setStep(step + 1)} data-testid="create-next">Next</Button>
            ) : (
              <Button
                data-testid="create-submit"
                disabled={create.isPending || !datasetId || !versionId || !title.trim() || !objective.trim() || Boolean(validateConfigs(configs)) || !repetitionsValid}
                onClick={() => create.mutate()}
              >
                {create.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <FlaskConical className="mr-1.5 h-4 w-4" />} Create experiment
              </Button>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}

const Review: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="flex flex-col rounded-md border border-border px-3 py-2">
    <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <span className="text-sm">{value}</span>
  </div>
);
