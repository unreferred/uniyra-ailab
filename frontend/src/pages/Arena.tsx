import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Check, Loader2, Play, ShieldQuestion, Swords, ExternalLink } from "lucide-react";
import {
  api,
  apiError,
  uploadAttachments,
  createAgentArena,
  createPromptArena,
  listAgentVersions,
  listPromptVersions,
} from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import {
  useArenaV6Catalog,
  useArenasV6,
  useArenaV6Summary,
  useAttachments,
  useKnowledgeCollections,
  useModels,
  useRubrics,
  useAgents,
  usePromptSpecs,
} from "@/lib/hooks";
import {
  Agent,
  AgentVersion,
  ArenaV6Catalog,
  ArenaV6ConfigMetrics,
  ArenaV6Experiment,
  BenchmarkPlan,
  Dataset,
  DatasetVersion,
  Experiment,
  PromptSpec,
  PromptVersion,
} from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { MultiModelSelect } from "@/components/common/ModelPicker";
import { EmptyState, ErrorNote, SectionHeader } from "@/components/common/Common";
import { ArenaResults } from "@/components/arena/ArenaResults";
import { PlanReview } from "@/components/experiment/PlanReview";
import { BenchmarkResponse } from "@/components/experiment/types";
import { nCost, nLatency, nNumber, nRate, nText } from "@/components/experiment/format";
import { cn } from "@/lib/utils";

const NONE = "__none__";

// ---------------------------------------------------------------------------
// Model Arena — the existing ad-hoc comparison UI, preserved unchanged.
// ---------------------------------------------------------------------------

function ModelArenaPanel() {
  const { activeId, activeProject } = useProjects();
  const { data: models = [] } = useModels();
  const enabled = models.filter((m) => m.enabled);
  const navigate = useNavigate();

  const [selected, setSelected] = useState<string[]>([]);
  const [task, setTask] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [blind, setBlind] = useState(false);
  const [result, setResult] = useState<Experiment | null>(null);
  const [attachmentIds, setAttachmentIds] = useState<string[]>([]);
  const [collectionIds, setCollectionIds] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);

  const toggle = (id: string) => setSelected((s) => s.includes(id) ? s.filter((x) => x !== id) : (s.length >= 4 ? s : [...s, id]));
  const toggleCollection = (id: string) =>
    setCollectionIds((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  const attachmentsQuery = useAttachments(activeId);
  const uploadedAttachments: any[] = Array.isArray((attachmentsQuery.data as any)?.items)
    ? (attachmentsQuery.data as any).items
    : [];
  const collectionsQuery = useKnowledgeCollections(activeId);
  const collectionsData: any = collectionsQuery.data;
  const collectionsList: any[] = Array.isArray(collectionsData)
    ? collectionsData
    : collectionsData?.items || collectionsData?.collections || [];

  const needsVision = uploadedAttachments
    .filter((a) => attachmentIds.includes(a.id))
    .some((a) => a.kind === "image" || (a.mime_type || "").startsWith("image/"));
  const modelsById = new Map(models.map((m: any) => [m.id, m]));
  const visionUnsupported = needsVision
    ? selected
        .map((id) => modelsById.get(id))
        .filter((m: any) => m && !((m.capabilities as any)?.vision ?? (m.modalities as any)?.input?.includes("image")))
        .map((m: any) => m.display_name || m.id)
    : [];
  const valid = selected.length >= 2 && task.trim().length > 0 && visionUnsupported.length === 0;

  const onFiles = async (files: FileList | null) => {
    if (!files || files.length === 0 || !activeId) return;
    setUploading(true);
    try {
      const created = await uploadAttachments(activeId, Array.from(files));
      const ids = created.map((a: any) => a.id).filter(Boolean);
      setAttachmentIds((prev) => [...prev, ...ids.filter((id: string) => !prev.includes(id))]);
      attachmentsQuery.refetch();
      toast.success(`Uploaded ${ids.length} attachment${ids.length === 1 ? "" : "s"}`);
    } catch (e) {
      toast.error(apiError(e));
    } finally {
      setUploading(false);
    }
  };

  const run = useMutation({
    mutationFn: async () => (await api.post("/arena/run", {
      project_id: activeId, task, system_prompt: systemPrompt, model_ids: selected, blind, params: { max_tokens: 1024 },
      attachment_ids: attachmentIds, knowledge_collection_ids: collectionIds,
    })).data,
    onSuccess: (data) => {
      // Blind responses are already identity-free views; keep them as returned
      // and only reveal through an explicit POST /reveal.
      const exp: Experiment = { ...data.experiment, runs: data.runs, evaluations: [] };
      setResult(exp);
      const failed = data.runs.filter((r: any) => r.status === "error").length;
      if (failed) toast.warning(`${failed} of ${data.runs.length} models failed — successful runs are preserved`);
      else toast.success("Arena complete");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const refetch = async () => {
    if (!result) return;
    const { data } = await api.get(`/experiments/${result.id}`);
    setResult(data);
  };

  if (!activeId) {
    return <div className="p-6"><EmptyState icon={<Swords className="h-6 w-6" />} title="No project selected" description="Select or create a project to run an Arena." /></div>;
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6">
      <SectionHeader title="Model Arena" caption={`Run one task across up to 4 models in "${activeProject?.name}" and compare, blind-test, and judge.`} />

      <Card className="space-y-4 p-4" data-testid="arena-config">
        <div>
          <label className="text-xs font-medium text-muted-foreground">Select models ({selected.length}/4)</label>
          <div className="mt-2">
            {enabled.length === 0
              ? <p className="text-sm text-muted-foreground">No enabled models. Add one in Models.</p>
              : <MultiModelSelect models={enabled} selected={selected} onToggle={toggle} max={4} />}
          </div>
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground">Task prompt</label>
          <Textarea data-testid="arena-task-input" value={task} onChange={(e) => setTask(e.target.value)} rows={4} className="mt-1" placeholder="Describe the task all models should attempt…" />
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground">System prompt (optional)</label>
          <Textarea data-testid="arena-system-input" value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} rows={2} className="mt-1" placeholder="Optional shared system instruction" />
        </div>
        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-sm" htmlFor="blind">
            <ShieldQuestion className="h-4 w-4 text-muted-foreground" />
            <span>Blind arena <span className="text-muted-foreground">— hide model identity until you reveal</span></span>
          </label>
          <Switch id="blind" data-testid="arena-blind-toggle" checked={blind} onCheckedChange={setBlind} />
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground">Shared attachments</label>
          <div className="mt-1 flex items-center gap-2">
            <Input
              type="file"
              multiple
              data-testid="arena-attachment-input"
              disabled={uploading}
              onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }}
            />
            {uploading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
          </div>
          {attachmentIds.length > 0 ? (
            <ul className="mt-2 space-y-1" data-testid="arena-attachment-list">
              {attachmentIds.map((id) => {
                const match = uploadedAttachments.find((a) => a.id === id);
                return (
                  <li key={id} className="flex items-center justify-between gap-2 rounded-md border border-border/60 px-2 py-1 text-xs" data-testid={`arena-attachment-${id}`}>
                    <span className="truncate">{match?.original_filename || match?.filename || id}</span>
                    <Button size="sm" variant="ghost" className="h-6 px-2 text-[11px]" data-testid={`arena-attachment-remove-${id}`} onClick={() => setAttachmentIds((s) => s.filter((x) => x !== id))}>
                      Remove
                    </Button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="mt-1 text-[11px] text-muted-foreground" data-testid="arena-attachment-empty">No shared attachments.</p>
          )}
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground">Shared knowledge collections</label>
          {collectionsQuery.isLoading ? (
            <p className="mt-1 text-[11px] text-muted-foreground">Loading collections…</p>
          ) : collectionsList.length === 0 ? (
            <p className="mt-1 text-[11px] text-muted-foreground" data-testid="arena-knowledge-empty">No knowledge collections in this project.</p>
          ) : (
            <div className="mt-1 space-y-1" data-testid="arena-knowledge-list">
              {collectionsList.map((c: any) => (
                <label key={c.id} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-xs hover:bg-accent/50" data-testid={`arena-knowledge-${c.id}`}>
                  <Checkbox checked={collectionIds.includes(c.id)} onCheckedChange={() => toggleCollection(c.id)} />
                  <span className="truncate">{c.name}</span>
                </label>
              ))}
            </div>
          )}
        </div>
        <div data-testid="arena-validation">
          {selected.length < 2 && <p className="text-xs text-muted-foreground">Select at least 2 models.</p>}
          {!task.trim() && <p className="text-xs text-muted-foreground">Enter a task prompt.</p>}
          {needsVision && (
            <p className="text-xs text-muted-foreground" data-testid="arena-vision-note">
              Shared image attachments require vision-capable models.
            </p>
          )}
          {visionUnsupported.length > 0 && (
            <p className="text-xs text-destructive" data-testid="arena-vision-error">
              {visionUnsupported.join(", ")} {visionUnsupported.length === 1 ? "does" : "do"} not support image input.
            </p>
          )}
        </div>
        {run.isError && <ErrorNote message={apiError(run.error)} />}
        <div className="flex justify-end">
          <Button data-testid="arena-run" disabled={!valid || run.isPending} onClick={() => run.mutate()}>
            {run.isPending ? <><Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> Running {selected.length} models…</> : <><Play className="mr-1.5 h-4 w-4" /> Run Arena</>}
          </Button>
        </div>
      </Card>

      {result && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="font-display text-lg font-semibold">Results</h3>
            <Button size="sm" variant="outline" data-testid="open-experiment-button" onClick={() => navigate(`/experiments/${result.id}`)}>
              <ExternalLink className="mr-1.5 h-4 w-4" /> Open as experiment
            </Button>
          </div>
          <ArenaResults experiment={result} onRefetch={refetch} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// V6.8 shared helpers for Agent Arena / Prompt Arena.
// ---------------------------------------------------------------------------

function evaluationKeysOf(catalog?: ArenaV6Catalog): string[] {
  const raw = catalog?.evaluation_keys ?? [];
  return raw
    .map((k) => (typeof k === "string" ? k : k?.key ?? ""))
    .filter((k): k is string => Boolean(k));
}

function toggleId(list: string[], id: string): string[] {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}

interface AgentVersionOption { id: string; agent: Agent; version: AgentVersion }

function useSelectableAgentVersions(projectId: string): AgentVersionOption[] {
  const { data: agents = [] } = useAgents(projectId);
  const versionQueries = useQueries({
    queries: agents.map((agent) => ({
      queryKey: ["agent-versions", agent.id],
      queryFn: async () => listAgentVersions(agent.id as string),
      enabled: !!agent.id,
    })),
  });
  return useMemo(() => {
    const rows: AgentVersionOption[] = [];
    agents.forEach((agent, index) => {
      const versions = (versionQueries[index]?.data as AgentVersion[] | undefined) ?? [];
      versions.forEach((version) => {
        if (version.id && (version.status === "frozen" || version.status === "deprecated")) {
          rows.push({ id: version.id, agent, version });
        }
      });
    });
    return rows;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agents, versionQueries]);
}

interface PromptVersionOption { id: string; prompt: PromptSpec; version: PromptVersion }

function useFrozenPromptVersions(projectId: string): PromptVersionOption[] {
  const { data: prompts = [] } = usePromptSpecs(projectId);
  const versionQueries = useQueries({
    queries: prompts.map((prompt) => ({
      queryKey: ["prompt-versions", prompt.id],
      queryFn: async () => listPromptVersions(prompt.id as string),
      enabled: !!prompt.id,
    })),
  });
  return useMemo(() => {
    const rows: PromptVersionOption[] = [];
    prompts.forEach((prompt, index) => {
      const versions = (versionQueries[index]?.data as PromptVersion[] | undefined) ?? [];
      versions.forEach((version) => {
        if (version.id && version.status === "frozen") {
          rows.push({ id: version.id, prompt, version });
        }
      });
    });
    return rows;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prompts, versionQueries]);
}

const DatasetPicker: React.FC<{
  testId: string;
  versionTestId: string;
  projectId: string;
  datasetId: string;
  versionId: string;
  onDatasetChange: (id: string) => void;
  onVersionChange: (id: string) => void;
}> = ({ testId, versionTestId, projectId, datasetId, versionId, onDatasetChange, onVersionChange }) => {
  const { data: datasets = [] } = useQuery<Dataset[]>({
    queryKey: ["arena-v6-datasets", projectId],
    queryFn: async () => (await api.get("/datasets", { params: { project_id: projectId } })).data,
    enabled: !!projectId,
  });
  const { data: versions = [] } = useQuery<DatasetVersion[]>({
    queryKey: ["arena-v6-dataset-versions", datasetId],
    queryFn: async () => (await api.get(`/datasets/${datasetId}/versions`)).data,
    enabled: !!datasetId,
  });
  const frozenVersions = versions.filter((v) => v.status === "frozen");

  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Dataset</label>
        <select
          className="mt-1 h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
          data-testid={testId}
          value={datasetId || NONE}
          onChange={(e) => onDatasetChange(e.target.value === NONE ? "" : e.target.value)}
        >
          <option value={NONE}>Select…</option>
          {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
        {datasets.length === 0 && <p className="mt-1 text-[11px] text-muted-foreground">No datasets in this project.</p>}
      </div>
      {datasetId && (
        <div>
          <label className="text-xs font-medium text-muted-foreground">Frozen dataset version</label>
          {frozenVersions.length === 0 ? (
            <p className="mt-1 text-xs text-muted-foreground" data-testid={`${testId}-no-frozen`}>No frozen version for this dataset.</p>
          ) : (
            <select
              className="mt-1 h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
              data-testid={versionTestId}
              value={versionId || NONE}
              onChange={(e) => onVersionChange(e.target.value === NONE ? "" : e.target.value)}
            >
              <option value={NONE}>Select…</option>
              {frozenVersions.map((v) => <option key={v.id} value={v.id}>{`v${v.version} · ${nNumber(v.case_count)} case(s)`}</option>)}
            </select>
          )}
        </div>
      )}
    </div>
  );
};

const ArenaSharedFields: React.FC<{
  projectId: string;
  title: string; setTitle: (v: string) => void;
  objective: string; setObjective: (v: string) => void;
  hypothesis: string; setHypothesis: (v: string) => void;
  repetitions: string; setRepetitions: (v: string) => void;
  evalKeys: string[]; selectedEvalKeys: string[]; onToggleEvalKey: (k: string) => void;
  rubricId: string; setRubricId: (v: string) => void;
  judgeModelId: string; setJudgeModelId: (v: string) => void;
  testPrefix: string;
}> = ({ title, setTitle, objective, setObjective, hypothesis, setHypothesis, repetitions, setRepetitions,
  evalKeys, selectedEvalKeys, onToggleEvalKey, rubricId, setRubricId, judgeModelId, setJudgeModelId, testPrefix }) => {
  const { data: models = [] } = useModels();
  const { data: rubrics = [] } = useRubrics();
  const repetitionNumber = Number(repetitions);
  const repetitionsValid = Number.isInteger(repetitionNumber) && repetitionNumber >= 1 && repetitionNumber <= 20;
  return (
    <div className="space-y-4">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Title</label>
        <Input value={title} onChange={(e) => setTitle(e.target.value)} className="mt-1" data-testid={`${testPrefix}-title`} placeholder="Arena title" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="text-xs font-medium text-muted-foreground">Objective (optional)</label>
          <Textarea value={objective} onChange={(e) => setObjective(e.target.value)} rows={2} className="mt-1" data-testid={`${testPrefix}-objective`} placeholder="What are you trying to measure?" />
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground">Hypothesis (optional)</label>
          <Textarea value={hypothesis} onChange={(e) => setHypothesis(e.target.value)} rows={2} className="mt-1" data-testid={`${testPrefix}-hypothesis`} placeholder="What do you expect, and why?" />
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="w-40">
          <label className="text-xs font-medium text-muted-foreground">Repetitions (1–20)</label>
          <Input type="number" min="1" max="20" value={repetitions} onChange={(e) => setRepetitions(e.target.value)} className="mt-1 h-9" data-testid={`${testPrefix}-repetitions`} />
          {!repetitionsValid && <p className="mt-1 text-xs text-destructive">Repetitions must be between 1 and 20.</p>}
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground">Rubric (optional)</label>
          <select
            className="mt-1 h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
            data-testid={`${testPrefix}-rubric`}
            value={rubricId}
            onChange={(e) => setRubricId(e.target.value)}
          >
            <option value={NONE}>No rubric</option>
            {rubrics.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground">Judge model (optional)</label>
          <select
            className="mt-1 h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
            data-testid={`${testPrefix}-judge-model`}
            value={judgeModelId}
            onChange={(e) => setJudgeModelId(e.target.value)}
          >
            <option value={NONE}>No judge model</option>
            {models.filter((m) => m.enabled).map((m) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
          </select>
        </div>
      </div>
      <div>
        <label className="text-xs font-medium text-muted-foreground">Evaluation keys ({selectedEvalKeys.length})</label>
        {evalKeys.length === 0 ? (
          <p className="mt-1 text-xs text-muted-foreground">No evaluation keys available from the catalog.</p>
        ) : (
          <div className="mt-1 flex flex-wrap gap-2" data-testid={`${testPrefix}-evaluation-keys`}>
            {evalKeys.map((k) => {
              const active = selectedEvalKeys.includes(k);
              return (
                <button
                  key={k}
                  type="button"
                  data-testid={`${testPrefix}-eval-${k}`}
                  onClick={() => onToggleEvalKey(k)}
                  className={cn(
                    "inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs transition-colors",
                    active ? "border-primary bg-accent text-foreground" : "border-border text-muted-foreground hover:bg-accent/50"
                  )}
                >
                  {active && <Check className="h-3 w-3" />} {k}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Agent Arena
// ---------------------------------------------------------------------------

function AgentArenaPanel({ projectId, onCreated }: { projectId: string; onCreated: (id: string) => void }) {
  const qc = useQueryClient();
  const { activeProject } = useProjects();
  const { data: catalog } = useArenaV6Catalog();
  const { data: models = [] } = useModels();
  const enabledModels = models.filter((m) => m.enabled);
  const agentVersionRows = useSelectableAgentVersions(projectId);
  const evalKeys = evaluationKeysOf(catalog);

  const [datasetId, setDatasetId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [repetitions, setRepetitions] = useState("1");
  const [rubricId, setRubricId] = useState(NONE);
  const [judgeModelId, setJudgeModelId] = useState(NONE);
  const [selectedAgentVersions, setSelectedAgentVersions] = useState<string[]>([]);
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [selectedEvalKeys, setSelectedEvalKeys] = useState<string[]>([]);

  const repetitionNumber = Number(repetitions);
  const repetitionsValid = Number.isInteger(repetitionNumber) && repetitionNumber >= 1 && repetitionNumber <= 20;
  const canSubmit = Boolean(datasetId && versionId && title.trim() && selectedAgentVersions.length > 0 && repetitionsValid);

  const create = useMutation({
    mutationFn: async () => createAgentArena({
      project_id: projectId,
      title: title.trim(),
      dataset_id: datasetId,
      dataset_version_id: versionId,
      agent_version_ids: selectedAgentVersions,
      model_ids: selectedModels,
      objective: objective.trim() || undefined,
      hypothesis: hypothesis.trim() || undefined,
      repetitions: repetitionNumber,
      evaluation_keys: selectedEvalKeys,
      rubric_id: rubricId === NONE ? null : rubricId,
      judge_model_id: judgeModelId === NONE ? null : judgeModelId,
    }),
    onSuccess: (experiment) => {
      toast.success("Agent arena created");
      qc.invalidateQueries({ queryKey: ["arenas-v6"] });
      if (experiment?.id) onCreated(experiment.id);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="arena-agent-create">
      <SectionHeader
        title="Agent Arena"
        caption={`Compare frozen agent versions on a pinned dataset in "${activeProject?.name || "this project"}". Leave models blank to use each agent version's fixed model.`}
      />
      <Card className="space-y-4 p-4">
        <DatasetPicker
          testId="arena-agent-dataset"
          versionTestId="arena-agent-dataset-version"
          projectId={projectId}
          datasetId={datasetId}
          versionId={versionId}
          onDatasetChange={(id) => { setDatasetId(id); setVersionId(""); }}
          onVersionChange={setVersionId}
        />

        <div>
          <label className="text-xs font-medium text-muted-foreground">Agent versions (frozen or deprecated) ({selectedAgentVersions.length})</label>
          {agentVersionRows.length === 0 ? (
            <p className="mt-1 text-xs text-muted-foreground" data-testid="arena-agent-versions-empty">No frozen or deprecated agent versions in this project.</p>
          ) : (
            <div className="mt-1 space-y-1" data-testid="arena-agent-versions">
              {agentVersionRows.map((row) => {
                const active = selectedAgentVersions.includes(row.id);
                return (
                  <button
                    key={row.id}
                    type="button"
                    data-testid={`arena-agent-version-${row.id}`}
                    onClick={() => setSelectedAgentVersions((s) => toggleId(s, row.id))}
                    className={cn(
                      "flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-sm transition-colors",
                      active ? "border-primary bg-accent" : "border-border hover:bg-accent/50"
                    )}
                  >
                    <span className="truncate">{nText(row.agent.name)} · v{nNumber(row.version.version)}</span>
                    <span className="flex items-center gap-2">
                      <Badge variant="outline" className="text-[10px]">{nText(row.version.status)}</Badge>
                      {active && <Check className="h-4 w-4 text-primary" />}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div>
          <label className="text-xs font-medium text-muted-foreground">Models (optional) — blank uses each agent version's model</label>
          <div className="mt-2" data-testid="arena-agent-models">
            {enabledModels.length === 0
              ? <p className="text-sm text-muted-foreground">No enabled models.</p>
              : <MultiModelSelect models={enabledModels} selected={selectedModels} onToggle={(id) => setSelectedModels((s) => toggleId(s, id))} max={8} />}
          </div>
        </div>

        <ArenaSharedFields
          projectId={projectId}
          title={title} setTitle={setTitle}
          objective={objective} setObjective={setObjective}
          hypothesis={hypothesis} setHypothesis={setHypothesis}
          repetitions={repetitions} setRepetitions={setRepetitions}
          evalKeys={evalKeys} selectedEvalKeys={selectedEvalKeys} onToggleEvalKey={(k) => setSelectedEvalKeys((s) => toggleId(s, k))}
          rubricId={rubricId} setRubricId={setRubricId}
          judgeModelId={judgeModelId} setJudgeModelId={setJudgeModelId}
          testPrefix="arena-agent"
        />

        {create.isError && <ErrorNote message={apiError(create.error)} />}
        <div className="flex justify-end">
          <Button data-testid="arena-agent-submit" disabled={!canSubmit || create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? <><Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> Creating…</> : "Create arena"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Prompt Arena
// ---------------------------------------------------------------------------

function PromptArenaPanel({ projectId, onCreated }: { projectId: string; onCreated: (id: string) => void }) {
  const qc = useQueryClient();
  const { activeProject } = useProjects();
  const { data: catalog } = useArenaV6Catalog();
  const { data: models = [] } = useModels();
  const enabledModels = models.filter((m) => m.enabled);
  const promptVersionRows = useFrozenPromptVersions(projectId);
  const agentVersionRows = useSelectableAgentVersions(projectId);
  const evalKeys = evaluationKeysOf(catalog);

  const [datasetId, setDatasetId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [modelId, setModelId] = useState("");
  const [agentVersionId, setAgentVersionId] = useState(NONE);
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [repetitions, setRepetitions] = useState("1");
  const [rubricId, setRubricId] = useState(NONE);
  const [judgeModelId, setJudgeModelId] = useState(NONE);
  const [selectedPromptVersions, setSelectedPromptVersions] = useState<string[]>([]);
  const [selectedEvalKeys, setSelectedEvalKeys] = useState<string[]>([]);

  const repetitionNumber = Number(repetitions);
  const repetitionsValid = Number.isInteger(repetitionNumber) && repetitionNumber >= 1 && repetitionNumber <= 20;
  const canSubmit = Boolean(datasetId && versionId && title.trim() && modelId && selectedPromptVersions.length > 0 && repetitionsValid);

  const create = useMutation({
    mutationFn: async () => createPromptArena({
      project_id: projectId,
      title: title.trim(),
      dataset_id: datasetId,
      dataset_version_id: versionId,
      prompt_version_ids: selectedPromptVersions,
      model_id: modelId,
      agent_version_id: agentVersionId === NONE ? null : agentVersionId,
      objective: objective.trim() || undefined,
      hypothesis: hypothesis.trim() || undefined,
      repetitions: repetitionNumber,
      evaluation_keys: selectedEvalKeys,
      rubric_id: rubricId === NONE ? null : rubricId,
      judge_model_id: judgeModelId === NONE ? null : judgeModelId,
    }),
    onSuccess: (experiment) => {
      toast.success("Prompt arena created");
      qc.invalidateQueries({ queryKey: ["arenas-v6"] });
      if (experiment?.id) onCreated(experiment.id);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="arena-prompt-create">
      <SectionHeader
        title="Prompt Arena"
        caption={`Compare frozen prompt versions against one model on a pinned dataset in "${activeProject?.name || "this project"}".`}
      />
      <Card className="space-y-4 p-4">
        <DatasetPicker
          testId="arena-prompt-dataset"
          versionTestId="arena-prompt-dataset-version"
          projectId={projectId}
          datasetId={datasetId}
          versionId={versionId}
          onDatasetChange={(id) => { setDatasetId(id); setVersionId(""); }}
          onVersionChange={setVersionId}
        />

        <div>
          <label className="text-xs font-medium text-muted-foreground">Model (required)</label>
          <select
            className="mt-1 h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
            data-testid="arena-prompt-model"
            value={modelId || NONE}
            onChange={(e) => setModelId(e.target.value === NONE ? "" : e.target.value)}
          >
            <option value={NONE}>Select…</option>
            {enabledModels.map((m) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
          </select>
        </div>

        <div>
          <label className="text-xs font-medium text-muted-foreground">Frozen prompt versions ({selectedPromptVersions.length})</label>
          {promptVersionRows.length === 0 ? (
            <p className="mt-1 text-xs text-muted-foreground" data-testid="arena-prompt-versions-empty">No frozen prompt versions in this project.</p>
          ) : (
            <div className="mt-1 space-y-1" data-testid="arena-prompt-versions">
              {promptVersionRows.map((row) => {
                const active = selectedPromptVersions.includes(row.id);
                return (
                  <button
                    key={row.id}
                    type="button"
                    data-testid={`arena-prompt-version-${row.id}`}
                    onClick={() => setSelectedPromptVersions((s) => toggleId(s, row.id))}
                    className={cn(
                      "flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-sm transition-colors",
                      active ? "border-primary bg-accent" : "border-border hover:bg-accent/50"
                    )}
                  >
                    <span className="truncate">{nText(row.prompt.name)} · v{nNumber(row.version.version)}</span>
                    <span className="flex items-center gap-2">
                      <Badge variant="outline" className="text-[10px]">{nText(row.version.status)}</Badge>
                      {active && <Check className="h-4 w-4 text-primary" />}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div>
          <label className="text-xs font-medium text-muted-foreground">Agent version (optional)</label>
          {agentVersionRows.length === 0 ? (
            <p className="mt-1 text-xs text-muted-foreground" data-testid="arena-prompt-agent-versions-empty">No frozen or deprecated agent versions in this project.</p>
          ) : (
            <select
              className="mt-1 h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
              data-testid="arena-prompt-agent-version"
              value={agentVersionId}
              onChange={(e) => setAgentVersionId(e.target.value)}
            >
              <option value={NONE}>No agent version</option>
              {agentVersionRows.map((row) => (
                <option key={row.id} value={row.id}>{`${nText(row.agent.name)} · v${nNumber(row.version.version)}`}</option>
              ))}
            </select>
          )}
        </div>

        <ArenaSharedFields
          projectId={projectId}
          title={title} setTitle={setTitle}
          objective={objective} setObjective={setObjective}
          hypothesis={hypothesis} setHypothesis={setHypothesis}
          repetitions={repetitions} setRepetitions={setRepetitions}
          evalKeys={evalKeys} selectedEvalKeys={selectedEvalKeys} onToggleEvalKey={(k) => setSelectedEvalKeys((s) => toggleId(s, k))}
          rubricId={rubricId} setRubricId={setRubricId}
          judgeModelId={judgeModelId} setJudgeModelId={setJudgeModelId}
          testPrefix="arena-prompt"
        />

        {create.isError && <ErrorNote message={apiError(create.error)} />}
        <div className="flex justify-end">
          <Button data-testid="arena-prompt-submit" disabled={!canSubmit || create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? <><Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> Creating…</> : "Create arena"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Plan / start flow (existing benchmark endpoints) + per-config summary.
// ---------------------------------------------------------------------------

const ArenaV6PlanFlow: React.FC<{ experimentId: string; experiment?: ArenaV6Experiment | null }> = ({ experimentId, experiment }) => {
  const qc = useQueryClient();
  const [confirmed, setConfirmed] = useState(false);

  const { data: benchmark } = useQuery<BenchmarkResponse>({
    queryKey: ["arena-v6-benchmark", experimentId],
    queryFn: async () => (await api.get(`/experiments/${experimentId}/benchmark`)).data,
    enabled: !!experimentId,
  });

  const plan: BenchmarkPlan | null = benchmark?.plan ?? null;

  const build = useMutation({
    mutationFn: async () => (await api.post(`/experiments/${experimentId}/benchmark/plan`)).data,
    onSuccess: () => {
      toast.success("Benchmark plan built");
      qc.invalidateQueries({ queryKey: ["arena-v6-benchmark", experimentId] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const start = useMutation({
    mutationFn: async () => (await api.post(`/experiments/${experimentId}/benchmark/start`, { plan_id: plan?.id, confirm: confirmed })).data,
    onSuccess: () => {
      toast.success("Benchmark started");
      qc.invalidateQueries({ queryKey: ["arena-v6-benchmark", experimentId] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const status = (experiment?.status || "").toUpperCase();
  const planStatus = (plan?.status || "").toLowerCase();
  const canBuild = Boolean(experiment) && status !== "ARCHIVED" && planStatus !== "running";
  const canStart = Boolean(plan) && planStatus !== "running" && planStatus !== "completed";

  return (
    <div className="space-y-3" data-testid="arena-v6-plan">
      <PlanReview
        plan={plan}
        building={build.isPending}
        starting={start.isPending}
        confirmed={confirmed}
        onConfirmedChange={setConfirmed}
        onBuild={() => build.mutate()}
        onStart={() => start.mutate()}
        canBuild={canBuild}
        canStart={canStart}
        disabledReason={status === "ARCHIVED" ? "This arena experiment is archived." : null}
      />
      <div className="flex justify-end">
        <Button size="sm" variant="outline" asChild>
          <Link to={`/experiments/${experimentId}`} data-testid="arena-v6-open-experiment">
            <ExternalLink className="mr-1.5 h-4 w-4" /> Open experiment
          </Link>
        </Button>
      </div>
    </div>
  );
};

const ArenaV6History: React.FC<{
  projectId: string;
  items: ArenaV6Experiment[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}> = ({ items, selectedId, onSelect }) => {
  const { data: summary, isLoading } = useArenaV6Summary(selectedId);
  const configs: ArenaV6ConfigMetrics[] = (summary?.configurations ?? []) as ArenaV6ConfigMetrics[];

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-4 pb-8 pt-2 lg:px-6">
      <Card className="space-y-3 p-4" data-testid="arena-v6-list">
        <div className="flex items-center gap-2">
          <Swords className="h-4 w-4 text-brand" />
          <span className="font-display font-semibold">Arena experiments</span>
          <Badge variant="outline" className="text-[10px]">{items.length}</Badge>
        </div>
        {items.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="arena-v6-list-empty">No arena experiments yet.</p>
        ) : (
          <ul className="divide-y divide-border">
            {items.map((e) => (
              <li key={e.id} className="flex items-center justify-between gap-3 py-2">
                <button
                  type="button"
                  data-testid="arena-v6-list-row"
                  onClick={() => onSelect(e.id)}
                  className={cn("min-w-0 flex-1 truncate text-left text-sm transition-colors hover:text-foreground", selectedId === e.id ? "font-medium text-foreground" : "text-muted-foreground")}
                >
                  {nText(e.title)}
                </button>
                <Badge variant="outline" className="shrink-0 text-[10px]">{nText(e.status)}</Badge>
                <Link to={`/experiments/${e.id}`} data-testid="arena-v6-list-link" className="shrink-0 text-xs text-primary hover:underline">
                  Open
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="space-y-3 p-4" data-testid="arena-v6-summary">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="font-display font-semibold">Arena summary</span>
            {summary?.experiment?.status && <Badge variant="outline" className="text-[10px] uppercase">{nText(summary.experiment.status)}</Badge>}
          </div>
          {selectedId && <span className="break-all font-mono text-[10px] text-muted-foreground" data-testid="arena-v6-selected-id">{selectedId}</span>}
        </div>

        {!selectedId ? (
          <p className="text-xs text-muted-foreground" data-testid="arena-v6-summary-empty">Create an arena or select one to see its per-configuration metrics.</p>
        ) : isLoading ? (
          <p className="text-xs text-muted-foreground">Loading summary…</p>
        ) : configs.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="arena-v6-summary-empty">No configurations reported for this arena yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/50">
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Label</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Configuration</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider text-center">Runs</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider text-center">Completion</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider text-center">Avg score</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider text-center">Tool viol.</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider text-center">Policy viol.</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider text-center">Latency</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider text-center">Tokens</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider text-center">Cost</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {configs.map((c) => (
                  <TableRow key={c.configuration_id} data-testid="arena-v6-config-row">
                    <TableCell className="whitespace-nowrap font-medium">{nText(c.label)}</TableCell>
                    <TableCell className="whitespace-nowrap font-mono text-[10px] text-muted-foreground">
                      {nText(c.model_id || c.agent_version_id || c.prompt_version_id)}
                    </TableCell>
                    <TableCell className="text-center font-mono text-xs" data-testid="arena-v6-run-count">{nNumber(c.run_count)}</TableCell>
                    <TableCell className="text-center font-mono text-xs" data-testid="arena-v6-completion">{nRate(c.completion_rate)}</TableCell>
                    <TableCell className="text-center font-mono text-xs" data-testid="arena-v6-avg-score">{nNumber(c.avg_score, 4)}</TableCell>
                    <TableCell className="text-center font-mono text-xs" data-testid="arena-v6-tool-violations">{nNumber(c.tool_violations)}</TableCell>
                    <TableCell className="text-center font-mono text-xs" data-testid="arena-v6-policy-violations">{nNumber(c.policy_violations)}</TableCell>
                    <TableCell className="text-center font-mono text-xs" data-testid="arena-v6-latency">{nLatency(c.latency_ms)}</TableCell>
                    <TableCell className="text-center font-mono text-xs" data-testid="arena-v6-tokens">{nNumber(c.total_tokens)}</TableCell>
                    <TableCell className="text-center font-mono text-xs" data-testid="arena-v6-cost">{nCost(c.estimated_cost)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        {selectedId && <ArenaV6PlanFlow experimentId={selectedId} experiment={summary?.experiment} />}
      </Card>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Arena page shell with the three tabs.
// ---------------------------------------------------------------------------

export default function Arena() {
  const { activeId } = useProjects();
  const { data: arenasData } = useArenasV6(activeId);
  const [selectedArenaId, setSelectedArenaId] = useState<string | null>(null);

  const items = useMemo<ArenaV6Experiment[]>(
    () => ((arenasData?.items ?? []) as ArenaV6Experiment[]),
    [arenasData]
  );

  useEffect(() => {
    if (selectedArenaId) return;
    if (items[0]?.id) setSelectedArenaId(items[0].id);
  }, [items, selectedArenaId]);

  return (
    <div data-testid="arena-v6-page">
      <Tabs defaultValue="model">
        <div className="mx-auto max-w-6xl px-4 pt-4 lg:px-6">
          <TabsList>
            <TabsTrigger value="model" data-testid="arena-tab-model">Model Arena</TabsTrigger>
            <TabsTrigger value="agent" data-testid="arena-tab-agent">Agent Arena</TabsTrigger>
            <TabsTrigger value="prompt" data-testid="arena-tab-prompt">Prompt Arena</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="model" className="mt-0">
          <ModelArenaPanel />
        </TabsContent>

        <TabsContent value="agent" className="mt-0">
          {activeId
            ? <AgentArenaPanel projectId={activeId} onCreated={setSelectedArenaId} />
            : <div className="p-6"><EmptyState icon={<Swords className="h-6 w-6" />} title="No project selected" description="Select or create a project to run an Agent Arena." /></div>}
        </TabsContent>

        <TabsContent value="prompt" className="mt-0">
          {activeId
            ? <PromptArenaPanel projectId={activeId} onCreated={setSelectedArenaId} />
            : <div className="p-6"><EmptyState icon={<Swords className="h-6 w-6" />} title="No project selected" description="Select or create a project to run a Prompt Arena." /></div>}
        </TabsContent>
      </Tabs>

      {activeId && (
        <ArenaV6History
          projectId={activeId}
          items={items}
          selectedId={selectedArenaId}
          onSelect={setSelectedArenaId}
        />
      )}
    </div>
  );
}
