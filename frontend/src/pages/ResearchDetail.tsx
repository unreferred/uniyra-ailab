import React, { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Save, Plus, Trash2, FlaskConical, FileText, MessageSquare, Users, Lightbulb, Database } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { ResearchSession, Experiment, Artifact, Conversation, Council as CouncilType, Finding, Dataset } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { LoadingSpinner, SectionHeader } from "@/components/common/Common";

export default function ResearchDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { activeId } = useProjects();

  const { data: session, isLoading, refetch } = useQuery<ResearchSession>({
    queryKey: ["research-detail", id],
    queryFn: async () => (await api.get(`/research/${id}`)).data,
    enabled: !!id,
  });
  const { data: experiments = [] } = useQuery<Experiment[]>({
    queryKey: ["experiments", activeId], queryFn: async () => (await api.get(`/experiments?project_id=${activeId}`)).data, enabled: !!activeId,
  });
  const { data: artifacts = [] } = useQuery<Artifact[]>({
    queryKey: ["artifacts", activeId], queryFn: async () => (await api.get(`/artifacts?project_id=${activeId}`)).data, enabled: !!activeId,
  });
  const { data: conversations = [] } = useQuery<Conversation[]>({
    queryKey: ["conversations", activeId], queryFn: async () => (await api.get(`/conversations?project_id=${activeId}`)).data, enabled: !!activeId,
  });
  const { data: councils = [] } = useQuery<CouncilType[]>({
    queryKey: ["councils", activeId], queryFn: async () => (await api.get(`/councils?project_id=${activeId}`)).data, enabled: !!activeId,
  });
  const { data: findings = [] } = useQuery<Finding[]>({
    queryKey: ["findings", activeId], queryFn: async () => (await api.get(`/findings?project_id=${activeId}`)).data, enabled: !!activeId,
  });
  const { data: datasets = [] } = useQuery<Dataset[]>({
    queryKey: ["datasets", activeId], queryFn: async () => (await api.get(`/datasets?project_id=${activeId}`)).data, enabled: !!activeId,
  });

  const [notes, setNotes] = useState("");
  const [status, setStatus] = useState("active");
  const [sources, setSources] = useState<{ title: string; url: string; note: string }[]>([]);
  const [tagsInput, setTagsInput] = useState("");

  useEffect(() => {
    if (session) {
      setNotes(session.notes); setStatus(session.status); setSources(session.sources || []);
      setTagsInput((session.tags || []).join(", "));
    }
  }, [session?.id]); // eslint-disable-line

  const patch = useMutation({
    mutationFn: async (body: any) => (await api.patch(`/research/${id}`, body)).data,
    onSuccess: () => { refetch(); },
    onError: (e) => toast.error(apiError(e)),
  });

  if (isLoading || !session) return <div className="p-6"><LoadingSpinner label="Loading…" /></div>;

  const linkArr = (field: "related_experiment_ids" | "related_artifact_ids" | "related_conversation_ids" | "related_council_ids" | "related_finding_ids" | "related_dataset_ids", v: string, label: string) => {
    const cur = (session as any)[field] || [];
    if (cur.includes(v)) return;
    patch.mutate({ [field]: [...cur, v] });
    toast.success(`${label} linked`);
  };
  const unlinkArr = (field: string, v: string) => patch.mutate({ [field]: ((session as any)[field] || []).filter((x: string) => x !== v) });
  const saveTags = () => {
    const tags = tagsInput.split(",").map((t) => t.trim()).filter(Boolean);
    patch.mutate({ tags });
    toast.success("Tags saved");
  };

  const LinkCard: React.FC<{ testId: string; icon: React.ReactNode; title: string; selectTestId: string; placeholder: string; options: { id: string; label: string }[]; onPick: (v: string) => void; linked: { id: string; label: string; onNav?: () => void }[]; onUnlink: (v: string) => void }> =
    ({ testId, icon, title, selectTestId, placeholder, options, onPick, linked, onUnlink }) => (
      <Card className="p-4" data-testid={testId}>
        <div className="mb-2 flex items-center gap-1.5 font-display font-semibold">{icon} {title}</div>
        <Select onValueChange={onPick}>
          <SelectTrigger className="mb-2 h-8" data-testid={selectTestId}><SelectValue placeholder={placeholder} /></SelectTrigger>
          <SelectContent>{options.map((o) => <SelectItem key={o.id} value={o.id}>{o.label}</SelectItem>)}</SelectContent>
        </Select>
        <div className="space-y-1">
          {linked.map((e) => (
            <div key={e.id} className="flex items-center justify-between rounded-md border border-border px-2 py-1.5 text-sm">
              {e.onNav
                ? <button className="truncate text-left hover:underline" onClick={e.onNav} data-testid={`goto-${testId}-${e.id}`}>{e.label}</button>
                : <span className="truncate">{e.label}</span>}
              <Trash2 className="h-3.5 w-3.5 cursor-pointer text-muted-foreground hover:text-destructive" onClick={() => onUnlink(e.id)} />
            </div>
          ))}
          {linked.length === 0 && <p className="text-xs text-muted-foreground">None linked.</p>}
        </div>
      </Card>
    );

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-4 lg:p-6">
      <button onClick={() => navigate("/research")} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground" data-testid="back-to-research"><ArrowLeft className="h-4 w-4" /> Research</button>
      <SectionHeader title={session.title} caption={session.research_question}
        action={
          <Select value={status} onValueChange={(v) => { setStatus(v); patch.mutate({ status: v }); }}>
            <SelectTrigger className="w-36" data-testid="research-status-select"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="active">Active</SelectItem><SelectItem value="paused">Paused</SelectItem><SelectItem value="complete">Complete</SelectItem></SelectContent>
          </Select>
        } />

      <Card className="p-4" data-testid="research-tags-card">
        <label className="text-xs font-medium text-muted-foreground">Tags (comma-separated)</label>
        <div className="mt-1 flex gap-2">
          <Input data-testid="research-tags-input" value={tagsInput} onChange={(e) => setTagsInput(e.target.value)} placeholder="e.g. alignment, eval, q3" />
          <Button size="sm" data-testid="save-research-tags" onClick={saveTags}><Save className="mr-1.5 h-4 w-4" /> Save</Button>
        </div>
        {(session.tags || []).length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">{session.tags.map((t) => <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>)}</div>
        )}
      </Card>

      <Card className="p-4">
        <label className="text-xs font-medium text-muted-foreground">Notes</label>
        <Textarea data-testid="research-notes" value={notes} onChange={(e) => setNotes(e.target.value)} rows={6} className="mt-1" placeholder="Working notes, hypotheses, observations…" />
        <div className="mt-2 flex justify-end"><Button size="sm" data-testid="save-research-notes" onClick={() => { patch.mutate({ notes }); toast.success("Notes saved"); }}><Save className="mr-1.5 h-4 w-4" /> Save notes</Button></div>
      </Card>

      <Card className="p-4" data-testid="research-sources">
        <div className="mb-2 flex items-center justify-between"><span className="font-display font-semibold">Sources</span>
          <Button size="sm" variant="ghost" data-testid="add-source" onClick={() => setSources((s) => [...s, { title: "", url: "", note: "" }])}><Plus className="mr-1.5 h-4 w-4" /> Add</Button>
        </div>
        <div className="space-y-2">
          {sources.length === 0 && <p className="text-sm text-muted-foreground">No sources yet.</p>}
          {sources.map((s, i) => (
            <div key={i} className="grid grid-cols-12 gap-2" data-testid={`source-row-${i}`}>
              <Input className="col-span-4" placeholder="Title" value={s.title} onChange={(e) => setSources((x) => x.map((v, idx) => idx === i ? { ...v, title: e.target.value } : v))} />
              <Input className="col-span-7 font-mono text-xs" placeholder="https://…" value={s.url} onChange={(e) => setSources((x) => x.map((v, idx) => idx === i ? { ...v, url: e.target.value } : v))} />
              <Button className="col-span-1" size="sm" variant="ghost" onClick={() => setSources((x) => x.filter((_, idx) => idx !== i))}><Trash2 className="h-4 w-4" /></Button>
            </div>
          ))}
        </div>
        {sources.length > 0 && <div className="mt-2 flex justify-end"><Button size="sm" variant="outline" data-testid="save-sources" onClick={() => { patch.mutate({ sources }); toast.success("Sources saved"); }}>Save sources</Button></div>}
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <LinkCard testId="linked-experiments" icon={<FlaskConical className="h-4 w-4" />} title="Linked experiments"
          selectTestId="link-experiment-select" placeholder="Link an experiment…"
          options={experiments.map((e) => ({ id: e.id, label: e.title }))}
          onPick={(v) => linkArr("related_experiment_ids", v, "Experiment")}
          linked={(session.linked_experiments || []).map((e) => ({ id: e.id, label: e.title, onNav: () => navigate(`/experiments/${e.id}`) }))}
          onUnlink={(v) => unlinkArr("related_experiment_ids", v)} />
        <LinkCard testId="linked-artifacts" icon={<FileText className="h-4 w-4" />} title="Linked artifacts"
          selectTestId="link-artifact-select" placeholder="Link an artifact…"
          options={artifacts.map((a) => ({ id: a.id, label: a.title }))}
          onPick={(v) => linkArr("related_artifact_ids", v, "Artifact")}
          linked={(session.linked_artifacts || []).map((a) => ({ id: a.id, label: a.title, onNav: () => navigate("/artifacts") }))}
          onUnlink={(v) => unlinkArr("related_artifact_ids", v)} />
        <LinkCard testId="linked-conversations" icon={<MessageSquare className="h-4 w-4" />} title="Linked conversations"
          selectTestId="link-conversation-select" placeholder="Link a conversation…"
          options={conversations.map((c) => ({ id: c.id, label: c.title }))}
          onPick={(v) => linkArr("related_conversation_ids", v, "Conversation")}
          linked={(session.linked_conversations || []).map((c) => ({ id: c.id, label: c.title, onNav: () => navigate("/workspace") }))}
          onUnlink={(v) => unlinkArr("related_conversation_ids", v)} />
        <LinkCard testId="linked-councils" icon={<Users className="h-4 w-4" />} title="Linked councils"
          selectTestId="link-council-select" placeholder="Link a council…"
          options={councils.map((c) => ({ id: c.id, label: c.title }))}
          onPick={(v) => linkArr("related_council_ids", v, "Council")}
          linked={(session.linked_councils || []).map((c) => ({ id: c.id, label: c.title, onNav: () => navigate("/council") }))}
          onUnlink={(v) => unlinkArr("related_council_ids", v)} />
        <LinkCard testId="linked-findings" icon={<Lightbulb className="h-4 w-4" />} title="Linked findings"
          selectTestId="link-finding-select" placeholder="Link a finding…"
          options={findings.map((f) => ({ id: f.id, label: f.title }))}
          onPick={(v) => linkArr("related_finding_ids", v, "Finding")}
          linked={(session.linked_findings || []).map((f) => ({ id: f.id, label: f.title, onNav: () => navigate("/findings") }))}
          onUnlink={(v) => unlinkArr("related_finding_ids", v)} />
        <LinkCard testId="linked-datasets" icon={<Database className="h-4 w-4" />} title="Linked datasets"
          selectTestId="link-dataset-select" placeholder="Link a dataset…"
          options={datasets.map((d) => ({ id: d.id, label: d.name }))}
          onPick={(v) => linkArr("related_dataset_ids", v, "Dataset")}
          linked={(session.linked_datasets || []).map((d) => ({ id: d.id, label: d.name, onNav: () => navigate(`/datasets/${d.id}`) }))}
          onUnlink={(v) => unlinkArr("related_dataset_ids", v)} />
      </div>
    </div>
  );
}
