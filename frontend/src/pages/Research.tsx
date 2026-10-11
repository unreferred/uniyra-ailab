import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Microscope, Plus } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { ResearchSession } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { EmptyState, SectionHeader, LoadingSpinner } from "@/components/common/Common";
import { relTime } from "@/lib/format";

export default function Research() {
  const { activeId, activeProject } = useProjects();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [question, setQuestion] = useState("");

  const { data: sessions = [], isLoading } = useQuery<ResearchSession[]>({
    queryKey: ["research", activeId],
    queryFn: async () => (await api.get(`/research?project_id=${activeId}`)).data,
    enabled: !!activeId,
  });

  const create = useMutation({
    mutationFn: async () => (await api.post("/research", { project_id: activeId, title, research_question: question })).data,
    onSuccess: (d) => { toast.success("Research session created"); qc.invalidateQueries({ queryKey: ["research", activeId] }); setOpen(false); setTitle(""); setQuestion(""); navigate(`/research/${d.id}`); },
    onError: (e) => toast.error(apiError(e)),
  });

  if (!activeId) return <div className="p-6"><EmptyState icon={<Microscope className="h-6 w-6" />} title="No project selected" description="Select a project to organise research." /></div>;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 lg:p-6">
      <SectionHeader title="Research" caption={`Organise questions, notes and evidence for "${activeProject?.name}".`}
        action={<Button size="sm" data-testid="new-research-button" onClick={() => setOpen(true)}><Plus className="mr-1.5 h-4 w-4" /> New session</Button>} />

      {isLoading ? <LoadingSpinner label="Loading…" /> : sessions.length === 0 ? (
        <EmptyState icon={<Microscope className="h-6 w-6" />} title="No research sessions" description="Start a session to capture a research question, notes, sources and link the experiments that inform it." testId="research-empty" />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {sessions.map((s) => (
            <Card key={s.id} className="cursor-pointer p-4 transition-colors hover:bg-accent/40 fade-in-up" data-testid={`research-card-${s.id}`} onClick={() => navigate(`/research/${s.id}`)}>
              <div className="flex items-center justify-between">
                <span className="font-medium">{s.title}</span>
                <Badge variant="secondary" className="text-[10px]">{s.status}</Badge>
              </div>
              {s.research_question && <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{s.research_question}</p>}
              <div className="mt-2 flex gap-3 text-[11px] text-muted-foreground">
                <span>{s.related_experiment_ids.length} experiments</span>
                <span>{s.sources.length} sources</span>
                <span className="ml-auto">{relTime(s.updated_at)}</span>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent data-testid="new-research-dialog">
          <DialogHeader><DialogTitle className="font-display">New research session</DialogTitle>
            <DialogDescription>Start a research session to track a question, notes, sources and linked evidence.</DialogDescription></DialogHeader>
          <div className="space-y-3">
            <div><label className="text-xs font-medium text-muted-foreground">Title</label><Input data-testid="research-title-input" value={title} onChange={(e) => setTitle(e.target.value)} className="mt-1" /></div>
            <div><label className="text-xs font-medium text-muted-foreground">Research question</label><Textarea data-testid="research-question-input" value={question} onChange={(e) => setQuestion(e.target.value)} rows={3} className="mt-1" /></div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button data-testid="research-create-submit" disabled={!title.trim() || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "Creating…" : "Create"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
