import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileText, Trash2, Eye } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { Artifact } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { EmptyState, SectionHeader, LoadingSpinner, CopyButton } from "@/components/common/Common";
import { MarkdownView } from "@/components/common/Markdown";
import { fmtDate } from "@/lib/format";

export default function Artifacts() {
  const { activeId, activeProject } = useProjects();
  const qc = useQueryClient();
  const [viewing, setViewing] = useState<Artifact | null>(null);

  const { data: artifacts = [], isLoading } = useQuery<Artifact[]>({
    queryKey: ["artifacts", activeId],
    queryFn: async () => (await api.get(`/artifacts?project_id=${activeId}`)).data,
    enabled: !!activeId,
  });

  const del = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/artifacts/${id}`)).data,
    onSuccess: () => { toast.success("Artifact deleted"); qc.invalidateQueries({ queryKey: ["artifacts", activeId] }); },
    onError: (e) => toast.error(apiError(e)),
  });

  if (!activeId) return <div className="p-6"><EmptyState icon={<FileText className="h-6 w-6" />} title="No project selected" description="Select a project to view its artifacts." /></div>;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6">
      <SectionHeader title="Artifacts" caption={`Saved outputs, notes and reports for "${activeProject?.name}".`} />
      {isLoading ? <LoadingSpinner label="Loading artifacts…" /> : artifacts.length === 0 ? (
        <EmptyState icon={<FileText className="h-6 w-6" />} title="No artifacts yet" description="Save a useful model response from the Workspace to capture it as an artifact." testId="artifacts-empty" />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {artifacts.map((a) => (
            <Card key={a.id} className="flex flex-col p-4 fade-in-up" data-testid={`artifact-card-${a.id}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2"><span className="truncate font-medium">{a.title}</span><Badge variant="secondary" className="text-[10px]">{a.artifact_type}</Badge></div>
                  <p className="mt-0.5 text-xs text-muted-foreground">{a.source} · v{a.version} · {fmtDate(a.created_at)}</p>
                  {(a.creator_name || a.model_name || a.run_id) && (
                    <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                      {[a.creator_name && `by ${a.creator_name}`, a.model_name && `model ${a.model_name}`, a.run_id && `run ${a.run_id.slice(0, 8)}`].filter(Boolean).join(" · ")}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 gap-1">
                  <CopyButton value={a.content} />
                  <Button size="sm" variant="ghost" data-testid={`view-artifact-${a.id}`} onClick={() => setViewing(a)}><Eye className="h-4 w-4" /></Button>
                  <Button size="sm" variant="ghost" className="text-destructive" data-testid={`delete-artifact-${a.id}`} onClick={() => del.mutate(a.id)}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </div>
              <p className="mt-2 line-clamp-3 text-xs text-muted-foreground">{a.content.slice(0, 220)}</p>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={!!viewing} onOpenChange={(v) => !v && setViewing(null)}>
        <DialogContent className="max-h-[80vh] max-w-2xl overflow-y-auto" data-testid="artifact-view-dialog">
          <DialogHeader><DialogTitle className="font-display">{viewing?.title}</DialogTitle>
            <DialogDescription>Saved artifact content and its provenance.</DialogDescription></DialogHeader>
          {viewing && (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
                <Badge variant="secondary" className="text-[10px]">{viewing.artifact_type}</Badge>
                {viewing.creator_name && <span>by {viewing.creator_name}</span>}
                {viewing.model_name && <span>model: {viewing.model_name}</span>}
                {viewing.run_id && <span className="font-mono">run: {viewing.run_id.slice(0, 12)}</span>}
                <span>{viewing.source}{viewing.source_ref ? ` · ${viewing.source_ref.slice(0, 12)}` : ""}</span>
              </div>
              <MarkdownView content={viewing.content} />
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
