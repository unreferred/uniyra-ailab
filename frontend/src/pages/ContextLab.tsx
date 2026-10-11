import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  AlertTriangle, Archive, ChevronDown, ChevronRight, ExternalLink, Eye, EyeOff, GitCompare,
  Layers, Pencil, Plus, Snowflake, SlidersHorizontal, Sparkles, Trash2,
} from "lucide-react";
import {
  api,
  apiError,
  compareContextPackVersions,
  createContextBudgetPolicy,
  createContextPack,
  createContextPackVersion,
  createStarterContextPacks,
  deleteContextBudgetPolicy,
  deleteContextPack,
  deprecateContextPackVersion,
  diffRunContexts,
  freezeContextPackVersion,
  getRunAssembly,
  previewContextAssembly,
  updateContextBudgetPolicy,
  updateContextPackVersion,
} from "@/lib/api";
import {
  useContextBudgetPolicies,
  useContextCatalog,
  useContextPackVersions,
  useContextPacks,
  useRunContext,
} from "@/lib/hooks";
import { useProjects } from "@/context/ProjectContext";
import {
  CatalogOption,
  ContextAssemblyComponent,
  ContextAssemblyView,
  ContextBudgetPolicy,
  ContextComponent,
  ContextPack,
  ContextPackVersion,
  ContextPackVersionCompare,
  ContextPreviewResult,
  ContextRunDiff,
  ObsPoint,
  Observability,
} from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState, ErrorNote, LoadingSpinner, SectionHeader } from "@/components/common/Common";
import { fmtDate } from "@/lib/format";
import { nText, nTokens, shortHash, sumOrNull } from "@/components/experiment/format";
import { KnowledgeProvenance } from "@/components/workspace/KnowledgeProvenance";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

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

function catalogOptions(list?: CatalogOption[] | null): { value: string; label: string }[] {
  if (!Array.isArray(list)) return [];
  const out: { value: string; label: string }[] = [];
  for (const item of list) {
    if (item == null) continue;
    if (typeof item === "string") out.push({ value: item, label: item });
    else if (item.key) out.push({ value: item.key, label: item.label || item.key });
  }
  return out;
}

function parseNumberOrNull(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

const STATUS_CLASS: Record<string, string> = {
  draft: "border-amber-500/40 bg-amber-500/10 text-amber-600",
  frozen: "border-emerald-500/40 bg-emerald-500/10 text-emerald-600",
  deprecated: "border-muted-foreground/30 bg-muted/40 text-muted-foreground",
  archived: "border-muted-foreground/30 bg-muted/40 text-muted-foreground",
  active: "border-emerald-500/40 bg-emerald-500/10 text-emerald-600",
};

const StatusBadge: React.FC<{ value?: string | null; testId?: string }> = ({ value, testId }) => (
  <Badge variant="outline" className={`text-[10px] ${STATUS_CLASS[value || ""] || "border-border text-muted-foreground"}`} data-testid={testId}>
    {nText(value)}
  </Badge>
);

// ---------------------------------------------------------------------------
// Inspector (preserved run-snapshot inspector + new assembly section)
// ---------------------------------------------------------------------------

interface Group {
  type: string;
  comps: ContextComponent[];
  estimated: number;
  known: number;
  hasUnknown: boolean;
}

function groupComponents(components: ContextComponent[]): Group[] {
  const map = new Map<string, ContextComponent[]>();
  components.forEach((c) => {
    const key = c.component_type || "OTHER";
    const list = map.get(key) || [];
    list.push(c);
    map.set(key, list);
  });
  return Array.from(map.entries())
    .map(([type, comps]) => {
      const sorted = comps.slice().sort((a, b) => (a.ordering ?? 0) - (b.ordering ?? 0));
      return {
        type,
        comps: sorted,
        estimated: sorted.reduce((s, c) => s + (c.estimated_tokens ?? 0), 0),
        known: sorted.reduce((s, c) => s + (c.token_count ?? 0), 0),
        hasUnknown: sorted.some((c) => c.token_count == null),
      };
    })
    .sort((a, b) => (a.comps[0]?.ordering ?? 0) - (b.comps[0]?.ordering ?? 0));
}

const Stat: React.FC<{ label: string; value: React.ReactNode; testId?: string }> = ({ label, value, testId }) => (
  <div className="flex flex-col gap-0.5" data-testid={testId}>
    <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <span className="break-all font-mono text-sm">{value}</span>
  </div>
);

const RunPicker: React.FC<{ value: string; onChange: (v: string) => void }> = ({ value, onChange }) => {
  const { data, isLoading } = useQuery<Observability>({
    queryKey: ["observability", "context-run-picker"],
    queryFn: async () => (await api.get("/observability")).data,
  });
  const options = useMemo<ObsPoint[]>(() => {
    const seen = new Set<string>();
    const list: ObsPoint[] = [];
    [...(data?.series || [])]
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
      .forEach((p) => {
        if (!p.run_id || seen.has(p.run_id)) return;
        seen.add(p.run_id);
        list.push(p);
      });
    return list.slice(0, 100);
  }, [data]);

  return (
    <Select value={value || ""} onValueChange={onChange}>
      <SelectTrigger data-testid="context-run-picker"><SelectValue placeholder={isLoading ? "Loading runs…" : "Select a run…"} /></SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o.run_id as string} value={o.run_id as string}>
            {o.model_name} · {String(o.run_id).slice(0, 8)} · {fmtDate(o.created_at)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
};

const provenanceEntries = (c: ContextComponent): { label: string; score: number | null }[] => {
  const raw: any = c.metadata || {};
  const meta: any = raw.provenance && typeof raw.provenance === "object" ? raw.provenance : raw;
  const out: { label: string; score: number | null }[] = [];
  if (Array.isArray(meta.sources) && meta.sources.length > 0) {
    meta.sources.slice(0, 20).forEach((s: any, i: number) => {
      if (s == null) return;
      if (typeof s === "string") {
        out.push({ label: s, score: Array.isArray(meta.scores) && Number.isFinite(Number(meta.scores[i])) ? Number(meta.scores[i]) : null });
        return;
      }
      const label =
        s.display_name || s.collection_name || s.document_id || s.chunk_id || `source ${out.length + 1}`;
      const score =
        s.score != null && Number.isFinite(Number(s.score))
          ? Number(s.score)
          : Array.isArray(meta.scores) && Number.isFinite(Number(meta.scores[i]))
            ? Number(meta.scores[i])
            : null;
      out.push({ label: String(label), score });
    });
    return out;
  }
  const chunkIds: any[] = Array.isArray(meta.chunk_ids) ? meta.chunk_ids : [];
  const scores: any[] = Array.isArray(meta.scores) ? meta.scores : [];
  chunkIds.slice(0, 20).forEach((id: any, i: number) => {
    out.push({
      label: String(id ?? `chunk ${i + 1}`),
      score: Number.isFinite(Number(scores[i])) ? Number(scores[i]) : null,
    });
  });
  return out;
};

const groupLabel = (type: string): string => {
  if (type === "RETRIEVED_CONTEXT") return "Retrieved context";
  if (type === "PROJECT_INSTRUCTIONS") return "Project instructions";
  return type;
};

const ComponentCard: React.FC<{ component: ContextComponent; index: number }> = ({ component: c, index }) => {
  const key = `${c.component_type}-${index}`;
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const long = (c.content || "").length > 2000;
  const knowledge = c.component_type === "RETRIEVED_CONTEXT";
  const show = revealed[key] ?? (!knowledge && !long);

  return (
    <div className="rounded-md border border-border bg-card p-3 text-xs" data-testid={`context-component-${index}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className="text-[10px]">{c.component_type}</Badge>
        {(c.component_type === "RETRIEVED_CONTEXT" || c.component_type === "PROJECT_INSTRUCTIONS") && (
          <Badge variant="secondary" className="text-[10px]" data-testid={`context-kind-${index}`}>{groupLabel(c.component_type)}</Badge>
        )}
        <Badge variant="secondary" className="text-[10px]">order {c.ordering}</Badge>
        {c.included ? (
          <Badge variant="secondary" className="text-[10px]">included</Badge>
        ) : (
          <Badge variant="destructive" className="text-[10px]">excluded{c.excluded_reason ? `: ${c.excluded_reason}` : ""}</Badge>
        )}
        {c.token_count != null ? (
          <Badge variant="outline" className="text-[10px]">known {c.token_count} tok</Badge>
        ) : (
          <Badge variant="outline" className="text-[10px] text-amber-600">est. {nTokens(c.estimated_tokens)} tok</Badge>
        )}
        {c.truncated && <Badge variant="destructive" className="text-[10px]">truncated{c.truncation_reason ? `: ${c.truncation_reason}` : ""}</Badge>}
      </div>

      <div className="mt-2 grid gap-1 font-mono text-[10px] text-muted-foreground sm:grid-cols-2">
        {c.source_ref && <span className="break-all">source: {sourceRefDisplay(c.source_ref)}</span>}
        {c.content_hash && <span className="break-all">hash: {c.content_hash}</span>}
        {c.estimated_method && <span>method: {c.estimated_method}</span>}
      </div>

      {knowledge && <KnowledgeProvenance metadata={c.metadata} testId={"context-knowledge-provenance-" + index} />}

      <div className="mt-2">
        <button
          type="button"
          className="flex items-center gap-1 text-[10px] text-muted-foreground transition-colors hover:text-foreground"
          data-testid={`context-reveal-${index}`}
          onClick={() => setRevealed((p) => ({ ...p, [key]: !show }))}
        >
          {show ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
          {show ? "Hide content" : "Reveal content"}
        </button>
        {show ? (
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/50 p-2 text-[10px] scrollbar-thin">
            {c.content}
          </pre>
        ) : (
          <p className="mt-1 text-[10px] italic text-muted-foreground">Content hidden ({(c.content || "").length} chars).</p>
        )}
      </div>

      {c.component_type === "RETRIEVED_CONTEXT" && provenanceEntries(c).length > 0 && (
        <div className="mt-2" data-testid={`context-provenance-${index}`}>
          <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Chunk sources</div>
          <ul className="mt-1 space-y-0.5">
            {provenanceEntries(c).map((e, i) => (
              <li key={i} className="font-mono text-[10px] text-muted-foreground">
                {e.label}{e.score != null ? ` · ${e.score.toFixed(4)}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      {c.metadata && Object.keys(c.metadata).length > 0 && (
        <div className="mt-2">
          <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Metadata</div>
          <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted/50 p-2 font-mono text-[10px] scrollbar-thin">
            {JSON.stringify(c.metadata, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
};

function dedupFlag(c: ContextAssemblyComponent): boolean {
  const meta: any = c.metadata || {};
  if (meta.deduplicated === true) return true;
  if (Number.isFinite(Number(meta.dedup_count)) && Number(meta.dedup_count) > 0) return true;
  if (Number.isFinite(Number(meta.deduplicated_count)) && Number(meta.deduplicated_count) > 0) return true;
  return false;
}

const SOURCE_REF_LABELS: Record<string, string> = {
  prompt_version: "Prompt version",
  prompt: "Prompt",
  context_pack_version: "Context pack version",
  context_pack: "Context pack",
  project_policy: "Project policy",
  project_invariant: "Project invariant",
  knowledge_collection: "Knowledge collection",
  knowledge_document: "Knowledge document",
  attachment: "Attachment",
  project_file: "Project file",
  dataset_case: "Dataset case",
  dataset: "Dataset",
  agent_version: "Agent version",
  run: "Run",
};

function sourceRefHref(type: string | null, id: string | null): string | null {
  if (!id) return null;
  if (type === "prompt_version" || type === "prompt") return `/prompt-lab/prompts/${id}`;
  if (type === "context_pack_version" || type === "context_pack") return "/context-lab";
  if (type === "project_policy" || type === "project_invariant") return "/prompt-lab";
  return null;
}

function parseSourceRef(raw: any): { type: string | null; id: string | null } | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === "object") {
    const type = raw.type ?? raw.kind ?? null;
    const id = raw.id ?? raw.ref_id ?? null;
    if (type === null && id === null) return null;
    return { type: type === null ? null : String(type), id: id === null ? null : String(id) };
  }
  const text = String(raw).trim();
  if (!text) return null;
  if (text.startsWith("{")) {
    try { return parseSourceRef(JSON.parse(text)); } catch { /* not JSON, treat as opaque ref */ }
  }
  if (text.includes(":")) {
    const index = text.indexOf(":");
    const type = text.slice(0, index).trim();
    const id = text.slice(index + 1).trim();
    return { type: type || null, id: id || null };
  }
  return { type: null, id: text };
}

function sourceRefDisplay(raw: any): string {
  const ref = parseSourceRef(raw);
  if (!ref) return "n/a";
  if (ref.type && ref.id) return `${ref.type}:${ref.id}`;
  return ref.id || ref.type || "n/a";
}

function metadataCount(metadata: any, keys: string[]): number | null {
  if (!metadata || typeof metadata !== "object") return null;
  for (const key of keys) {
    const value = Number(metadata[key]);
    if (metadata[key] !== null && metadata[key] !== undefined && Number.isFinite(value)) return value;
  }
  return null;
}

const WhyProvenance: React.FC<{
  component: ContextAssemblyComponent;
  index: number | string;
  testIdPrefix: string;
  excludedReason?: string | null;
}> = ({ component: c, index, testIdPrefix, excludedReason }) => {
  const [open, setOpen] = useState(false);
  const meta: any = c.metadata || {};
  const ref = parseSourceRef(c.source_ref as any);
  const componentType = c.component_type ? String(c.component_type) : null;
  const sourceClass = c.source_class ? String(c.source_class) : null;
  const isRetrieval =
    /RETRIEV|KNOWLEDGE/.test((componentType || "").toUpperCase()) ||
    (sourceClass || "").toLowerCase() === "knowledge";
  const chunkCount = isRetrieval ? metadataCount(meta, ["chunk_count", "chunks", "returned_count"]) : null;
  const deduplicatedCount = isRetrieval ? metadataCount(meta, ["deduplicated_count", "dedup_count"]) : null;
  const reason = excludedReason ?? c.excluded_reason ?? null;
  const href = ref ? sourceRefHref(ref.type, ref.id) : null;
  const label = ref?.type ? SOURCE_REF_LABELS[ref.type] || ref.type : "Source";
  const hasProvenance = Boolean(ref || chunkCount !== null || deduplicatedCount !== null || reason);

  return (
    <div className="mt-1">
      <button
        type="button"
        className="flex items-center gap-1 text-[10px] text-muted-foreground transition-colors hover:text-foreground"
        data-testid={`${testIdPrefix}-toggle-${index}`}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        Why is this here?
      </button>
      {open ? (
        <div
          className="mt-1 space-y-0.5 rounded border border-border/70 bg-muted/30 p-2 font-mono text-[10px] text-muted-foreground"
          data-testid={`${testIdPrefix}-${index}`}
        >
          {(componentType || sourceClass) ? (
            <p data-testid={`${testIdPrefix}-type-${index}`}>
              {nText(componentType)}{sourceClass ? ` · ${sourceClass}` : ""}
            </p>
          ) : null}
          {ref ? (
            <p className="break-all" data-testid={`${testIdPrefix}-source-ref-${index}`}>
              {label}: {href && ref.id ? <Link to={href} className="text-primary hover:underline">{nText(ref.id)}</Link> : nText(ref.id)}
            </p>
          ) : null}
          {chunkCount !== null ? <p data-testid={`${testIdPrefix}-chunks-${index}`}>chunk count: {chunkCount}</p> : null}
          {deduplicatedCount !== null ? <p data-testid={`${testIdPrefix}-dedup-${index}`}>deduplicated count: {deduplicatedCount}</p> : null}
          {reason ? <p data-testid={`${testIdPrefix}-excluded-${index}`}>excluded: {nText(reason)}</p> : null}
          {!hasProvenance ? <p data-testid={`${testIdPrefix}-none-${index}`}>No provenance recorded</p> : null}
        </div>
      ) : null}
    </div>
  );
};

const WhySummary: React.FC<{ components: ContextAssemblyComponent[] }> = ({ components }) => {
  const groups = useMemo(() => {
    const map = new Map<string, { source_class: string; count: number; tokens: (number | null)[] }>();
    for (const component of components) {
      if (component.included === false) continue;
      const key = component.source_class || "OTHER";
      const entry = map.get(key) || { source_class: key, count: 0, tokens: [] };
      entry.count += 1;
      entry.tokens.push(component.estimated_tokens ?? null);
      map.set(key, entry);
    }
    return Array.from(map.values())
      .sort((a, b) => a.source_class.localeCompare(b.source_class))
      .map((entry) => ({ source_class: entry.source_class, count: entry.count, estimated_tokens: sumOrNull(entry.tokens) }));
  }, [components]);

  return (
    <div className="space-y-1" data-testid="context-why-summary">
      <h4 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Included sources by source class</h4>
      {groups.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="context-why-summary-empty">n/a — no included components.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40">
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Source class</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Count</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Est. tokens</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.map((group) => (
                <TableRow key={group.source_class} data-testid="context-why-summary-row">
                  <TableCell className="text-xs" data-testid="context-why-summary-class">{nText(group.source_class)}</TableCell>
                  <TableCell className="text-right font-mono text-xs" data-testid="context-why-summary-count">{group.count}</TableCell>
                  <TableCell className="text-right font-mono text-xs" data-testid="context-why-summary-tokens">{nTokens(group.estimated_tokens)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
};

const AssemblyComponentRow: React.FC<{ component: ContextAssemblyComponent; index: number; excluded?: ContextAssemblyComponent[] }> = ({ component: c, index, excluded }) => {
  const excludedMatch = (excluded || []).find(
    (entry) => entry.component_type === c.component_type && entry.source_ref === c.source_ref && entry.ordering === c.ordering
  );
  return (
    <div className="rounded-md border border-border bg-card p-2 text-xs" data-testid={`context-assembly-component-${index}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className="text-[10px]" data-testid={`context-assembly-type-${index}`}>{nText(c.component_type)}</Badge>
        <Badge variant="secondary" className="text-[10px]" data-testid={`context-assembly-source-class-${index}`}>{nText(c.source_class)}</Badge>
        <Badge variant="secondary" className="text-[10px]">order {nText(c.ordering)}</Badge>
        {c.included ? (
          <Badge variant="secondary" className="text-[10px]">included</Badge>
        ) : (
          <Badge variant="destructive" className="text-[10px]" data-testid={`context-assembly-excluded-${index}`}>
            excluded{c.excluded_reason ? `: ${c.excluded_reason}` : ""}
          </Badge>
        )}
        <Badge variant="outline" className="text-[10px]" data-testid={`context-assembly-token-${index}`}>
          {c.token_count != null ? `${c.token_count} tok` : `est. ${nTokens(c.estimated_tokens)} tok`}
        </Badge>
        {c.truncated && (
          <Badge variant="destructive" className="text-[10px]" data-testid={`context-assembly-truncated-${index}`}>
            truncated{c.truncation_reason ? `: ${c.truncation_reason}` : ""}
          </Badge>
        )}
        {dedupFlag(c) && <Badge variant="outline" className="text-[10px]" data-testid={`context-assembly-dedup-${index}`}>deduplicated</Badge>}
      </div>
      <div className="mt-1 grid gap-1 font-mono text-[10px] text-muted-foreground sm:grid-cols-2">
        {c.estimated_method && <span>method: {nText(c.estimated_method)}</span>}
        {c.content_hash && <span className="break-all">hash: {shortHash(c.content_hash)}</span>}
        {c.source_ref && <span className="break-all">source: {sourceRefDisplay(c.source_ref)}</span>}
      </div>
      {c.content_excerpt && (
        <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/50 p-2 text-[10px] scrollbar-thin">
          {c.content_excerpt}
        </pre>
      )}
      <WhyProvenance
        component={c}
        index={index}
        testIdPrefix="context-why-provenance"
        excludedReason={excludedMatch?.excluded_reason}
      />
    </div>
  );
};

const AssemblySection: React.FC<{ runId: string }> = ({ runId }) => {
  const { data, isLoading, isError, error } = useQuery<ContextAssemblyView>({
    queryKey: ["context-assembly", runId],
    queryFn: async () => getRunAssembly(runId),
    enabled: !!runId,
  });

  if (isLoading) return <LoadingSpinner label="Loading context assembly…" />;
  if (isError) return <ErrorNote message={apiError(error)} />;
  if (!data) return null;

  if (data.available === false) {
    return (
      <Card className="p-4" data-testid="context-assembly-unavailable">
        <h3 className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Assembly</h3>
        <p className="text-xs text-muted-foreground">{data.reason || "No context assembly available for this run."}</p>
      </Card>
    );
  }

  const components = asArray<ContextAssemblyComponent>(data.components);
  const excluded = asArray<ContextAssemblyComponent>(data.excluded_components);
  const summaries = asArray<any>(data.sources_summary);
  const ref = data.snapshot_reference;

  return (
    <Card className="space-y-3 p-4" data-testid="context-assembly">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Assembly</h3>
        <div className="flex flex-wrap items-center gap-2 text-[10px] font-mono text-muted-foreground">
          <span data-testid="context-assembly-snapshot-ref">
            snapshot: {ref?.id ? shortHash(ref.id) : "n/a"}
          </span>
          <Badge variant="outline" className="text-[9px]">{ref?.available ? "snapshot available" : "snapshot n/a"}</Badge>
        </div>
      </div>

      {components.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="context-assembly-empty">No assembly components returned.</p>
      ) : (
        <div className="space-y-2">
          {components.map((c, i) => <AssemblyComponentRow key={i} component={c} index={i} excluded={excluded} />)}
        </div>
      )}

      <WhySummary components={components} />

      {excluded.length > 0 && (
        <div className="space-y-1" data-testid="context-assembly-excluded">
          <h4 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Excluded</h4>
          {excluded.map((c, i) => (
            <div key={i} className="rounded border border-border/70 px-2 py-1" data-testid={`context-assembly-excluded-row-${i}`}>
              <div className="flex items-center justify-between gap-2 text-[11px]">
                <span className="font-medium">{nText(c.component_type)}</span>
                <span className="text-muted-foreground">{nText(c.excluded_reason)}</span>
                <span className="font-mono text-[10px] text-muted-foreground">{nTokens(c.estimated_tokens)}</span>
              </div>
              <WhyProvenance
                component={c}
                index={i}
                testIdPrefix="context-why-provenance-excluded"
                excludedReason={c.excluded_reason}
              />
            </div>
          ))}
        </div>
      )}

      {summaries.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-border" data-testid="context-assembly-sources-summary">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40">
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Source class</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Count</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Included</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Est. tokens</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {summaries.map((s, i) => (
                <TableRow key={i} data-testid="context-assembly-source-row">
                  <TableCell className="text-xs">{nText(s.source_class)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{nText(s.count)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{nText(s.included_count)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{nTokens(s.estimated_tokens)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </Card>
  );
};

const InspectorTab: React.FC<{ runId: string; setRun: (v: string) => void }> = ({ runId, setRun }) => {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const { data, isLoading, isError, error } = useRunContext(runId || null);

  const snapshot = data?.snapshot;
  const available = data ? data.available !== false : true;
  const groups = useMemo(() => groupComponents(snapshot?.components || []), [snapshot]);
  const excluded = snapshot?.excluded_components || [];
  const warnings = snapshot?.warnings || [];
  const totalEstimated = snapshot?.estimated_total_tokens ?? snapshot?.total_context_tokens ?? null;
  const knownTotal = snapshot?.known_token_count ?? null;

  return (
    <div className="space-y-6">
      <Card className="flex flex-col gap-2 p-4 sm:flex-row sm:items-center">
        <div className="flex-1">
          <RunPicker value={runId} onChange={setRun} />
        </div>
        {runId && <div className="break-all font-mono text-[11px] text-muted-foreground">run {runId}</div>}
      </Card>

      {!runId ? (
        <EmptyState icon={<Layers className="h-6 w-6" />} title="Pick a run" description="Select a run to inspect its assembled context." testId="context-lab-empty" />
      ) : isLoading ? (
        <LoadingSpinner label="Loading context…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : data && !available ? (
        <ErrorNote message={data.message || "Legacy run — trace unavailable"} />
      ) : !snapshot ? (
        <div className="rounded-md border border-dashed border-border/70 bg-card/40 px-4 py-8 text-center" data-testid="context-lab-no-snapshot">
          <p className="text-sm font-medium">No context snapshot for this run</p>
          <p className="mt-1 text-xs text-muted-foreground">This run predates context snapshots, so no component breakdown is available.</p>
        </div>
      ) : (
        <div className="space-y-5">
          <Card className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-4" data-testid="context-totals">
            <Stat label="Estimated total" testId="context-total-estimated" value={<>{nTokens(totalEstimated)} <Badge variant="outline" className="ml-1 text-[9px]">ESTIMATED</Badge></>} />
            <Stat label="Known total" testId="context-total-known" value={nTokens(knownTotal)} />
            <Stat
              label="Provider input tokens"
              testId="context-provider-tokens"
              value={
                data.provider_input_tokens != null
                  ? <>{data.provider_input_tokens} <Badge variant="secondary" className="ml-1 text-[9px]">provider-reported (authoritative)</Badge></>
                  : "n/a"
              }
            />
            <Stat label="Estimated method" value={snapshot.estimated_method || "n/a"} />
            <Stat label="Context hash" value={snapshot.context_hash || "n/a"} />
            <Stat label="Strategy" value={snapshot.strategy || "n/a"} />
            <Stat label="Over budget" value={snapshot.over_budget ? <Badge variant="destructive">over budget</Badge> : <Badge variant="secondary">within budget</Badge>} />
            <Stat label="Unknown components" value={String(snapshot.unknown_token_component_count ?? groups.filter((g) => g.hasUnknown).reduce((n, g) => n + g.comps.filter((c) => c.token_count == null).length, 0))} />
          </Card>

          <Card className="p-4" data-testid="context-summary-table">
            <h3 className="mb-3 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Component summary</h3>
            <div className="overflow-x-auto rounded-md border border-border">
              <table className="w-full text-xs">
                <thead className="bg-muted/40 text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2">Component type</th>
                    <th className="px-3 py-2 text-right">#</th>
                    <th className="px-3 py-2 text-right">Estimated tokens</th>
                    <th className="px-3 py-2 text-right">Known tokens</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {groups.map((g) => (
                    <tr key={g.type}>
                      <td className="px-3 py-2 font-medium">
                        <span className="flex items-center gap-1.5">
                          {g.type}
                          {g.hasUnknown && <span title="Some components have no known token count"><AlertTriangle className="h-3 w-3 text-amber-500" /></span>}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right font-mono">{g.comps.length}</td>
                      <td className="px-3 py-2 text-right font-mono">{nTokens(g.estimated)}</td>
                      <td className="px-3 py-2 text-right font-mono">{nTokens(g.known)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {warnings.length > 0 && (
            <div className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/10 p-3" data-testid="context-warnings">
              {warnings.map((w, i) => (
                <p key={i} className="flex items-start gap-1.5 text-[11px] text-amber-600">
                  <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> {w}
                </p>
              ))}
            </div>
          )}

          <div className="space-y-2">
            <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Components</h3>
            {groups.map((g) => {
              const open = expanded[g.type] ?? true;
              return (
                <div key={g.type} className="rounded-md border border-border">
                  <button
                    type="button"
                    data-testid={`context-group-${g.type}`}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs"
                    onClick={() => setExpanded((p) => ({ ...p, [g.type]: !open }))}
                  >
                    {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                    <span className="flex-1 font-medium">
                      {groupLabel(g.type)}
                      {(g.type === "RETRIEVED_CONTEXT" || g.type === "PROJECT_INSTRUCTIONS") && (
                        <span className="ml-2 font-mono text-[10px] text-muted-foreground">({g.type})</span>
                      )}
                    </span>
                    <span className="font-mono text-[10px] text-muted-foreground">{g.comps.length} · {nTokens(g.estimated)}</span>
                  </button>
                  {open && (
                    <div className="space-y-2 border-t border-border p-2">
                      {g.comps.map((c, i) => <ComponentCard key={`${g.type}-${i}`} component={c} index={i} />)}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {excluded.length > 0 && (
            <Card className="p-4" data-testid="context-excluded">
              <h3 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Excluded components</h3>
              <div className="space-y-1">
                {excluded.map((c, i) => (
                  <div key={i} className="flex items-center justify-between gap-2 rounded border border-border/70 px-3 py-1.5 text-[11px]">
                    <span className="font-medium">{c.component_type}</span>
                    <span className="text-muted-foreground">{c.excluded_reason || "excluded"}</span>
                    <span className="font-mono text-[10px] text-muted-foreground">{nTokens(c.estimated_tokens)}</span>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      )}

      {runId && <AssemblySection runId={runId} />}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Packs — list, versions, lifecycle and version comparison
// ---------------------------------------------------------------------------

const PackVersionCompareView: React.FC<{ compare: ContextPackVersionCompare }> = ({ compare }) => {
  const added = asArray<string>(compare.items?.added);
  const removed = asArray<string>(compare.items?.removed);
  const changed = asArray<any>(compare.items?.changed);
  return (
    <div className="space-y-2 rounded-md border border-border p-3" data-testid="context-pack-compare-result">
      <div className="flex flex-wrap gap-4 text-xs" data-testid="context-pack-compare-summary">
        <span>added: <span className="font-mono">{nText(compare.summary?.items_added)}</span></span>
        <span>removed: <span className="font-mono">{nText(compare.summary?.items_removed)}</span></span>
        <span>changed: <span className="font-mono">{nText(compare.summary?.items_changed)}</span></span>
        <span>content changed: <span className="font-mono">{compare.summary?.content_changed == null ? "n/a" : String(compare.summary.content_changed)}</span></span>
        <span>token delta: <span className="font-mono">{compare.summary?.token_delta == null ? "n/a" : `${compare.summary.token_delta > 0 ? "+" : ""}${compare.summary.token_delta}`}</span></span>
      </div>
      <div className="grid gap-2 md:grid-cols-3">
        <div className="space-y-0.5 text-xs" data-testid="context-pack-compare-added">
          <p className="text-[10px] font-mono uppercase tracking-wider text-emerald-600">Added</p>
          {added.length === 0 ? <p className="text-muted-foreground">none</p> : added.map((key, i) => (
            <p key={i} className="font-mono text-emerald-600" data-testid="context-pack-compare-added-item">+ {nText(key)}</p>
          ))}
        </div>
        <div className="space-y-0.5 text-xs" data-testid="context-pack-compare-removed">
          <p className="text-[10px] font-mono uppercase tracking-wider text-destructive">Removed</p>
          {removed.length === 0 ? <p className="text-muted-foreground">none</p> : removed.map((key, i) => (
            <p key={i} className="font-mono text-destructive" data-testid="context-pack-compare-removed-item">- {nText(key)}</p>
          ))}
        </div>
        <div className="space-y-0.5 text-xs" data-testid="context-pack-compare-changed">
          <p className="text-[10px] font-mono uppercase tracking-wider text-amber-600">Changed</p>
          {changed.length === 0 ? <p className="text-muted-foreground">none</p> : changed.map((entry, i) => {
            const key = typeof entry === "string" ? entry : entry?.key;
            return <p key={i} className="font-mono text-amber-600" data-testid="context-pack-compare-changed-item">~ {nText(key)}</p>;
          })}
        </div>
      </div>
    </div>
  );
};

const PackVersionCompare: React.FC<{ packId: string; versions: ContextPackVersion[] }> = ({ packId, versions }) => {
  const [fromId, setFromId] = useState("");
  const [toId, setToId] = useState("");
  const [compare, setCompare] = useState<ContextPackVersionCompare | null>(null);

  useEffect(() => {
    if (versions.length === 0) return;
    setFromId((cur) => (cur && versions.some((v) => v.id === cur) ? cur : ((versions[versions.length - 1].id as string) || "")));
    setToId((cur) => (cur && versions.some((v) => v.id === cur) ? cur : ((versions[0].id as string) || "")));
  }, [versions]);

  const compareMut = useMutation({
    mutationFn: async () => compareContextPackVersions(packId, fromId, toId),
    onSuccess: (res) => setCompare(res as ContextPackVersionCompare),
    onError: (e) => toast.error(apiError(e)),
  });

  if (versions.length < 2) {
    return <p className="text-xs text-muted-foreground">n/a — at least two versions are required to compare.</p>;
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={fromId} onValueChange={(v) => { setFromId(v); setCompare(null); }}>
          <SelectTrigger className="w-48" data-testid="context-pack-compare-from"><SelectValue placeholder="From version" /></SelectTrigger>
          <SelectContent>
            {versions.map((v, i) => <SelectItem key={v.id || i} value={v.id as string}>v{nText(v.version)} · {nText(v.status)}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={toId} onValueChange={(v) => { setToId(v); setCompare(null); }}>
          <SelectTrigger className="w-48" data-testid="context-pack-compare-to"><SelectValue placeholder="To version" /></SelectTrigger>
          <SelectContent>
            {versions.map((v, i) => <SelectItem key={v.id || i} value={v.id as string}>v{nText(v.version)} · {nText(v.status)}</SelectItem>)}
          </SelectContent>
        </Select>
        <Button size="sm" data-testid="context-pack-compare" disabled={!fromId || !toId || compareMut.isPending} onClick={() => compareMut.mutate()}>
          Compare
        </Button>
      </div>
      {compare && <PackVersionCompareView compare={compare} />}
    </div>
  );
};

type ItemRow = { key: string; title: string; source_type: string; content: string };
const EMPTY_ITEM: ItemRow = { key: "", title: "", source_type: "", content: "" };

const itemsToRows = (items?: ContextPackVersion["items"]): ItemRow[] =>
  asArray<any>(items).map((i) => ({
    key: i?.key || "",
    title: i?.title || "",
    source_type: i?.source_type || "",
    content: i?.content || "",
  }));

const rowsToItems = (rows: ItemRow[]) =>
  rows
    .filter((r) => r.key.trim() || r.title.trim() || r.content.trim())
    .map((r, index) => ({
      key: r.key.trim(),
      title: r.title.trim(),
      source_type: r.source_type || undefined,
      content: r.content,
      order: index,
    }));

const PackDetail: React.FC<{ pack: ContextPack; projectId: string; sourceTypes: { value: string; label: string }[] }> = ({ pack, projectId, sourceTypes }) => {
  const qc = useQueryClient();
  const packId = pack.id as string;
  const { data, isLoading } = useContextPackVersions(packId);
  const versions = asArray<ContextPackVersion>(data);

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ContextPackVersion | null>(null);
  const [description, setDescription] = useState("");
  const [rows, setRows] = useState<ItemRow[]>([]);
  const [formError, setFormError] = useState<string | null>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["context-pack-versions", packId] });
    qc.invalidateQueries({ queryKey: ["context-packs", projectId] });
  };

  const openCreate = () => {
    setEditing(null);
    setDescription("");
    setRows([{ ...EMPTY_ITEM, source_type: sourceTypes[0]?.value || "" }]);
    setFormError(null);
    setOpen(true);
  };

  const openEdit = (version: ContextPackVersion) => {
    setEditing(version);
    setDescription(version.description || "");
    const existing = itemsToRows(version.items);
    setRows(existing.length > 0 ? existing : [{ ...EMPTY_ITEM, source_type: sourceTypes[0]?.value || "" }]);
    setFormError(null);
    setOpen(true);
  };

  const submit = () => {
    const items = rowsToItems(rows);
    if (items.length === 0) {
      setFormError("Add at least one item with a key, title or content.");
      return;
    }
    setFormError(null);
    if (editing?.id) updateMut.mutate({ description, items });
    else createMut.mutate({ description, items });
  };

  const createMut = useMutation({
    mutationFn: async (body: { description: string; items: ReturnType<typeof rowsToItems> }) => createContextPackVersion(packId, body),
    onSuccess: () => { toast.success("Draft version created"); invalidate(); setOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const updateMut = useMutation({
    mutationFn: async (body: { description: string; items: ReturnType<typeof rowsToItems> }) => updateContextPackVersion(packId, editing!.id as string, body),
    onSuccess: () => { toast.success("Draft version updated"); invalidate(); setOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const freezeMut = useMutation({
    mutationFn: async (versionId: string) => freezeContextPackVersion(packId, versionId),
    onSuccess: () => { toast.success("Version frozen"); invalidate(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const deprecateMut = useMutation({
    mutationFn: async (versionId: string) => deprecateContextPackVersion(packId, versionId),
    onSuccess: () => { toast.success("Version deprecated"); invalidate(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const setRow = (index: number, patch: Partial<ItemRow>) =>
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));

  return (
    <div className="space-y-3 border-t border-border p-3" data-testid="context-pack-detail">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Versions</h3>
        <Button size="sm" data-testid="context-pack-new-version" onClick={openCreate}>
          <Plus className="mr-1.5 h-4 w-4" /> New version
        </Button>
      </div>

      {isLoading ? (
        <LoadingSpinner label="Loading versions…" />
      ) : versions.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="context-pack-versions-empty">No versions returned.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <Table data-testid="context-pack-versions-table">
            <TableHeader>
              <TableRow className="bg-muted/50">
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Version</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Status</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Items</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Content hash</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Created</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {versions.map((v, i) => {
                const vid = v.id as string;
                const isDraft = v.status === "draft";
                const canDeprecate = v.status === "draft" || v.status === "frozen";
                return (
                  <TableRow key={v.id || i} data-testid="context-pack-version-row">
                    <TableCell className="font-mono text-xs" data-testid="context-pack-version-number">v{nText(v.version)}</TableCell>
                    <TableCell><StatusBadge value={v.status} testId="context-pack-version-status" /></TableCell>
                    <TableCell className="text-right font-mono text-xs">{nText(Array.isArray(v.items) ? v.items.length : null)}</TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground" title={v.content_hash || undefined}>{shortHash(v.content_hash)}</TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground">{isoDate(v.created_at)}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap justify-end gap-1">
                        {isDraft && (
                          <Button size="sm" variant="ghost" title="Edit draft" data-testid="context-pack-version-edit" onClick={() => openEdit(v)}>
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                        )}
                        {isDraft && (
                          <Button size="sm" variant="ghost" title="Freeze" data-testid="context-pack-version-freeze" disabled={freezeMut.isPending || !vid} onClick={() => freezeMut.mutate(vid)}>
                            <Snowflake className="h-3.5 w-3.5" />
                          </Button>
                        )}
                        {canDeprecate && (
                          <Button size="sm" variant="ghost" title="Deprecate" data-testid="context-pack-version-deprecate" disabled={deprecateMut.isPending || !vid} onClick={() => deprecateMut.mutate(vid)}>
                            <Archive className="h-3.5 w-3.5" />
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

      <div className="space-y-2">
        <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Compare versions</h3>
        <PackVersionCompare packId={packId} versions={versions} />
      </div>

      <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) setEditing(null); }}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto" data-testid="context-pack-version-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">{editing ? `Edit draft v${nText(editing.version)}` : "New pack version"}</DialogTitle>
            <DialogDescription>Pack versions are immutable once frozen. Only draft versions are editable.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Description</label>
              <Input data-testid="context-pack-version-description-input" value={description} onChange={(e) => setDescription(e.target.value)} className="mt-1" />
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-muted-foreground">Items</label>
                <Button size="sm" variant="outline" data-testid="context-pack-item-add"
                  onClick={() => setRows((prev) => [...prev, { ...EMPTY_ITEM, source_type: sourceTypes[0]?.value || "" }])}>
                  <Plus className="mr-1.5 h-3.5 w-3.5" /> Add item
                </Button>
              </div>
              {rows.map((row, i) => (
                <div key={i} className="grid gap-2 rounded-md border border-border p-2 md:grid-cols-[1fr_1fr_1fr_auto]" data-testid="context-pack-item-row">
                  <Input placeholder="key" value={row.key} data-testid={`context-pack-item-key-${i}`} onChange={(e) => setRow(i, { key: e.target.value })} className="font-mono text-xs" />
                  <Input placeholder="title" value={row.title} data-testid={`context-pack-item-title-${i}`} onChange={(e) => setRow(i, { title: e.target.value })} className="text-xs" />
                  <Select value={row.source_type || ""} onValueChange={(v) => setRow(i, { source_type: v })}>
                    <SelectTrigger data-testid={`context-pack-item-source-${i}`}><SelectValue placeholder="source type" /></SelectTrigger>
                    <SelectContent>
                      {sourceTypes.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                  <Button size="sm" variant="ghost" className="text-destructive" data-testid={`context-pack-item-remove-${i}`}
                    onClick={() => setRows((prev) => prev.filter((_, idx) => idx !== i))}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                  <Textarea placeholder="content" value={row.content} data-testid={`context-pack-item-content-${i}`} onChange={(e) => setRow(i, { content: e.target.value })} rows={2} className="md:col-span-4 text-xs" />
                </div>
              ))}
            </div>
            <ErrorNote message={formError} />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button data-testid="context-pack-version-submit" disabled={createMut.isPending || updateMut.isPending} onClick={submit}>
              {createMut.isPending || updateMut.isPending ? "Saving…" : editing ? "Save changes" : "Create version"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

const PacksTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const qc = useQueryClient();
  const { data, isLoading, isError, error } = useContextPacks(projectId);
  const { data: catalog } = useContextCatalog();
  const packs = asArray<ContextPack>(data);
  const sourceTypes = catalogOptions(catalog?.source_types);
  const starterPacks = asArray<any>(catalog?.starter_packs);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState({ name: "", description: "" });
  const [deleteTarget, setDeleteTarget] = useState<ContextPack | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["context-packs", projectId] });

  const createMut = useMutation({
    mutationFn: async () => createContextPack({ project_id: projectId, name: form.name.trim(), description: form.description || undefined }),
    onSuccess: () => { toast.success("Pack created"); invalidate(); setDialogOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const deleteMut = useMutation({
    mutationFn: async (id: string) => deleteContextPack(id),
    onSuccess: () => { toast.success("Pack deleted"); invalidate(); setDeleteTarget(null); },
    onError: (e) => toast.error(apiError(e)),
  });

  const starterMut = useMutation({
    mutationFn: async () => createStarterContextPacks(projectId),
    onSuccess: (res: any) => {
      const created = Array.isArray(res?.created) ? res.created.length : null;
      const existing = Array.isArray(res?.existing) ? res.existing.length : null;
      if (created === null && existing === null) toast.success("Starter packs added");
      else toast.success(`Starter packs: ${created ?? 0} created, ${existing ?? 0} already present`);
      invalidate();
      qc.invalidateQueries({ queryKey: ["context-catalog"] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const openCreate = () => { setForm({ name: "", description: "" }); setDialogOpen(true); };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          Context packs are versioned, immutable units of reusable context.
          {starterPacks.length > 0 && <span className="ml-1">{starterPacks.length} starter packs available.</span>}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" data-testid="context-packs-starters" disabled={starterMut.isPending} onClick={() => starterMut.mutate()}>
            <Sparkles className="mr-1.5 h-4 w-4" /> Add starter packs
          </Button>
          <Button size="sm" data-testid="context-pack-create" onClick={openCreate}>
            <Plus className="mr-1.5 h-4 w-4" /> New pack
          </Button>
        </div>
      </div>

      {isLoading ? (
        <LoadingSpinner label="Loading packs…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : packs.length === 0 ? (
        <EmptyState
          icon={<Layers className="h-6 w-6" />}
          title="No context packs"
          description="Create a pack or seed a starter set from the catalog."
          testId="context-packs-empty"
        />
      ) : (
        <div className="space-y-2">
          {packs.map((p, i) => {
            const pid = p.id as string;
            const open = expandedId === pid;
            return (
              <div key={p.id || i} className="rounded-md border border-border" data-testid="context-pack-row">
                <div className="flex items-center gap-2 px-3 py-2">
                  <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" data-testid="context-pack-expand"
                    onClick={() => setExpandedId(open ? null : pid)} disabled={!pid}>
                    {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium" data-testid="context-pack-name">{nText(p.name)}</span>
                      {p.description && <span className="block truncate text-[11px] text-muted-foreground">{p.description}</span>}
                    </span>
                  </button>
                  <StatusBadge value={p.status} />
                  <Badge variant="outline" className="text-[10px]" data-testid="context-pack-latest-version">latest v{nText(p.latest_version)}</Badge>
                  <span className="font-mono text-[10px] text-muted-foreground">{isoDate(p.updated_at || p.created_at)}</span>
                  <Button size="sm" variant="ghost" className="text-destructive" data-testid="context-pack-delete" onClick={() => setDeleteTarget(p)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
                {open && pid && <PackDetail pack={p} projectId={projectId} sourceTypes={sourceTypes} />}
              </div>
            );
          })}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent data-testid="context-pack-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">New context pack</DialogTitle>
            <DialogDescription>A pack groups reusable context items. Create a version to add items.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Name</label>
              <Input data-testid="context-pack-name-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Description</label>
              <Textarea data-testid="context-pack-description-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={3} className="mt-1" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button data-testid="context-pack-submit" disabled={!form.name.trim() || createMut.isPending} onClick={() => createMut.mutate()}>
              {createMut.isPending ? "Saving…" : "Create pack"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleteTarget} onOpenChange={(v) => !v && setDeleteTarget(null)}>
        <AlertDialogContent data-testid="context-pack-delete-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete context pack</AlertDialogTitle>
            <AlertDialogDescription>Delete "{nText(deleteTarget?.name)}"? This removes the pack and its versions.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction data-testid="context-pack-delete-confirm"
              onClick={(e) => { e.preventDefault(); if (deleteTarget?.id) deleteMut.mutate(deleteTarget.id); }}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Diff — run context diff + pack version compare
// ---------------------------------------------------------------------------

const normalizeRunDiff = (diff: ContextRunDiff): { type: string; status: string }[] => {
  const out: { type: string; status: string }[] = [];
  const map = diff.component_diffs;
  if (map && typeof map === "object" && !Array.isArray(map)) {
    Object.entries<any>(map).forEach(([type, d]) => out.push({ type, status: String(d?.status ?? d?.change ?? "unknown") }));
  }
  if (Array.isArray(diff.components)) {
    diff.components.forEach((d) => out.push({ type: String(d?.component_type ?? "unknown"), status: String(d?.status ?? "unknown") }));
  }
  return out.filter((d) => d.status !== "unchanged");
};

const RunDiffView: React.FC<{ diff: ContextRunDiff }> = ({ diff }) => {
  if (diff.available === false) {
    return (
      <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-600" data-testid="context-diff-run-unavailable">
        Run context diff unavailable: {diff.reason || "a context snapshot is missing for one of the runs"}
      </div>
    );
  }
  const changes = normalizeRunDiff(diff);
  const groups: { label: string; status: string; testId: string; className: string }[] = [
    { label: "Added", status: "added", testId: "context-diff-added", className: "text-emerald-600" },
    { label: "Removed", status: "removed", testId: "context-diff-removed", className: "text-destructive" },
    { label: "Changed", status: "changed", testId: "context-diff-changed", className: "text-amber-600" },
  ];
  return (
    <div className="space-y-3" data-testid="context-diff-run-result">
      <div className="flex flex-wrap gap-2 text-[10px] font-mono text-muted-foreground">
        <Badge variant="outline" className="text-[9px]">hash A {shortHash(diff.context_hash_a)}</Badge>
        <Badge variant="outline" className="text-[9px]">hash B {shortHash(diff.context_hash_b)}</Badge>
        <Badge variant="secondary" className="text-[9px]">tok {nTokens(diff.estimated_tokens_a)} → {nTokens(diff.estimated_tokens_b)}</Badge>
      </div>
      {changes.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="context-diff-run-none">No component changes.</p>
      ) : (
        <div className="grid gap-2 md:grid-cols-3">
          {groups.map((g) => {
            const list = changes.filter((c) => c.status === g.status);
            return (
              <div key={g.status} className="space-y-0.5 text-xs" data-testid={g.testId}>
                <p className={`text-[10px] font-mono uppercase tracking-wider ${g.className}`}>{g.label}</p>
                {list.length === 0 ? <p className="text-muted-foreground">none</p> : list.map((c, i) => (
                  <p key={i} className={`font-mono ${g.className}`} data-testid={`${g.testId}-item`}>{nText(c.type)}</p>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

const DiffTab: React.FC<{ projectId: string | null }> = ({ projectId }) => {
  const [runA, setRunA] = useState("");
  const [runB, setRunB] = useState("");
  const [runDiff, setRunDiff] = useState<ContextRunDiff | null>(null);

  const runDiffMut = useMutation({
    mutationFn: async () => diffRunContexts(runA, runB),
    onSuccess: (res) => setRunDiff(res as ContextRunDiff),
    onError: (e) => toast.error(apiError(e)),
  });

  const { data: packsData } = useContextPacks(projectId);
  const packs = useMemo(() => asArray<ContextPack>(packsData), [packsData]);
  const [packId, setPackId] = useState("");
  useEffect(() => {
    setPackId((cur) => (cur && packs.some((p) => p.id === cur) ? cur : ((packs[0]?.id as string) || "")));
  }, [packs]);
  const effectivePackId = packId || (packs[0]?.id as string) || "";
  const { data: versionsData } = useContextPackVersions(effectivePackId || null);
  const versions = asArray<ContextPackVersion>(versionsData);

  return (
    <div className="space-y-5">
      <Card className="space-y-3 p-4" data-testid="context-diff-run-panel">
        <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Run context diff</h3>
        <div className="flex flex-wrap items-center gap-2">
          <Input placeholder="Run A id" value={runA} data-testid="context-diff-run-a" onChange={(e) => { setRunA(e.target.value); setRunDiff(null); }} className="w-64 font-mono text-xs" />
          <Input placeholder="Run B id" value={runB} data-testid="context-diff-run-b" onChange={(e) => { setRunB(e.target.value); setRunDiff(null); }} className="w-64 font-mono text-xs" />
          <Button size="sm" data-testid="context-diff-run-compare" disabled={!runA.trim() || !runB.trim() || runDiffMut.isPending} onClick={() => runDiffMut.mutate()}>
            <GitCompare className="mr-1.5 h-4 w-4" /> Compare runs
          </Button>
        </div>
        {runDiff && <RunDiffView diff={runDiff} />}
      </Card>

      <Card className="space-y-3 p-4" data-testid="context-diff-pack-panel">
        <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Pack version compare</h3>
        {!projectId ? (
          <p className="text-xs text-muted-foreground">Select a project to compare pack versions.</p>
        ) : packs.length === 0 ? (
          <p className="text-xs text-muted-foreground">No context packs available.</p>
        ) : (
          <>
            <Select value={effectivePackId} onValueChange={(v) => setPackId(v)}>
              <SelectTrigger className="w-72" data-testid="context-diff-pack-select"><SelectValue placeholder="Select pack" /></SelectTrigger>
              <SelectContent>
                {packs.map((p, i) => <SelectItem key={p.id || i} value={p.id as string}>{nText(p.name)}</SelectItem>)}
              </SelectContent>
            </Select>
            {effectivePackId && <PackVersionCompare packId={effectivePackId} versions={versions} />}
          </>
        )}
      </Card>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Budget — policies + live preview
// ---------------------------------------------------------------------------

type BudgetForm = {
  name: string;
  max_context_tokens: string;
  reserve_output_tokens: string;
  knowledge_budget: string;
  history_budget: string;
  file_budget: string;
  policy_budget: string;
  status: string;
};

const EMPTY_BUDGET: BudgetForm = {
  name: "", max_context_tokens: "", reserve_output_tokens: "", knowledge_budget: "",
  history_budget: "", file_budget: "", policy_budget: "", status: "active",
};

const budgetBody = (form: BudgetForm) => ({
  name: form.name.trim(),
  max_context_tokens: parseNumberOrNull(form.max_context_tokens),
  reserve_output_tokens: parseNumberOrNull(form.reserve_output_tokens),
  knowledge_budget: parseNumberOrNull(form.knowledge_budget),
  history_budget: parseNumberOrNull(form.history_budget),
  file_budget: parseNumberOrNull(form.file_budget),
  policy_budget: parseNumberOrNull(form.policy_budget),
  status: form.status,
});

const BUDGET_FIELDS: { key: keyof BudgetForm; label: string }[] = [
  { key: "max_context_tokens", label: "Max context tokens" },
  { key: "reserve_output_tokens", label: "Reserve output tokens" },
  { key: "knowledge_budget", label: "Knowledge budget" },
  { key: "history_budget", label: "History budget" },
  { key: "file_budget", label: "File budget" },
  { key: "policy_budget", label: "Policy budget" },
];

const STRATEGIES = ["FULL", "RECENT_MESSAGES", "EVIDENCE_VIEW", "SUMMARIZED"];

const BudgetPreviewView: React.FC<{ result: ContextPreviewResult }> = ({ result }) => {
  const assembly = result.assembly || null;
  if (!assembly) {
    return <p className="text-xs text-muted-foreground" data-testid="context-preview-empty">No preview assembly returned.</p>;
  }
  const components = asArray<ContextAssemblyComponent>(assembly.components);
  const excluded = asArray<ContextAssemblyComponent>(assembly.excluded_components);
  return (
    <div className="space-y-3" data-testid="context-preview-result">
      <div className="flex flex-wrap gap-3 text-xs">
        <span>est. total: <span className="font-mono" data-testid="context-preview-estimated">{nTokens(assembly.estimated_total_tokens)}</span></span>
        <span>known: <span className="font-mono" data-testid="context-preview-known">{nTokens(assembly.known_token_count)}</span></span>
        <span>unknown components: <span className="font-mono">{nText(assembly.unknown_token_component_count)}</span></span>
        <span>hash: <span className="font-mono">{shortHash(assembly.context_hash)}</span></span>
        <span>strategy: <span className="font-mono">{nText(assembly.strategy)}</span></span>
        <span>over budget: {assembly.over_budget ? <Badge variant="destructive">over budget</Badge> : <Badge variant="secondary">within budget</Badge>}</span>
        <span>budget applied: <span className="font-mono">{assembly.budget_policy_applied == null ? "n/a" : String(assembly.budget_policy_applied)}</span></span>
      </div>

      <div className="overflow-x-auto rounded-md border border-border">
        <Table data-testid="context-preview-components">
          <TableHeader>
            <TableRow className="bg-muted/40">
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Component</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Source class</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Order</TableHead>
              <TableHead className="text-[10px] font-mono uppercase tracking-wider">Included</TableHead>
              <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Tokens</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {components.map((c, i) => (
              <TableRow key={i} data-testid="context-preview-component">
                <TableCell className="text-xs">{nText(c.component_type)}</TableCell>
                <TableCell className="text-xs" data-testid="context-preview-component-source">{nText(c.source_class)}</TableCell>
                <TableCell className="font-mono text-xs">{nText(c.ordering)}</TableCell>
                <TableCell className="text-xs">{c.included === false ? "no" : c.included === true ? "yes" : "n/a"}</TableCell>
                <TableCell className="text-right font-mono text-xs" data-testid="context-preview-component-tokens">
                  {c.token_count != null ? c.token_count : nTokens(c.estimated_tokens)}
                </TableCell>
              </TableRow>
            ))}
            {components.length === 0 && (
              <TableRow><TableCell colSpan={5} className="text-xs text-muted-foreground">No components.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {excluded.length > 0 && (
        <div className="space-y-1 text-xs" data-testid="context-preview-excluded">
          <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Excluded</p>
          {excluded.map((c, i) => (
            <p key={i} className="font-mono text-muted-foreground">{nText(c.component_type)} · {nText(c.excluded_reason)}</p>
          ))}
        </div>
      )}

      {asArray<string>(assembly.warnings).length > 0 && (
        <div className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/10 p-2" data-testid="context-preview-warnings">
          {asArray<string>(assembly.warnings).map((w, i) => (
            <p key={i} className="text-[11px] text-amber-600">{nText(w)}</p>
          ))}
        </div>
      )}
    </div>
  );
};

const BudgetTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const qc = useQueryClient();
  const { data, isLoading, isError, error } = useContextBudgetPolicies(projectId);
  const policies = asArray<ContextBudgetPolicy>(data);

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ContextBudgetPolicy | null>(null);
  const [form, setForm] = useState<BudgetForm>(EMPTY_BUDGET);
  const [deleteTarget, setDeleteTarget] = useState<ContextBudgetPolicy | null>(null);

  const [previewPolicyId, setPreviewPolicyId] = useState("");
  const [previewSystem, setPreviewSystem] = useState("");
  const [previewMessages, setPreviewMessages] = useState("");
  const [previewStrategy, setPreviewStrategy] = useState(STRATEGIES[0]);
  const [previewMaxTokens, setPreviewMaxTokens] = useState("");
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewResult, setPreviewResult] = useState<ContextPreviewResult | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["context-budget-policies", projectId] });

  const openCreate = () => { setEditing(null); setForm(EMPTY_BUDGET); setOpen(true); };
  const openEdit = (p: ContextBudgetPolicy) => {
    setEditing(p);
    setForm({
      name: p.name || "",
      max_context_tokens: p.max_context_tokens == null ? "" : String(p.max_context_tokens),
      reserve_output_tokens: p.reserve_output_tokens == null ? "" : String(p.reserve_output_tokens),
      knowledge_budget: p.knowledge_budget == null ? "" : String(p.knowledge_budget),
      history_budget: p.history_budget == null ? "" : String(p.history_budget),
      file_budget: p.file_budget == null ? "" : String(p.file_budget),
      policy_budget: p.policy_budget == null ? "" : String(p.policy_budget),
      status: p.status || "active",
    });
    setOpen(true);
  };

  const createMut = useMutation({
    mutationFn: async () => createContextBudgetPolicy({ project_id: projectId, ...budgetBody(form) }),
    onSuccess: () => { toast.success("Budget policy created"); invalidate(); setOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const updateMut = useMutation({
    mutationFn: async () => updateContextBudgetPolicy(editing!.id as string, budgetBody(form)),
    onSuccess: () => { toast.success("Budget policy updated"); invalidate(); setOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const deleteMut = useMutation({
    mutationFn: async (id: string) => deleteContextBudgetPolicy(id),
    onSuccess: () => { toast.success("Budget policy deleted"); invalidate(); setDeleteTarget(null); },
    onError: (e) => toast.error(apiError(e)),
  });

  const previewMut = useMutation({
    mutationFn: async () => {
      let messages: any = [];
      const trimmed = previewMessages.trim();
      if (trimmed) {
        try {
          messages = JSON.parse(trimmed);
        } catch {
          throw new Error("Messages must be valid JSON.");
        }
      }
      return previewContextAssembly({
        project_id: projectId,
        system_prompt: previewSystem,
        messages,
        project_context: {},
        retrieved_context: [],
        attachments: [],
        tool_definitions: [],
        dataset_case: null,
        strategy: previewStrategy,
        context_options: {},
        ...(previewPolicyId ? { budget_policy_id: previewPolicyId } : {}),
        ...(parseNumberOrNull(previewMaxTokens) != null ? { max_context_tokens: parseNumberOrNull(previewMaxTokens) } : {}),
      });
    },
    onSuccess: (res) => { setPreviewError(null); setPreviewResult(res as ContextPreviewResult); },
    onError: (e) => { const message = apiError(e); setPreviewError(message); toast.error(message); },
  });

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">Budget policies cap context tokens per source. Blank fields are unknown, not zero.</p>
        <Button size="sm" data-testid="context-budget-policy-create" onClick={openCreate}>
          <Plus className="mr-1.5 h-4 w-4" /> New policy
        </Button>
      </div>

      {isLoading ? (
        <LoadingSpinner label="Loading budget policies…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : policies.length === 0 ? (
        <EmptyState
          icon={<SlidersHorizontal className="h-6 w-6" />}
          title="No budget policies"
          description="Create a budget policy to cap context tokens and reserve output tokens."
          testId="context-budget-policies-empty"
        />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table data-testid="context-budget-policy-table">
            <TableHeader>
              <TableRow className="bg-muted/50">
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Name</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Max ctx</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Reserve out</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Knowledge</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">History</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">File</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Policy</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Status</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {policies.map((p, i) => (
                <TableRow key={p.id || i} data-testid="context-budget-policy-row">
                  <TableCell className="text-xs font-medium" data-testid="context-budget-policy-name">{nText(p.name)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{nTokens(p.max_context_tokens)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{nTokens(p.reserve_output_tokens)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{nTokens(p.knowledge_budget)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{nTokens(p.history_budget)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{nTokens(p.file_budget)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{nTokens(p.policy_budget)}</TableCell>
                  <TableCell><StatusBadge value={p.status} /></TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button size="sm" variant="ghost" data-testid="context-budget-policy-edit" onClick={() => openEdit(p)}>
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" className="text-destructive" data-testid="context-budget-policy-delete" onClick={() => setDeleteTarget(p)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Card className="space-y-3 p-4" data-testid="context-preview-panel">
        <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Preview assembly</h3>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2">
            <label className="text-xs font-medium text-muted-foreground">System prompt</label>
            <Textarea data-testid="context-preview-system" value={previewSystem} onChange={(e) => setPreviewSystem(e.target.value)} rows={3} className="mt-1" />
          </div>
          <div className="md:col-span-2">
            <label className="text-xs font-medium text-muted-foreground">Messages (JSON array)</label>
            <Textarea data-testid="context-preview-messages" value={previewMessages} onChange={(e) => setPreviewMessages(e.target.value)} rows={3} className="mt-1 font-mono text-xs" placeholder='[{"role":"user","content":"hello"}]' />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Strategy</label>
            <Select value={previewStrategy} onValueChange={setPreviewStrategy}>
              <SelectTrigger className="mt-1" data-testid="context-preview-strategy"><SelectValue /></SelectTrigger>
              <SelectContent>
                {STRATEGIES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Budget policy</label>
            <Select value={previewPolicyId} onValueChange={setPreviewPolicyId}>
              <SelectTrigger className="mt-1" data-testid="context-preview-budget-policy"><SelectValue placeholder="None" /></SelectTrigger>
              <SelectContent>
                {policies.map((p, i) => <SelectItem key={p.id || i} value={(p.id as string) || `policy-${i}`}>{nText(p.name)}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Max context tokens</label>
            <Input type="number" data-testid="context-preview-max-tokens" value={previewMaxTokens} onChange={(e) => setPreviewMaxTokens(e.target.value)} className="mt-1 font-mono" placeholder="unknown" />
          </div>
          <div className="flex items-end">
            <Button size="sm" data-testid="context-preview-submit" disabled={previewMut.isPending} onClick={() => previewMut.mutate()}>
              {previewMut.isPending ? "Previewing…" : "Preview assembly"}
            </Button>
          </div>
        </div>
        <ErrorNote message={previewError} />
        {previewResult && <BudgetPreviewView result={previewResult} />}
      </Card>

      <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) setEditing(null); }}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto" data-testid="context-budget-policy-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">{editing ? "Edit budget policy" : "New budget policy"}</DialogTitle>
            <DialogDescription>Leave a field blank when the budget is unknown.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Name</label>
              <Input data-testid="context-budget-policy-name-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              {BUDGET_FIELDS.map((field) => (
                <div key={field.key}>
                  <label className="text-xs font-medium text-muted-foreground">{field.label}</label>
                  <Input
                    type="number"
                    data-testid={`context-budget-policy-${field.key}-input`}
                    value={form[field.key]}
                    onChange={(e) => setForm({ ...form, [field.key]: e.target.value })}
                    className="mt-1 font-mono"
                    placeholder="unknown"
                  />
                </div>
              ))}
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Status</label>
              <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
                <SelectTrigger className="mt-1" data-testid="context-budget-policy-status-input"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {["active", "archived"].map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button data-testid="context-budget-policy-submit" disabled={!form.name.trim() || createMut.isPending || updateMut.isPending}
              onClick={() => (editing ? updateMut.mutate() : createMut.mutate())}>
              {createMut.isPending || updateMut.isPending ? "Saving…" : editing ? "Save changes" : "Create policy"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleteTarget} onOpenChange={(v) => !v && setDeleteTarget(null)}>
        <AlertDialogContent data-testid="context-budget-policy-delete-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete budget policy</AlertDialogTitle>
            <AlertDialogDescription>Delete "{nText(deleteTarget?.name)}"? This cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction data-testid="context-budget-policy-delete-confirm"
              onClick={(e) => { e.preventDefault(); if (deleteTarget?.id) deleteMut.mutate(deleteTarget.id); }}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const NoProject: React.FC = () => (
  <EmptyState
    icon={<Layers className="h-6 w-6" />}
    title="No project selected"
    description="Select a project to manage context packs and budget policies."
    testId="context-lab-no-project"
  />
);

export default function ContextLab() {
  const { activeId } = useProjects();
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run") || "";

  const setRun = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("run", value); else next.delete("run");
    setSearchParams(next, { replace: true });
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="context-lab-page">
      <SectionHeader
        title="Context Lab"
        caption="Inspect the exact context snapshot sent to a model, manage versioned context packs, diff runs or pack versions, and preview assembly against budget policies."
        action={
          runId ? (
            <Link to={`/trace?run=${runId}`} className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline" data-testid="context-trace-link">
              View trace <ExternalLink className="h-3 w-3" />
            </Link>
          ) : undefined
        }
      />

      <Tabs defaultValue="inspector">
        <TabsList data-testid="context-lab-tabs">
          <TabsTrigger value="inspector" data-testid="context-lab-tab-inspector">Inspector</TabsTrigger>
          <TabsTrigger value="packs" data-testid="context-lab-tab-packs">Packs</TabsTrigger>
          <TabsTrigger value="diff" data-testid="context-lab-tab-diff">Diff</TabsTrigger>
          <TabsTrigger value="budget" data-testid="context-lab-tab-budget">Budget</TabsTrigger>
        </TabsList>
        <TabsContent value="inspector" className="mt-4">
          <InspectorTab runId={runId} setRun={setRun} />
        </TabsContent>
        <TabsContent value="packs" className="mt-4">
          {activeId ? <PacksTab projectId={activeId} /> : <NoProject />}
        </TabsContent>
        <TabsContent value="diff" className="mt-4">
          <DiffTab projectId={activeId} />
        </TabsContent>
        <TabsContent value="budget" className="mt-4">
          {activeId ? <BudgetTab projectId={activeId} /> : <NoProject />}
        </TabsContent>
      </Tabs>
    </div>
  );
}
