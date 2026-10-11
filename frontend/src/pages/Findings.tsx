import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Lightbulb, Plus, Trash2, Link2, ExternalLink, Pencil, FlaskConical, ShieldAlert } from "lucide-react";
import { api, apiError, promoteFindingToRegression, promoteFindingToRedTeam, linkFinding } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { Finding, Experiment, ResearchSession, Artifact, FindingEvidence, Conversation, Council as CouncilType, Dataset, Evaluation } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState, ErrorNote, SectionHeader, LoadingSpinner } from "@/components/common/Common";
import { nText } from "@/components/experiment/format";

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "provisional", label: "Provisional" },
  { value: "validated", label: "Validated" },
  { value: "superseded", label: "Superseded" },
  { value: "resolved", label: "Resolved" },
  { value: "reproduced", label: "Reproduced" },
  { value: "accepted-risk", label: "Accepted risk" },
  { value: "needs-investigation", label: "Needs investigation" },
];

const STATUS_COLORS: Record<string, string> = {
  provisional: "secondary",
  validated: "default",
  superseded: "outline",
  resolved: "default",
  reproduced: "secondary",
  "accepted-risk": "outline",
  "needs-investigation": "destructive",
};

export default function Findings() {
  const { activeId, activeProject } = useProjects();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<Finding | null>(null);
  const [editing, setEditing] = useState<Finding | null>(null);

  const [title, setTitle] = useState("");
  const [statement, setStatement] = useState("");
  const [description, setDescription] = useState("");
  const [tagsInput, setTagsInput] = useState("");
  const [confidence, setConfidence] = useState("medium");
  const [status, setStatus] = useState("provisional");
  const [notes, setNotes] = useState("");
  const [evidence, setEvidence] = useState<FindingEvidence[]>([]);
  const [promotionResult, setPromotionResult] = useState<any>(null);
  const [linkType, setLinkType] = useState("run");
  const [linkRef, setLinkRef] = useState("");
  const [linkLabel, setLinkLabel] = useState("");
  const [linkError, setLinkError] = useState<string | null>(null);

  const { data: findings = [], isLoading } = useQuery<Finding[]>({
    queryKey: ["findings", activeId], queryFn: async () => (await api.get(`/findings?project_id=${activeId}`)).data, enabled: !!activeId,
  });
  const { data: experiments = [] } = useQuery<Experiment[]>({ queryKey: ["experiments", activeId], queryFn: async () => (await api.get(`/experiments?project_id=${activeId}`)).data, enabled: !!activeId });
  const { data: research = [] } = useQuery<ResearchSession[]>({ queryKey: ["research", activeId], queryFn: async () => (await api.get(`/research?project_id=${activeId}`)).data, enabled: !!activeId });
  const { data: artifacts = [] } = useQuery<Artifact[]>({ queryKey: ["artifacts", activeId], queryFn: async () => (await api.get(`/artifacts?project_id=${activeId}`)).data, enabled: !!activeId });
  const { data: conversations = [] } = useQuery<Conversation[]>({ queryKey: ["conversations", activeId], queryFn: async () => (await api.get(`/conversations?project_id=${activeId}`)).data, enabled: !!activeId });
  const { data: councils = [] } = useQuery<CouncilType[]>({ queryKey: ["councils", activeId], queryFn: async () => (await api.get(`/councils?project_id=${activeId}`)).data, enabled: !!activeId });
  const { data: datasets = [] } = useQuery<Dataset[]>({ queryKey: ["datasets", activeId], queryFn: async () => (await api.get(`/datasets?project_id=${activeId}`)).data, enabled: !!activeId });
  const { data: evaluations = [] } = useQuery<Evaluation[]>({
    queryKey: ["evaluations", activeId],
    queryFn: async () => {
      const exps: Experiment[] = (await api.get(`/experiments?project_id=${activeId}`)).data;
      const out: Evaluation[] = [];
      for (const e of exps.slice(0, 20)) {
        try { const { data } = await api.get(`/experiments/${e.id}`); (data.evaluations || []).forEach((ev: Evaluation) => out.push({ ...ev, experiment_id: e.id })); } catch { /* ignore */ }
      }
      return out;
    },
    enabled: !!activeId,
  });
  const runOptions = (experiments.flatMap((e) => e.runs || []).slice(0, 100)) as { id: string; model_name?: string }[];

  const parseTags = (s: string) => s.split(",").map((t) => t.trim()).filter(Boolean);
  const reset = () => { setTitle(""); setStatement(""); setDescription(""); setTagsInput(""); setConfidence("medium"); setStatus("provisional"); setNotes(""); setEvidence([]); setEditing(null); };
  const create = useMutation({
    mutationFn: async () => (await api.post("/findings", { project_id: activeId, title, statement, description, confidence, status, notes, tags: parseTags(tagsInput), evidence })).data,
    onSuccess: () => { toast.success("Finding recorded"); qc.invalidateQueries({ queryKey: ["findings", activeId] }); setOpen(false); reset(); },
    onError: (e) => toast.error(apiError(e)),
  });
  const update = useMutation({
    mutationFn: async () => (await api.patch(`/findings/${editing!.id}`, { title, statement, description, confidence, status, notes, tags: parseTags(tagsInput), evidence })).data,
    onSuccess: () => { toast.success("Finding updated"); qc.invalidateQueries({ queryKey: ["findings", activeId] }); setOpen(false); reset(); setDetail(null); },
    onError: (e) => toast.error(apiError(e)),
  });
  const patchStatus = useMutation({
    mutationFn: async ({ id, s }: { id: string; s: string }) => (await api.patch(`/findings/${id}`, { status: s })).data,
    onSuccess: () => { toast.success("Status updated"); qc.invalidateQueries({ queryKey: ["findings", activeId] }); },
    onError: (e) => toast.error(apiError(e)),
  });
  const del = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/findings/${id}`)).data,
    onSuccess: () => { toast.success("Finding deleted"); qc.invalidateQueries({ queryKey: ["findings", activeId] }); },
    onError: (e) => toast.error(apiError(e)),
  });

  const refreshDetail = async (id: string) => { const { data } = await api.get(`/findings/${id}`); setDetail(data); };
  const promoteRegression = useMutation({
    mutationFn: async (id: string) => promoteFindingToRegression(id),
    onSuccess: async (data: any, id: string) => { setPromotionResult({ kind: "regression", data }); toast.success("Regression case created"); await refreshDetail(id); qc.invalidateQueries({ queryKey: ["findings", activeId] }); },
    onError: (e) => toast.error(apiError(e)),
  });
  const promoteRedTeam = useMutation({
    mutationFn: async (id: string) => promoteFindingToRedTeam(id),
    onSuccess: async (data: any, id: string) => { setPromotionResult({ kind: "red-team", data }); toast.success("Red-team case created"); await refreshDetail(id); qc.invalidateQueries({ queryKey: ["findings", activeId] }); },
    onError: (e) => toast.error(apiError(e)),
  });
  const linkEvidence = useMutation({
    mutationFn: async (id: string) => linkFinding(id, { type: linkType, ref_id: linkRef.trim(), label: linkLabel.trim() || undefined }),
    onSuccess: async (_data: any, id: string) => { toast.success("Evidence linked"); setLinkRef(""); setLinkLabel(""); setLinkError(null); await refreshDetail(id); },
    onError: (e) => { setLinkError(apiError(e)); toast.error(apiError(e)); },
  });

  const addEvidence = (type: string, ref_id: string, label: string) => {
    if (!ref_id) return;
    if (evidence.some((e) => e.type === type && e.ref_id === ref_id)) return;
    setEvidence((ev) => [...ev, { type, ref_id, label }]);
  };
  const openDetail = async (f: Finding) => { setPromotionResult(null); setLinkRef(""); setLinkLabel(""); setLinkError(null); const { data } = await api.get(`/findings/${f.id}`); setDetail(data); };
  const startCreate = () => { reset(); setOpen(true); };
  const startEdit = (f: Finding) => {
    setEditing(f); setTitle(f.title); setStatement(f.statement || ""); setDescription(f.description || "");
    setTagsInput((f.tags || []).join(", ")); setConfidence(f.confidence); setStatus(f.status);
    setNotes(f.notes || ""); setEvidence(f.evidence || []); setOpen(true); setDetail(null);
  };

  if (!activeId) return <div className="p-6"><EmptyState icon={<Lightbulb className="h-6 w-6" />} title="No project selected" description="Select a project to record findings." /></div>;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 lg:p-6">
      <SectionHeader title="Findings" caption={`Conclusions you've validated in "${activeProject?.name}", linked to their evidence.`}
        action={<Button size="sm" data-testid="new-finding-button" onClick={startCreate}><Plus className="mr-1.5 h-4 w-4" /> New finding</Button>} />

      {isLoading ? <LoadingSpinner label="Loading…" /> : findings.length === 0 ? (
        <EmptyState icon={<Lightbulb className="h-6 w-6" />} title="No findings yet" description="Record a conclusion and link the experiments or research that support it." testId="findings-empty" />
      ) : (
        <div className="grid gap-3">
          {findings.map((f) => (
            <Card key={f.id} className="p-4 fade-in-up" data-testid={`finding-card-${f.id}`}>
              <div className="flex items-start justify-between gap-3">
                <button className="min-w-0 text-left" onClick={() => openDetail(f)} data-testid={`open-finding-${f.id}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{f.title}</span>
                    <Badge variant={STATUS_COLORS[f.status] as any} className="text-[10px]">{f.status}</Badge>
                    <Badge variant="outline" className="text-[10px]">conf: {f.confidence}</Badge>
                    {(f.tags || []).map((t) => <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>)}
                  </div>
                  {f.statement && <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{f.statement}</p>}
                  {f.description && <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{f.description}</p>}
                  <p className="mt-1 text-[11px] text-muted-foreground">{f.evidence.length} evidence link(s)</p>
                </button>
                <div className="flex shrink-0 items-center gap-1">
                  <Button size="sm" variant="ghost" data-testid={`edit-finding-${f.id}`} onClick={() => startEdit(f)}><Pencil className="h-4 w-4" /></Button>
                  <Select value={f.status} onValueChange={(s) => patchStatus.mutate({ id: f.id, s })}>
                    <SelectTrigger className="h-8 w-36" data-testid={`finding-status-${f.id}`}><SelectValue /></SelectTrigger>
                    <SelectContent>{STATUS_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
                  </Select>
                  <Button size="sm" variant="ghost" className="text-destructive" data-testid={`delete-finding-${f.id}`} onClick={() => del.mutate(f.id)}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* create/edit dialog */}
      <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) reset(); }}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto" data-testid="new-finding-dialog">
          <DialogHeader><DialogTitle className="font-display">{editing ? "Edit finding" : "New finding"}</DialogTitle>
            <DialogDescription>Record a research conclusion and attach the experiments, runs, evaluations or other evidence that supports it.</DialogDescription></DialogHeader>
          <div className="space-y-3">
            <div><label className="text-xs font-medium text-muted-foreground">Title</label><Input data-testid="finding-title-input" value={title} onChange={(e) => setTitle(e.target.value)} className="mt-1" /></div>
            <div><label className="text-xs font-medium text-muted-foreground">Statement</label><Textarea data-testid="finding-statement-input" value={statement} onChange={(e) => setStatement(e.target.value)} rows={3} className="mt-1" /></div>
            <div><label className="text-xs font-medium text-muted-foreground">Description</label><Textarea data-testid="finding-description-input" value={description} onChange={(e) => setDescription(e.target.value)} rows={3} className="mt-1" placeholder="Extended description, context, caveats…" /></div>
            <div><label className="text-xs font-medium text-muted-foreground">Tags (comma-separated)</label><Input data-testid="finding-tags-input" value={tagsInput} onChange={(e) => setTagsInput(e.target.value)} className="mt-1" placeholder="e.g. eval, safety" /></div>
            <div><label className="text-xs font-medium text-muted-foreground">Notes</label><Textarea data-testid="finding-notes-input" value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} className="mt-1" /></div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="text-xs font-medium text-muted-foreground">Confidence</label>
                <Select value={confidence} onValueChange={setConfidence}><SelectTrigger className="mt-1" data-testid="finding-confidence-select"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="low">Low</SelectItem><SelectItem value="medium">Medium</SelectItem><SelectItem value="high">High</SelectItem></SelectContent></Select>
              </div>
              <div><label className="text-xs font-medium text-muted-foreground">Status</label>
                <Select value={status} onValueChange={setStatus}><SelectTrigger className="mt-1" data-testid="finding-status-input"><SelectValue /></SelectTrigger><SelectContent>{STATUS_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent></Select>
              </div>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Evidence</label>
              <div className="mt-1 grid grid-cols-3 gap-2">
                <Select onValueChange={(v) => { const e = experiments.find((x) => x.id === v); if (e) addEvidence("experiment", e.id, e.title); }}>
                  <SelectTrigger className="h-8" data-testid="evidence-experiment-select"><SelectValue placeholder="+ Experiment" /></SelectTrigger>
                  <SelectContent>{experiments.map((e) => <SelectItem key={e.id} value={e.id}>{e.title}</SelectItem>)}</SelectContent>
                </Select>
                <Select onValueChange={(v) => { const r = research.find((x) => x.id === v); if (r) addEvidence("research", r.id, r.title); }}>
                  <SelectTrigger className="h-8" data-testid="evidence-research-select"><SelectValue placeholder="+ Research" /></SelectTrigger>
                  <SelectContent>{research.map((r) => <SelectItem key={r.id} value={r.id}>{r.title}</SelectItem>)}</SelectContent>
                </Select>
                <Select onValueChange={(v) => { const a = artifacts.find((x) => x.id === v); if (a) addEvidence("artifact", a.id, a.title); }}>
                  <SelectTrigger className="h-8" data-testid="evidence-artifact-select"><SelectValue placeholder="+ Artifact" /></SelectTrigger>
                  <SelectContent>{artifacts.map((a) => <SelectItem key={a.id} value={a.id}>{a.title}</SelectItem>)}</SelectContent>
                </Select>
                <Select onValueChange={(v) => { const r = runOptions.find((x) => x.id === v); if (r) addEvidence("run", r.id, r.model_name || r.id.slice(0, 8)); }}>
                  <SelectTrigger className="h-8" data-testid="evidence-run-select"><SelectValue placeholder="+ Run" /></SelectTrigger>
                  <SelectContent>{runOptions.map((r) => <SelectItem key={r.id} value={r.id}>{(r.model_name || "run") + " · " + r.id.slice(0, 8)}</SelectItem>)}</SelectContent>
                </Select>
                <Select onValueChange={(v) => { const ev = evaluations.find((x) => x.id === v); if (ev) addEvidence("evaluation", ev.id, `Eval ${ev.id.slice(0, 8)}`); }}>
                  <SelectTrigger className="h-8" data-testid="evidence-evaluation-select"><SelectValue placeholder="+ Evaluation" /></SelectTrigger>
                  <SelectContent>{evaluations.map((ev) => <SelectItem key={ev.id} value={ev.id}>{`Eval ${ev.id.slice(0, 8)} · ${ev.eval_type}`}</SelectItem>)}</SelectContent>
                </Select>
                <Select onValueChange={(v) => { const c = councils.find((x) => x.id === v); if (c) addEvidence("council", c.id, c.title); }}>
                  <SelectTrigger className="h-8" data-testid="evidence-council-select"><SelectValue placeholder="+ Council" /></SelectTrigger>
                  <SelectContent>{councils.map((c) => <SelectItem key={c.id} value={c.id}>{c.title}</SelectItem>)}</SelectContent>
                </Select>
                <Select onValueChange={(v) => { const d = datasets.find((x) => x.id === v); if (d) addEvidence("dataset", d.id, d.name); }}>
                  <SelectTrigger className="h-8" data-testid="evidence-dataset-select"><SelectValue placeholder="+ Dataset" /></SelectTrigger>
                  <SelectContent>{datasets.map((d) => <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>)}</SelectContent>
                </Select>
                <Select onValueChange={(v) => { const c = conversations.find((x) => x.id === v); if (c) addEvidence("conversation", c.id, c.title); }}>
                  <SelectTrigger className="h-8" data-testid="evidence-conversation-select"><SelectValue placeholder="+ Conversation" /></SelectTrigger>
                  <SelectContent>{conversations.map((c) => <SelectItem key={c.id} value={c.id}>{c.title}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {evidence.map((e, i) => (
                  <Badge key={i} variant="secondary" className="gap-1 text-[10px]" data-testid={`evidence-chip-${i}`}>
                    {e.type}: {e.label}
                    <button onClick={() => setEvidence((ev) => ev.filter((_, idx) => idx !== i))}>×</button>
                  </Badge>
                ))}
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => { setOpen(false); reset(); }}>Cancel</Button>
            {editing
              ? <Button data-testid="finding-update-submit" disabled={!title.trim() || update.isPending} onClick={() => update.mutate()}>{update.isPending ? "Saving…" : "Save changes"}</Button>
              : <Button data-testid="finding-create-submit" disabled={!title.trim() || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "Saving…" : "Record finding"}</Button>}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* detail dialog with navigable evidence */}
      <Dialog open={!!detail} onOpenChange={(v) => !v && setDetail(null)}>
        <DialogContent data-testid="finding-detail-dialog">
          <DialogHeader><DialogTitle className="font-display">{detail?.title}</DialogTitle>
            <DialogDescription>Finding details with links to supporting evidence.</DialogDescription></DialogHeader>
          {detail && (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <Badge variant={STATUS_COLORS[detail.status] as any}>{detail.status}</Badge>
                <Badge variant="outline">confidence: {detail.confidence}</Badge>
                {(detail.tags || []).map((t) => <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>)}
              </div>
              {detail.statement && <p className="text-sm">{detail.statement}</p>}
              {detail.description && (
                <div>
                  <div className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Description</div>
                  <p className="whitespace-pre-wrap text-sm text-muted-foreground">{detail.description}</p>
                </div>
              )}
              {detail.notes && <p className="text-xs text-muted-foreground">Notes: {detail.notes}</p>}
              <div>
                <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground"><Link2 className="h-3 w-3" /> Supporting evidence</div>
                <div className="space-y-1">
                  {(detail.resolved_evidence || []).length === 0 && <p className="text-sm text-muted-foreground">No evidence linked.</p>}
                  {(detail.resolved_evidence || []).map((e, i) => (
                    <div key={i} className="flex items-center justify-between rounded-md border border-border px-2.5 py-1.5 text-sm" data-testid={`resolved-evidence-${i}`}>
                      <span className="flex items-center gap-2"><Badge variant="outline" className="text-[10px]">{e.type}</Badge>{e.title || e.label}{!e.exists && <span className="text-xs text-destructive">(deleted)</span>}</span>
                      {e.exists && e.route && <Button size="sm" variant="ghost" data-testid={`nav-evidence-${i}`} onClick={() => { setDetail(null); navigate(e.route!); }}><ExternalLink className="h-4 w-4" /></Button>}
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground"><Link2 className="h-3 w-3" /> Promote &amp; link</div>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" data-testid="finding-promote-regression" disabled={promoteRegression.isPending} onClick={() => promoteRegression.mutate(detail.id)}>
                    <FlaskConical className="mr-1.5 h-3.5 w-3.5" /> Create regression case
                  </Button>
                  <Button size="sm" variant="outline" data-testid="finding-promote-red-team" disabled={promoteRedTeam.isPending} onClick={() => promoteRedTeam.mutate(detail.id)}>
                    <ShieldAlert className="mr-1.5 h-3.5 w-3.5" /> Create red-team case
                  </Button>
                </div>
                {promotionResult && (
                  <div className="mt-2 rounded-md border border-border px-2.5 py-2 text-xs" data-testid="finding-promotion-result">
                    <span className="capitalize text-muted-foreground">{promotionResult.kind} case created · </span>
                    <span data-testid="promotion-dataset-version">dataset version: {nText(promotionResult.data?.dataset_version_id || promotionResult.data?.version_id || promotionResult.data?.version?.id)}</span>
                    <span> · </span>
                    <span data-testid="promotion-case-id">case: {nText(promotionResult.data?.case_id || promotionResult.data?.dataset_case_id || promotionResult.data?.case?.id)}</span>
                  </div>
                )}
                <div className="mt-3 flex flex-wrap items-end gap-2">
                  <div>
                    <label className="text-[11px] text-muted-foreground">Link type</label>
                    <Select value={linkType} onValueChange={setLinkType}>
                      <SelectTrigger className="h-8 w-36" data-testid="finding-link-type"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {["run", "evaluation", "experiment", "dataset", "research", "artifact", "council", "conversation", "failure", "red_team_case"].map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <label className="text-[11px] text-muted-foreground">Reference ID</label>
                    <Input data-testid="finding-link-ref" value={linkRef} onChange={(e) => setLinkRef(e.target.value)} className="mt-0.5 h-8 w-52 font-mono text-xs" placeholder="id_…" />
                  </div>
                  <div>
                    <label className="text-[11px] text-muted-foreground">Label (optional)</label>
                    <Input data-testid="finding-link-label" value={linkLabel} onChange={(e) => setLinkLabel(e.target.value)} className="mt-0.5 h-8 w-40 text-xs" />
                  </div>
                  <Button size="sm" variant="outline" data-testid="finding-link-submit" disabled={!linkRef.trim() || linkEvidence.isPending} onClick={() => linkEvidence.mutate(detail.id)}>Link evidence</Button>
                </div>
                <ErrorNote className="mt-2" message={linkError} />
              </div>

              {Array.isArray((detail as any).promotions) && (detail as any).promotions.length > 0 && (
                <div>
                  <div className="mb-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Promotions</div>
                  <div className="space-y-1">
                    {(detail as any).promotions.map((p: any, i: number) => (
                      <div key={p?.promotion_id || p?.id || i} className="flex flex-wrap items-center gap-2 rounded-md border border-border px-2.5 py-1.5 text-xs" data-testid="finding-promotion-row">
                        <Badge variant="outline" className="text-[10px]">{nText(p?.kind)}</Badge>
                        <span className="font-mono text-[10px] text-muted-foreground">version: {nText(p?.dataset_version_id)}</span>
                        <span className="font-mono text-[10px] text-muted-foreground">case: {nText(p?.case_id)}</span>
                        {p?.dataset_id && (
                          <Link className="text-[10px] text-primary underline-offset-4 hover:underline" to={`/datasets/${p.dataset_id}`} data-testid="finding-promotion-dataset-link">dataset</Link>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
