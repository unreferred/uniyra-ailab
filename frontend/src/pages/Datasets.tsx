import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Database, Plus, Trash2, ChevronRight } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { Dataset, DatasetVersion } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { EmptyState, SectionHeader, LoadingSpinner } from "@/components/common/Common";
import { relTime } from "@/lib/format";

type DatasetListItem = Dataset & { latest_version?: DatasetVersion | null };

export default function Datasets() {
  const { activeId, activeProject } = useProjects();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tagsInput, setTagsInput] = useState("");

  const { data: datasets = [], isLoading } = useQuery<DatasetListItem[]>({
    queryKey: ["datasets", activeId],
    queryFn: async () => {
      const list: Dataset[] = (await api.get(`/datasets?project_id=${activeId}`)).data;
      return Promise.all(
        list.map(async (d) => {
          try {
            const versions: DatasetVersion[] = (await api.get(`/datasets/${d.id}/versions`)).data;
            return { ...d, latest_version: versions[0] ?? null };
          } catch {
            return { ...d, latest_version: null };
          }
        })
      );
    },
    enabled: !!activeId,
  });

  const create = useMutation({
    mutationFn: async () => (await api.post("/datasets", {
      project_id: activeId, name, description,
      tags: tagsInput.split(",").map((t) => t.trim()).filter(Boolean),
    })).data,
    onSuccess: (d) => {
      toast.success("Dataset created");
      qc.invalidateQueries({ queryKey: ["datasets", activeId] });
      setOpen(false); setName(""); setDescription(""); setTagsInput("");
      navigate(`/datasets/${d.id}`);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const del = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/datasets/${id}`)).data,
    onSuccess: () => { toast.success("Dataset deleted"); qc.invalidateQueries({ queryKey: ["datasets", activeId] }); },
    onError: (e) => toast.error(apiError(e)),
  });

  if (!activeId) return <div className="p-6"><EmptyState icon={<Database className="h-6 w-6" />} title="No project selected" description="Select a project to manage evaluation datasets." /></div>;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 lg:p-6">
      <SectionHeader title="Datasets" caption={`Reusable evaluation case collections for "${activeProject?.name}".`}
        action={<Button size="sm" data-testid="new-dataset-button" onClick={() => setOpen(true)}><Plus className="mr-1.5 h-4 w-4" /> New dataset</Button>} />

      {isLoading ? <LoadingSpinner label="Loading…" /> : datasets.length === 0 ? (
        <EmptyState icon={<Database className="h-6 w-6" />} title="No datasets yet" description="Create a dataset to collect input / expected-output evaluation cases." testId="datasets-empty" />
      ) : (
        <div className="grid gap-3">
          {datasets.map((d) => (
            <Card key={d.id} className="p-4 fade-in-up" data-testid={`dataset-card-${d.id}`}>
              <div className="flex items-center justify-between gap-3">
                <button className="min-w-0 flex-1 text-left" onClick={() => navigate(`/datasets/${d.id}`)} data-testid={`open-dataset-${d.id}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{d.name}</span>
                    {d.latest_version ? (
                      <>
                        <Badge variant="outline" className="text-[10px]">v{d.latest_version.version} · {d.latest_version.case_count} case(s)</Badge>
                        <Badge variant={d.latest_version.status === "frozen" ? "secondary" : "default"} className="text-[10px]">{d.latest_version.status}</Badge>
                      </>
                    ) : d.case_count != null ? (
                      <Badge variant="outline" className="text-[10px]">{d.case_count} cases</Badge>
                    ) : null}
                    {(d.tags || []).map((t) => <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>)}
                  </div>
                  {d.description && <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{d.description}</p>}
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {d.latest_version ? `updated ${relTime(d.latest_version.updated_at || d.latest_version.created_at)}` : `updated ${relTime(d.updated_at)}`}
                  </p>
                </button>
                <div className="flex shrink-0 items-center gap-1">
                  <Button size="sm" variant="ghost" onClick={() => navigate(`/datasets/${d.id}`)}><ChevronRight className="h-4 w-4" /></Button>
                  <Button size="sm" variant="ghost" className="text-destructive" data-testid={`delete-dataset-${d.id}`} onClick={() => del.mutate(d.id)}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent data-testid="new-dataset-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">New dataset</DialogTitle>
            <DialogDescription>Create a collection of input / expected-output evaluation cases. A draft version is created automatically.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div><label className="text-xs font-medium text-muted-foreground">Name</label><Input data-testid="dataset-name-input" value={name} onChange={(e) => setName(e.target.value)} className="mt-1" /></div>
            <div><label className="text-xs font-medium text-muted-foreground">Description</label><Textarea data-testid="dataset-desc-input" value={description} onChange={(e) => setDescription(e.target.value)} rows={3} className="mt-1" /></div>
            <div><label className="text-xs font-medium text-muted-foreground">Tags (comma-separated)</label><Input data-testid="dataset-tags-input" value={tagsInput} onChange={(e) => setTagsInput(e.target.value)} className="mt-1" /></div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button data-testid="dataset-create-submit" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "Creating…" : "Create"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
