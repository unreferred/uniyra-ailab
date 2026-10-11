import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, Check, FileText, GitCompare, Loader2, ShieldAlert } from "lucide-react";
import {
  apiError,
  approvePrdVersion,
  comparePrdVersions,
  generatePrd,
  getPrdReadiness,
  getPrdVersion,
  listPrdVersions,
} from "@/lib/api";
import { PrdDiff, PrdDiffSection, PrdVersion, PrdVersionSummary } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { MarkdownView } from "@/components/common/Markdown";
import { nText } from "@/components/experiment/format";
import { cn } from "@/lib/utils";

export interface PrdViewProps {
  projectId: string;
  conversationId?: string;
  className?: string;
}

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

function normState(value?: string | null): string {
  return String(value || "").toUpperCase();
}

function readinessBadgeVariant(state?: string | null): BadgeVariant {
  switch (normState(state)) {
    case "READY":
      return "default";
    case "NEEDS_VERIFICATION":
      return "secondary";
    case "BLOCKED":
      return "destructive";
    case "INCOMPLETE":
      return "outline";
    default:
      return "outline";
  }
}

function statusBadgeVariant(status?: string | null): BadgeVariant {
  const s = normState(status);
  if (s === "APPROVED") return "default";
  if (s === "DRAFT") return "secondary";
  if (s === "SUPERSEDED" || s === "REJECTED") return "destructive";
  return "outline";
}

function diffBadgeVariant(status?: string | null): BadgeVariant {
  const s = String(status || "").toLowerCase();
  if (s === "added") return "default";
  if (s === "removed") return "destructive";
  if (s === "changed") return "secondary";
  return "outline";
}

function lineClass(op?: string | null): string {
  const s = String(op || "").toLowerCase();
  if (s === "added" || s === "add" || s === "+" || s === "insert") {
    return "bg-emerald-500/10 text-emerald-500";
  }
  if (s === "removed" || s === "remove" || s === "-" || s === "delete") {
    return "bg-destructive/10 text-destructive";
  }
  return "text-muted-foreground";
}

function lineMarker(op?: string | null): string {
  const s = String(op || "").toLowerCase();
  if (s === "added" || s === "add" || s === "+" || s === "insert") return "+";
  if (s === "removed" || s === "remove" || s === "-" || s === "delete") return "-";
  return " ";
}

function itemText(item: any): string {
  if (!item || typeof item !== "object") return "";
  return String(item.value ?? item.text ?? item.statement ?? "").trim();
}

function itemTopic(item: any): string {
  if (!item || typeof item !== "object") return "";
  return String(item.topic_key ?? item.kind ?? "").trim();
}

function conflictText(conflict: any): string {
  if (!conflict || typeof conflict !== "object") return "";
  const prev = conflict.previous_value;
  const next = conflict.new_value;
  if (prev != null && next != null) return `${prev} → ${next}`;
  return String(conflict.value ?? conflict.resolution ?? "").trim();
}

function truncate(value: unknown, max: number): string {
  const text = value == null ? "" : String(value);
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1))}…`;
}

function sourceRevisionText(rev?: Record<string, any> | null): string {
  if (!rev || typeof rev !== "object") return "n/a";
  const entries = Object.entries(rev);
  if (entries.length === 0) return "n/a";
  return entries
    .slice(0, 4)
    .map(([key, value]) => `${key}=${truncate(value, 24)}`)
    .join(" · ");
}

function PlanningCallout({
  testId,
  title,
  tone,
  children,
}: {
  testId: string;
  title: string;
  tone: "assumption" | "verification" | "conflict" | "question";
  children: React.ReactNode;
}): React.JSX.Element {
  const toneClass =
    tone === "assumption" || tone === "conflict"
      ? "border-amber-500/40 bg-amber-500/5"
      : "border-sky-500/40 bg-sky-500/5";
  const headingClass = tone === "assumption" || tone === "conflict" ? "text-amber-500" : "text-sky-500";
  return (
    <div data-testid={testId} className={cn("rounded-md border p-2", toneClass)}>
      <h5 className={cn("flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider", headingClass)}>
        {tone === "conflict" ? <ShieldAlert className="h-3 w-3" /> : tone === "assumption" ? <AlertTriangle className="h-3 w-3" /> : <FileText className="h-3 w-3" />}
        {title}
      </h5>
      <div className="mt-1 space-y-1">{children}</div>
    </div>
  );
}

export function PrdView({ projectId, conversationId, className }: PrdViewProps): React.JSX.Element {
  const qc = useQueryClient();
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [fromId, setFromId] = useState<string>("");
  const [toId, setToId] = useState<string>("");

  const readinessQuery = useQuery({
    queryKey: ["prd-readiness", projectId, conversationId],
    queryFn: () => getPrdReadiness(projectId, conversationId),
    retry: false,
    enabled: !!projectId,
  });

  const versionsQuery = useQuery({
    queryKey: ["prd-versions", projectId],
    queryFn: () => listPrdVersions(projectId),
    retry: false,
    enabled: !!projectId,
  });

  const versionQuery = useQuery({
    queryKey: ["prd-version", selectedVersionId],
    queryFn: () => getPrdVersion(selectedVersionId as string, projectId),
    retry: false,
    enabled: !!selectedVersionId,
  });

  const versionList = useMemo<PrdVersionSummary[]>(() => {
    const items = versionsQuery.data?.versions || [];
    return [...items].sort((a, b) => (b.version ?? 0) - (a.version ?? 0));
  }, [versionsQuery.data]);

  useEffect(() => {
    if (versionList.length === 0) return;
    const ids = versionList.map((v) => v.id).filter((id): id is string => !!id);
    if (ids.length === 0) return;
    setSelectedVersionId((prev) => (prev && ids.includes(prev) ? prev : ids[0]));
    setToId((prev) => (prev && ids.includes(prev) ? prev : ids[0]));
    setFromId((prev) => (prev && ids.includes(prev) ? prev : ids[1] || ids[0]));
  }, [versionList]);

  const generateMutation = useMutation({
    mutationFn: () => generatePrd({ project_id: projectId, conversation_id: conversationId ?? null }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["prd-versions", projectId] });
      qc.invalidateQueries({ queryKey: ["prd-readiness", projectId, conversationId] });
      if (created?.id) {
        qc.setQueryData(["prd-version", created.id], created);
        setSelectedVersionId(created.id);
        setToId(created.id);
      }
      toast.success("PRD generated");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const approveMutation = useMutation({
    mutationFn: (versionId: string) => approvePrdVersion(versionId, projectId),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ["prd-versions", projectId] });
      if (updated?.id) qc.setQueryData(["prd-version", updated.id], updated);
      toast.success("PRD version approved");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const compareMutation = useMutation({
    mutationFn: () => comparePrdVersions(projectId, fromId, toId),
    onError: (e) => toast.error(apiError(e)),
  });

  const readiness = readinessQuery.data;
  const dimensions = readiness?.dimensions || [];
  const selectedSummary = versionList.find((v) => v.id === selectedVersionId);
  const fullVersion = versionQuery.data;
  const version: PrdVersion | PrdVersionSummary | undefined = fullVersion ?? selectedSummary;
  const planningState: any = fullVersion?.content?.planning_state || null;

  const assumptions: any[] = planningState?.assumptions || [];
  const verificationItems: any[] = planningState?.verification_items || [];
  const conflicts: any[] = planningState?.conflicts || [];
  const openQuestions: any[] = planningState?.open_questions || [];

  const diff: PrdDiff | undefined = compareMutation.data;
  const canCompare = versionList.length >= 2 && !!fromId && !!toId;

  const renderDiffSection = (section: PrdDiffSection, index: number) => {
    const key = section.key || `section-${index}`;
    return (
      <div
        key={key}
        data-testid={`prd-diff-section-${key}`}
        className="overflow-hidden rounded border border-border bg-background"
      >
        <div className="flex items-center justify-between gap-2 border-b border-border px-2 py-1">
          <span className="truncate text-xs font-medium text-foreground">{nText(section.title || key)}</span>
          <Badge variant={diffBadgeVariant(section.status)} className="px-1 py-0 text-[9px]">
            {nText(section.status)}
          </Badge>
        </div>
        {(section.line_diff || []).length > 0 && (
          <div className="py-0.5">
            {(section.line_diff || []).map((line, lineIndex) => (
              <div
                key={`${key}-line-${lineIndex}`}
                className={cn("whitespace-pre-wrap break-all px-2 py-0.5 font-mono text-[10px]", lineClass(line.op))}
              >
                <span className="mr-1 select-none">{lineMarker(line.op)}</span>
                {nText(line.text)}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div data-testid="prd-view" className={cn("flex h-full min-h-0 flex-col", className)}>
      <ScrollArea className="flex-1">
        <div className="flex flex-col gap-4 p-1 pr-3">
          {/* Readiness */}
          <section data-testid="prd-readiness" className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <h3 className="font-display text-sm font-semibold tracking-tight text-foreground">PRD Readiness</h3>
              {readinessQuery.isFetching && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
            </div>
            {readinessQuery.isLoading && <LoadingSpinner label="Checking PRD readiness…" />}
            {readinessQuery.isError && <ErrorNote message={apiError(readinessQuery.error)} />}
            {readiness && (
              <>
                <div className="divide-y divide-border rounded-md border border-border bg-card">
                  {dimensions.map((dim, index) => {
                    const key = dim.key || `dim-${index}`;
                    return (
                      <div
                        key={key}
                        data-testid={`prd-readiness-dim-${key}`}
                        className="flex items-center justify-between gap-3 px-2.5 py-1.5"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-xs font-medium text-foreground">{nText(dim.label || key)}</p>
                          <p className="truncate text-[10px] text-muted-foreground">
                            {nText(dim.detail)} · {dim.item_count ?? 0} items
                          </p>
                        </div>
                        <span data-testid={`prd-readiness-state-${key}`} className="shrink-0">
                          <Badge variant={readinessBadgeVariant(dim.state)} className="px-1.5 py-0 text-[10px]">
                            {nText(dim.state)}
                          </Badge>
                        </span>
                      </div>
                    );
                  })}
                  {dimensions.length === 0 && (
                    <p className="px-2.5 py-2 text-[11px] text-muted-foreground">No readiness dimensions reported.</p>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <span
                    data-testid="prd-blockers"
                    className="inline-flex items-center gap-1 rounded border border-border bg-muted/30 px-2 py-0.5 font-mono text-muted-foreground"
                  >
                    <ShieldAlert className="h-3 w-3" />
                    Blockers: {readiness.blocker_count ?? (readiness.blockers || []).length}
                  </span>
                  <span data-testid="prd-ready-summary" className="text-muted-foreground">
                    <span className={cn("font-medium", readiness.ready ? "text-emerald-500" : "text-amber-500")}>
                      {readiness.ready ? "Ready" : "Not ready"}
                    </span>
                    {readiness.summary ? ` · ${readiness.summary}` : ""}
                  </span>
                </div>
              </>
            )}
          </section>

          <Separator />

          {/* Generate */}
          <section className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="font-display text-sm font-semibold tracking-tight text-foreground">Living PRD</h3>
              <p className="text-[10px] text-muted-foreground">
                Compile the current planning state into a versioned document.
              </p>
            </div>
            <Button
              size="sm"
              data-testid="prd-generate"
              disabled={generateMutation.isPending}
              onClick={() => generateMutation.mutate()}
            >
              {generateMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <FileText className="h-3.5 w-3.5" />
              )}
              Generate PRD
            </Button>
          </section>

          <Separator />

          {/* Versions */}
          <section className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <h3 className="font-display text-sm font-semibold tracking-tight text-foreground">Versions</h3>
              {versionsQuery.isFetching && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
            </div>
            {versionsQuery.isLoading && <LoadingSpinner label="Loading versions…" />}
            {versionsQuery.isError && <ErrorNote message={apiError(versionsQuery.error)} />}
            {!versionsQuery.isLoading && !versionsQuery.isError && versionList.length === 0 && (
              <div
                data-testid="prd-empty"
                className="rounded-md border border-dashed border-border bg-card/40 px-3 py-4 text-center"
              >
                <p className="text-xs font-medium text-foreground">No PRD versions yet</p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">
                  Generate a PRD to start the living document for this project.
                </p>
              </div>
            )}
            {versionList.length > 0 && (
              <div data-testid="prd-version-list" className="divide-y divide-border rounded-md border border-border bg-card">
                {versionList.map((item) => {
                  const isSelected = item.id === selectedVersionId;
                  return (
                    <button
                      key={item.id || item.version}
                      type="button"
                      data-testid={`prd-version-${item.version}`}
                      onClick={() => item.id && setSelectedVersionId(item.id)}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left transition-colors",
                        isSelected ? "bg-accent/60" : "hover:bg-accent/40"
                      )}
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="font-mono text-xs font-medium text-foreground">v{item.version}</span>
                        <span data-testid={`prd-version-status-${item.id}`} className="shrink-0">
                          <Badge variant={statusBadgeVariant(item.status)} className="px-1.5 py-0 text-[9px]">
                            {nText(item.status)}
                          </Badge>
                        </span>
                        {item.title && <span className="truncate text-[11px] text-muted-foreground">{item.title}</span>}
                      </span>
                      <span className="shrink-0 font-mono text-[9px] text-muted-foreground">{nText(item.created_at)}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </section>

          {/* Selected version viewer */}
          {version && (
            <section
              data-testid="prd-version-viewer"
              className="space-y-2 rounded-md border border-border bg-card p-3"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <h4 className="truncate font-display text-sm font-semibold text-foreground">
                    {version.title || `PRD v${version.version}`}
                  </h4>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <Badge variant={statusBadgeVariant(version.status)} className="text-[10px]">
                      {nText(version.status)}
                    </Badge>
                    {fullVersion?.compiler_version && (
                      <Badge variant="outline" className="font-mono text-[9px]">
                        compiler {fullVersion.compiler_version}
                      </Badge>
                    )}
                  </div>
                </div>
                {version.status !== "APPROVED" && version.id && (
                  <Button
                    size="sm"
                    variant="outline"
                    data-testid={`prd-approve-${version.id}`}
                    disabled={approveMutation.isPending}
                    onClick={() => approveMutation.mutate(version.id as string)}
                  >
                    {approveMutation.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Check className="h-3.5 w-3.5" />
                    )}
                    Approve
                  </Button>
                )}
              </div>

              <div
                data-testid="prd-version-meta"
                className="grid grid-cols-[92px_1fr] gap-x-2 gap-y-0.5 font-mono text-[10px] text-muted-foreground"
              >
                <span>version</span>
                <span className="text-foreground">v{nText(version.version)}</span>
                <span>compiler</span>
                <span className="break-all text-foreground">{nText(fullVersion?.compiler_version)}</span>
                <span>content hash</span>
                <span className="break-all text-foreground">{version.content_hash ? truncate(version.content_hash, 16) : "n/a"}</span>
                <span>source rev</span>
                <span className="break-all text-foreground">{sourceRevisionText(version.source_revision)}</span>
                <span>created</span>
                <span className="text-foreground">{nText(version.created_at)}</span>
              </div>

              <div data-testid="prd-markdown" className="rounded border border-border bg-background p-2 text-xs text-foreground">
                <MarkdownView content={fullVersion?.markdown || ""} />
              </div>

              {assumptions.length > 0 && (
                <PlanningCallout testId="prd-assumptions" title="Assumptions (not confirmed facts)" tone="assumption">
                  {assumptions.map((item, index) => (
                    <div key={item?.id || `assumption-${index}`} className="text-[11px] text-foreground">
                      {itemTopic(item) && <span className="mr-1 font-mono text-[9px] text-muted-foreground">{itemTopic(item)}</span>}
                      {nText(itemText(item))}
                    </div>
                  ))}
                </PlanningCallout>
              )}

              {verificationItems.length > 0 && (
                <PlanningCallout testId="prd-verification" title="Needs verification (not confirmed)" tone="verification">
                  {verificationItems.map((item, index) => (
                    <div key={item?.id || `verification-${index}`} className="text-[11px] text-foreground">
                      {itemTopic(item) && <span className="mr-1 font-mono text-[9px] text-muted-foreground">{itemTopic(item)}</span>}
                      {nText(itemText(item))}
                    </div>
                  ))}
                </PlanningCallout>
              )}

              {conflicts.length > 0 && (
                <PlanningCallout testId="prd-conflicts" title="Open conflicts" tone="conflict">
                  {conflicts.map((conflict, index) => (
                    <div key={conflict?.id || `conflict-${index}`} className="text-[11px] text-foreground">
                      {nText(conflictText(conflict))}
                    </div>
                  ))}
                </PlanningCallout>
              )}

              {openQuestions.length > 0 && (
                <PlanningCallout testId="prd-open-questions" title="Open questions" tone="question">
                  {openQuestions.map((item, index) => (
                    <div key={item?.id || `question-${index}`} className="text-[11px] text-foreground">
                      {nText(itemText(item))}
                    </div>
                  ))}
                </PlanningCallout>
              )}

              <p className="text-[10px] text-muted-foreground">
                An approved PRD is a versioned planning document — it is not a certified asset and does not couple to
                V7 certification.
              </p>
            </section>
          )}

          <Separator />

          {/* Diff */}
          <section data-testid="prd-compare" className="space-y-2">
            <div className="flex items-center gap-2">
              <GitCompare className="h-3.5 w-3.5 text-muted-foreground" />
              <h3 className="font-display text-sm font-semibold tracking-tight text-foreground">Compare versions</h3>
              {compareMutation.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
            </div>
            {versionList.length < 2 && (
              <p data-testid="prd-compare-hint" className="text-[10px] text-muted-foreground">
                At least two PRD versions are required to compare. Generate another version first.
              </p>
            )}
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-0.5 text-[10px] text-muted-foreground">
                <span>From</span>
                <select
                  data-testid="prd-compare-from"
                  value={fromId}
                  disabled={versionList.length < 2}
                  onChange={(event) => setFromId(event.target.value)}
                  className="h-7 rounded border border-border bg-background px-2 font-mono text-[11px] text-foreground disabled:opacity-50"
                >
                  {versionList.map((item) => (
                    <option key={item.id} value={item.id || ""}>
                      v{item.version}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-0.5 text-[10px] text-muted-foreground">
                <span>To</span>
                <select
                  data-testid="prd-compare-to"
                  value={toId}
                  disabled={versionList.length < 2}
                  onChange={(event) => setToId(event.target.value)}
                  className="h-7 rounded border border-border bg-background px-2 font-mono text-[11px] text-foreground disabled:opacity-50"
                >
                  {versionList.map((item) => (
                    <option key={item.id} value={item.id || ""}>
                      v{item.version}
                    </option>
                  ))}
                </select>
              </label>
              <Button
                size="sm"
                variant="outline"
                data-testid="prd-compare-run"
                disabled={!canCompare || compareMutation.isPending}
                onClick={() => compareMutation.mutate()}
              >
                Compare
              </Button>
            </div>

            {compareMutation.isError && <ErrorNote message={apiError(compareMutation.error)} />}

            {diff && (
              <div data-testid="prd-diff" className="space-y-2">
                <div className="flex flex-wrap items-center gap-2 rounded border border-border bg-muted/20 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                  <span>
                    v{nText(diff.from?.version)} → v{nText(diff.to?.version)}
                  </span>
                  {Object.entries(diff.summary || {}).map(([key, value]) => (
                    <Badge key={key} variant="outline" className="px-1 py-0 text-[9px]">
                      {key}: {value}
                    </Badge>
                  ))}
                </div>

                <div className="space-y-1.5">
                  {(diff.sections || []).map((section, index) => renderDiffSection(section, index))}
                  {(diff.sections || []).length === 0 && (
                    <p className="text-[10px] text-muted-foreground">No section-level changes were reported.</p>
                  )}
                </div>

                <div data-testid="prd-diff-affected" className="rounded border border-border bg-card p-2">
                  <h5 className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                    Affected sections
                  </h5>
                  {(diff.affected_sections || []).length > 0 ? (
                    <ul className="mt-1 space-y-0.5">
                      {(diff.affected_sections || []).map((section, index) => (
                        <li key={section.key || `affected-${index}`} className="text-[11px] text-foreground">
                          {nText(section.title || section.key)}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-1 text-[10px] text-muted-foreground">No affected sections.</p>
                  )}
                  <div className="mt-1.5 flex flex-wrap gap-1.5 border-t border-border pt-1.5 font-mono text-[9px] text-muted-foreground">
                    {Object.entries(diff.summary || {}).map(([key, value]) => (
                      <span key={`summary-${key}`}>
                        {key}: {value}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </section>
        </div>
      </ScrollArea>
    </div>
  );
}

export default PrdView;
