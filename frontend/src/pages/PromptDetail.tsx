import React, { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft, Archive, Eye, FileCode2, Pencil, Play, Plus, RotateCcw, Snowflake, Trash2,
} from "lucide-react";
import {
  api,
  apiError,
  clonePromptVersion,
  comparePromptVersions,
  compilePrompt,
  createPromptVersion,
  deprecatePromptVersion,
  freezePromptVersion,
  getCompiledPrompt,
  listProjectPolicies,
  previewPromptCompilation,
  restorePromptVersion,
  updatePrompt,
  updatePromptVersion,
} from "@/lib/api";
import {
  useCompiledPrompts,
  usePromptBlocks,
  usePromptCompilerCatalog,
  usePromptDetail,
  usePromptUsage,
  usePromptVersions,
} from "@/lib/hooks";
import {
  CatalogOption,
  CompiledPrompt,
  CompiledPromptSection,
  ProjectPolicy,
  PromptBlock,
  PromptCompileRequest,
  PromptUsageRun,
  PromptUsageVersion,
  PromptVersion,
  PromptVersionDiff,
  PromptVersionDiffLine,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Switch } from "@/components/ui/switch";
import { CopyButton, EmptyState, ErrorNote, LoadingSpinner, SectionHeader } from "@/components/common/Common";
import { nText, shortHash } from "@/components/experiment/format";
import { cn } from "@/lib/utils";

const SPEC_STATUSES = ["draft", "active", "deprecated", "archived"];

const STATUS_CLASS: Record<string, string> = {
  draft: "border-amber-500/40 bg-amber-500/10 text-amber-600",
  frozen: "border-emerald-500/40 bg-emerald-500/10 text-emerald-600",
  deprecated: "border-muted-foreground/30 bg-muted/40 text-muted-foreground",
};

function asArray<T = any>(data: any): T[] {
  if (Array.isArray(data)) return data as T[];
  if (Array.isArray(data?.items)) return data.items as T[];
  if (Array.isArray(data?.results)) return data.results as T[];
  return [];
}

function isoDate(value?: string | null): string {
  if (!value) return "n/a";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "n/a" : d.toLocaleDateString();
}

function statusClass(status?: string | null): string {
  return STATUS_CLASS[status || ""] || "border-border text-muted-foreground";
}

function entryId(entry: string | Record<string, any>): string | null {
  if (typeof entry === "string") return entry;
  if (entry && typeof entry === "object") return (entry.id as string) || null;
  return null;
}

function blockLabel(entry: string | Record<string, any> | null | undefined, byId: Record<string, string>): string {
  if (entry === null || entry === undefined) return "n/a";
  if (typeof entry === "string") return byId[entry] || entry;
  const label = (entry.name ?? entry.label ?? entry.id) as unknown;
  return nText(label);
}

function parseJsonObject(text: string, label: string): { value?: Record<string, any>; error: string | null } {
  const trimmed = text.trim();
  if (!trimmed) return { value: undefined, error: null };
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return { value: parsed as Record<string, any>, error: null };
    }
    return { value: undefined, error: `${label} must be a JSON object.` };
  } catch {
    return { value: undefined, error: `${label} must be valid JSON.` };
  }
}

function countOf(list: unknown): number | null {
  return Array.isArray(list) ? list.length : null;
}

function catalogOptionValue(option: CatalogOption): string {
  if (option === null || option === undefined) return "";
  if (typeof option === "string") return option;
  return ((option.key as string) || (option.label as string) || "");
}

function catalogOptionLabel(option: CatalogOption): string {
  if (option === null || option === undefined) return "";
  if (typeof option === "string") return option;
  return ((option.label as string) || (option.key as string) || "");
}

const TRUST_ORDER = ["TRUSTED_INSTRUCTION", "TRUSTED_POLICY", "UNTRUSTED_CONTENT", "TOOL_OUTPUT"];

const TRUST_CLASS: Record<string, string> = {
  TRUSTED_INSTRUCTION: "border-emerald-500/40 bg-emerald-500/10 text-emerald-600",
  TRUSTED_POLICY: "border-blue-500/40 bg-blue-500/10 text-blue-600",
  UNTRUSTED_CONTENT: "border-amber-500/40 bg-amber-500/10 text-amber-600",
  TOOL_OUTPUT: "border-muted-foreground/30 bg-muted/40 text-muted-foreground",
};

function trustClass(trust?: string | null): string {
  return TRUST_CLASS[trust || ""] || "border-border text-muted-foreground";
}

function isUntrustedTrust(trust?: string | null): boolean {
  return trust === "UNTRUSTED_CONTENT" || trust === "TOOL_OUTPUT";
}

function parseJsonArray(text: string, label: string): { value?: any[]; error: string | null } {
  const trimmed = text.trim();
  if (!trimmed) return { value: undefined, error: null };
  try {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed)) return { value: parsed, error: null };
    return { value: undefined, error: `${label} must be a JSON array.` };
  } catch {
    return { value: undefined, error: `${label} must be valid JSON.` };
  }
}

type BuilderMode = "simple" | "structured" | "raw";

type ExperimentUsageRow = {
  experiment_id: string;
  label: string | null;
  versions: number[];
  runs: number;
};

type VersionForm = {
  content: string;
  system_prompt: string;
  variablesText: string;
  structuredSpecText: string;
  notes: string;
  blockIds: string[];
  goal: string;
  role: string;
  constraintsText: string;
  expectedOutput: string;
  objective: string;
  phasesText: string;
  validationText: string;
  outputFormat: string;
  outputSectionsText: string;
};

const EMPTY_FORM: VersionForm = {
  content: "", system_prompt: "", variablesText: "", structuredSpecText: "", notes: "", blockIds: [],
  goal: "", role: "", constraintsText: "", expectedOutput: "",
  objective: "", phasesText: "", validationText: "", outputFormat: "", outputSectionsText: "",
};

function splitLines(text: string): string[] {
  return String(text || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function assembleSimple(goal: string, role: string, constraints: string[], expectedOutput: string): string {
  const parts: string[] = [];
  if (goal.trim()) parts.push(`## Goal\n${goal.trim()}`);
  if (role.trim()) parts.push(`## Role\n${role.trim()}`);
  if (constraints.length > 0) parts.push(`## Constraints\n${constraints.map((item) => `- ${item}`).join("\n")}`);
  if (expectedOutput.trim()) parts.push(`## Expected output\n${expectedOutput.trim()}`);
  return parts.join("\n\n");
}

function assembleStructured(
  objective: string,
  phases: string[],
  validation: string[],
  outputFormat: string,
  requiredSections: string[]
): string {
  const parts: string[] = [];
  if (objective.trim()) parts.push(`## Objective\n${objective.trim()}`);
  if (phases.length > 0) parts.push(`## Execution phases\n${phases.map((item) => `- ${item}`).join("\n")}`);
  if (validation.length > 0) parts.push(`## Validation\n${validation.map((item) => `- ${item}`).join("\n")}`);
  const contract: string[] = [];
  if (outputFormat.trim()) contract.push(`format: ${outputFormat.trim()}`);
  if (requiredSections.length > 0) {
    contract.push(`required sections:\n${requiredSections.map((item) => `- ${item}`).join("\n")}`);
  }
  if (contract.length > 0) parts.push(`## Output contract\n${contract.join("\n")}`);
  return parts.join("\n\n");
}

const diffLineClass = (op?: string | null): string => {
  if (op === "insert") return "bg-emerald-500/10 text-emerald-600";
  if (op === "delete") return "bg-destructive/10 text-destructive";
  return "text-muted-foreground";
};

function hasDifferences(diff: PromptVersionDiff | null): boolean {
  if (!diff) return false;
  const summary = diff.summary || {};
  const summaryChanged =
    (summary.lines_added || 0) > 0 ||
    (summary.lines_removed || 0) > 0 ||
    (summary.fields_changed || 0) > 0;
  const contentChanged = (diff.content_diff || []).some((l) => l.op !== "equal");
  const fieldsChanged = (diff.fields || []).some((f) => f.changed);
  const variables = diff.variables || {};
  const blocks = diff.blocks || {};
  return (
    summaryChanged ||
    contentChanged ||
    fieldsChanged ||
    (variables.added?.length || 0) > 0 ||
    (variables.removed?.length || 0) > 0 ||
    (variables.changed?.length || 0) > 0 ||
    (blocks.added?.length || 0) > 0 ||
    (blocks.removed?.length || 0) > 0
  );
}

function CompileResultPanel({ result }: { result: CompiledPrompt }) {
  const groups = useMemo(() => {
    const sections = Array.isArray(result.sections) ? result.sections : [];
    const map = new Map<string, CompiledPromptSection[]>();
    for (const section of sections) {
      const trust = (section.trust as string) || "n/a";
      const list = map.get(trust) || [];
      list.push(section);
      map.set(trust, list);
    }
    const rank = (trust: string) => {
      const index = TRUST_ORDER.indexOf(trust);
      return index === -1 ? TRUST_ORDER.length : index;
    };
    return Array.from(map.entries())
      .sort((a, b) => rank(a[0]) - rank(b[0]))
      .map(([trust, items]) => ({ trust, items }));
  }, [result.sections]);

  const messages = Array.isArray(result.messages) ? result.messages : [];
  const blocksUsed = Array.isArray(result.blocks_used) ? result.blocks_used : [];
  const policiesUsed = Array.isArray(result.policies_used) ? result.policies_used : [];
  const invariantsUsed = Array.isArray(result.invariants_used) ? result.invariants_used : [];

  return (
    <div className="space-y-3 rounded-md border border-border p-3" data-testid="prompt-compile-result">
      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">compiler</span>
          <span className="font-mono" data-testid="prompt-compile-compiler-version">{nText(result.compiler_version)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">target</span>
          <span className="font-mono" data-testid="prompt-compile-result-target">{nText(result.target)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">mode</span>
          <span className="font-mono" data-testid="prompt-compile-result-mode">{nText(result.mode)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">token estimate</span>
          <span className="font-mono" data-testid="prompt-compile-token-estimate">{nText(result.token_estimate)}</span>
        </div>
      </div>

      <div className="flex items-center gap-1 text-xs">
        <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">checksum</span>
        <span className="font-mono" data-testid="prompt-compile-checksum">{shortHash(result.checksum)}</span>
        {result.checksum ? <CopyButton value={result.checksum} testId="prompt-compile-checksum-copy" /> : null}
      </div>

      <div>
        <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">rendered text</p>
        <pre
          className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 font-mono text-[11px]"
          data-testid="prompt-compile-rendered-text"
        >
          {nText(result.rendered_text)}
        </pre>
      </div>

      <div className="space-y-1">
        <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">messages</p>
        {messages.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="prompt-compile-messages-empty">n/a</p>
        ) : (
          messages.map((message, index) => (
            <div key={index} className="rounded border border-border p-2 text-xs" data-testid="prompt-compile-message">
              <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{nText(message.role)}</span>
              <p className="mt-0.5 whitespace-pre-wrap break-words">{nText(message.content)}</p>
            </div>
          ))
        )}
      </div>

      <div className="space-y-2">
        <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">sections</p>
        {groups.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="prompt-compile-sections-empty">n/a</p>
        ) : (
          groups.map((group) => (
            <div key={group.trust} className="space-y-1" data-testid="prompt-compile-section-group" data-trust={group.trust}>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className={cn("text-[10px]", trustClass(group.trust))} data-testid="prompt-compile-section-trust">
                  {nText(group.trust)}
                </Badge>
                {isUntrustedTrust(group.trust) ? (
                  <span className="text-[10px] font-mono text-amber-600" data-testid="prompt-compile-untrusted-label">
                    untrusted — never treated as policy
                  </span>
                ) : null}
              </div>
              {group.items.map((section, index) => (
                <div key={index} className="rounded border border-border p-2 text-xs" data-testid="prompt-compile-section" data-trust={group.trust}>
                  <div className="flex flex-wrap items-center gap-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                    <span>{nText(section.kind)}</span>
                    <span>· tokens: {nText(section.token_estimate)}</span>
                    {section.source_ref ? <span>· source: {nText(section.source_ref)}</span> : null}
                  </div>
                  {section.title ? <p className="mt-1 font-medium">{nText(section.title)}</p> : null}
                  <p className="mt-1 whitespace-pre-wrap break-words">{nText(section.content)}</p>
                </div>
              ))}
            </div>
          ))
        )}
      </div>

      <div className="grid gap-2 text-xs sm:grid-cols-3">
        <div>
          <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
            blocks used ({nText(countOf(result.blocks_used))})
          </p>
          {blocksUsed.length === 0 ? (
            <p className="text-muted-foreground" data-testid="prompt-compile-blocks-empty">n/a</p>
          ) : (
            blocksUsed.map((block, index) => (
              <p key={block.id || index} data-testid="prompt-compile-block-used">
                {nText(block.name || block.id)}{block.version != null ? ` v${nText(block.version)}` : ""}
              </p>
            ))
          )}
        </div>
        <div>
          <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
            policies used ({nText(countOf(result.policies_used))})
          </p>
          {policiesUsed.length === 0 ? (
            <p className="text-muted-foreground" data-testid="prompt-compile-policies-empty">n/a</p>
          ) : (
            policiesUsed.map((policy, index) => (
              <p key={policy.id || index} data-testid="prompt-compile-policy-used">
                {nText(policy.name || policy.family_id || policy.id)}{policy.domain ? ` · ${nText(policy.domain)}` : ""}
              </p>
            ))
          )}
        </div>
        <div>
          <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
            invariants used ({nText(countOf(result.invariants_used))})
          </p>
          {invariantsUsed.length === 0 ? (
            <p className="text-muted-foreground" data-testid="prompt-compile-invariants-empty">n/a</p>
          ) : (
            invariantsUsed.map((invariant, index) => (
              <p key={invariant.id || index} data-testid="prompt-compile-invariant-used">
                {nText(invariant.code || invariant.id)}{invariant.severity ? ` · ${nText(invariant.severity)}` : ""}
              </p>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

export default function PromptDetail() {
  const { id } = useParams<{ id: string }>();
  const promptId = id as string;
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: spec, isLoading: specLoading, isError: specError, error: specErr } = usePromptDetail(promptId);
  const { data: versionsData, isLoading: versionsLoading } = usePromptVersions(promptId);
  const { data: usage, isLoading: usageLoading } = usePromptUsage(promptId, true);
  const { data: blocksData } = usePromptBlocks(spec?.project_id || undefined);

  const versions = asArray<PromptVersion>(versionsData);
  const blocks = asArray<PromptBlock>(blocksData);
  const blocksById = useMemo(() => {
    const map: Record<string, string> = {};
    for (const block of blocks) {
      if (block?.id) map[block.id] = (block.name as string) || block.id;
    }
    return map;
  }, [blocks]);

  const [specOpen, setSpecOpen] = useState(false);
  const [specForm, setSpecForm] = useState({ name: "", purpose: "", description: "", status: "draft" });

  const [formOpen, setFormOpen] = useState(false);
  const [editingVersion, setEditingVersion] = useState<PromptVersion | null>(null);
  const [form, setForm] = useState<VersionForm>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [builderMode, setBuilderMode] = useState<BuilderMode>("raw");

  const usageVersions = asArray<PromptUsageVersion>(usage?.versions);
  const usageRuns = useMemo(() => {
    const list: PromptUsageRun[] = [];
    for (const item of usageVersions) {
      for (const run of asArray<PromptUsageRun>(item.used_by?.runs)) list.push(run);
    }
    return list;
  }, [usageVersions]);
  const usageRunIds = useMemo(
    () => Array.from(new Set(usageRuns.map((run) => run.run_id).filter((value): value is string => Boolean(value)))),
    [usageRuns]
  );

  const experimentRows = useMemo<ExperimentUsageRow[]>(() => {
    const map = new Map<string, ExperimentUsageRow>();
    for (const item of usageVersions) {
      const experiments = asArray<any>(item.used_by?.experiments);
      const runs = asArray<PromptUsageRun>(item.used_by?.runs);
      for (const experiment of experiments) {
        const id = (experiment?.experiment_id as string) || null;
        if (!id) continue;
        const entry = map.get(id) || { experiment_id: id, label: null, versions: [], runs: 0 };
        if (experiment?.label) entry.label = experiment.label;
        if (item.version != null && !entry.versions.includes(item.version)) entry.versions.push(item.version);
        entry.runs += runs.filter((run) => run.experiment_id === id).length;
        map.set(id, entry);
      }
    }
    return Array.from(map.values()).sort((a, b) => a.experiment_id.localeCompare(b.experiment_id));
  }, [usageVersions]);

  const { data: policiesData } = useQuery<ProjectPolicy[]>({
    queryKey: ["project-policies", spec?.project_id || null, "builder"],
    queryFn: async () => (typeof listProjectPolicies === "function" ? listProjectPolicies(spec?.project_id as string) : []),
    enabled: Boolean(spec?.project_id) && builderMode === "structured",
  });
  const policies = asArray<ProjectPolicy>(policiesData);
  const activePolicies = useMemo(
    () => policies.filter((policy) => !policy.status || policy.status === "active"),
    [policies]
  );

  const { data: findingsData, isError: findingsError } = useQuery<any[]>({
    queryKey: ["prompt-findings", promptId, usageRunIds.join(",")],
    queryFn: async () => {
      const groups = await Promise.all(
        usageRunIds.map(async (runId) => {
          const res = await api.get("/failures", { params: { run_id: runId } });
          return asArray<any>(res?.data).map((failure) => ({ ...failure, run_id: failure?.run_id || runId }));
        })
      );
      return groups.flat();
    },
    enabled: usageRunIds.length > 0,
  });
  const findings = asArray<any>(findingsData);

  const [viewVersion, setViewVersion] = useState<PromptVersion | null>(null);

  const [fromId, setFromId] = useState<string>("");
  const [toId, setToId] = useState<string>("");
  const [diff, setDiff] = useState<PromptVersionDiff | null>(null);

  const [compileVersion, setCompileVersion] = useState<PromptVersion | null>(null);
  const [compileOpen, setCompileOpen] = useState(false);
  const [compileTarget, setCompileTarget] = useState("");
  const [compileMode, setCompileMode] = useState("");
  const [compileVars, setCompileVars] = useState<{ key: string; value: string }[]>([]);
  const [compileBlockIds, setCompileBlockIds] = useState<string[]>([]);
  const [compileIncludePolicies, setCompileIncludePolicies] = useState(false);
  const [compileIncludeInvariants, setCompileIncludeInvariants] = useState(false);
  const [compilePackIds, setCompilePackIds] = useState("");
  const [compileUntrusted, setCompileUntrusted] = useState({ title: "", content: "" });
  const [compileToolOutput, setCompileToolOutput] = useState({ title: "", content: "" });
  const [compileToolDefs, setCompileToolDefs] = useState("");
  const [compileError, setCompileError] = useState<string | null>(null);
  const [compileResult, setCompileResult] = useState<CompiledPrompt | null>(null);

  const { data: compileCatalogData } = usePromptCompilerCatalog(compileOpen);
  const catalogTargets = asArray<CatalogOption>(compileCatalogData?.targets);
  const catalogModes = asArray<CatalogOption>(compileCatalogData?.modes);

  const historyVersion = useMemo(() => {
    const compilable = versions.find((version) => version.status === "frozen" || version.status === "deprecated");
    return compilable || versions[0] || null;
  }, [versions]);
  const historyVersionId = (historyVersion?.id as string) || null;
  const compiledHistory = useCompiledPrompts(historyVersionId, spec?.project_id ?? null);
  const compiledItems = asArray<CompiledPrompt>(compiledHistory.data);

  useEffect(() => {
    if (!compileTarget && catalogTargets.length > 0) {
      setCompileTarget(catalogOptionValue(catalogTargets[0]));
    }
  }, [catalogTargets, compileTarget]);

  useEffect(() => {
    if (!compileMode && catalogModes.length > 0) {
      setCompileMode(catalogOptionValue(catalogModes[0]));
    }
  }, [catalogModes, compileMode]);

  const openCompile = (version: PromptVersion) => {
    const declared = version.variables && typeof version.variables === "object" ? version.variables : {};
    setCompileVersion(version);
    setCompileVars(
      Object.keys(declared).map((key) => {
        const raw = (declared as Record<string, any>)[key];
        const value = raw === null || raw === undefined ? "" : typeof raw === "string" ? raw : JSON.stringify(raw);
        return { key, value };
      })
    );
    setCompileBlockIds(
      asArray<string | Record<string, any>>(version.blocks)
        .map((entry) => entryId(entry))
        .filter((entry): entry is string => Boolean(entry))
    );
    setCompileTarget("");
    setCompileMode("");
    setCompileIncludePolicies(false);
    setCompileIncludeInvariants(false);
    setCompilePackIds("");
    setCompileUntrusted({ title: "", content: "" });
    setCompileToolOutput({ title: "", content: "" });
    setCompileToolDefs("");
    setCompileError(null);
    setCompileResult(null);
    setCompileOpen(true);
  };

  const toggleCompileBlock = (blockId: string, checked: boolean) => {
    setCompileBlockIds((current) => {
      if (checked) {
        return current.includes(blockId) ? current : [...current, blockId];
      }
      return current.filter((value) => value !== blockId);
    });
  };

  const updateCompileVar = (index: number, patch: Partial<{ key: string; value: string }>) => {
    setCompileVars((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };

  const addCompileVar = () => setCompileVars((current) => [...current, { key: "", value: "" }]);

  const removeCompileVar = (index: number) =>
    setCompileVars((current) => current.filter((_, i) => i !== index));

  const buildCompileBody = (): PromptCompileRequest | null => {
    const toolDefs = parseJsonArray(compileToolDefs, "Tool definitions");
    if (toolDefs.error) {
      setCompileError(toolDefs.error);
      toast.error(toolDefs.error);
      return null;
    }
    const variables: Record<string, any> = {};
    for (const row of compileVars) {
      const key = row.key.trim();
      if (key) variables[key] = row.value;
    }
    const packIds = compilePackIds
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    const untrusted = compileUntrusted.title.trim() || compileUntrusted.content.trim()
      ? [{ title: compileUntrusted.title, content: compileUntrusted.content }]
      : [];
    const toolOutputs = compileToolOutput.title.trim() || compileToolOutput.content.trim()
      ? [{ title: compileToolOutput.title, content: compileToolOutput.content }]
      : [];
    setCompileError(null);
    return {
      project_id: (spec?.project_id as string) || "",
      prompt_version_id: (compileVersion?.id as string) || "",
      target: compileTarget,
      mode: compileMode,
      variables,
      block_ids: compileBlockIds,
      policy_ids: [],
      include_policies: compileIncludePolicies,
      include_invariants: compileIncludeInvariants,
      context_pack_version_ids: packIds,
      untrusted_context: untrusted,
      tool_outputs: toolOutputs,
      ...(toolDefs.value ? { tool_definitions: toolDefs.value } : {}),
    };
  };

  useEffect(() => {
    const list = asArray<PromptVersion>(versionsData);
    if (list.length === 0) return;
    setFromId((cur) => (cur && list.some((v) => v.id === cur) ? cur : ((list[list.length - 1].id as string) || "")));
    setToId((cur) => (cur && list.some((v) => v.id === cur) ? cur : ((list[0].id as string) || "")));
  }, [versionsData]);

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ["prompt-versions", promptId] });
    qc.invalidateQueries({ queryKey: ["prompt-detail", promptId] });
    qc.invalidateQueries({ queryKey: ["prompt-usage", promptId] });
  };

  const openSpecEdit = () => {
    setSpecForm({
      name: spec?.name || "",
      purpose: spec?.purpose || "",
      description: spec?.description || "",
      status: spec?.status || "draft",
    });
    setSpecOpen(true);
  };

  const specSave = useMutation({
    mutationFn: async () => updatePrompt(promptId, {
      name: specForm.name.trim(),
      purpose: specForm.purpose,
      description: specForm.description,
      status: specForm.status,
    }),
    onSuccess: () => { toast.success("Prompt spec updated"); qc.invalidateQueries({ queryKey: ["prompt-detail", promptId] }); setSpecOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const openCreate = () => {
    setEditingVersion(null);
    setForm(EMPTY_FORM);
    setBuilderMode("raw");
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (version: PromptVersion) => {
    const structured = version.structured_spec && typeof version.structured_spec === "object"
      ? (version.structured_spec as Record<string, any>)
      : null;
    const outputContract = (structured?.output_contract || {}) as Record<string, any>;
    setEditingVersion(version);
    setForm({
      content: version.content || "",
      system_prompt: version.system_prompt || "",
      variablesText: version.variables ? JSON.stringify(version.variables, null, 2) : "",
      structuredSpecText: structured ? JSON.stringify(structured, null, 2) : "",
      notes: version.notes || "",
      blockIds: asArray<string | Record<string, any>>(version.blocks)
        .map((entry) => entryId(entry))
        .filter((entry): entry is string => Boolean(entry)),
      goal: "",
      role: "",
      constraintsText: "",
      expectedOutput: "",
      objective: typeof structured?.objective === "string" ? structured.objective : "",
      phasesText: asArray<string>(structured?.phases).join("\n"),
      validationText: asArray<string>(structured?.validation).join("\n"),
      outputFormat: typeof outputContract.format === "string" ? outputContract.format : "",
      outputSectionsText: asArray<string>(outputContract.required_sections).join("\n"),
    });
    setBuilderMode(structured ? "structured" : "raw");
    setFormError(null);
    setFormOpen(true);
  };

  const builderContent = useMemo<string>(() => {
    if (builderMode === "simple") {
      return assembleSimple(form.goal, form.role, splitLines(form.constraintsText), form.expectedOutput);
    }
    if (builderMode === "structured") {
      return assembleStructured(
        form.objective,
        splitLines(form.phasesText),
        splitLines(form.validationText),
        form.outputFormat,
        splitLines(form.outputSectionsText)
      );
    }
    return form.content;
  }, [builderMode, form]);

  const buildBody = (): Record<string, any> | null => {
    const variables = parseJsonObject(form.variablesText, "Variables");
    if (variables.error) { setFormError(variables.error); return null; }
    let structuredValue: Record<string, any> | undefined;
    if (builderMode === "structured") {
      structuredValue = {
        objective: form.objective.trim(),
        blocks: form.blockIds,
        policies: activePolicies.map((policy) => policy.id).filter(Boolean),
        variables: variables.value ?? {},
        phases: splitLines(form.phasesText),
        validation: splitLines(form.validationText),
        output_contract: {
          format: form.outputFormat.trim() || null,
          required_sections: splitLines(form.outputSectionsText),
        },
      };
    } else {
      const structured = parseJsonObject(form.structuredSpecText, "Structured spec");
      if (structured.error) { setFormError(structured.error); return null; }
      structuredValue = structured.value;
    }
    setFormError(null);
    const content = builderMode === "raw" ? form.content : builderContent;
    const systemPrompt = builderMode === "simple" ? form.role : form.system_prompt;
    const body: Record<string, any> = {
      content,
      system_prompt: systemPrompt,
      notes: form.notes,
      blocks: form.blockIds,
    };
    if (variables.value !== undefined) body.variables = variables.value;
    if (structuredValue !== undefined) body.structured_spec = structuredValue;
    return body;
  };

  const createVersionMut = useMutation({
    mutationFn: async (body: Record<string, any>) => createPromptVersion(promptId, body),
    onSuccess: () => { toast.success("Draft version created"); invalidateAll(); setFormOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const updateVersionMut = useMutation({
    mutationFn: async (body: Record<string, any>) => updatePromptVersion(promptId, editingVersion!.id as string, body),
    onSuccess: () => { toast.success("Draft version updated"); invalidateAll(); setFormOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const freezeMut = useMutation({
    mutationFn: async (versionId: string) => freezePromptVersion(promptId, versionId),
    onSuccess: () => { toast.success("Version frozen"); invalidateAll(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const deprecateMut = useMutation({
    mutationFn: async (versionId: string) => deprecatePromptVersion(promptId, versionId),
    onSuccess: () => { toast.success("Version deprecated"); invalidateAll(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const cloneMut = useMutation({
    mutationFn: async (versionId: string) => clonePromptVersion(promptId, versionId),
    onSuccess: () => { toast.success("Version cloned to a new draft"); invalidateAll(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const restoreMut = useMutation({
    mutationFn: async (versionId: string) => restorePromptVersion(promptId, versionId),
    onSuccess: () => { toast.success("Version restored"); invalidateAll(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const compareMut = useMutation({
    mutationFn: async () => comparePromptVersions(promptId, fromId, toId),
    onSuccess: (res) => setDiff(res as PromptVersionDiff),
    onError: (e) => toast.error(apiError(e)),
  });

  const previewCompileMut = useMutation({
    mutationFn: async (body: PromptCompileRequest) => previewPromptCompilation(body),
    onSuccess: (res) => setCompileResult(res as CompiledPrompt),
    onError: (e) => toast.error(apiError(e)),
  });

  const compileMut = useMutation({
    mutationFn: async (body: PromptCompileRequest) => compilePrompt(body),
    onSuccess: (res) => {
      setCompileResult(res as CompiledPrompt);
      qc.invalidateQueries({ queryKey: ["compiled-prompts"] });
      toast.success("Prompt compiled");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const viewCompiledMut = useMutation({
    mutationFn: async (id: string) => getCompiledPrompt(id),
    onSuccess: (res) => setCompileResult(res as CompiledPrompt),
    onError: (e) => toast.error(apiError(e)),
  });

  const runPreview = () => {
    const body = buildCompileBody();
    if (!body) return;
    previewCompileMut.mutate(body);
  };

  const runCompile = () => {
    const body = buildCompileBody();
    if (!body) return;
    compileMut.mutate(body);
  };

  const viewCompiled = (item: CompiledPrompt) => {
    setCompileVersion(historyVersion);
    setCompileOpen(true);
    if (item.id) viewCompiledMut.mutate(item.id as string);
  };

  const submitForm = () => {
    const body = buildBody();
    if (!body) return;
    if (editingVersion?.id) updateVersionMut.mutate(body);
    else createVersionMut.mutate(body);
  };

  const toggleBlock = (blockId: string, checked: boolean) => {
    setForm((current) => {
      if (checked) {
        return current.blockIds.includes(blockId)
          ? current
          : { ...current, blockIds: [...current.blockIds, blockId] };
      }
      return { ...current, blockIds: current.blockIds.filter((value) => value !== blockId) };
    });
  };

  if (specLoading) {
    return (
      <div className="mx-auto max-w-6xl p-4 lg:p-6" data-testid="prompt-detail-page">
        <LoadingSpinner label="Loading prompt…" />
      </div>
    );
  }

  if (specError || !spec) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 p-4 lg:p-6" data-testid="prompt-detail-page">
        <Button variant="ghost" size="sm" onClick={() => navigate("/prompt-lab")} data-testid="back-to-prompt-lab">
          <ArrowLeft className="mr-1.5 h-4 w-4" /> Prompt Lab
        </Button>
        <ErrorNote message={apiError(specErr) || "Prompt not found"} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="prompt-detail-page">
      <button
        onClick={() => navigate("/prompt-lab")}
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        data-testid="back-to-prompt-lab"
      >
        <ArrowLeft className="h-4 w-4" /> Prompt Lab
      </button>

      <SectionHeader
        title={nText(spec.name)}
        caption={nText(spec.purpose)}
        action={
          <div className="flex shrink-0 items-center gap-2">
            <Badge variant="outline" className="text-[10px]" data-testid="prompt-spec-status">{nText(spec.status)}</Badge>
            <Button size="sm" variant="outline" data-testid="prompt-spec-edit-open" onClick={openSpecEdit}>
              <Pencil className="mr-1.5 h-4 w-4" /> Edit spec
            </Button>
          </div>
        }
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="prompt-meta">
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">versions</span>
          <span className="font-mono text-sm" data-testid="prompt-versions-count">{nText(spec.versions_count)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">created</span>
          <span className="font-mono text-sm">{isoDate(spec.created_at)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">updated</span>
          <span className="font-mono text-sm">{isoDate(spec.updated_at)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">description</span>
          <span className="text-sm text-muted-foreground">{nText(spec.description)}</span>
        </div>
      </div>

      <Card className="space-y-3 p-4" data-testid="prompt-versions-panel">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Versions</h3>
          <Button size="sm" data-testid="prompt-new-draft" onClick={openCreate}>
            <Plus className="mr-1.5 h-4 w-4" /> New draft
          </Button>
        </div>

        {versionsLoading ? (
          <LoadingSpinner label="Loading versions…" />
        ) : versions.length === 0 ? (
          <p className="text-xs text-muted-foreground">No versions returned.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <Table data-testid="prompt-versions-table">
              <TableHeader>
                <TableRow className="bg-muted/50">
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Version</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Status</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Created</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Content hash</TableHead>
                  <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {versions.map((version, index) => {
                  const vid = version.id as string;
                  const isDraft = version.status === "draft";
                  const canDeprecate = version.status === "draft" || version.status === "frozen";
                  return (
                    <TableRow key={version.id || index} data-testid="prompt-version-row">
                      <TableCell className="font-mono text-xs" data-testid="prompt-version-number">v{nText(version.version)}</TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={cn("text-[10px]", statusClass(version.status))}
                          data-testid="prompt-version-status"
                        >
                          {nText(version.status)}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-[11px] text-muted-foreground">{isoDate(version.created_at)}</TableCell>
                      <TableCell
                        className="font-mono text-[11px] text-muted-foreground"
                        title={version.content_hash || undefined}
                        data-testid="prompt-version-hash"
                      >
                        {shortHash(version.content_hash)}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap justify-end gap-1">
                          <Button size="sm" variant="ghost" title="View" data-testid="prompt-version-view" onClick={() => setViewVersion(version)}>
                            <Eye className="h-3.5 w-3.5" />
                          </Button>
                          {isDraft && (
                            <Button size="sm" variant="ghost" title="Edit draft" data-testid="prompt-version-edit" onClick={() => openEdit(version)}>
                              <Pencil className="h-3.5 w-3.5" />
                            </Button>
                          )}
                          {isDraft && (
                            <Button
                              size="sm"
                              variant="ghost"
                              title="Freeze"
                              data-testid="prompt-version-freeze"
                              disabled={freezeMut.isPending}
                              onClick={() => vid && freezeMut.mutate(vid)}
                            >
                              <Snowflake className="h-3.5 w-3.5" />
                            </Button>
                          )}
                          {canDeprecate && (
                            <Button
                              size="sm"
                              variant="ghost"
                              title="Deprecate"
                              data-testid="prompt-version-deprecate"
                              disabled={deprecateMut.isPending || !vid}
                              onClick={() => deprecateMut.mutate(vid)}
                            >
                              <Archive className="h-3.5 w-3.5" />
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="ghost"
                            title="Clone to new draft"
                            data-testid="prompt-version-clone"
                            disabled={cloneMut.isPending || !vid}
                            onClick={() => cloneMut.mutate(vid)}
                          >
                            <Plus className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            title="Restore"
                            data-testid="prompt-version-restore"
                            disabled={restoreMut.isPending || !vid}
                            onClick={() => restoreMut.mutate(vid)}
                          >
                            <RotateCcw className="h-3.5 w-3.5" />
                          </Button>
                          {(version.status === "frozen" || version.status === "deprecated") && (
                            <Button
                              size="sm"
                              variant="ghost"
                              title="Preview / Compile"
                              data-testid="prompt-compile-open"
                              onClick={() => openCompile(version)}
                            >
                              <Play className="h-3.5 w-3.5" />
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </Card>

      <Card className="space-y-3 p-4" data-testid="prompt-compare-panel">
        <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Compare versions</h3>
        {versions.length < 2 ? (
          <p className="text-xs text-muted-foreground">n/a — at least two versions are required to compare.</p>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Select value={fromId} onValueChange={(value) => { setFromId(value); setDiff(null); }}>
              <SelectTrigger className="w-52" data-testid="prompt-compare-from"><SelectValue placeholder="From version" /></SelectTrigger>
              <SelectContent>
                {versions.map((version, index) => (
                  <SelectItem key={version.id || index} value={version.id as string}>v{nText(version.version)} · {nText(version.status)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={toId} onValueChange={(value) => { setToId(value); setDiff(null); }}>
              <SelectTrigger className="w-52" data-testid="prompt-compare-to"><SelectValue placeholder="To version" /></SelectTrigger>
              <SelectContent>
                {versions.map((version, index) => (
                  <SelectItem key={version.id || index} value={version.id as string}>v{nText(version.version)} · {nText(version.status)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              size="sm"
              data-testid="prompt-compare"
              disabled={!fromId || !toId || compareMut.isPending}
              onClick={() => compareMut.mutate()}
            >
              Compare
            </Button>
          </div>
        )}

        {diff && (
          <div className="space-y-3" data-testid="prompt-compare-result">
            {!hasDifferences(diff) ? (
              <p className="text-xs text-muted-foreground" data-testid="prompt-compare-no-diff">No differences</p>
            ) : (
              <>
                <div className="flex flex-wrap gap-4 text-xs" data-testid="prompt-compare-summary">
                  <span>lines added: <span className="font-mono">{nText(diff.summary?.lines_added)}</span></span>
                  <span>lines removed: <span className="font-mono">{nText(diff.summary?.lines_removed)}</span></span>
                  <span>fields changed: <span className="font-mono">{nText(diff.summary?.fields_changed)}</span></span>
                </div>

                <div className="space-y-0.5 rounded-md border border-border p-2" data-testid="prompt-content-diff">
                  {(diff.content_diff || []).map((line: PromptVersionDiffLine, index) => (
                    <div
                      key={index}
                      data-testid="prompt-diff-line"
                      data-op={line.op || "equal"}
                      className={cn("grid grid-cols-[2.5rem_2.5rem_1fr] gap-2 rounded px-1.5 py-0.5 font-mono text-[11px]", diffLineClass(line.op))}
                    >
                      <span className="text-right tabular">{nText(line.from_line)}</span>
                      <span className="text-right tabular">{nText(line.to_line)}</span>
                      <span className="whitespace-pre-wrap break-words">{nText(line.text)}</span>
                    </div>
                  ))}
                </div>

                <div className="space-y-1 text-xs" data-testid="prompt-compare-fields">
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Fields</p>
                  {(diff.fields || []).length === 0 ? (
                    <p className="text-muted-foreground">n/a</p>
                  ) : (
                    (diff.fields || []).map((field, index) => (
                      <div key={index} className="flex flex-wrap gap-2" data-testid="prompt-diff-field">
                        <span className="font-mono">{nText(field.field)}</span>
                        <span className={field.changed ? "text-amber-600" : "text-muted-foreground"}>
                          {field.changed ? "changed" : "unchanged"}
                        </span>
                        {field.changed && (
                          <span className="text-muted-foreground">
                            {nText(field.from)} → {nText(field.to)}
                          </span>
                        )}
                      </div>
                    ))
                  )}
                </div>

                <div className="grid gap-3 md:grid-cols-2" data-testid="prompt-compare-collections">
                  <div className="space-y-1 text-xs">
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Variables</p>
                    <p>added: {nText(diff.variables?.added?.length)}</p>
                    <p>removed: {nText(diff.variables?.removed?.length)}</p>
                    <p>changed: {nText(diff.variables?.changed?.length)}</p>
                  </div>
                  <div className="space-y-1 text-xs">
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Blocks</p>
                    <p>added: {nText(diff.blocks?.added?.length)}</p>
                    <p>removed: {nText(diff.blocks?.removed?.length)}</p>
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </Card>

      <Card className="space-y-3 p-4" data-testid="prompt-usage-panel">
        <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Usage</h3>
        {usageLoading ? (
          <LoadingSpinner label="Loading usage…" />
        ) : usageVersions.length === 0 ? (
          <EmptyState
            icon={<FileCode2 className="h-6 w-6" />}
            title="No usage recorded"
            description="This prompt is not referenced by any experiment or run yet."
            testId="prompt-usage-empty"
          />
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-4 text-xs" data-testid="prompt-usage-totals">
              <span>experiments: <span className="font-mono">{nText(usage?.totals?.experiments)}</span></span>
              <span>runs: <span className="font-mono">{nText(usage?.totals?.runs)}</span></span>
              <span>evaluations: <span className="font-mono">{nText(usage?.totals?.evaluations)}</span></span>
            </div>
            {usageVersions.map((item, index) => {
              const usedBy = item.used_by || null;
              const experiments = asArray<any>(usedBy?.experiments);
              const runs = asArray<PromptUsageRun>(usedBy?.runs);
              return (
                <div key={item.id || index} className="space-y-2 rounded-md border border-border p-3" data-testid="prompt-usage-version">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs">v{nText(item.version)}</span>
                    <Badge variant="outline" className={cn("text-[10px]", statusClass(item.status))}>{nText(item.status)}</Badge>
                  </div>
                  <div className="flex flex-wrap gap-4 text-xs" data-testid="prompt-usage-version-counts">
                    <span>experiments: <span className="font-mono">{nText(countOf(usedBy?.experiments))}</span></span>
                    <span>runs: <span className="font-mono">{nText(countOf(usedBy?.runs))}</span></span>
                  </div>
                  <div className="space-y-1 text-xs">
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Experiments</p>
                    {experiments.length === 0 ? (
                      <p className="text-muted-foreground" data-testid="prompt-usage-experiment">n/a</p>
                    ) : (
                      experiments.map((experiment, expIndex) => (
                        <Link
                          key={experiment.experiment_id || expIndex}
                          to={`/experiments/${experiment.experiment_id}`}
                          className="block truncate text-primary hover:underline"
                          data-testid="prompt-usage-experiment"
                        >
                          {nText(experiment.label || experiment.experiment_id)}
                        </Link>
                      ))
                    )}
                  </div>
                  <div className="space-y-1 text-xs">
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Runs</p>
                    {runs.length === 0 ? (
                      <p className="text-muted-foreground" data-testid="prompt-usage-run">n/a</p>
                    ) : (
                      runs.map((run, runIndex) => (
                        <div key={run.run_id || runIndex} className="flex flex-wrap items-center gap-2" data-testid="prompt-usage-run">
                          <Link to={`/trace?run=${run.run_id}`} className="text-primary hover:underline">
                            {nText(run.run_id)}
                          </Link>
                          <span className="text-muted-foreground">{nText(run.status)}</span>
                          {asArray<any>(run.evaluations).length === 0 ? (
                            <span className="text-muted-foreground" data-testid="prompt-usage-evaluation">n/a</span>
                          ) : (
                            asArray<any>(run.evaluations).map((evaluation, evalIndex) => (
                              <Badge key={evaluation.id || evalIndex} variant="outline" className="text-[10px]" data-testid="prompt-usage-evaluation">
                                {nText(evaluation.evaluator_type)}: {nText(evaluation.outcome)}
                              </Badge>
                            ))
                          )}
                        </div>
                      ))
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <Card className="space-y-3 p-4" data-testid="prompt-experiments">
        <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Experiments</h3>
        {experimentRows.length === 0 ? (
          <EmptyState
            icon={<FileCode2 className="h-6 w-6" />}
            title="No experiments"
            description="This prompt is not referenced by any experiment yet."
            testId="prompt-experiments-empty"
          />
        ) : (
          <div className="space-y-2">
            {experimentRows.map((row) => (
              <div
                key={row.experiment_id}
                className="flex flex-wrap items-center gap-3 rounded-md border border-border p-2 text-xs"
                data-testid="prompt-experiment-row"
              >
                <Link
                  to={`/experiments/${row.experiment_id}`}
                  className="text-primary hover:underline"
                  data-testid="prompt-experiment-link"
                >
                  {nText(row.label || row.experiment_id)}
                </Link>
                <span className="font-mono text-[10px] text-muted-foreground" data-testid="prompt-experiment-id">
                  {row.experiment_id}
                </span>
                <span data-testid="prompt-experiment-versions">
                  versions: {row.versions.length > 0 ? row.versions.map((value) => `v${value}`).join(", ") : "n/a"}
                </span>
                <span data-testid="prompt-experiment-runs">runs: {row.runs}</span>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card className="space-y-3 p-4" data-testid="prompt-findings">
        <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Findings</h3>
        {findings.length === 0 ? (
          <div className="space-y-2">
            {findingsError ? (
              <p className="text-[11px] text-amber-600" data-testid="prompt-findings-unavailable">
                Findings unavailable — showing evaluation outcomes instead.
              </p>
            ) : null}
            {usageRuns.length === 0 ? (
              <p className="text-xs text-muted-foreground" data-testid="prompt-findings-empty">n/a</p>
            ) : (
              <>
                <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground" data-testid="prompt-findings-fallback-label">
                  Evaluation outcomes
                </p>
                {usageRuns.map((run, index) => (
                  <div key={run.run_id || index} className="flex flex-wrap items-center gap-2 text-xs" data-testid="prompt-findings-run">
                    {run.run_id ? (
                      <Link to={`/trace?run=${run.run_id}`} className="text-primary hover:underline">
                        {nText(run.run_id)}
                      </Link>
                    ) : (
                      <span>{nText(run.run_id)}</span>
                    )}
                    <span className="text-muted-foreground">{nText(run.status)}</span>
                    {asArray<any>(run.evaluations).length === 0 ? (
                      <span className="text-muted-foreground" data-testid="prompt-findings-evaluation">n/a</span>
                    ) : (
                      asArray<any>(run.evaluations).map((evaluation, evalIndex) => (
                        <Badge key={evaluation.id || evalIndex} variant="outline" className="text-[10px]" data-testid="prompt-findings-evaluation">
                          {nText(evaluation.evaluator_type)}: {nText(evaluation.outcome)}
                        </Badge>
                      ))
                    )}
                  </div>
                ))}
              </>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            {findings.map((finding, index) => (
              <div
                key={finding.id || index}
                className="flex flex-wrap items-center gap-3 rounded-md border border-border p-2 text-xs"
                data-testid="prompt-finding-row"
              >
                <Link
                  to={`/failures?run_id=${encodeURIComponent(finding.run_id || "")}`}
                  className="font-mono text-primary hover:underline"
                  data-testid="prompt-finding-link"
                >
                  {nText(finding.id)}
                </Link>
                <span data-testid="prompt-finding-domain">{nText(finding.failure_domain)}</span>
                <span data-testid="prompt-finding-taxonomy">{nText(finding.taxonomy_code || finding.category)}</span>
                <Badge variant="outline" className="text-[10px]" data-testid="prompt-finding-severity">{nText(finding.severity)}</Badge>
                <Badge variant="outline" className="text-[10px]" data-testid="prompt-finding-status">{nText(finding.status)}</Badge>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card className="space-y-3 p-4" data-testid="prompt-compile-history">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Compiled prompts</h3>
          {historyVersion ? (
            <span className="font-mono text-[11px] text-muted-foreground">
              v{nText(historyVersion.version)} · {nText(historyVersion.status)}
            </span>
          ) : null}
        </div>
        {compiledHistory.isLoading ? (
          <LoadingSpinner label="Loading compiled prompts…" />
        ) : compiledItems.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="prompt-compile-history-empty">
            n/a — no compiled prompts for this version.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <Table data-testid="prompt-compile-history-table">
              <TableHeader>
                <TableRow className="bg-muted/50">
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Id</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Checksum</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Target</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Tokens</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Created</TableHead>
                  <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {compiledItems.map((item, index) => (
                  <TableRow key={item.id || index} data-testid="prompt-compile-history-row">
                    <TableCell className="font-mono text-[11px]">{nText(item.id)}</TableCell>
                    <TableCell className="font-mono text-[11px]">{shortHash(item.checksum)}</TableCell>
                    <TableCell className="text-xs">{nText(item.target)}</TableCell>
                    <TableCell className="font-mono text-[11px]">{nText(item.token_estimate)}</TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground">{isoDate(item.created_at)}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        title="View"
                        data-testid="prompt-compile-history-view"
                        disabled={!item.id || viewCompiledMut.isPending}
                        onClick={() => viewCompiled(item)}
                      >
                        <Eye className="h-3.5 w-3.5" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Card>

      <Dialog
        open={compileOpen}
        onOpenChange={(open) => {
          setCompileOpen(open);
          if (!open) {
            setCompileVersion(null);
            setCompileResult(null);
          }
        }}
      >
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto" data-testid="prompt-compile-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Preview / Compile v{nText(compileVersion?.version)}</DialogTitle>
            <DialogDescription>
              Preview an ephemeral compilation (persisted: false) or compile and persist it (persisted: true).
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Target</label>
                <Select value={compileTarget} onValueChange={setCompileTarget}>
                  <SelectTrigger className="mt-1" data-testid="prompt-compile-target">
                    <SelectValue placeholder="Select target" />
                  </SelectTrigger>
                  <SelectContent>
                    {catalogTargets.length === 0 ? (
                      <SelectItem value="__none" disabled>
                        n/a
                      </SelectItem>
                    ) : (
                      catalogTargets.map((option, index) => (
                        <SelectItem key={`${catalogOptionValue(option)}-${index}`} value={catalogOptionValue(option)}>
                          {catalogOptionLabel(option)}
                        </SelectItem>
                      ))
                    )}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Mode</label>
                <Select value={compileMode} onValueChange={setCompileMode}>
                  <SelectTrigger className="mt-1" data-testid="prompt-compile-mode">
                    <SelectValue placeholder="Select mode" />
                  </SelectTrigger>
                  <SelectContent>
                    {catalogModes.length === 0 ? (
                      <SelectItem value="__none" disabled>
                        n/a
                      </SelectItem>
                    ) : (
                      catalogModes.map((option, index) => (
                        <SelectItem key={`${catalogOptionValue(option)}-${index}`} value={catalogOptionValue(option)}>
                          {catalogOptionLabel(option)}
                        </SelectItem>
                      ))
                    )}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-muted-foreground">Variables</label>
                <Button size="sm" variant="ghost" data-testid="prompt-compile-variable-add" onClick={addCompileVar}>
                  <Plus className="mr-1 h-3 w-3" /> Add variable
                </Button>
              </div>
              {compileVars.length === 0 ? (
                <p className="mt-1 text-xs text-muted-foreground">n/a — this version declares no variables.</p>
              ) : (
                <div className="mt-1 space-y-1.5">
                  {compileVars.map((row, index) => (
                    <div key={index} className="flex items-center gap-2">
                      <Input
                        className="font-mono text-xs"
                        placeholder="key"
                        value={row.key}
                        onChange={(e) => updateCompileVar(index, { key: e.target.value })}
                        data-testid="prompt-compile-variable-key"
                      />
                      <Input
                        className="font-mono text-xs"
                        placeholder="value"
                        value={row.value}
                        onChange={(e) => updateCompileVar(index, { value: e.target.value })}
                        data-testid="prompt-compile-variable-value"
                      />
                      <Button
                        size="sm"
                        variant="ghost"
                        title="Remove variable"
                        data-testid="prompt-compile-variable-remove"
                        onClick={() => removeCompileVar(index)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground">Blocks</label>
              <div className="mt-1 space-y-1.5 rounded-md border border-border p-2">
                {blocks.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No blocks available.</p>
                ) : (
                  blocks.map((block) => {
                    const blockId = block.id as string;
                    return (
                      <label key={blockId} className="flex items-center gap-2 text-xs">
                        <Checkbox
                          checked={compileBlockIds.includes(blockId)}
                          onCheckedChange={(checked) => toggleCompileBlock(blockId, checked === true)}
                          data-testid={`prompt-compile-block-${blockId}`}
                        />
                        <span>
                          {nText(block.name)} <span className="text-muted-foreground">({nText(block.category)})</span>
                        </span>
                      </label>
                    );
                  })
                )}
              </div>
            </div>

            <div className="flex flex-wrap gap-6">
              <label className="flex items-center gap-2 text-xs">
                <Switch
                  checked={compileIncludePolicies}
                  onCheckedChange={setCompileIncludePolicies}
                  data-testid="prompt-compile-include-policies"
                />
                <span>Include policies</span>
              </label>
              <label className="flex items-center gap-2 text-xs">
                <Switch
                  checked={compileIncludeInvariants}
                  onCheckedChange={setCompileIncludeInvariants}
                  data-testid="prompt-compile-include-invariants"
                />
                <span>Include invariants</span>
              </label>
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground">Context pack version ids (comma-separated)</label>
              <Input
                className="mt-1 font-mono text-xs"
                placeholder="pv-1, pv-2"
                value={compilePackIds}
                onChange={(e) => setCompilePackIds(e.target.value)}
                data-testid="prompt-compile-context-packs"
              />
            </div>

            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">Untrusted context (one item)</label>
              <Input
                className="font-mono text-xs"
                placeholder="title"
                value={compileUntrusted.title}
                onChange={(e) => setCompileUntrusted({ ...compileUntrusted, title: e.target.value })}
                data-testid="prompt-compile-untrusted-title"
              />
              <Textarea
                className="font-mono text-xs"
                rows={2}
                placeholder="content"
                value={compileUntrusted.content}
                onChange={(e) => setCompileUntrusted({ ...compileUntrusted, content: e.target.value })}
                data-testid="prompt-compile-untrusted-content"
              />
            </div>

            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">Tool outputs (one item)</label>
              <Input
                className="font-mono text-xs"
                placeholder="title"
                value={compileToolOutput.title}
                onChange={(e) => setCompileToolOutput({ ...compileToolOutput, title: e.target.value })}
                data-testid="prompt-compile-tool-output-title"
              />
              <Textarea
                className="font-mono text-xs"
                rows={2}
                placeholder="content"
                value={compileToolOutput.content}
                onChange={(e) => setCompileToolOutput({ ...compileToolOutput, content: e.target.value })}
                data-testid="prompt-compile-tool-output-content"
              />
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground">Tool definitions (JSON array)</label>
              <Textarea
                className="mt-1 font-mono text-xs"
                rows={3}
                placeholder='[{"name": "search"}]'
                value={compileToolDefs}
                onChange={(e) => setCompileToolDefs(e.target.value)}
                data-testid="prompt-compile-tool-definitions"
              />
            </div>

            <ErrorNote message={compileError} />
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setCompileOpen(false)}>
              Close
            </Button>
            <Button
              variant="outline"
              data-testid="prompt-compile-preview"
              disabled={previewCompileMut.isPending || !compileVersion}
              onClick={runPreview}
            >
              {previewCompileMut.isPending ? "Previewing…" : "Preview"}
            </Button>
            <Button
              data-testid="prompt-compile-compile"
              disabled={compileMut.isPending || !compileVersion}
              onClick={runCompile}
            >
              {compileMut.isPending ? "Compiling…" : "Compile"}
            </Button>
          </DialogFooter>

          {compileResult ? <CompileResultPanel result={compileResult} /> : null}
        </DialogContent>
      </Dialog>

      <Dialog open={specOpen} onOpenChange={setSpecOpen}>
        <DialogContent data-testid="prompt-spec-edit-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Edit prompt spec</DialogTitle>
            <DialogDescription>Update the specification metadata. Versions are immutable and managed separately.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Name</label>
              <Input data-testid="prompt-spec-edit-name" value={specForm.name} onChange={(e) => setSpecForm({ ...specForm, name: e.target.value })} className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Purpose</label>
              <Input data-testid="prompt-spec-edit-purpose" value={specForm.purpose} onChange={(e) => setSpecForm({ ...specForm, purpose: e.target.value })} className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Description</label>
              <Textarea data-testid="prompt-spec-edit-description" value={specForm.description} onChange={(e) => setSpecForm({ ...specForm, description: e.target.value })} rows={3} className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Status</label>
              <Select value={specForm.status} onValueChange={(value) => setSpecForm({ ...specForm, status: value })}>
                <SelectTrigger className="mt-1" data-testid="prompt-spec-edit-status"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {SPEC_STATUSES.map((status) => <SelectItem key={status} value={status}>{status}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setSpecOpen(false)}>Cancel</Button>
            <Button
              data-testid="prompt-spec-edit-submit"
              disabled={!specForm.name.trim() || specSave.isPending}
              onClick={() => specSave.mutate()}
            >
              {specSave.isPending ? "Saving…" : "Save changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={formOpen} onOpenChange={(open) => { setFormOpen(open); if (!open) setEditingVersion(null); }}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto" data-testid="prompt-version-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">{editingVersion ? `Edit draft v${nText(editingVersion.version)}` : "New draft version"}</DialogTitle>
            <DialogDescription>{editingVersion ? "Only draft versions are editable." : "Create a new draft version. Freeze it to make it immutable."}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <Tabs value={builderMode} onValueChange={(value) => setBuilderMode(value as BuilderMode)}>
              <TabsList data-testid="prompt-builder-modes">
                <TabsTrigger value="simple" data-testid="prompt-builder-mode-simple">Simple</TabsTrigger>
                <TabsTrigger value="structured" data-testid="prompt-builder-mode-structured">Structured</TabsTrigger>
                <TabsTrigger value="raw" data-testid="prompt-builder-mode-raw">Raw</TabsTrigger>
              </TabsList>

              <TabsContent value="simple" className="space-y-3 pt-2">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Goal</label>
                  <Textarea data-testid="prompt-builder-goal" value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })} rows={2} className="mt-1" />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Role</label>
                  <Textarea data-testid="prompt-builder-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} rows={2} className="mt-1" />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Constraints (one per line)</label>
                  <Textarea data-testid="prompt-builder-constraints" value={form.constraintsText} onChange={(e) => setForm({ ...form, constraintsText: e.target.value })} rows={3} className="mt-1" />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Expected output</label>
                  <Textarea data-testid="prompt-builder-expected-output" value={form.expectedOutput} onChange={(e) => setForm({ ...form, expectedOutput: e.target.value })} rows={3} className="mt-1" />
                </div>
              </TabsContent>

              <TabsContent value="structured" className="space-y-3 pt-2">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Objective</label>
                  <Textarea data-testid="prompt-builder-objective" value={form.objective} onChange={(e) => setForm({ ...form, objective: e.target.value })} rows={2} className="mt-1" />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Policies (informational — active project policies)</label>
                  <div className="mt-1 space-y-1 rounded-md border border-border p-2" data-testid="prompt-builder-policies">
                    {activePolicies.length === 0 ? (
                      <p className="text-xs text-muted-foreground" data-testid="prompt-builder-policies-empty">n/a — no active project policies.</p>
                    ) : (
                      activePolicies.map((policy) => (
                        <p key={policy.id || policy.family_id || nText(policy.name)} className="text-xs" data-testid="prompt-builder-policy">
                          {nText(policy.name || policy.family_id || policy.id)}
                          <span className="ml-1 text-muted-foreground">({nText(policy.domain)})</span>
                        </p>
                      ))
                    )}
                  </div>
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Blocks</label>
                  <div className="mt-1 space-y-1.5 rounded-md border border-border p-2">
                    {blocks.length === 0 ? (
                      <p className="text-xs text-muted-foreground">No blocks available.</p>
                    ) : (
                      blocks.map((block) => {
                        const blockId = block.id as string;
                        return (
                          <label key={blockId} className="flex items-center gap-2 text-xs">
                            <Checkbox
                              checked={form.blockIds.includes(blockId)}
                              onCheckedChange={(checked) => toggleBlock(blockId, checked === true)}
                              data-testid={`prompt-version-block-${blockId}`}
                            />
                            <span>{nText(block.name)} <span className="text-muted-foreground">({nText(block.category)})</span></span>
                          </label>
                        );
                      })
                    )}
                  </div>
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Variables (JSON)</label>
                  <Textarea data-testid="prompt-builder-variables" value={form.variablesText} onChange={(e) => setForm({ ...form, variablesText: e.target.value })} rows={3} className="mt-1 font-mono text-xs" placeholder='{"topic": "string"}' />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Execution phases (one per line)</label>
                  <Textarea data-testid="prompt-builder-phases" value={form.phasesText} onChange={(e) => setForm({ ...form, phasesText: e.target.value })} rows={3} className="mt-1" />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Validation (one per line)</label>
                  <Textarea data-testid="prompt-builder-validation" value={form.validationText} onChange={(e) => setForm({ ...form, validationText: e.target.value })} rows={3} className="mt-1" />
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Output contract format</label>
                    <Input data-testid="prompt-builder-output-format" value={form.outputFormat} onChange={(e) => setForm({ ...form, outputFormat: e.target.value })} className="mt-1" placeholder="markdown" />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Required sections (one per line)</label>
                    <Textarea data-testid="prompt-builder-output-sections" value={form.outputSectionsText} onChange={(e) => setForm({ ...form, outputSectionsText: e.target.value })} rows={2} className="mt-1" />
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="raw" className="space-y-3 pt-2">
                <p className="text-[11px] text-muted-foreground" data-testid="prompt-builder-raw-note">
                  Raw mode saves content and system prompt verbatim. Blocks and project policies are not injected into the content.
                </p>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Content</label>
                  <Textarea data-testid="prompt-version-content-input" value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} rows={5} className="mt-1" />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">System prompt</label>
                  <Textarea data-testid="prompt-version-system-input" value={form.system_prompt} onChange={(e) => setForm({ ...form, system_prompt: e.target.value })} rows={3} className="mt-1" />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Variables (JSON)</label>
                  <Textarea data-testid="prompt-version-variables-input" value={form.variablesText} onChange={(e) => setForm({ ...form, variablesText: e.target.value })} rows={3} className="mt-1 font-mono text-xs" placeholder='{"topic": "string"}' />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Structured spec (JSON)</label>
                  <Textarea data-testid="prompt-version-structured-input" value={form.structuredSpecText} onChange={(e) => setForm({ ...form, structuredSpecText: e.target.value })} rows={3} className="mt-1 font-mono text-xs" placeholder='{"sections": []}' />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Notes</label>
                  <Textarea data-testid="prompt-version-notes-input" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} rows={2} className="mt-1" />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Blocks</label>
                  <div className="mt-1 space-y-1.5 rounded-md border border-border p-2">
                    {blocks.length === 0 ? (
                      <p className="text-xs text-muted-foreground">No blocks available.</p>
                    ) : (
                      blocks.map((block) => {
                        const blockId = block.id as string;
                        return (
                          <label key={blockId} className="flex items-center gap-2 text-xs">
                            <Checkbox
                              checked={form.blockIds.includes(blockId)}
                              onCheckedChange={(checked) => toggleBlock(blockId, checked === true)}
                              data-testid={`prompt-version-block-${blockId}`}
                            />
                            <span>{nText(block.name)} <span className="text-muted-foreground">({nText(block.category)})</span></span>
                          </label>
                        );
                      })
                    )}
                  </div>
                </div>
              </TabsContent>
            </Tabs>

            <div className="space-y-1">
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Preview (exactly what will be saved)</p>
              <pre
                className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 font-mono text-[11px]"
                data-testid="prompt-builder-preview"
              >
                {builderContent}
              </pre>
            </div>
            <ErrorNote message={formError} />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setFormOpen(false)}>Cancel</Button>
            <Button
              data-testid="prompt-new-draft-submit"
              disabled={!builderContent.trim() || createVersionMut.isPending || updateVersionMut.isPending}
              onClick={submitForm}
            >
              {createVersionMut.isPending || updateVersionMut.isPending ? "Saving…" : editingVersion ? "Save changes" : "Create draft"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!viewVersion} onOpenChange={(open) => !open && setViewVersion(null)}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto" data-testid="prompt-version-view-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Version v{nText(viewVersion?.version)}</DialogTitle>
            <DialogDescription className="flex items-center gap-2">
              <Badge variant="outline" className={cn("text-[10px]", statusClass(viewVersion?.status))}>{nText(viewVersion?.status)}</Badge>
              <span className="font-mono text-[11px]">{shortHash(viewVersion?.content_hash)}</span>
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-xs">
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Content</p>
              <pre className="mt-1 whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 font-mono text-[11px]">{nText(viewVersion?.content)}</pre>
            </div>
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">System prompt</p>
              <pre className="mt-1 whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 font-mono text-[11px]">{nText(viewVersion?.system_prompt)}</pre>
            </div>
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Variables</p>
              <pre className="mt-1 whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 font-mono text-[11px]">{viewVersion?.variables ? JSON.stringify(viewVersion.variables, null, 2) : "n/a"}</pre>
            </div>
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Structured spec</p>
              <pre className="mt-1 whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 font-mono text-[11px]">{viewVersion?.structured_spec ? JSON.stringify(viewVersion.structured_spec, null, 2) : "n/a"}</pre>
            </div>
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Blocks</p>
              {asArray<string | Record<string, any>>(viewVersion?.blocks).length === 0 ? (
                <p className="mt-1 text-muted-foreground">n/a</p>
              ) : (
                <ul className="mt-1 list-disc space-y-0.5 pl-5">
                  {asArray<string | Record<string, any>>(viewVersion?.blocks).map((entry, index) => (
                    <li key={index}>{blockLabel(entry, blocksById)}</li>
                  ))}
                </ul>
              )}
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div><span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">created</span><span>{isoDate(viewVersion?.created_at)}</span></div>
              <div><span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">updated</span><span>{isoDate(viewVersion?.updated_at)}</span></div>
              <div><span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">frozen</span><span>{isoDate(viewVersion?.frozen_at)}</span></div>
              <div><span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">deprecated</span><span>{isoDate(viewVersion?.deprecated_at)}</span></div>
            </div>
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Notes</p>
              <p className="mt-1 text-muted-foreground">{nText(viewVersion?.notes)}</p>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
