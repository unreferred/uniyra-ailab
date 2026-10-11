import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Bot, Check, Loader2, Plus } from "lucide-react";
import { apiError, createAgent, createAgentVersion } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import {
  useAgentCatalog,
  useAgents,
  useContextBudgetPolicies,
  useContextPackVersions,
  useContextPacks,
  useKnowledgeCollections,
  useModels,
  usePromptSpecs,
  usePromptVersions,
  useRubrics,
  useTools,
} from "@/lib/hooks";
import {
  Agent,
  CatalogOption,
  ContextBudgetPolicy,
  ContextPack,
  ContextPackVersion,
  KnowledgeCollection,
  PromptSpec,
  PromptVersion,
  ToolRecord,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState, ErrorNote, LoadingSpinner, SectionHeader } from "@/components/common/Common";
import { SingleModelSelect } from "@/components/common/ModelPicker";
import { nText } from "@/components/experiment/format";
import { CONTEXT_STRATEGIES } from "@/components/workspace/ContextPopover";
import { cn } from "@/lib/utils";

const NONE = "__none__";

const STEPS = [
  "Identity", "Prompt", "Model", "Context", "Knowledge", "Tools", "Runtime", "Evaluation", "Review",
];

const EVALUATOR_KEYS = ["assertions", "knowledge_grounding", "tool_policy", "red_team"];
const REASONING_EFFORTS = ["low", "medium", "high"];
const RUNTIME_FIELDS: { key: string; label: string }[] = [
  { key: "max_tool_rounds", label: "Max tool rounds" },
  { key: "max_tool_calls", label: "Max tool calls" },
  { key: "max_runtime_seconds", label: "Max runtime (s)" },
  { key: "max_input_tokens", label: "Max input tokens" },
  { key: "max_output_tokens", label: "Max output tokens" },
  { key: "max_cost", label: "Max cost" },
];

type Opt = { value: string; label: string };

function asArray<T = any>(data: any): T[] {
  if (Array.isArray(data)) return data as T[];
  if (Array.isArray(data?.items)) return data.items as T[];
  if (Array.isArray(data?.results)) return data.results as T[];
  return [];
}

function toOptions(list?: CatalogOption[] | null): Opt[] {
  if (!Array.isArray(list)) return [];
  const out: Opt[] = [];
  for (const item of list) {
    if (item == null) continue;
    if (typeof item === "string") out.push({ value: item, label: item });
    else if (item.key) out.push({ value: item.key, label: item.label || item.key });
  }
  return out;
}

function isoDate(value?: string | null): string {
  if (!value) return "n/a";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "n/a" : d.toLocaleDateString();
}

function parseList(text: string): string[] {
  return text
    .split(/[\n,]+/)
    .map((value) => value.trim())
    .filter(Boolean);
}

function numOrNull(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

type AgentDraft = {
  name: string;
  role: string;
  purpose: string;
  responsibilities: string;
  nonResponsibilities: string;
  status: string;
  promptSpecId: string;
  promptVersionId: string;
  modelId: string;
  contextPackVersionIds: string[];
  budgetPolicyId: string;
  contextStrategy: string;
  knowledgeEnabled: boolean;
  knowledgeCollectionIds: string[];
  retrievalTopK: string;
  citationRequired: boolean;
  knowledgeOnlyMode: boolean;
  allowedToolIds: string[];
  deniedToolIds: string[];
  runtime: Record<string, string>;
  temperature: string;
  reasoningEffort: string;
  evaluatorKeys: string[];
  rubricId: string;
  judgeModelId: string;
  threshold: string;
};

const emptyRuntime = (): Record<string, string> =>
  RUNTIME_FIELDS.reduce<Record<string, string>>((acc, field) => ({ ...acc, [field.key]: "" }), {});

const EMPTY_DRAFT: AgentDraft = {
  name: "",
  role: "",
  purpose: "",
  responsibilities: "",
  nonResponsibilities: "",
  status: "active",
  promptSpecId: "",
  promptVersionId: "",
  modelId: "",
  contextPackVersionIds: [],
  budgetPolicyId: "",
  contextStrategy: "FULL",
  knowledgeEnabled: false,
  knowledgeCollectionIds: [],
  retrievalTopK: "",
  citationRequired: false,
  knowledgeOnlyMode: false,
  allowedToolIds: [],
  deniedToolIds: [],
  runtime: emptyRuntime(),
  temperature: "",
  reasoningEffort: "",
  evaluatorKeys: [],
  rubricId: "",
  judgeModelId: "",
  threshold: "",
};

const StatusBadge: React.FC<{ value?: string | null }> = ({ value }) => (
  <Badge variant="outline" className="text-[10px]">{nText(value)}</Badge>
);

const PromptVersionSelect: React.FC<{
  promptId: string;
  value: string;
  onChange: (value: string) => void;
}> = ({ promptId, value, onChange }) => {
  const { data, isLoading } = usePromptVersions(promptId || undefined);
  const versions = asArray<PromptVersion>(data);
  if (!promptId) {
    return <p className="mt-1 text-xs text-muted-foreground" data-testid="agent-prompt-version-hint">Select a prompt spec first.</p>;
  }
  if (isLoading) return <LoadingSpinner label="Loading versions…" />;
  if (versions.length === 0) {
    return <p className="mt-1 text-xs text-muted-foreground" data-testid="agent-prompt-versions-empty">n/a — this prompt has no versions.</p>;
  }
  return (
    <Select value={value || NONE} onValueChange={(next) => onChange(next === NONE ? "" : next)}>
      <SelectTrigger className="mt-1 h-9" data-testid="agent-prompt-version">
        <SelectValue placeholder="Select prompt version" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE}>Select…</SelectItem>
        {versions.map((version) => (
          <SelectItem key={version.id} value={version.id as string}>
            v{nText(version.version)} · {nText(version.status)}{version.status === "draft" ? " (draft)" : ""}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
};

const PackVersionRow: React.FC<{
  pack: ContextPack;
  selected: string[];
  onToggle: (id: string) => void;
}> = ({ pack, selected, onToggle }) => {
  const { data, isLoading } = useContextPackVersions(pack.id || undefined);
  const versions = asArray<ContextPackVersion>(data);
  return (
    <div className="rounded-md border border-border p-2">
      <p className="text-xs font-medium">{nText(pack.name)}</p>
      {isLoading ? (
        <LoadingSpinner label="Loading versions…" />
      ) : versions.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">n/a</p>
      ) : (
        <div className="mt-1 space-y-1">
          {versions.map((version) => {
            const id = version.id as string;
            const checked = selected.includes(id);
            return (
              <label key={id} className="flex items-center gap-2 text-xs">
                <Checkbox
                  checked={checked}
                  data-testid={`agent-context-version-${id}`}
                  onCheckedChange={(state) => {
                    if ((state === true) !== checked) onToggle(id);
                  }}
                />
                v{nText(version.version)} · {nText(version.status)}
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
};

const PackVersionPicker: React.FC<{
  packs: ContextPack[];
  selected: string[];
  onToggle: (id: string) => void;
}> = ({ packs, selected, onToggle }) => {
  if (packs.length === 0) {
    return <p className="text-xs text-muted-foreground" data-testid="agent-context-packs-empty">n/a — no context packs in this project.</p>;
  }
  return (
    <div className="max-h-56 space-y-2 overflow-y-auto" data-testid="agent-context-pack-picker">
      {packs.map((pack, index) => (
        <PackVersionRow key={pack.id || index} pack={pack} selected={selected} onToggle={onToggle} />
      ))}
    </div>
  );
};

const Review: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="flex flex-col rounded-md border border-border px-3 py-2">
    <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <span className="text-sm">{value}</span>
  </div>
);

export default function AgentLab() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const qc = useQueryClient();
  const { activeId, activeProject } = useProjects();

  const { data: agentsData, isLoading, isError, error } = useAgents(activeId);
  const { data: catalog } = useAgentCatalog();
  const { data: models = [] } = useModels();
  const { data: rubrics = [] } = useRubrics();
  const { data: toolsData } = useTools(activeId);
  const { data: promptsData } = usePromptSpecs(activeId);
  const { data: packsData } = useContextPacks(activeId);
  const { data: budgetData } = useContextBudgetPolicies(activeId);
  const { data: knowledgeData } = useKnowledgeCollections(activeId);

  const agents = asArray<Agent>(agentsData);
  const prompts = asArray<PromptSpec>(promptsData);
  const packs = asArray<ContextPack>(packsData);
  const budgets = asArray<ContextBudgetPolicy>(budgetData);
  const collections = asArray<KnowledgeCollection>(knowledgeData);
  const tools = toolsData?.items || [];

  const roleOptions = useMemo(() => toOptions(catalog?.roles), [catalog]);
  const statusOptions = useMemo(() => toOptions(catalog?.statuses), [catalog]);
  const strategyOptions = useMemo(() => CONTEXT_STRATEGIES.map((s) => ({ value: s, label: s })), []);

  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<AgentDraft>(EMPTY_DRAFT);

  const set = <K extends keyof AgentDraft>(key: K, value: AgentDraft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));

  const resetDraft = () => {
    setDraft({ ...EMPTY_DRAFT, role: roleOptions[0]?.value || "", runtime: emptyRuntime() });
    setStep(0);
  };

  const openWizard = () => {
    resetDraft();
    setOpen(true);
  };

  useEffect(() => {
    if (!draft.role && roleOptions.length > 0) {
      setDraft((current) => ({ ...current, role: roleOptions[0].value }));
    }
  }, [roleOptions, draft.role]);

  useEffect(() => {
    if (!draft.modelId && models.length > 0) {
      const first = models.find((model) => model.enabled) || models[0];
      if (first?.id) setDraft((current) => ({ ...current, modelId: first.id }));
    }
  }, [models, draft.modelId]);

  const capturePrefillRef = useRef(false);
  useEffect(() => {
    if (capturePrefillRef.current) return;
    const promptSpecId = searchParams.get("capturePromptId") || "";
    const promptVersionId = searchParams.get("captureVersionId") || "";
    if (!promptSpecId || !promptVersionId) return;
    if (!prompts.some((prompt) => prompt.id === promptSpecId)) return;
    capturePrefillRef.current = true;
    setDraft({
      ...EMPTY_DRAFT,
      role: roleOptions[0]?.value || "",
      runtime: emptyRuntime(),
      name: searchParams.get("captureName") || "",
      purpose: searchParams.get("capturePurpose") || "",
      promptSpecId,
      promptVersionId,
    });
    setStep(1);
    setOpen(true);
    setSearchParams({}, { replace: true });
  }, [prompts, roleOptions, searchParams, setSearchParams]);

  const toggleId = (list: string[], id: string): string[] =>
    list.includes(id) ? list.filter((value) => value !== id) : [...list, id];

  const toggleContextPackVersion = (id: string) =>
    setDraft((current) => ({ ...current, contextPackVersionIds: toggleId(current.contextPackVersionIds, id) }));

  const toggleKnowledgeCollection = (id: string) =>
    setDraft((current) => ({ ...current, knowledgeCollectionIds: toggleId(current.knowledgeCollectionIds, id) }));

  const toggleEvaluator = (key: string) =>
    setDraft((current) => ({ ...current, evaluatorKeys: toggleId(current.evaluatorKeys, key) }));

  const toggleAllowedTool = (id: string) =>
    setDraft((current) => {
      const has = current.allowedToolIds.includes(id);
      return {
        ...current,
        allowedToolIds: has ? current.allowedToolIds.filter((value) => value !== id) : [...current.allowedToolIds, id],
        deniedToolIds: !has ? current.deniedToolIds.filter((value) => value !== id) : current.deniedToolIds,
      };
    });

  const toggleDeniedTool = (id: string) =>
    setDraft((current) => {
      const has = current.deniedToolIds.includes(id);
      return {
        ...current,
        deniedToolIds: has ? current.deniedToolIds.filter((value) => value !== id) : [...current.deniedToolIds, id],
        allowedToolIds: !has ? current.allowedToolIds.filter((value) => value !== id) : current.allowedToolIds,
      };
    });

  const buildVersionBody = (): Record<string, any> => {
    const body: Record<string, any> = { model_id: draft.modelId };
    if (draft.promptVersionId) body.prompt_version_id = draft.promptVersionId;

    const contextPolicy: Record<string, any> = {};
    if (draft.contextPackVersionIds.length) contextPolicy.context_pack_version_ids = draft.contextPackVersionIds;
    if (draft.budgetPolicyId) contextPolicy.budget_policy_id = draft.budgetPolicyId;
    if (draft.contextStrategy) contextPolicy.strategy = draft.contextStrategy;
    body.context_policy = contextPolicy;

    body.tool_policy = {
      default_deny: true,
      allowed_tool_ids: draft.allowedToolIds,
      denied_tool_ids: draft.deniedToolIds,
      mode: "allowlist",
    };

    body.knowledge_policy = {
      knowledge_enabled: draft.knowledgeEnabled,
      allowed_collection_ids: draft.knowledgeCollectionIds,
      retrieval_top_k: numOrNull(draft.retrievalTopK),
      citation_required: draft.citationRequired,
      knowledge_only_mode: draft.knowledgeOnlyMode,
    };

    const runtime: Record<string, any> = {};
    for (const field of RUNTIME_FIELDS) {
      const value = numOrNull(draft.runtime[field.key] || "");
      if (value !== null) runtime[field.key] = value;
    }
    const temperature = numOrNull(draft.temperature);
    if (temperature !== null) runtime.temperature = temperature;
    if (draft.reasoningEffort) runtime.reasoning_effort = draft.reasoningEffort;
    body.runtime_policy = runtime;

    body.output_contract = { format: "text" };

    const evaluationProfile: Record<string, any> = {};
    if (draft.evaluatorKeys.length) evaluationProfile.evaluator_keys = draft.evaluatorKeys;
    if (draft.rubricId) evaluationProfile.rubric_id = draft.rubricId;
    if (draft.judgeModelId) evaluationProfile.judge_model_id = draft.judgeModelId;
    const threshold = numOrNull(draft.threshold);
    if (threshold !== null) evaluationProfile.threshold = threshold;
    body.evaluation_profile = evaluationProfile;

    body.routing = { mode: "fixed" };
    body.metadata = {};
    return body;
  };

  const createMut = useMutation({
    mutationFn: async () => {
      const agent = await createAgent({
        project_id: activeId as string,
        name: draft.name.trim(),
        role: draft.role || undefined,
        purpose: draft.purpose || undefined,
        responsibilities: parseList(draft.responsibilities),
        non_responsibilities: parseList(draft.nonResponsibilities),
        status: draft.status || "active",
      });
      if (!agent?.id) throw new Error("Agent creation returned no id");
      const version = await createAgentVersion(agent.id, buildVersionBody());
      return { agent, version };
    },
    onSuccess: () => {
      toast.success("Agent and v1 created");
      qc.invalidateQueries({ queryKey: ["agents", activeId] });
      setOpen(false);
      resetDraft();
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const canNext = step === 0 ? Boolean(draft.name.trim()) : true;
  const canSubmit = Boolean(draft.name.trim() && draft.modelId) && !createMut.isPending;

  const selectedPrompt = prompts.find((prompt) => prompt.id === draft.promptSpecId) || null;
  const selectedPromptVersion = draft.promptVersionId;
  const selectedModel = models.find((model) => model.id === draft.modelId) || null;
  const selectedBudget = budgets.find((budget) => budget.id === draft.budgetPolicyId) || null;
  const selectedRubric = rubrics.find((rubric) => rubric.id === draft.rubricId) || null;
  const selectedJudge = models.find((model) => model.id === draft.judgeModelId) || null;
  const toolName = (id: string) => tools.find((tool: ToolRecord) => tool.id === id)?.display_name || id;
  const collectionName = (id: string) =>
    collections.find((collection) => collection.id === id)?.name || id;

  if (!activeId) {
    return (
      <div className="mx-auto max-w-6xl p-4 lg:p-6" data-testid="agent-lab-page">
        <EmptyState
          icon={<Bot className="h-6 w-6" />}
          title="No project selected"
          description="Select a project to manage its agents and versioned configurations."
          testId="agent-lab-no-project"
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="agent-lab-page">
      <SectionHeader
        title="Agent Lab"
        caption={`Project-scoped agents with append-only configuration versions for "${activeProject?.name || "this project"}".`}
        action={
          <Button size="sm" data-testid="agent-create" onClick={openWizard}>
            <Plus className="mr-1.5 h-4 w-4" /> New agent
          </Button>
        }
      />

      {isLoading ? (
        <LoadingSpinner label="Loading agents…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : agents.length === 0 ? (
        <EmptyState
          icon={<Bot className="h-6 w-6" />}
          title="No agents yet"
          description="Create an agent to pin a model, prompt, context, tools, knowledge and evaluation policy into an immutable version."
          testId="agents-empty"
        />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table data-testid="agent-table">
            <TableHeader>
              <TableRow className="bg-muted/50">
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Name</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Role</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Status</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Latest version</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Updated</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {agents.map((agent, index) => (
                <TableRow key={agent.id || index} data-testid="agent-row">
                  <TableCell className="text-xs font-medium" data-testid="agent-name">
                    <button
                      type="button"
                      className="text-left text-primary hover:underline"
                      disabled={!agent.id}
                      onClick={() => agent.id && navigate(`/agents/${agent.id}`)}
                    >
                      {nText(agent.name)}
                    </button>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground" data-testid="agent-role">{nText(agent.role)}</TableCell>
                  <TableCell><StatusBadge value={agent.status} /></TableCell>
                  <TableCell className="font-mono text-xs tabular" data-testid="agent-latest-version">
                    {agent.latest_version == null ? "n/a" : `v${agent.latest_version}`}
                  </TableCell>
                  <TableCell className="font-mono text-[11px] text-muted-foreground">{isoDate(agent.updated_at)}</TableCell>
                  <TableCell className="text-right">
                    <Button
                      size="sm"
                      variant="ghost"
                      data-testid="agent-open"
                      disabled={!agent.id}
                      onClick={() => agent.id && navigate(`/agents/${agent.id}`)}
                    >
                      Open
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) resetDraft();
        }}
      >
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto" data-testid="agent-create-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">New agent</DialogTitle>
            <DialogDescription>
              Configure the agent across nine steps. Creating an agent also creates its first immutable draft version (v1).
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-wrap items-center gap-1.5" data-testid="agent-wizard-stepper">
            {STEPS.map((label, index) => (
              <React.Fragment key={label}>
                <span
                  className={cn(
                    "flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px]",
                    index === step ? "border-primary bg-accent" : "border-border text-muted-foreground"
                  )}
                >
                  <span className="font-mono">{index + 1}</span> {label}
                </span>
                {index < STEPS.length - 1 && <span className="text-muted-foreground">→</span>}
              </React.Fragment>
            ))}
          </div>

          <div className="space-y-3" data-testid="agent-wizard-step" data-step={step}>
            {step === 0 && (
              <div className="space-y-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Name</label>
                  <Input
                    className="mt-1"
                    data-testid="agent-name-input"
                    value={draft.name}
                    onChange={(e) => set("name", e.target.value)}
                    placeholder="Release reviewer"
                  />
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Role</label>
                    <Select value={draft.role || NONE} onValueChange={(v) => set("role", v === NONE ? "" : v)}>
                      <SelectTrigger className="mt-1 h-9" data-testid="agent-role-input"><SelectValue placeholder="Select role" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>Select…</SelectItem>
                        {roleOptions.map((option) => (
                          <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Status</label>
                    <Select value={draft.status} onValueChange={(v) => set("status", v)}>
                      <SelectTrigger className="mt-1 h-9" data-testid="agent-status-input"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {(statusOptions.length > 0 ? statusOptions : [{ value: "active", label: "active" }, { value: "archived", label: "archived" }]).map((option) => (
                          <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Purpose</label>
                  <Textarea className="mt-1" rows={3} data-testid="agent-purpose-input" value={draft.purpose} onChange={(e) => set("purpose", e.target.value)} />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Responsibilities (comma or newline separated)</label>
                  <Textarea className="mt-1" rows={3} data-testid="agent-responsibilities-input" value={draft.responsibilities} onChange={(e) => set("responsibilities", e.target.value)} />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Non-responsibilities (comma or newline separated)</label>
                  <Textarea className="mt-1" rows={3} data-testid="agent-non-responsibilities-input" value={draft.nonResponsibilities} onChange={(e) => set("nonResponsibilities", e.target.value)} />
                </div>
              </div>
            )}

            {step === 1 && (
              <div className="space-y-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Prompt spec</label>
                  <Select
                    value={draft.promptSpecId || NONE}
                    onValueChange={(v) => {
                      setDraft((current) => ({ ...current, promptSpecId: v === NONE ? "" : v, promptVersionId: "" }));
                    }}
                  >
                    <SelectTrigger className="mt-1 h-9" data-testid="agent-prompt-select"><SelectValue placeholder="Select prompt spec" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NONE}>No prompt</SelectItem>
                      {prompts.map((prompt) => (
                        <SelectItem key={prompt.id} value={prompt.id as string}>{nText(prompt.name)}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Prompt version</label>
                  <PromptVersionSelect
                    promptId={draft.promptSpecId}
                    value={draft.promptVersionId}
                    onChange={(value) => set("promptVersionId", value)}
                  />
                </div>
                {draft.promptVersionId && (
                  <p className="text-[11px] text-muted-foreground">
                    Pinned prompt version: <span className="font-mono">{nText(selectedPrompt?.name)}</span> ·{" "}
                    <span className="font-mono">{draft.promptVersionId}</span>
                  </p>
                )}
              </div>
            )}

            {step === 2 && (
              <div className="space-y-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Model (fixed binding)</label>
                  <div className="mt-1">
                    <SingleModelSelect
                      models={models}
                      value={draft.modelId}
                      onChange={(value) => set("modelId", value)}
                      testId="agent-model-select"
                      placeholder="Select model"
                    />
                  </div>
                </div>
                {models.length === 0 && (
                  <p className="text-xs text-muted-foreground" data-testid="agent-models-empty">n/a — no models are configured.</p>
                )}
                {selectedModel && (
                  <p className="text-[11px] text-muted-foreground">
                    {nText(selectedModel.display_name)} · <span className="font-mono">{nText(selectedModel.model_identifier)}</span>
                  </p>
                )}
              </div>
            )}

            {step === 3 && (
              <div className="space-y-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Context pack versions</label>
                  <div className="mt-1">
                    <PackVersionPicker
                      packs={packs}
                      selected={draft.contextPackVersionIds}
                      onToggle={toggleContextPackVersion}
                    />
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Budget policy</label>
                    <Select
                      value={draft.budgetPolicyId || NONE}
                      onValueChange={(v) => set("budgetPolicyId", v === NONE ? "" : v)}
                    >
                      <SelectTrigger className="mt-1 h-9" data-testid="agent-budget-policy"><SelectValue placeholder="No budget policy" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>No budget policy</SelectItem>
                        {budgets.map((budget) => (
                          <SelectItem key={budget.id} value={budget.id as string}>{nText(budget.name)}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Strategy</label>
                    <Select value={draft.contextStrategy} onValueChange={(v) => set("contextStrategy", v)}>
                      <SelectTrigger className="mt-1 h-9" data-testid="agent-context-strategy"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {strategyOptions.map((option) => (
                          <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <p className="text-[11px] text-muted-foreground" data-testid="agent-context-selected-count">
                  {draft.contextPackVersionIds.length} context pack version(s) selected
                </p>
              </div>
            )}

            {step === 4 && (
              <div className="space-y-3">
                <label className="flex items-center gap-2 text-sm">
                  <Switch
                    checked={draft.knowledgeEnabled}
                    data-testid="agent-knowledge-enabled"
                    onCheckedChange={(checked) => set("knowledgeEnabled", checked === true)}
                  />
                  Knowledge enabled
                </label>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Allowed collections</label>
                  {collections.length === 0 ? (
                    <p className="mt-1 text-xs text-muted-foreground" data-testid="agent-knowledge-collections-empty">n/a — no knowledge collections in this project.</p>
                  ) : (
                    <div className="mt-1 space-y-1" data-testid="agent-knowledge-collections">
                      {collections.map((collection) => {
                        const id = collection.id as string;
                        return (
                          <label key={id} className="flex items-center gap-2 text-xs">
                            <Checkbox
                              checked={draft.knowledgeCollectionIds.includes(id)}
                              data-testid={`agent-knowledge-collection-${id}`}
                              onCheckedChange={(checked) => {
                                if ((checked === true) !== draft.knowledgeCollectionIds.includes(id)) toggleKnowledgeCollection(id);
                              }}
                            />
                            {nText(collection.name)}
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Retrieval top K</label>
                    <Input
                      type="number"
                      className="mt-1 h-9"
                      data-testid="agent-retrieval-top-k"
                      value={draft.retrievalTopK}
                      onChange={(e) => set("retrievalTopK", e.target.value)}
                      placeholder="unset"
                    />
                  </div>
                  <div className="space-y-2 pt-5">
                    <label className="flex items-center gap-2 text-xs">
                      <Switch checked={draft.citationRequired} data-testid="agent-citation-required" onCheckedChange={(checked) => set("citationRequired", checked === true)} />
                      Citation required
                    </label>
                    <label className="flex items-center gap-2 text-xs">
                      <Switch checked={draft.knowledgeOnlyMode} data-testid="agent-knowledge-only" onCheckedChange={(checked) => set("knowledgeOnlyMode", checked === true)} />
                      Knowledge-only mode
                    </label>
                  </div>
                </div>
              </div>
            )}

            {step === 5 && (
              <div className="space-y-3">
                <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground" data-testid="agent-default-deny">
                  default_deny is enforced: only explicitly allowed tools can be called.
                </p>
                {tools.length === 0 ? (
                  <p className="text-xs text-muted-foreground" data-testid="agent-tools-empty">n/a — no tools are registered.</p>
                ) : (
                  <div className="max-h-64 overflow-y-auto rounded-md border border-border" data-testid="agent-tools">
                    <Table>
                      <TableHeader>
                        <TableRow className="bg-muted/50">
                          <TableHead className="text-[10px] font-mono uppercase tracking-wider">Tool</TableHead>
                          <TableHead className="text-[10px] font-mono uppercase tracking-wider">Allowed</TableHead>
                          <TableHead className="text-[10px] font-mono uppercase tracking-wider">Denied</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {tools.map((tool: ToolRecord) => {
                          const id = tool.id as string;
                          return (
                            <TableRow key={id} data-testid="agent-tool-row">
                              <TableCell className="text-xs">{nText(tool.display_name || tool.name)}</TableCell>
                              <TableCell>
                                <Checkbox
                                  checked={draft.allowedToolIds.includes(id)}
                                  data-testid={`agent-tool-allow-${id}`}
                                  onCheckedChange={(checked) => {
                                    if ((checked === true) !== draft.allowedToolIds.includes(id)) toggleAllowedTool(id);
                                  }}
                                />
                              </TableCell>
                              <TableCell>
                                <Checkbox
                                  checked={draft.deniedToolIds.includes(id)}
                                  data-testid={`agent-tool-deny-${id}`}
                                  onCheckedChange={(checked) => {
                                    if ((checked === true) !== draft.deniedToolIds.includes(id)) toggleDeniedTool(id);
                                  }}
                                />
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </div>
            )}

            {step === 6 && (
              <div className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  {RUNTIME_FIELDS.map((field) => (
                    <div key={field.key}>
                      <label className="text-xs font-medium text-muted-foreground">{field.label}</label>
                      <Input
                        type="number"
                        className="mt-1 h-9"
                        data-testid={`agent-runtime-${field.key}`}
                        value={draft.runtime[field.key] || ""}
                        onChange={(e) => set("runtime", { ...draft.runtime, [field.key]: e.target.value })}
                        placeholder="unset"
                      />
                    </div>
                  ))}
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Temperature</label>
                    <Input
                      type="number"
                      step="0.1"
                      className="mt-1 h-9"
                      data-testid="agent-temperature"
                      value={draft.temperature}
                      onChange={(e) => set("temperature", e.target.value)}
                      placeholder="unset"
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Reasoning effort</label>
                    <Select
                      value={draft.reasoningEffort || NONE}
                      onValueChange={(v) => set("reasoningEffort", v === NONE ? "" : v)}
                    >
                      <SelectTrigger className="mt-1 h-9" data-testid="agent-reasoning-effort"><SelectValue placeholder="unset" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>unset</SelectItem>
                        {REASONING_EFFORTS.map((effort) => (
                          <SelectItem key={effort} value={effort}>{effort}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </div>
            )}

            {step === 7 && (
              <div className="space-y-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Evaluator keys</label>
                  <div className="mt-1 space-y-1" data-testid="agent-evaluators">
                    {EVALUATOR_KEYS.map((key) => (
                      <label key={key} className="flex items-center gap-2 text-xs">
                        <Checkbox
                          checked={draft.evaluatorKeys.includes(key)}
                          data-testid={`agent-evaluator-${key}`}
                          onCheckedChange={(checked) => {
                            if ((checked === true) !== draft.evaluatorKeys.includes(key)) toggleEvaluator(key);
                          }}
                        />
                        {key}
                      </label>
                    ))}
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-3">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Rubric</label>
                    <Select value={draft.rubricId || NONE} onValueChange={(v) => set("rubricId", v === NONE ? "" : v)}>
                      <SelectTrigger className="mt-1 h-9" data-testid="agent-rubric"><SelectValue placeholder="No rubric" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>No rubric</SelectItem>
                        {rubrics.map((rubric) => (
                          <SelectItem key={rubric.id} value={rubric.id}>{rubric.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Judge model (optional)</label>
                    <Select value={draft.judgeModelId || NONE} onValueChange={(v) => set("judgeModelId", v === NONE ? "" : v)}>
                      <SelectTrigger className="mt-1 h-9" data-testid="agent-judge-model"><SelectValue placeholder="No judge model" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>No judge model</SelectItem>
                        {models.filter((model) => model.enabled).map((model) => (
                          <SelectItem key={model.id} value={model.id}>{model.display_name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Threshold</label>
                    <Input
                      type="number"
                      step="0.01"
                      className="mt-1 h-9"
                      data-testid="agent-threshold"
                      value={draft.threshold}
                      onChange={(e) => set("threshold", e.target.value)}
                      placeholder="unset"
                    />
                  </div>
                </div>
              </div>
            )}

            {step === 8 && (
              <div className="space-y-3" data-testid="agent-review">
                <div className="grid gap-2 sm:grid-cols-2">
                  <Review label="Name" value={nText(draft.name.trim())} />
                  <Review label="Role" value={nText(draft.role)} />
                  <Review label="Status" value={nText(draft.status)} />
                  <Review label="Model" value={selectedModel ? nText(selectedModel.display_name) : "n/a"} />
                  <Review label="Prompt spec" value={selectedPrompt ? nText(selectedPrompt.name) : "none"} />
                  <Review label="Prompt version" value={nText(selectedPromptVersion)} />
                  <Review label="Context packs" value={`${draft.contextPackVersionIds.length}`} />
                  <Review label="Budget policy" value={selectedBudget ? nText(selectedBudget.name) : "none"} />
                  <Review label="Context strategy" value={nText(draft.contextStrategy)} />
                  <Review label="Knowledge enabled" value={draft.knowledgeEnabled ? "yes" : "no"} />
                  <Review label="Knowledge collections" value={`${draft.knowledgeCollectionIds.length}`} />
                  <Review label="Retrieval top K" value={draft.retrievalTopK.trim() || "unset"} />
                  <Review label="Allowed tools" value={draft.allowedToolIds.map(toolName).join(", ") || "none"} />
                  <Review label="Denied tools" value={draft.deniedToolIds.map(toolName).join(", ") || "none"} />
                  <Review label="Evaluator keys" value={draft.evaluatorKeys.join(", ") || "none"} />
                  <Review label="Rubric" value={selectedRubric ? nText(selectedRubric.name) : "none"} />
                  <Review label="Judge model" value={selectedJudge ? nText(selectedJudge.display_name) : "none"} />
                  <Review label="Threshold" value={draft.threshold.trim() || "unset"} />
                </div>
                <div>
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Purpose</h4>
                  <p className="whitespace-pre-wrap text-sm">{nText(draft.purpose.trim())}</p>
                </div>
                <div>
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Responsibilities</h4>
                  <p className="whitespace-pre-wrap text-sm">{parseList(draft.responsibilities).join(", ") || "n/a"}</p>
                </div>
                <div>
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Non-responsibilities</h4>
                  <p className="whitespace-pre-wrap text-sm">{parseList(draft.nonResponsibilities).join(", ") || "n/a"}</p>
                </div>
                <div className="rounded-md border border-border p-3 text-xs" data-testid="agent-review-config">
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Runtime policy</p>
                  <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px]">
                    {JSON.stringify(
                      Object.fromEntries(
                        RUNTIME_FIELDS
                          .map((field) => [field.key, numOrNull(draft.runtime[field.key] || "")] as const)
                          .filter(([, value]) => value !== null)
                      ),
                      null,
                      2
                    )}
                  </pre>
                </div>
                <ErrorNote message={createMut.isError ? apiError(createMut.error) : null} />
              </div>
            )}
          </div>

          <DialogFooter className="flex items-center justify-between gap-2 sm:justify-between">
            <Button variant="ghost" data-testid="agent-create-back" onClick={() => setStep((current) => Math.max(0, current - 1))} disabled={step === 0}>
              <ArrowLeft className="mr-1.5 h-4 w-4" /> Back
            </Button>
            <div className="flex items-center gap-2">
              <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
              {step < STEPS.length - 1 ? (
                <Button data-testid="agent-create-next" disabled={!canNext} onClick={() => setStep((current) => Math.min(STEPS.length - 1, current + 1))}>
                  Next
                </Button>
              ) : (
                <Button data-testid="agent-create-submit" disabled={!canSubmit} onClick={() => createMut.mutate()}>
                  {createMut.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Check className="mr-1.5 h-4 w-4" />}
                  Create agent + v1
                </Button>
              )}
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Card className="p-4" data-testid="agent-lab-help">
        <p className="text-xs text-muted-foreground">
          Agents are project-scoped identities. Every agent owns an append-only version history; a version pins a fixed model
          plus its prompt, context, tools, knowledge and evaluation policy. Frozen and deprecated versions are immutable.
        </p>
      </Card>
    </div>
  );
}
