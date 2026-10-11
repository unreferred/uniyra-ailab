import React, { useMemo, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft, Archive, Bot, Check, Eye, Loader2, Pencil, Play, Plus, Scale, Snowflake, Trash2,
} from "lucide-react";
import {
  apiError,
  cloneAgentVersion,
  compareAgentVersions,
  createAgentVersion,
  deprecateAgentVersion,
  evaluateAgentRun,
  freezeAgentVersion,
  getAgentEvaluationCatalog,
  getAgentRun,
  getAgentUsage,
  listAgentRunEvaluations,
  listAgentRuns,
  runAgentVersion,
  updateAgent,
} from "@/lib/api";
import { useAgentCatalog, useAgentDetail, useAgentVersions, useModels } from "@/lib/hooks";
import {
  Agent,
  AgentEvaluationCatalog,
  AgentEvaluationRequest,
  AgentEvaluationResult,
  AgentRunEvaluationList,
  AgentUsage,
  AgentUsageEvaluation,
  AgentUsageFinding,
  AgentRunList,
  AgentRunRequest,
  AgentRunResult,
  AgentRunSummary,
  AgentUsageRun,
  AgentVersion,
  AgentVersionCompare,
  AgentVersionDiffField,
  AgentRuntimeUnsupportedField,
  CatalogOption,
  EvaluationV5,
  KnowledgePolicyApplied,
  PermissionDenial,
  ToolPolicyApplied,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, ErrorNote, LoadingSpinner, SectionHeader } from "@/components/common/Common";
import { useProjects } from "@/context/ProjectContext";
import { SingleModelSelect } from "@/components/common/ModelPicker";
import { nCost, nLatency, nText, nTokens, shortHash } from "@/components/experiment/format";
import {
  EvaluationDetail,
  formatScore,
  outcomeClasses,
  outcomeLabel,
} from "@/components/evaluation/outcome";
import { EvaluationDetailDialog } from "@/components/evaluation/EvaluationDetailDialog";
import { cn } from "@/lib/utils";

const NONE = "__none__";

// react-markdown / remark-gfm ship as ESM-only, which breaks Jest transforms for
// any test importing this page. Load the renderer lazily so runs that never show
// an output (and tests that never open the result panel) stay independent of it.
const LazyMarkdownView = React.lazy(() =>
  import("@/components/common/Markdown").then((mod) => ({ default: mod.MarkdownView }))
);

const STATUS_CLASS: Record<string, string> = {
  draft: "border-amber-500/40 bg-amber-500/10 text-amber-600",
  frozen: "border-emerald-500/40 bg-emerald-500/10 text-emerald-600",
  deprecated: "border-muted-foreground/30 bg-muted/40 text-muted-foreground",
};

function statusClass(status?: string | null): string {
  return STATUS_CLASS[status || ""] || "border-border text-muted-foreground";
}

function asArray<T = any>(data: any): T[] {
  if (Array.isArray(data)) return data as T[];
  if (Array.isArray(data?.items)) return data.items as T[];
  if (Array.isArray(data?.results)) return data.results as T[];
  return [];
}

function toOptions(list?: CatalogOption[] | null): { value: string; label: string }[] {
  if (!Array.isArray(list)) return [];
  const out: { value: string; label: string }[] = [];
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

function listText(list?: string[] | null): string {
  if (!Array.isArray(list) || list.length === 0) return "n/a";
  return list.join(", ");
}

// An evaluator is judge-derived only when the backend says so through
// evaluator_type/evaluator.type; rule/deterministic types are labeled as such.
// An unrecognized type renders "n/a" rather than a fabricated derivation label.
function evaluatorKind(evaluation: EvaluationV5): "deterministic" | "judge-derived" | "n/a" {
  const type = String(
    evaluation?.evaluator?.type || evaluation?.evaluator_type || evaluation?.eval_type || ""
  ).toLowerCase();
  if (!type) return "n/a";
  if (type.includes("judge") || type.includes("llm")) return "judge-derived";
  if (
    type.includes("deterministic") ||
    type.includes("rule") ||
    type.includes("assert") ||
    type.includes("rubric")
  ) {
    return "deterministic";
  }
  return "n/a";
}

function evaluationScore(evaluation: EvaluationV5): number | null {
  const score = evaluation?.result?.score;
  if (score !== undefined) return score ?? null;
  if (evaluation?.parsed_score !== undefined) return evaluation.parsed_score ?? null;
  return null;
}

const PolicyBlock: React.FC<{ label: string; value: any; testId: string }> = ({ label, value, testId }) => (
  <div className="rounded-md border border-border p-2">
    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</p>
    <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px]" data-testid={testId}>
      {value === null || value === undefined ? "n/a" : JSON.stringify(value, null, 2)}
    </pre>
  </div>
);

const VersionViewer: React.FC<{ version: AgentVersion | null }> = ({ version }) => {
  if (!version) return <p className="text-sm text-muted-foreground">n/a</p>;
  return (
    <div className="space-y-3" data-testid="agent-version-viewer">
      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">version</span>
          <span className="font-mono" data-testid="agent-version-view-number">{nText(version.version)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">status</span>
          <span className="font-mono" data-testid="agent-version-view-status">{nText(version.status)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">model</span>
          <span className="font-mono" data-testid="agent-version-view-model">{nText(version.model_id)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">prompt version</span>
          <span className="font-mono" data-testid="agent-version-view-prompt">{nText(version.prompt_version_id)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">content hash</span>
          <span className="font-mono" data-testid="agent-version-view-hash">{shortHash(version.content_hash)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">source version</span>
          <span className="font-mono">{nText(version.source_version_id)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">created</span>
          <span className="font-mono">{isoDate(version.created_at)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">frozen</span>
          <span className="font-mono">{isoDate(version.frozen_at)}</span>
        </div>
      </div>

      <div className="grid gap-2 md:grid-cols-2">
        <PolicyBlock label="context_policy" value={version.context_policy} testId="agent-version-policy-context_policy" />
        <PolicyBlock label="tool_policy" value={version.tool_policy} testId="agent-version-policy-tool_policy" />
        <PolicyBlock label="knowledge_policy" value={version.knowledge_policy} testId="agent-version-policy-knowledge_policy" />
        <PolicyBlock label="runtime_policy" value={version.runtime_policy} testId="agent-version-policy-runtime_policy" />
        <PolicyBlock label="output_contract" value={version.output_contract} testId="agent-version-policy-output_contract" />
        <PolicyBlock label="evaluation_profile" value={version.evaluation_profile} testId="agent-version-policy-evaluation_profile" />
        <PolicyBlock label="routing" value={version.routing} testId="agent-version-policy-routing" />
        <PolicyBlock label="metadata" value={version.metadata} testId="agent-version-policy-metadata" />
      </div>
    </div>
  );
};

const CompareFieldRow: React.FC<{ field: AgentVersionDiffField }> = ({ field }) => (
  <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="agent-compare-field">
    <span className="font-mono">{nText(field.field)}</span>
    <span className={field.changed ? "text-amber-600" : "text-muted-foreground"}>
      {field.changed ? "changed" : "unchanged"}
    </span>
    {field.changed && (
      <span className="text-muted-foreground">
        {nText(JSON.stringify(field.from))} → {nText(JSON.stringify(field.to))}
      </span>
    )}
  </div>
);

function dedupeUnsupported(list: AgentRuntimeUnsupportedField[]): AgentRuntimeUnsupportedField[] {
  const seen = new Set<string>();
  const out: AgentRuntimeUnsupportedField[] = [];
  for (const item of list) {
    const key = item?.field ? String(item.field) : "";
    if (key && seen.has(key)) continue;
    if (key) seen.add(key);
    out.push(item);
  }
  return out;
}

const RunPermissionsPanel: React.FC<{ result: AgentRunResult; unsupported: AgentRuntimeUnsupportedField[] }> = ({
  result,
  unsupported,
}) => {
  const run = result.run || null;
  const denials = asArray<PermissionDenial>(
    (result as any).permission_denials ?? (run as any)?.permission_denials
  );
  const toolPolicy = ((result as any).tool_policy_applied ?? (run as any)?.tool_policy_applied ?? null) as ToolPolicyApplied | null;
  const knowledgePolicy = ((result as any).knowledge_policy_applied ?? (run as any)?.knowledge_policy_applied ?? null) as KnowledgePolicyApplied | null;

  const supportsTools = toolPolicy?.model_capability?.supports_tools;
  const unsupportedFields = new Set(
    unsupported.map((item) => (item?.field ? String(item.field) : "")).filter(Boolean)
  );

  const boolText = (value: boolean | null | undefined): string =>
    value === true ? "Yes" : value === false ? "No" : "n/a";
  const countText = (list?: string[] | null): string => (Array.isArray(list) ? String(list.length) : "n/a");
  const idsText = (list?: string[] | null): string =>
    Array.isArray(list) && list.length > 0 ? list.join(", ") : "n/a";
  const denialResource = (denial: PermissionDenial): string =>
    denial.name ? String(denial.name) : denial.resource_id ? String(denial.resource_id) : "n/a";

  return (
    <div className="space-y-3 rounded-md border border-border p-3" data-testid="agent-run-permissions">
      <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Permissions</p>

      <div className="space-y-1 rounded-md border border-border p-2" data-testid="agent-run-tool-policy">
        <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Tool policy applied</p>
        <p className="text-xs">default deny: {boolText(toolPolicy?.default_deny)}</p>
        <p className="text-xs">enabled tools: {countText(toolPolicy?.enabled_tool_ids)}</p>
        <p className="text-xs">
          allowed / denied: {countText(toolPolicy?.allowed_tool_ids)} / {countText(toolPolicy?.denied_tool_ids)}
        </p>
        <p className="text-xs" data-testid="agent-run-tool-ids">enabled tool ids: {idsText(toolPolicy?.enabled_tool_ids)}</p>
        <p className="text-xs" data-testid="agent-run-model-tools-note">
          {supportsTools === false
            ? "Model does not support tools — tool use denied"
            : supportsTools === true
              ? "Model supports tools"
              : "n/a"}
        </p>
      </div>

      <div className="space-y-1 rounded-md border border-border p-2" data-testid="agent-run-knowledge-policy">
        <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Knowledge policy applied</p>
        <p className="text-xs">knowledge enabled: {boolText(knowledgePolicy?.knowledge_enabled)}</p>
        <p className="text-xs">allowed collections: {countText(knowledgePolicy?.allowed_collection_ids)}</p>
        <p className="text-xs">
          retrieval top k: {knowledgePolicy?.retrieval_top_k == null ? "n/a" : knowledgePolicy.retrieval_top_k}
        </p>
        {unsupportedFields.has("knowledge_only_mode") && (
          <p className="text-xs" data-testid="agent-run-knowledge-only-unsupported">
            knowledge_only_mode: not runtime-enforced
          </p>
        )}
        {unsupportedFields.has("citation_required") && (
          <p className="text-xs" data-testid="agent-run-citation-unsupported">
            citation_required: not runtime-enforced
          </p>
        )}
      </div>

      <div className="space-y-1" data-testid="agent-run-denials">
        <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Permission denials</p>
        {denials.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="agent-run-denials-empty">No permission denials</p>
        ) : (
          <div className="overflow-x-auto rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/50">
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Kind</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Resource</TableHead>
                  <TableHead className="text-[10px] font-mono uppercase tracking-wider">Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {denials.map((denial, index) => (
                  <TableRow key={`${denial.kind || "denial"}-${index}`} data-testid="agent-run-denial-row">
                    <TableCell className="font-mono text-[11px]">{nText(denial.kind)}</TableCell>
                    <TableCell className="text-xs">{denialResource(denial)}</TableCell>
                    <TableCell className="text-xs">{nText(denial.reason)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  );
};

const RunEvaluations: React.FC<{
  runId: string | null;
  catalog?: AgentEvaluationCatalog | null;
}> = ({ runId, catalog }) => {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const evaluationsQuery = useQuery<AgentRunEvaluationList>({
    queryKey: ["agent-run-evaluations", runId],
    queryFn: async () => {
      if (!runId || typeof listAgentRunEvaluations !== "function") return { items: [] };
      return listAgentRunEvaluations(runId);
    },
    enabled: !!runId,
  });
  const rows = asArray<EvaluationV5>(evaluationsQuery.data?.items);

  const agentDimensions = toOptions(catalog?.agent_dimensions);
  const promptDimensions = toOptions(catalog?.prompt_dimensions);

  // Only known scores are shown. A dimension the catalog reports but that no
  // evaluation scored renders NOT_EVALUATED, never a fabricated number.
  const dimensionValue = (key: string): string => {
    for (const evaluation of rows) {
      const scores = (evaluation.scores || {}) as Record<string, number | null | undefined>;
      if (Object.prototype.hasOwnProperty.call(scores, key)) {
        const value = scores[key];
        if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
        return formatScore(value);
      }
    }
    return "NOT_EVALUATED";
  };

  const dimensionBlock = (label: string, dimensions: { value: string; label: string }[]) =>
    dimensions.length === 0 ? null : (
      <div className="space-y-1">
        <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
          {label} (informational)
        </p>
        {dimensions.map((dimension) => (
          <div key={dimension.value} className="flex flex-wrap gap-2 text-xs" data-testid="agent-eval-dimension">
            <span className="font-mono">{dimension.label}</span>
            <span className="text-muted-foreground">{dimensionValue(dimension.value)}</span>
          </div>
        ))}
      </div>
    );

  return (
    <div className="space-y-3 rounded-md border border-border p-3" data-testid="agent-run-evaluations">
      <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Evaluations</p>

      {evaluationsQuery.isLoading ? (
        <LoadingSpinner label="Loading evaluations…" />
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="agent-run-evaluations-empty">
          No evaluations for this run yet.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <Table data-testid="agent-run-evaluations-table">
            <TableHeader>
              <TableRow className="bg-muted/50">
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Evaluation</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Evaluator</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Derivation</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Outcome</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Score</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Details</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((evaluation, index) => {
                const outcome = outcomeLabel(evaluation as unknown as EvaluationDetail);
                const kind = evaluatorKind(evaluation);
                const evaluatorType =
                  evaluation.evaluator?.type || evaluation.evaluator_type || evaluation.eval_type || null;
                return (
                  <TableRow key={evaluation.id || index} data-testid="agent-eval-row">
                    <TableCell className="font-mono text-[11px]">{nText(evaluation.id)}</TableCell>
                    <TableCell className="text-xs" data-testid="agent-eval-evaluator">
                      {nText(evaluatorType)}
                    </TableCell>
                    <TableCell className="text-xs" data-testid="agent-eval-kind">{kind}</TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={cn("text-[10px]", outcomeClasses(outcome))}
                        data-testid="agent-eval-outcome"
                      >
                        {outcome}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs" data-testid="agent-eval-score">
                      {formatScore(evaluationScore(evaluation))}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        data-testid="agent-eval-details"
                        disabled={!evaluation.id}
                        onClick={() => {
                          setSelectedId((evaluation.id as string) || null);
                          setDetailOpen(true);
                        }}
                      >
                        Details
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {(agentDimensions.length > 0 || promptDimensions.length > 0) && (
        <div className="space-y-2" data-testid="agent-run-eval-dimensions">
          {dimensionBlock("Agent dimensions", agentDimensions)}
          {dimensionBlock("Prompt dimensions", promptDimensions)}
        </div>
      )}

      <EvaluationDetailDialog evaluationId={selectedId} open={detailOpen} onOpenChange={setDetailOpen} />
    </div>
  );
};

const RunResultPanel: React.FC<{
  result: AgentRunResult;
  onEvaluateRun?: (runId: string) => void;
  evaluationCatalog?: AgentEvaluationCatalog | null;
}> = ({ result, onEvaluateRun, evaluationCatalog }) => {
  const run = result.run || null;
  const runId = (result.run_id as string) || (run?.id as string) || null;
  const status = (run?.status as string) || null;
  const outputText = (run?.text as string) || (run?.normalized_response as string) || null;
  const latency = (run?.latency_ms ?? null) as number | null;
  const inputTokens = (run?.input_tokens ?? null) as number | null;
  const outputTokens = (run?.output_tokens ?? null) as number | null;
  const estimatedCost = (run?.estimated_cost ?? null) as number | null;
  const costAvailable = run?.cost_available;
  const toolCalls = (run?.tool_call_count ?? null) as number | null;
  const policy = result.runtime_policy_applied || null;
  const applied = policy?.applied ?? null;

  const unsupported = useMemo<AgentRuntimeUnsupportedField[]>(() => {
    const raw = Array.isArray(policy?.unsupported) ? policy!.unsupported! : [];
    const extra = asArray<AgentRuntimeUnsupportedField>((result as any).policy_unsupported);
    return dedupeUnsupported([...raw, ...extra]);
  }, [policy, result]);

  const appliedEntries = useMemo<[string, any][]>(() => {
    if (applied === null || applied === undefined) return [];
    if (Array.isArray(applied)) return applied.map((value, index) => [String(index), value]);
    if (typeof applied === "object") return Object.entries(applied as Record<string, any>);
    return [["value", applied]];
  }, [applied]);

  const compiledPromptId = (result.compiled_prompt_id as string) || (result.compiled_prompt?.id as string) || null;
  const promptSpecId = (result.compiled_prompt?.prompt_spec_id as string) || (result.prompt_id as string) || null;

  return (
    <div className="space-y-3 rounded-md border border-border p-3" data-testid="agent-run-result">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs" data-testid="agent-run-metrics">
        <Badge variant="outline" className={cn("text-[10px]", statusClass(status))} data-testid="agent-run-status">
          {nText(status)}
        </Badge>
        <span data-testid="agent-run-latency">latency: {nLatency(latency)}</span>
        <span data-testid="agent-run-input-tokens">input tokens: {nTokens(inputTokens)}</span>
        <span data-testid="agent-run-output-tokens">output tokens: {nTokens(outputTokens)}</span>
        <span data-testid="agent-run-cost">
          cost: {nCost(estimatedCost, costAvailable == null ? undefined : costAvailable)}
        </span>
        <span data-testid="agent-run-tool-calls">tool calls: {toolCalls == null ? "n/a" : toolCalls}</span>
      </div>

      {runId && (
        <div className="flex flex-wrap items-center gap-2" data-testid="agent-run-actions">
          <Button
            size="sm"
            variant="outline"
            data-testid="agent-run-evaluate"
            onClick={() => onEvaluateRun?.(runId)}
          >
            <Scale className="mr-1.5 h-3.5 w-3.5" /> Evaluate
          </Button>
        </div>
      )}

      <div>
        <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Output</p>
        <div data-testid="agent-run-output">
          {outputText ? (
            <React.Suspense
              fallback={<pre className="whitespace-pre-wrap break-words font-mono text-[11px]">{outputText}</pre>}
            >
              <LazyMarkdownView content={outputText} />
            </React.Suspense>
          ) : (
            <p className="text-xs text-muted-foreground">n/a</p>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span>
          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">compiled prompt: </span>
          {promptSpecId ? (
            <Link
              to={`/prompt-lab/prompts/${promptSpecId}`}
              className="font-mono text-primary hover:underline"
              data-testid="agent-run-compiled-prompt"
            >
              {nText(compiledPromptId || promptSpecId)}
            </Link>
          ) : (
            <span className="font-mono" data-testid="agent-run-compiled-prompt">{nText(compiledPromptId)}</span>
          )}
        </span>
        {runId ? (
          <Link
            to={`/trace?run=${runId}`}
            className="text-primary hover:underline"
            data-testid="agent-run-trace-link"
          >
            Trace {runId}
          </Link>
        ) : (
          <span className="text-muted-foreground" data-testid="agent-run-trace-link">n/a</span>
        )}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-1" data-testid="agent-run-applied">
          <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
            Applied runtime policy
          </p>
          {appliedEntries.length === 0 ? (
            <p className="text-xs text-muted-foreground">n/a</p>
          ) : (
            appliedEntries.map(([field, value]) => (
              <div key={field} className="flex flex-wrap gap-2 text-xs" data-testid="agent-run-applied-field">
                <span className="font-mono">{nText(field)}</span>
                <span className="text-muted-foreground">
                  {nText(typeof value === "string" ? value : JSON.stringify(value))}
                </span>
              </div>
            ))
          )}
        </div>

        <div className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/5 p-2" data-testid="agent-run-unsupported">
          <p className="text-[10px] font-mono uppercase tracking-wider text-amber-600">Not applied by runtime</p>
          {unsupported.length === 0 ? (
            <p className="text-xs text-muted-foreground">n/a</p>
          ) : (
            <div data-testid="agent-run-policy-unsupported">
              {unsupported.map((item, index) => (
                <div key={item.field ? String(item.field) : index} className="text-xs" data-testid="agent-run-unsupported-field">
                  <span className="font-mono text-amber-600">{nText(item.field)}</span>
                  <span className="text-muted-foreground"> — {nText(item.reason)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <RunPermissionsPanel result={result} unsupported={unsupported} />

      <RunEvaluations runId={runId} catalog={evaluationCatalog} />
    </div>
  );
};

export default function AgentDetail() {
  const { id } = useParams<{ id: string }>();
  const agentId = id as string;
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: agent, isLoading, isError, error } = useAgentDetail(agentId);
  const { data: versionsData, isLoading: versionsLoading } = useAgentVersions(agentId);
  const { data: catalog } = useAgentCatalog();
  const { data: models = [] } = useModels();
  const evaluationCatalogQuery = useQuery<AgentEvaluationCatalog>({
    queryKey: ["agent-evaluation-catalog"],
    queryFn: async () => {
      if (typeof getAgentEvaluationCatalog !== "function") return {} as AgentEvaluationCatalog;
      return getAgentEvaluationCatalog();
    },
    staleTime: 60_000,
  });
  const evaluationCatalog = evaluationCatalogQuery.data || null;
  const projectCtx = useProjects();
  const activeProjectId = projectCtx?.activeId || "";

  const versions = asArray<AgentVersion>(versionsData);
  const roleOptions = useMemo(() => toOptions(catalog?.roles), [catalog]);
  const statusOptions = useMemo(() => toOptions(catalog?.statuses), [catalog]);

  const [selectedVersionId, setSelectedVersionId] = useState("");
  const selectedVersion = useMemo(() => {
    const chosen = versions.find((version) => version.id === selectedVersionId);
    if (chosen) return chosen;
    return versions.find((version) => version.status === "frozen") || versions[0] || null;
  }, [versions, selectedVersionId]);
  const effectiveVersionId = (selectedVersion?.id as string) || "";

  const modelName = (modelId?: string | null): string => {
    if (!modelId) return "n/a";
    const model = models.find((m) => m.id === modelId);
    return model?.display_name || modelId;
  };

  const [viewVersion, setViewVersion] = useState<AgentVersion | null>(null);

  const [runVersion, setRunVersion] = useState<AgentVersion | null>(null);
  const [runTask, setRunTask] = useState("");
  const [runVars, setRunVars] = useState<{ key: string; value: string }[]>([{ key: "", value: "" }]);
  const [runReserved, setRunReserved] = useState("");
  const [runResult, setRunResult] = useState<AgentRunResult | null>(null);
  const [viewRunOpen, setViewRunOpen] = useState(false);
  const [viewRunResult, setViewRunResult] = useState<AgentRunResult | null>(null);

  const [evaluateRunId, setEvaluateRunId] = useState<string | null>(null);
  const [evalProfileType, setEvalProfileType] = useState<"agent" | "prompt">("agent");
  const [evalKeys, setEvalKeys] = useState<string[]>([]);
  const [evalRubricId, setEvalRubricId] = useState("");
  const [evalJudgeModelId, setEvalJudgeModelId] = useState("");
  const [evalThreshold, setEvalThreshold] = useState("");
  const [evalRecordFailures, setEvalRecordFailures] = useState(false);
  const [evalNotes, setEvalNotes] = useState("");

  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState({
    name: "", role: "", status: "active", purpose: "", description: "",
    responsibilities: "", nonResponsibilities: "",
  });

  const [newVersionSource, setNewVersionSource] = useState<AgentVersion | null>(null);
  const [newVersionModelId, setNewVersionModelId] = useState("");

  const [fromId, setFromId] = useState("");
  const [toId, setToId] = useState("");
  const [diff, setDiff] = useState<AgentVersionCompare | null>(null);

  const usageQuery = useQuery<AgentUsage>({
    queryKey: ["agent-usage", agentId, effectiveVersionId],
    queryFn: async () => getAgentUsage(agentId, effectiveVersionId),
    enabled: !!effectiveVersionId,
  });
  const usage = usageQuery.data || null;
  const runs = asArray<AgentUsageRun>(usage?.runs);
  const evaluations = asArray<AgentUsageEvaluation>(usage?.evaluations);
  const findings = asArray<AgentUsageFinding>(usage?.findings);

  const projectId = (agent?.project_id as string) || activeProjectId || "";

  const runtimeRunsQuery = useQuery<AgentRunList>({
    queryKey: ["agent-runs", agentId, effectiveVersionId, projectId],
    queryFn: async () => {
      if (typeof listAgentRuns !== "function") return { items: [] } as AgentRunList;
      return listAgentRuns({
        agent_id: agentId,
        agent_version_id: effectiveVersionId,
        ...(projectId ? { project_id: projectId } : {}),
      });
    },
    enabled: !!effectiveVersionId,
  });
  const runtimeRuns = asArray<AgentRunSummary>(runtimeRunsQuery.data?.items);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["agent-detail", agentId] });
    qc.invalidateQueries({ queryKey: ["agent-versions", agentId] });
    qc.invalidateQueries({ queryKey: ["agent-usage", agentId] });
    qc.invalidateQueries({ queryKey: ["agents"] });
  };

  const openEdit = () => {
    setEditForm({
      name: agent?.name || "",
      role: agent?.role || "",
      status: agent?.status || "active",
      purpose: agent?.purpose || "",
      description: agent?.description || "",
      responsibilities: (agent?.responsibilities || []).join(", "),
      nonResponsibilities: (agent?.non_responsibilities || []).join(", "),
    });
    setEditOpen(true);
  };

  const updateMut = useMutation({
    mutationFn: async () =>
      updateAgent(agentId, {
        name: editForm.name.trim(),
        role: editForm.role || undefined,
        status: editForm.status || undefined,
        purpose: editForm.purpose,
        description: editForm.description,
        responsibilities: parseList(editForm.responsibilities),
        non_responsibilities: parseList(editForm.nonResponsibilities),
      }),
    onSuccess: () => { toast.success("Agent updated"); invalidate(); setEditOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const openNewVersion = (version: AgentVersion | null) => {
    setNewVersionSource(version);
    setNewVersionModelId((version?.model_id as string) || (models[0]?.id as string) || "");
  };

  const newVersionMut = useMutation({
    mutationFn: async () => createAgentVersion(agentId, { model_id: newVersionModelId }),
    onSuccess: () => { toast.success("Draft version created"); invalidate(); setNewVersionSource(null); },
    onError: (e) => toast.error(apiError(e)),
  });

  const freezeMut = useMutation({
    mutationFn: async (versionId: string) => freezeAgentVersion(agentId, versionId),
    onSuccess: () => { toast.success("Version frozen"); invalidate(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const deprecateMut = useMutation({
    mutationFn: async (versionId: string) => deprecateAgentVersion(agentId, versionId),
    onSuccess: () => { toast.success("Version deprecated"); invalidate(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const cloneMut = useMutation({
    mutationFn: async (versionId: string) => cloneAgentVersion(agentId, versionId),
    onSuccess: () => { toast.success("Version cloned to a new draft"); invalidate(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const updateRunVar = (index: number, patch: Partial<{ key: string; value: string }>) => {
    setRunVars((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };
  const addRunVar = () => setRunVars((current) => [...current, { key: "", value: "" }]);
  const removeRunVar = (index: number) =>
    setRunVars((current) => current.filter((_, i) => i !== index));

  const buildRunRequest = (): AgentRunRequest => {
    const variables: Record<string, any> = {};
    for (const row of runVars) {
      const key = row.key.trim();
      if (key) variables[key] = row.value;
    }
    const reserved = runReserved.trim();
    return {
      project_id: projectId,
      agent_id: agentId,
      agent_version_id: (runVersion?.id as string) || "",
      task: runTask.trim(),
      ...(Object.keys(variables).length > 0 ? { variables } : {}),
      ...(reserved && !Number.isNaN(Number(reserved)) ? { reserved_output_tokens: Number(reserved) } : {}),
    };
  };

  const runMut = useMutation({
    mutationFn: async () => runAgentVersion(buildRunRequest()),
    onSuccess: (result) => {
      setRunResult(result as AgentRunResult);
      toast.success("Agent run completed");
      qc.invalidateQueries({ queryKey: ["agent-runs", agentId] });
      qc.invalidateQueries({ queryKey: ["agent-usage", agentId] });
      qc.invalidateQueries({ queryKey: ["agent-usage"] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const viewRunMut = useMutation({
    mutationFn: async (runId: string) => {
      if (typeof getAgentRun !== "function") throw new Error("getAgentRun is not available");
      return getAgentRun(runId);
    },
    onSuccess: (result) => {
      setViewRunResult(result as AgentRunResult);
      setViewRunOpen(true);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const openRun = (version: AgentVersion) => {
    setRunVersion(version);
    setRunTask("");
    setRunVars([{ key: "", value: "" }]);
    setRunReserved("");
    setRunResult(null);
    runMut.reset();
  };

  const evalRuleOptions = useMemo(
    () =>
      toOptions(
        evalProfileType === "prompt"
          ? evaluationCatalog?.prompt_rule_keys
          : evaluationCatalog?.agent_rule_keys
      ),
    [evaluationCatalog, evalProfileType]
  );

  const evaluateMut = useMutation({
    mutationFn: async () => {
      if (!evaluateRunId) throw new Error("No agent run selected");
      if (typeof evaluateAgentRun !== "function") throw new Error("evaluateAgentRun is not available");
      const body: AgentEvaluationRequest = {
        profile_type: evalProfileType,
        evaluator_keys: evalKeys,
        use_agent_profile: evalKeys.length === 0,
        record_failures: evalRecordFailures,
      };
      if (evalRubricId.trim()) body.rubric_id = evalRubricId.trim();
      if (evalJudgeModelId) body.judge_model_id = evalJudgeModelId;
      if (evalThreshold.trim() !== "" && !Number.isNaN(Number(evalThreshold))) {
        body.threshold = Number(evalThreshold);
      }
      if (evalNotes.trim()) body.notes = evalNotes.trim();
      return evaluateAgentRun(evaluateRunId, body);
    },
    onSuccess: (result) => {
      const evaluated = result as AgentEvaluationResult;
      const evaluatedRunId = evaluated?.run_id || evaluateRunId;
      toast.success("Agent run evaluated");
      qc.invalidateQueries({ queryKey: ["agent-run-evaluations", evaluatedRunId] });
      qc.invalidateQueries({ queryKey: ["evaluations"] });
      qc.invalidateQueries({ queryKey: ["agent-usage", agentId] });
      setEvaluateRunId(null);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const openEvaluate = (runId: string) => {
    setEvaluateRunId(runId);
    setEvalProfileType("agent");
    setEvalKeys([]);
    setEvalRubricId("");
    setEvalJudgeModelId("");
    setEvalThreshold("");
    setEvalRecordFailures(false);
    setEvalNotes("");
    evaluateMut.reset();
  };

  const compareMut = useMutation({
    mutationFn: async () => compareAgentVersions(agentId, fromId, toId),
    onSuccess: (result) => setDiff(result as AgentVersionCompare),
    onError: (e) => toast.error(apiError(e)),
  });

  React.useEffect(() => {
    if (versions.length < 2) return;
    setFromId((current) => (current && versions.some((v) => v.id === current) ? current : ((versions[1]?.id as string) || "")));
    setToId((current) => (current && versions.some((v) => v.id === current) ? current : ((versions[0]?.id as string) || "")));
  }, [versionsData]); // eslint-disable-line

  if (isLoading) {
    return (
      <div className="mx-auto max-w-6xl p-4 lg:p-6" data-testid="agent-detail-page">
        <LoadingSpinner label="Loading agent…" />
      </div>
    );
  }

  if (isError || !agent) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 p-4 lg:p-6" data-testid="agent-detail-page">
        <Button variant="ghost" size="sm" onClick={() => navigate("/agents")} data-testid="back-to-agent-lab">
          <ArrowLeft className="mr-1.5 h-4 w-4" /> Agent Lab
        </Button>
        <ErrorNote message={apiError(error) || "Agent not found"} />
      </div>
    );
  }

  const policyDiff = diff?.policy_diff || null;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="agent-detail-page">
      <button
        onClick={() => navigate("/agents")}
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        data-testid="back-to-agent-lab"
      >
        <ArrowLeft className="h-4 w-4" /> Agent Lab
      </button>

      <SectionHeader
        title={nText(agent.name)}
        caption={nText(agent.purpose)}
        action={
          <div className="flex shrink-0 items-center gap-2">
            <Badge variant="outline" className="text-[10px]" data-testid="agent-status-badge">{nText(agent.status)}</Badge>
            <Badge variant="outline" className="text-[10px]" data-testid="agent-role-badge">{nText(agent.role)}</Badge>
            <Button size="sm" variant="outline" data-testid="agent-edit" onClick={openEdit}>
              <Pencil className="mr-1.5 h-4 w-4" /> Edit
            </Button>
          </div>
        }
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="agent-meta">
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">latest version</span>
          <span className="font-mono text-sm" data-testid="agent-latest-version">
            {agent.latest_version == null ? "n/a" : `v${agent.latest_version}`}
          </span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">created</span>
          <span className="font-mono text-sm">{isoDate(agent.created_at)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">updated</span>
          <span className="font-mono text-sm">{isoDate(agent.updated_at)}</span>
        </div>
        <div>
          <span className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground">description</span>
          <span className="text-sm text-muted-foreground" data-testid="agent-description">{nText(agent.description)}</span>
        </div>
      </div>

      <Card className="space-y-3 p-4" data-testid="agent-usage-panel">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Selected version</h3>
          {versions.length > 0 && (
            <Select value={effectiveVersionId || NONE} onValueChange={(value) => setSelectedVersionId(value === NONE ? "" : value)}>
              <SelectTrigger className="h-8 w-56" data-testid="agent-version-select"><SelectValue placeholder="Select version" /></SelectTrigger>
              <SelectContent>
                {versions.map((version) => (
                  <SelectItem key={version.id} value={version.id as string}>
                    v{nText(version.version)} · {nText(version.status)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
        {selectedVersion ? (
          <p className="font-mono text-[11px] text-muted-foreground" data-testid="agent-selected-version">
            v{nText(selectedVersion.version)} · {nText(selectedVersion.status)} · {modelName(selectedVersion.model_id)} · {shortHash(selectedVersion.content_hash)}
          </p>
        ) : (
          <p className="text-xs text-muted-foreground" data-testid="agent-no-version">n/a — this agent has no versions yet.</p>
        )}
      </Card>

      <Tabs defaultValue="versions">
        <TabsList data-testid="agent-tabs">
          <TabsTrigger value="overview" data-testid="agent-tab-overview">Overview</TabsTrigger>
          <TabsTrigger value="versions" data-testid="agent-tab-versions">Versions</TabsTrigger>
          <TabsTrigger value="runs" data-testid="agent-tab-runs"><span data-testid="agent-run-runs-tab">Runs</span></TabsTrigger>
          <TabsTrigger value="evaluations" data-testid="agent-tab-evaluations">Evaluations</TabsTrigger>
          <TabsTrigger value="findings" data-testid="agent-tab-findings">Findings</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Card className="space-y-2 p-4" data-testid="agent-overview-identity">
              <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Identity</h3>
              <p className="text-xs"><span className="text-muted-foreground">role:</span> {nText(agent.role)}</p>
              <p className="text-xs"><span className="text-muted-foreground">status:</span> {nText(agent.status)}</p>
              <p className="text-xs"><span className="text-muted-foreground">purpose:</span> {nText(agent.purpose)}</p>
              <p className="text-xs"><span className="text-muted-foreground">description:</span> {nText(agent.description)}</p>
              <p className="text-xs"><span className="text-muted-foreground">responsibilities:</span> {listText(agent.responsibilities)}</p>
              <p className="text-xs"><span className="text-muted-foreground">non-responsibilities:</span> {listText(agent.non_responsibilities)}</p>
            </Card>
            <Card className="space-y-2 p-4" data-testid="agent-overview-version">
              <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Selected version configuration</h3>
              {selectedVersion ? (
                <VersionViewer version={selectedVersion} />
              ) : (
                <p className="text-xs text-muted-foreground">n/a</p>
              )}
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="versions" className="mt-4 space-y-4">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Version history</h3>
            <Button size="sm" data-testid="agent-new-version" onClick={() => openNewVersion(selectedVersion)}>
              <Plus className="mr-1.5 h-4 w-4" /> New version
            </Button>
          </div>

          {versionsLoading ? (
            <LoadingSpinner label="Loading versions…" />
          ) : versions.length === 0 ? (
            <EmptyState
              icon={<Bot className="h-6 w-6" />}
              title="No versions"
              description="Create a version to pin a model and its configuration."
              testId="agent-versions-empty"
            />
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <Table data-testid="agent-versions-table">
                <TableHeader>
                  <TableRow className="bg-muted/50">
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Version</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Status</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Model</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Prompt</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Hash</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Created</TableHead>
                    <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {versions.map((version) => {
                    const vid = version.id as string;
                    const isDraft = version.status === "draft";
                    const canDeprecate = version.status === "draft" || version.status === "frozen";
                    return (
                      <TableRow key={version.id} data-testid="agent-version-row">
                        <TableCell className="font-mono text-xs" data-testid="agent-version-number">v{nText(version.version)}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className={cn("text-[10px]", statusClass(version.status))} data-testid="agent-version-status">
                            {nText(version.status)}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs" data-testid="agent-version-model">{modelName(version.model_id)}</TableCell>
                        <TableCell className="font-mono text-[11px] text-muted-foreground" data-testid="agent-version-prompt">
                          {nText(version.prompt_version_id)}
                        </TableCell>
                        <TableCell className="font-mono text-[11px] text-muted-foreground" title={version.content_hash || undefined} data-testid="agent-version-hash">
                          {shortHash(version.content_hash)}
                        </TableCell>
                        <TableCell className="font-mono text-[11px] text-muted-foreground">{isoDate(version.created_at)}</TableCell>
                        <TableCell>
                          <div className="flex flex-wrap justify-end gap-1">
                            <Button size="sm" variant="ghost" title="View" data-testid="agent-version-view" onClick={() => setViewVersion(version)}>
                              <Eye className="h-3.5 w-3.5" />
                            </Button>
                            {(version.status === "frozen" || version.status === "deprecated") && (
                              <Button
                                size="sm"
                                variant="ghost"
                                title="Run agent version"
                                data-testid="agent-run-open"
                                disabled={!vid}
                                onClick={() => openRun(version)}
                              >
                                <Play className="h-3.5 w-3.5" />
                              </Button>
                            )}
                            <Button size="sm" variant="ghost" title="New version" data-testid="agent-version-new" onClick={() => openNewVersion(version)}>
                              <Plus className="h-3.5 w-3.5" />
                            </Button>
                            <Button size="sm" variant="ghost" title="Clone" data-testid="agent-version-clone" disabled={cloneMut.isPending || !vid} onClick={() => cloneMut.mutate(vid)}>
                              <Check className="h-3.5 w-3.5" />
                            </Button>
                            {isDraft && (
                              <Button size="sm" variant="ghost" title="Freeze" data-testid="agent-version-freeze" disabled={freezeMut.isPending || !vid} onClick={() => freezeMut.mutate(vid)}>
                                <Snowflake className="h-3.5 w-3.5" />
                              </Button>
                            )}
                            {canDeprecate && (
                              <Button size="sm" variant="ghost" title="Deprecate" data-testid="agent-version-deprecate" disabled={deprecateMut.isPending || !vid} onClick={() => deprecateMut.mutate(vid)}>
                                <Archive className="h-3.5 w-3.5" />
                              </Button>
                            )}
                            <Button
                              size="sm"
                              variant="ghost"
                              title="Compare from this version"
                              data-testid="agent-version-compare"
                              disabled={!vid}
                              onClick={() => { setFromId(vid); setDiff(null); }}
                            >
                              Compare
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}

          <Card className="space-y-3 p-4" data-testid="agent-compare-panel">
            <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Compare versions</h3>
            {versions.length < 2 ? (
              <p className="text-xs text-muted-foreground" data-testid="agent-compare-disabled">n/a — at least two versions are required to compare.</p>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <Select value={fromId || NONE} onValueChange={(value) => { setFromId(value === NONE ? "" : value); setDiff(null); }}>
                  <SelectTrigger className="w-52" data-testid="agent-compare-from"><SelectValue placeholder="From version" /></SelectTrigger>
                  <SelectContent>
                    {versions.map((version) => (
                      <SelectItem key={version.id} value={version.id as string}>v{nText(version.version)} · {nText(version.status)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select value={toId || NONE} onValueChange={(value) => { setToId(value === NONE ? "" : value); setDiff(null); }}>
                  <SelectTrigger className="w-52" data-testid="agent-compare-to"><SelectValue placeholder="To version" /></SelectTrigger>
                  <SelectContent>
                    {versions.map((version) => (
                      <SelectItem key={version.id} value={version.id as string}>v{nText(version.version)} · {nText(version.status)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" data-testid="agent-compare" disabled={!fromId || !toId || compareMut.isPending} onClick={() => compareMut.mutate()}>
                  Compare
                </Button>
              </div>
            )}

            {diff && (
              <div className="space-y-3" data-testid="agent-compare-result">
                <div className="flex flex-wrap gap-4 text-xs" data-testid="agent-compare-summary">
                  <span>from: <span className="font-mono">v{nText((diff.from as any)?.version)}</span></span>
                  <span>to: <span className="font-mono">v{nText((diff.to as any)?.version)}</span></span>
                  <span>fields changed: <span className="font-mono" data-testid="agent-compare-fields-changed">{nText(diff.summary?.fields_changed)}</span></span>
                </div>

                <div className="space-y-1" data-testid="agent-compare-fields">
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Binding fields</p>
                  {asArray<AgentVersionDiffField>(diff.fields).map((field, index) => (
                    <CompareFieldRow key={field.field || index} field={field} />
                  ))}
                </div>

                <div className="grid gap-3 md:grid-cols-2" data-testid="agent-policy-diff">
                  <div className="space-y-1 rounded-md border border-border p-2" data-testid="agent-policy-diff-context">
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Context</p>
                    <p className="text-xs">added: {nText(policyDiff?.context?.added?.length)}</p>
                    <p className="text-xs">removed: {nText(policyDiff?.context?.removed?.length)}</p>
                    <p className="text-xs">changed: {nText(policyDiff?.context?.changed?.length)}</p>
                  </div>
                  <div className="space-y-1 rounded-md border border-border p-2" data-testid="agent-policy-diff-tools">
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Tools</p>
                    <p className="text-xs">allowed added: {nText(policyDiff?.tools?.allowed_added?.length)}</p>
                    <p className="text-xs">allowed removed: {nText(policyDiff?.tools?.allowed_removed?.length)}</p>
                    <p className="text-xs">denied added: {nText(policyDiff?.tools?.denied_added?.length)}</p>
                    <p className="text-xs">denied removed: {nText(policyDiff?.tools?.denied_removed?.length)}</p>
                  </div>
                  <div className="space-y-1 rounded-md border border-border p-2" data-testid="agent-policy-diff-knowledge">
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Knowledge</p>
                    <p className="text-xs">added: {nText(policyDiff?.knowledge?.added?.length)}</p>
                    <p className="text-xs">removed: {nText(policyDiff?.knowledge?.removed?.length)}</p>
                    <p className="text-xs">changed: {nText(policyDiff?.knowledge?.changed?.length)}</p>
                  </div>
                  <div className="space-y-1 rounded-md border border-border p-2" data-testid="agent-policy-diff-runtime">
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Runtime</p>
                    <p className="text-xs">changed fields: {nText(policyDiff?.runtime?.changed_fields?.length)}</p>
                  </div>
                  <div className="space-y-1 rounded-md border border-border p-2" data-testid="agent-policy-diff-output-contract">
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Output contract</p>
                    <p className="text-xs">changed fields: {nText(policyDiff?.output_contract?.changed_fields?.length)}</p>
                  </div>
                  <div className="space-y-1 rounded-md border border-border p-2" data-testid="agent-policy-diff-evaluation">
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Evaluation</p>
                    <p className="text-xs">changed fields: {nText(policyDiff?.evaluation?.changed_fields?.length)}</p>
                  </div>
                </div>
              </div>
            )}
          </Card>
        </TabsContent>

        <TabsContent value="runs" className="mt-4 space-y-4">
          <div className="space-y-2">
            <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Runtime runs</h3>
            {runtimeRunsQuery.isLoading ? (
              <LoadingSpinner label="Loading runs…" />
            ) : runtimeRuns.length === 0 ? (
              <EmptyState
                icon={<Bot className="h-6 w-6" />}
                title="No runs"
                description="No agent runtime runs reference the selected version."
                testId="agent-runs-empty"
              />
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border">
                <Table data-testid="agent-runs-table">
                  <TableHeader>
                    <TableRow className="bg-muted/50">
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Run</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Status</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Latency</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Created</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Trace</TableHead>
                      <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {runtimeRuns.map((run, index) => {
                      const rid = (run.id || run.run_id) as string | undefined;
                      return (
                        <TableRow key={rid || index} data-testid="agent-run-row">
                          <TableCell className="font-mono text-[11px]">{nText(rid)}</TableCell>
                          <TableCell className="text-xs" data-testid="agent-run-row-status">{nText(run.status)}</TableCell>
                          <TableCell className="font-mono text-[11px]">{nLatency(run.latency_ms ?? null)}</TableCell>
                          <TableCell className="font-mono text-[11px] text-muted-foreground">{isoDate(run.created_at)}</TableCell>
                          <TableCell className="text-xs">
                            {rid ? (
                              <Link to={`/trace?run=${rid}`} className="text-primary hover:underline">Trace</Link>
                            ) : (
                              "n/a"
                            )}
                          </TableCell>
                          <TableCell className="text-right">
                            <Button
                              size="sm"
                              variant="ghost"
                              title="View run"
                              data-testid="agent-run-view"
                              disabled={!rid || viewRunMut.isPending}
                              onClick={() => rid && viewRunMut.mutate(rid)}
                            >
                              <Eye className="h-3.5 w-3.5" />
                            </Button>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>

          <div className="space-y-2">
            <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Referenced runs</h3>
            {usageQuery.isLoading ? (
              <LoadingSpinner label="Loading referenced runs…" />
            ) : runs.length === 0 ? (
              <EmptyState
                icon={<Bot className="h-6 w-6" />}
                title="No runs"
                description="No runs reference the selected agent version."
                testId="agent-runs-usage-empty"
              />
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border">
                <Table data-testid="agent-runs-table-usage">
                  <TableHeader>
                    <TableRow className="bg-muted/50">
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Run</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Experiment</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Status</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Created</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {runs.map((run, index) => (
                      <TableRow key={run.run_id || index} data-testid="agent-usage-run">
                        <TableCell className="font-mono text-[11px]">
                          {run.run_id ? (
                            <Link to={`/trace?run=${run.run_id}`} className="text-primary hover:underline">{nText(run.run_id)}</Link>
                          ) : "n/a"}
                        </TableCell>
                        <TableCell className="text-xs">
                          {run.experiment_id ? (
                            <Link to={`/experiments/${run.experiment_id}`} className="text-primary hover:underline">{nText(run.experiment_id)}</Link>
                          ) : "n/a"}
                        </TableCell>
                        <TableCell className="text-xs">{nText(run.status)}</TableCell>
                        <TableCell className="font-mono text-[11px] text-muted-foreground">{isoDate(run.created_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        </TabsContent>
        <TabsContent value="evaluations" className="mt-4">
          {usageQuery.isLoading ? (
            <LoadingSpinner label="Loading evaluations…" />
          ) : evaluations.length === 0 ? (
            <EmptyState
              icon={<Bot className="h-6 w-6" />}
              title="No evaluations"
              description="No evaluations reference runs of the selected agent version."
              testId="agent-evaluations-empty"
            />
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <Table data-testid="agent-evaluations-table">
                <TableHeader>
                  <TableRow className="bg-muted/50">
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Evaluation</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Run</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Outcome</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Evaluator</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {evaluations.map((evaluation, index) => (
                    <TableRow key={evaluation.id || index} data-testid="agent-usage-evaluation">
                      <TableCell className="font-mono text-[11px]">{nText(evaluation.id)}</TableCell>
                      <TableCell className="font-mono text-[11px]">{nText(evaluation.run_id)}</TableCell>
                      <TableCell className="text-xs">{nText(evaluation.outcome)}</TableCell>
                      <TableCell className="text-xs">{nText(evaluation.evaluator_type)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </TabsContent>

        <TabsContent value="findings" className="mt-4">
          {usageQuery.isLoading ? (
            <LoadingSpinner label="Loading findings…" />
          ) : findings.length === 0 ? (
            <EmptyState
              icon={<Bot className="h-6 w-6" />}
              title="No findings"
              description="No findings reference runs of the selected agent version."
              testId="agent-findings-empty"
            />
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <Table data-testid="agent-findings-table">
                <TableHeader>
                  <TableRow className="bg-muted/50">
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Finding</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Failure domain</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Taxonomy</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Severity</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {findings.map((finding, index) => (
                    <TableRow key={finding.id || index} data-testid="agent-usage-finding">
                      <TableCell className="font-mono text-[11px]">{nText(finding.id)}</TableCell>
                      <TableCell className="text-xs">{nText(finding.failure_domain)}</TableCell>
                      <TableCell className="font-mono text-[11px]">{nText(finding.taxonomy_code)}</TableCell>
                      <TableCell className="text-xs">{nText(finding.severity)}</TableCell>
                      <TableCell className="text-xs">{nText(finding.status)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </TabsContent>
      </Tabs>

      <Dialog open={!!viewVersion} onOpenChange={(open) => !open && setViewVersion(null)}>
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto" data-testid="agent-version-view-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Agent version v{nText(viewVersion?.version)}</DialogTitle>
            <DialogDescription>Immutable configuration snapshot. Missing values render n/a.</DialogDescription>
          </DialogHeader>
          <VersionViewer version={viewVersion} />
        </DialogContent>
      </Dialog>

      <Dialog open={editOpen} onOpenChange={(open) => setEditOpen(open)}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto" data-testid="agent-edit-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Edit agent</DialogTitle>
            <DialogDescription>Identity fields are editable; versions remain immutable.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Name</label>
              <Input className="mt-1" data-testid="agent-edit-name" value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Role</label>
                <Select value={editForm.role || NONE} onValueChange={(value) => setEditForm({ ...editForm, role: value === NONE ? "" : value })}>
                  <SelectTrigger className="mt-1 h-9" data-testid="agent-edit-role"><SelectValue placeholder="Select role" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>Select…</SelectItem>
                    {roleOptions.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Status</label>
                <Select value={editForm.status} onValueChange={(value) => setEditForm({ ...editForm, status: value })}>
                  <SelectTrigger className="mt-1 h-9" data-testid="agent-edit-status"><SelectValue /></SelectTrigger>
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
              <Textarea className="mt-1" rows={2} data-testid="agent-edit-purpose" value={editForm.purpose} onChange={(e) => setEditForm({ ...editForm, purpose: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Description</label>
              <Textarea className="mt-1" rows={2} data-testid="agent-edit-description" value={editForm.description} onChange={(e) => setEditForm({ ...editForm, description: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Responsibilities (comma or newline separated)</label>
              <Textarea className="mt-1" rows={2} data-testid="agent-edit-responsibilities" value={editForm.responsibilities} onChange={(e) => setEditForm({ ...editForm, responsibilities: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Non-responsibilities (comma or newline separated)</label>
              <Textarea className="mt-1" rows={2} data-testid="agent-edit-non-responsibilities" value={editForm.nonResponsibilities} onChange={(e) => setEditForm({ ...editForm, nonResponsibilities: e.target.value })} />
            </div>
            <ErrorNote message={updateMut.isError ? apiError(updateMut.error) : null} />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditOpen(false)}>Cancel</Button>
            <Button data-testid="agent-edit-submit" disabled={!editForm.name.trim() || updateMut.isPending} onClick={() => updateMut.mutate()}>
              {updateMut.isPending ? "Saving…" : "Save changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!newVersionSource} onOpenChange={(open) => !open && setNewVersionSource(null)}>
        <DialogContent data-testid="agent-new-version-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">New version</DialogTitle>
            <DialogDescription>Creates a new draft version with a fixed model binding.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Model</label>
              <div className="mt-1">
                <SingleModelSelect models={models} value={newVersionModelId} onChange={setNewVersionModelId} testId="agent-new-version-model" placeholder="Select model" />
              </div>
            </div>
            <p className="text-[11px] text-muted-foreground" data-testid="agent-new-version-source">
              Source: {newVersionSource ? `v${nText(newVersionSource.version)}` : "none"}
            </p>
            <ErrorNote message={newVersionMut.isError ? apiError(newVersionMut.error) : null} />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setNewVersionSource(null)}>Cancel</Button>
            <Button data-testid="agent-new-version-submit" disabled={!newVersionModelId || newVersionMut.isPending} onClick={() => newVersionMut.mutate()}>
              {newVersionMut.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Plus className="mr-1.5 h-4 w-4" />}
              Create draft
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!runVersion}
        onOpenChange={(open) => {
          if (!open) {
            setRunVersion(null);
            setRunResult(null);
          }
        }}
      >
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto" data-testid="agent-run-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Run agent v{nText(runVersion?.version)}</DialogTitle>
            <DialogDescription>
              Executes this frozen version through the backend agent runtime. Unsupported runtime
              settings are reported, never silently applied.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Task</label>
              <Textarea
                className="mt-1"
                rows={4}
                data-testid="agent-run-task"
                value={runTask}
                onChange={(e) => setRunTask(e.target.value)}
                placeholder="What should the agent do?"
              />
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-muted-foreground">Variables</label>
                <Button size="sm" variant="ghost" data-testid="agent-run-variable-add" onClick={addRunVar}>
                  <Plus className="h-3.5 w-3.5" />
                </Button>
              </div>
              {runVars.map((row, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Input
                    className="h-8 flex-1"
                    data-testid="agent-run-variable-key"
                    value={row.key}
                    onChange={(e) => updateRunVar(index, { key: e.target.value })}
                    placeholder="name"
                  />
                  <Input
                    className="h-8 flex-1"
                    data-testid="agent-run-variable-value"
                    value={row.value}
                    onChange={(e) => updateRunVar(index, { value: e.target.value })}
                    placeholder="value"
                  />
                  <Button size="sm" variant="ghost" data-testid="agent-run-variable-remove" onClick={() => removeRunVar(index)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground">Reserved output tokens</label>
              <Input
                className="mt-1 h-8 w-40"
                inputMode="numeric"
                data-testid="agent-run-reserved"
                value={runReserved}
                onChange={(e) => setRunReserved(e.target.value)}
                placeholder="optional"
              />
            </div>

            <ErrorNote message={runMut.isError ? apiError(runMut.error) : null} />
            {runResult ? (
              <RunResultPanel
                result={runResult}
                onEvaluateRun={openEvaluate}
                evaluationCatalog={evaluationCatalog}
              />
            ) : null}
          </div>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => {
                setRunVersion(null);
                setRunResult(null);
              }}
            >
              Close
            </Button>
            <Button
              data-testid="agent-run-submit"
              disabled={!runTask.trim() || !runVersion?.id || runMut.isPending}
              onClick={() => runMut.mutate()}
            >
              {runMut.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Play className="mr-1.5 h-4 w-4" />}
              Run
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={viewRunOpen}
        onOpenChange={(open) => {
          setViewRunOpen(open);
          if (!open) setViewRunResult(null);
        }}
      >
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto" data-testid="agent-run-view-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Agent run</DialogTitle>
            <DialogDescription>
              Stored run result loaded from the backend. Permission denials and applied policies are
              reported as recorded, never reconstructed.
            </DialogDescription>
          </DialogHeader>
          {viewRunMut.isPending ? (
            <LoadingSpinner label="Loading run…" />
          ) : viewRunResult ? (
            <RunResultPanel
              result={viewRunResult}
              onEvaluateRun={openEvaluate}
              evaluationCatalog={evaluationCatalog}
            />
          ) : (
            <p className="text-xs text-muted-foreground">n/a</p>
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!evaluateRunId}
        onOpenChange={(open) => {
          if (!open) {
            setEvaluateRunId(null);
            evaluateMut.reset();
          }
        }}
      >
        <DialogContent className="max-h-[85vh] max-w-xl overflow-y-auto" data-testid="agent-evaluate-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Evaluate agent run</DialogTitle>
            <DialogDescription>
              Evaluates run {nText(evaluateRunId)} through the backend agent-evaluation profile.
              Deterministic rules and judge-derived evaluations are labeled separately; missing
              values render n/a.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Profile type</label>
                <Select
                  value={evalProfileType}
                  onValueChange={(value) => {
                    setEvalProfileType(value as "agent" | "prompt");
                    setEvalKeys([]);
                  }}
                >
                  <SelectTrigger className="mt-1 h-9" data-testid="agent-evaluate-profile-type">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="agent">agent</SelectItem>
                    <SelectItem value="prompt">prompt</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Threshold (optional)</label>
                <Input
                  className="mt-1 h-9"
                  inputMode="numeric"
                  data-testid="agent-evaluate-threshold"
                  value={evalThreshold}
                  onChange={(e) => setEvalThreshold(e.target.value)}
                  placeholder="e.g. 0.7"
                />
              </div>
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground">Rule keys</label>
              {evalRuleOptions.length === 0 ? (
                <p className="mt-1 text-[11px] text-muted-foreground" data-testid="agent-evaluate-rule-empty">
                  n/a — the catalog reports no rule keys for this profile.
                </p>
              ) : (
                <div className="mt-1 space-y-1" data-testid="agent-evaluate-rules">
                  {evalRuleOptions.map((option) => (
                    <label key={option.value} className="flex items-center gap-2 text-xs">
                      <Checkbox
                        data-testid={`agent-evaluate-rule-${option.value}`}
                        checked={evalKeys.includes(option.value)}
                        onCheckedChange={(state) =>
                          setEvalKeys((current) =>
                            state
                              ? [...current, option.value]
                              : current.filter((key) => key !== option.value)
                          )
                        }
                      />
                      <span className="font-mono">{option.label}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Rubric id (optional)</label>
                <Input
                  className="mt-1 h-9"
                  data-testid="agent-evaluate-rubric"
                  value={evalRubricId}
                  onChange={(e) => setEvalRubricId(e.target.value)}
                  placeholder="optional"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Judge model (optional)</label>
                <div className="mt-1">
                  <SingleModelSelect
                    models={models}
                    value={evalJudgeModelId}
                    onChange={setEvalJudgeModelId}
                    testId="agent-evaluate-judge-model"
                    placeholder="No judge model"
                  />
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <Switch
                data-testid="agent-evaluate-record-failures"
                checked={evalRecordFailures}
                onCheckedChange={setEvalRecordFailures}
              />
              <span className="text-xs text-muted-foreground">Record failures</span>
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground">Notes</label>
              <Textarea
                className="mt-1"
                rows={2}
                data-testid="agent-evaluate-notes"
                value={evalNotes}
                onChange={(e) => setEvalNotes(e.target.value)}
              />
            </div>

            <ErrorNote message={evaluateMut.isError ? apiError(evaluateMut.error) : null} />
          </div>

          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => {
                setEvaluateRunId(null);
                evaluateMut.reset();
              }}
            >
              Cancel
            </Button>
            <Button
              data-testid="agent-evaluate-submit"
              disabled={!evaluateRunId || evaluateMut.isPending}
              onClick={() => evaluateMut.mutate()}
            >
              {evaluateMut.isPending ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <Scale className="mr-1.5 h-4 w-4" />
              )}
              Evaluate
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
