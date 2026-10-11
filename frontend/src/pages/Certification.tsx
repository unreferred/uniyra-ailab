import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BadgeCheck, CheckCircle2, ClipboardCheck, Info, Plus, XCircle } from "lucide-react";
import {
  apiError,
  createCertificationCandidate,
  evaluateCertificationReadiness,
  getCertificationCandidate,
  getCertificationPolicy,
  listCertificationCandidates,
} from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { CertificationCandidate, CertificationReadiness } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState, ErrorNote, LoadingSpinner, SectionHeader } from "@/components/common/Common";
import { nText } from "@/components/experiment/format";
import { cn } from "@/lib/utils";

const SUBJECT_TYPES = ["model", "configuration", "experiment", "run", "dataset", "pipeline"];

function stateClasses(state?: string | null): string {
  switch ((state || "").toUpperCase()) {
    case "READY_FOR_CERTIFICATION":
      return "border-emerald-500/40 bg-emerald-500/15 text-emerald-600";
    case "NOT_READY":
      return "border-amber-500/40 bg-amber-500/15 text-amber-600";
    case "BLOCKED":
      return "border-destructive/40 bg-destructive/15 text-destructive";
    case "DRAFT":
    default:
      return "border-border bg-muted text-muted-foreground";
  }
}

const parseIds = (s: string): string[] =>
  s.split(/[\s,]+/).map((t) => t.trim()).filter(Boolean);

function subjectSummary(subject?: Record<string, any> | null): { label: string; value: string }[] {
  if (!subject || typeof subject !== "object") return [];
  return Object.entries(subject)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => ({ label: k, value: typeof v === "object" ? JSON.stringify(v) : String(v) }));
}

function evidenceEntries(evidence?: Record<string, any> | null): { label: string; value: string }[] {
  if (!evidence || typeof evidence !== "object") return [];
  return Object.entries(evidence).map(([k, v]) => {
    if (Array.isArray(v)) return { label: k, value: v.length === 0 ? "n/a" : `${v.length}` };
    if (v === null || v === undefined || v === "") return { label: k, value: "n/a" };
    if (typeof v === "object") return { label: k, value: JSON.stringify(v) };
    return { label: k, value: String(v) };
  });
}

// Readiness criteria may be an ordered array or an object keyed by criterion.
function criteriaList(raw: any): any[] {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  if (typeof raw === "object") {
    return Object.entries(raw).map(([k, v]) =>
      v && typeof v === "object" ? { key: k, ...(v as Record<string, any>) } : { key: k, passed: v }
    );
  }
  return [];
}

function snapshotList(raw: any): any[] {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  if (typeof raw === "object") return Object.values(raw);
  return [];
}

// Bundle snapshots may be attached directly to readiness or nested under a
// bundle object; both shapes are tolerated. Missing snapshots return [].
function readinessSnapshot(readiness: any, key: string): any[] {
  if (!readiness || typeof readiness !== "object") return [];
  const direct = readiness[key];
  if (direct) return snapshotList(direct);
  const nested = readiness.bundle || readiness.bundle_snapshot;
  if (nested && typeof nested === "object" && nested[key]) return snapshotList(nested[key]);
  return [];
}

function driftFailures(readiness: any): any[] {
  const direct = snapshotList(readiness?.unresolved_drift_failures);
  if (direct.length > 0) return direct;
  const nested = readiness?.bundle || readiness?.bundle_snapshot;
  return snapshotList(nested?.unresolved_drift_failures);
}

const EVIDENCE_SNAPSHOTS: { key: string; title: string }[] = [
  { key: "agent_versions", title: "Agent versions" },
  { key: "prompt_versions", title: "Prompt versions" },
  { key: "context_pack_versions", title: "Context pack versions" },
  { key: "drift_analyses", title: "Drift analyses" },
];

const CreateCandidateDialog: React.FC<{
  projectId: string;
  onOpenChange: (open: boolean) => void;
  onCreated: (id: string) => void;
}> = ({ projectId, onOpenChange, onCreated }) => {
  const qc = useQueryClient();
  const [label, setLabel] = useState("");
  const [subjectType, setSubjectType] = useState("model");
  const [subjectId, setSubjectId] = useState("");
  const [runIds, setRunIds] = useState("");
  const [evaluationIds, setEvaluationIds] = useState("");
  const [failureIds, setFailureIds] = useState("");
  const [agentVersionIds, setAgentVersionIds] = useState("");
  const [promptVersionIds, setPromptVersionIds] = useState("");
  const [contextPackVersionIds, setContextPackVersionIds] = useState("");
  const [driftAnalysisIds, setDriftAnalysisIds] = useState("");
  const [environment, setEnvironment] = useState("");
  const [notes, setNotes] = useState("");
  const [envError, setEnvError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      let environmentObj: Record<string, any> | undefined;
      if (environment.trim()) {
        try {
          const parsed = JSON.parse(environment);
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("Environment must be a JSON object.");
          }
          environmentObj = parsed;
        } catch (e: any) {
          setEnvError(e?.message || "Invalid JSON.");
          throw e;
        }
      }
      const subject: Record<string, any> = { type: subjectType };
      if (subjectId.trim()) {
        subject.id = subjectId.trim();
        subject[`${subjectType}_id`] = subjectId.trim();
      }
      return createCertificationCandidate({
        project_id: projectId,
        label: label.trim(),
        subject,
        evidence: {
          run_ids: parseIds(runIds),
          evaluation_ids: parseIds(evaluationIds),
          failure_ids: parseIds(failureIds),
          agent_version_ids: parseIds(agentVersionIds),
          prompt_version_ids: parseIds(promptVersionIds),
          context_pack_version_ids: parseIds(contextPackVersionIds),
          drift_analysis_ids: parseIds(driftAnalysisIds),
        },
        environment: environmentObj,
        notes: notes.trim() || undefined,
      });
    },
    onSuccess: (data: CertificationCandidate) => {
      toast.success("Certification candidate created");
      qc.invalidateQueries({ queryKey: ["certification-candidates"] });
      if (data?.id) onCreated(data.id);
      onOpenChange(false);
    },
    onError: (e) => {
      if (!envError) toast.error(apiError(e));
    },
  });

  return (
    <Dialog open onOpenChange={(o) => onOpenChange(o)}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg" data-testid="certification-create-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">New certification candidate</DialogTitle>
          <DialogDescription>
            Prepare a subject and its evidence for readiness review. This creates a candidate only — it does not certify anything.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Label</label>
            <Input data-testid="certification-label" value={label} onChange={(e) => setLabel(e.target.value)} className="mt-1" placeholder="e.g. Support agent v3" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Subject type</label>
              <Select value={subjectType} onValueChange={setSubjectType}>
                <SelectTrigger className="mt-1" data-testid="certification-subject-type"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {SUBJECT_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Subject ID (optional)</label>
              <Input data-testid="certification-subject-id" value={subjectId} onChange={(e) => setSubjectId(e.target.value)} className="mt-1 font-mono text-xs" placeholder="model_…" />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Run IDs</label>
              <Textarea data-testid="certification-evidence-run-ids" value={runIds} onChange={(e) => setRunIds(e.target.value)} rows={3} className="mt-1 font-mono text-[11px]" placeholder="comma / newline" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Evaluation IDs</label>
              <Textarea data-testid="certification-evidence-evaluation-ids" value={evaluationIds} onChange={(e) => setEvaluationIds(e.target.value)} rows={3} className="mt-1 font-mono text-[11px]" placeholder="comma / newline" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Failure IDs</label>
              <Textarea data-testid="certification-evidence-failure-ids" value={failureIds} onChange={(e) => setFailureIds(e.target.value)} rows={3} className="mt-1 font-mono text-[11px]" placeholder="comma / newline" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Agent version IDs</label>
              <Textarea data-testid="certification-evidence-agent-versions" value={agentVersionIds} onChange={(e) => setAgentVersionIds(e.target.value)} rows={3} className="mt-1 font-mono text-[11px]" placeholder="comma / newline" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Prompt version IDs</label>
              <Textarea data-testid="certification-evidence-prompt-versions" value={promptVersionIds} onChange={(e) => setPromptVersionIds(e.target.value)} rows={3} className="mt-1 font-mono text-[11px]" placeholder="comma / newline" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Context pack version IDs</label>
              <Textarea data-testid="certification-evidence-context-versions" value={contextPackVersionIds} onChange={(e) => setContextPackVersionIds(e.target.value)} rows={3} className="mt-1 font-mono text-[11px]" placeholder="comma / newline" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Drift analysis IDs</label>
              <Textarea data-testid="certification-evidence-drift" value={driftAnalysisIds} onChange={(e) => setDriftAnalysisIds(e.target.value)} rows={3} className="mt-1 font-mono text-[11px]" placeholder="comma / newline" />
            </div>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Environment (JSON, optional)</label>
            <Textarea data-testid="certification-environment" value={environment} onChange={(e) => { setEnvironment(e.target.value); setEnvError(null); }} rows={3} className="mt-1 font-mono text-[11px]" placeholder='{"model":"…","region":"…"}' />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Notes</label>
            <Textarea data-testid="certification-notes" value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} className="mt-1 text-xs" />
          </div>
          <ErrorNote message={envError || (create.isError && !envError ? apiError(create.error) : null)} />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button data-testid="certification-create-submit" disabled={!label.trim() || create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? "Creating…" : "Create candidate"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default function Certification() {
  const { activeId, activeProject, projects } = useProjects();
  const qc = useQueryClient();
  const [projectId, setProjectId] = useState<string>(activeId || "");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [readinessOverride, setReadinessOverride] = useState<CertificationReadiness | null>(null);

  useEffect(() => {
    if (activeId && !projectId) setProjectId(activeId);
  }, [activeId, projectId]);

  const { data: policy } = useQuery<any>({
    queryKey: ["certification-policy"],
    queryFn: async () => getCertificationPolicy(),
  });

  const { data: listData, isLoading, isError, error } = useQuery<any>({
    queryKey: ["certification-candidates", projectId],
    queryFn: async () => listCertificationCandidates({ project_id: projectId || undefined }),
    enabled: !!projectId,
  });
  const candidates = useMemo<CertificationCandidate[]>(() => {
    const raw: any = listData;
    if (Array.isArray(raw)) return raw as CertificationCandidate[];
    return (raw?.items ?? []) as CertificationCandidate[];
  }, [listData]);

  useEffect(() => {
    setSelectedId((current) => {
      if (current && candidates.some((c) => c.id === current)) return current;
      return candidates[0]?.id ?? null;
    });
  }, [candidates]);

  useEffect(() => {
    setReadinessOverride(null);
  }, [selectedId]);

  const { data: detailData, isError: detailError, error: detailErr } = useQuery<any>({
    queryKey: ["certification-candidate", selectedId],
    queryFn: async () => getCertificationCandidate(selectedId as string),
    enabled: !!selectedId,
  });

  const listCandidate = candidates.find((c) => c.id === selectedId) || null;
  const candidate: CertificationCandidate | null = detailData || listCandidate;
  const readiness: CertificationReadiness | null = readinessOverride || candidate?.readiness || null;
  const state = readiness?.state || candidate?.state || "DRAFT";
  const criteria = criteriaList(readiness?.criteria);
  const unresolved = readiness?.unresolved_failures || [];
  const drift = driftFailures(readiness);
  const snapshots = EVIDENCE_SNAPSHOTS.map((section) => ({
    ...section,
    entries: readinessSnapshot(readiness, section.key),
  }));
  const subject = subjectSummary(candidate?.subject);
  const evidence = evidenceEntries(candidate?.evidence);

  const evaluate = useMutation({
    mutationFn: async () => evaluateCertificationReadiness(selectedId as string),
    onSuccess: (data: CertificationReadiness) => {
      toast.success("Readiness evaluated");
      setReadinessOverride(data);
      qc.setQueryData(["certification-candidate", selectedId], (old: any) => ({ ...(old || {}), readiness: data, state: data?.state }));
      qc.invalidateQueries({ queryKey: ["certification-candidates"] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="certification-page">
      <SectionHeader
        title="Certification"
        caption={`Prepare certification candidates for "${activeProject?.name || "this project"}" and evaluate their readiness.`}
        action={
          <Button size="sm" data-testid="certification-create-open" disabled={!projectId} onClick={() => setCreateOpen(true)}>
            <Plus className="mr-1.5 h-4 w-4" /> New candidate
          </Button>
        }
      />

      <Card className="p-3" data-testid="certification-policy-version">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <ClipboardCheck className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-muted-foreground">Policy</span>
          {policy ? (
            <>
              <Badge variant="outline" className="font-mono text-[10px]" data-testid="certification-policy-id">{nText(policy.id)}</Badge>
              <Badge variant="secondary" className="font-mono text-[10px]">{nText(policy.version)}</Badge>
              {policy.description && <span className="text-muted-foreground">{policy.description}</span>}
            </>
          ) : (
            <span className="text-muted-foreground">n/a</span>
          )}
        </div>
      </Card>

      <Card className="border-amber-500/40 bg-amber-500/10 p-3" data-testid="certification-no-auto">
        <div className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-500">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <p>
            These are candidate / preparation states only. Readiness is a heuristic pre-check and no automatic certification is performed;
            actual certification belongs to V7. A READY_FOR_CERTIFICATION state is not a certification.
          </p>
        </div>
      </Card>

      {!projectId ? (
        <EmptyState icon={<BadgeCheck className="h-6 w-6" />} title="No project selected" description="Select a project to prepare certification candidates." testId="certification-no-project" />
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="p-4 lg:col-span-1" data-testid="certification-candidate-list">
            <h3 className="mb-3 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Candidates</h3>
            {isLoading ? (
              <LoadingSpinner label="Loading candidates…" />
            ) : isError ? (
              <ErrorNote message={apiError(error)} />
            ) : candidates.length === 0 ? (
              <p className="text-xs text-muted-foreground" data-testid="certification-candidates-empty">No certification candidates yet.</p>
            ) : (
              <ul className="divide-y divide-border">
                {candidates.map((c) => {
                  const cState = c.readiness?.state || c.state || "DRAFT";
                  return (
                    <li key={c.id}>
                      <button
                        type="button"
                        data-testid="certification-candidate-row"
                        onClick={() => setSelectedId(c.id ?? null)}
                        className={"flex w-full items-center gap-2 py-2 text-left text-sm transition-colors hover:bg-accent/40 " + (selectedId === c.id ? "bg-accent/40" : "")}
                      >
                        <span className="min-w-0 flex-1 truncate font-medium">{nText(c.label)}</span>
                        <Badge variant="outline" className={cn("shrink-0 text-[9px]", stateClasses(cState))} data-testid="certification-state">
                          {nText(cState)}
                        </Badge>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>

          <Card className="space-y-4 p-4 lg:col-span-2" data-testid="certification-detail">
            {!selectedId ? (
              <p className="text-xs text-muted-foreground" data-testid="certification-detail-empty">Select a candidate to inspect its readiness.</p>
            ) : (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-display text-base font-semibold">{nText(candidate?.label)}</h3>
                      <Badge variant="outline" className={cn("text-[10px]", stateClasses(state))} data-testid="certification-detail-state">
                        {nText(state)}
                      </Badge>
                    </div>
                    <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">{nText(candidate?.id)}</p>
                  </div>
                  <Button size="sm" data-testid="certification-evaluate-readiness" disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>
                    <ClipboardCheck className="mr-1.5 h-4 w-4" /> {evaluate.isPending ? "Evaluating…" : "Evaluate readiness"}
                  </Button>
                </div>
                <ErrorNote message={evaluate.isError ? apiError(evaluate.error) : detailError ? apiError(detailErr) : null} />

                <div>
                  <h4 className="mb-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Subject</h4>
                  {subject.length === 0 ? (
                    <p className="text-xs text-muted-foreground">n/a</p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {subject.map((s) => (
                        <Badge key={s.label} variant="outline" className="font-mono text-[10px]">{s.label}: {s.value}</Badge>
                      ))}
                    </div>
                  )}
                </div>

                <div>
                  <h4 className="mb-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Evidence completeness</h4>
                  {evidence.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No evidence attached.</p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {evidence.map((e) => (
                        <Badge key={e.label} variant="secondary" className="text-[10px]">{e.label}: {e.value}</Badge>
                      ))}
                    </div>
                  )}
                  <div className="mt-3 grid gap-2 sm:grid-cols-2" data-testid="certification-evidence-snapshots">
                    {snapshots.map((snap) => (
                      <div key={snap.key} className="rounded-md border border-border px-2.5 py-2" data-testid={`certification-snapshot-${snap.key.replace(/_/g, "-")}`}>
                        <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{snap.title}</span>
                        {snap.entries.length === 0 ? (
                          <p className="mt-1 text-xs text-muted-foreground">n/a</p>
                        ) : (
                          <ul className="mt-1 space-y-1">
                            {snap.entries.map((entry: any, i: number) => (
                              <li key={entry?.id || i} className="flex flex-wrap items-center gap-1.5 text-[11px]" data-testid={`certification-snapshot-${snap.key.replace(/_/g, "-")}-entry`}>
                                <span className="break-all font-mono text-muted-foreground">{nText(entry?.id)}</span>
                                {entry?.version !== null && entry?.version !== undefined && (
                                  <Badge variant="outline" className="text-[9px]">v{nText(entry?.version)}</Badge>
                                )}
                                {entry?.status ? (
                                  <Badge variant="secondary" className="text-[9px]">{nText(entry?.status)}</Badge>
                                ) : (
                                  <span className="text-muted-foreground">status n/a</span>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                <div>
                  <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Criteria</h4>
                  {criteria.length === 0 ? (
                    <p className="text-xs text-muted-foreground" data-testid="certification-criteria-empty">No readiness evaluation yet. Run “Evaluate readiness”.</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {criteria.map((c: any, i: number) => {
                        const passed = c?.passed === true;
                        const failed = c?.passed === false;
                        return (
                          <li key={c?.key || i} className="flex items-start gap-2 rounded-md border border-border px-2.5 py-1.5 text-xs" data-testid="certification-criterion">
                            {passed ? (
                              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
                            ) : failed ? (
                              <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
                            ) : (
                              <span className="mt-0.5 h-3.5 w-3.5 shrink-0 text-center font-mono text-[10px] text-muted-foreground">?</span>
                            )}
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="font-medium">{nText(c?.key || c?.label)}</span>
                                <Badge variant={passed ? "secondary" : failed ? "destructive" : "outline"} className="text-[9px]">
                                  {passed ? "pass" : failed ? "fail" : "n/a"}
                                </Badge>
                              </div>
                              {(c?.detail || c?.reason || c?.description) && (
                                <p className="mt-0.5 text-[11px] text-muted-foreground">{c.detail || c.reason || c.description}</p>
                              )}
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>

                <div>
                  <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Unresolved failures</h4>
                  {unresolved.length === 0 ? (
                    <p className="text-xs text-muted-foreground" data-testid="certification-unresolved-empty">No unresolved failures.</p>
                  ) : (
                    <div className="overflow-x-auto rounded-lg border border-border">
                      <table className="w-full text-sm">
                        <thead className="bg-muted/50 text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                          <tr>
                            <th className="px-3 py-2">Title</th>
                            <th className="px-3 py-2">Domain</th>
                            <th className="px-3 py-2">Taxonomy</th>
                            <th className="px-3 py-2">Severity</th>
                            <th className="px-3 py-2">Status</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-border">
                          {unresolved.map((u: any, i: number) => (
                            <tr key={u?.id || i} data-testid="certification-unresolved-failure">
                              <td className="max-w-[220px] truncate px-3 py-2 font-medium">{nText(u?.title)}</td>
                              <td className="px-3 py-2 text-xs"><Badge variant="outline" className="text-[9px]">{nText(u?.failure_domain)}</Badge></td>
                              <td className="px-3 py-2 font-mono text-[10px] text-muted-foreground">{nText(u?.taxonomy_code)}</td>
                              <td className="px-3 py-2"><Badge variant={u?.severity === "critical" || u?.severity === "high" ? "destructive" : "secondary"} className="text-[9px]">{nText(u?.severity)}</Badge></td>
                              <td className="px-3 py-2 text-xs text-muted-foreground">{nText(u?.status)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <div>
                  <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Unresolved drift failures</h4>
                  {drift.length === 0 ? (
                    <p className="text-xs text-muted-foreground" data-testid="certification-drift-empty">No unresolved drift failures.</p>
                  ) : (
                    <div className="overflow-x-auto rounded-lg border border-border">
                      <table className="w-full text-sm">
                        <thead className="bg-muted/50 text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                          <tr>
                            <th className="px-3 py-2">Title</th>
                            <th className="px-3 py-2">Drift type</th>
                            <th className="px-3 py-2">Taxonomy</th>
                            <th className="px-3 py-2">Severity</th>
                            <th className="px-3 py-2">Status</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-border">
                          {drift.map((d: any, i: number) => (
                            <tr key={d?.id || i} data-testid="certification-drift-failure">
                              <td className="max-w-[220px] truncate px-3 py-2 font-medium">{nText(d?.title)}</td>
                              <td className="px-3 py-2 text-xs"><Badge variant="outline" className="text-[9px]">{nText(d?.drift_type || d?.failure_domain)}</Badge></td>
                              <td className="px-3 py-2 font-mono text-[10px] text-muted-foreground">{nText(d?.taxonomy_code)}</td>
                              <td className="px-3 py-2"><Badge variant={d?.severity === "critical" || d?.severity === "high" ? "destructive" : "secondary"} className="text-[9px]">{nText(d?.severity)}</Badge></td>
                              <td className="px-3 py-2 text-xs text-muted-foreground">{nText(d?.status)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <p className="text-[11px] text-muted-foreground">
                  Readiness state: <span className="font-mono">{nText(state)}</span> · automatic certification: false · certification performed: false.
                </p>
              </>
            )}
          </Card>
        </div>
      )}

      {createOpen && projectId && (
        <CreateCandidateDialog projectId={projectId} onOpenChange={setCreateOpen} onCreated={(id) => setSelectedId(id)} />
      )}
    </div>
  );
}
