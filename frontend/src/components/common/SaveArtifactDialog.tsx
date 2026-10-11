import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, apiError } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { useModels } from "@/lib/hooks";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

export const ARTIFACT_TYPES = [
  { v: "research_report", l: "Research report" },
  { v: "architecture", l: "Architecture" },
  { v: "code", l: "Code" },
  { v: "prompt", l: "Prompt" },
  { v: "evaluation_report", l: "Evaluation report" },
  { v: "experiment_result", l: "Experiment result" },
  { v: "decision_memo", l: "Decision memo" },
  { v: "dataset", l: "Dataset" },
  { v: "generic_document", l: "Generic document" },
  // legacy (kept readable for older artifacts)
  { v: "research_note", l: "Research note (legacy)" },
  { v: "report", l: "Report (legacy)" },
  { v: "text", l: "Generic text (legacy)" },
];

export const SaveArtifactDialog: React.FC<{
  open: boolean; onOpenChange: (v: boolean) => void;
  content: string; defaultTitle?: string; source?: string; sourceRef?: string;
  modelId?: string; modelName?: string; runId?: string;
}> = ({ open, onOpenChange, content, defaultTitle, source = "workspace", sourceRef, modelId, modelName, runId }) => {
  const { activeId } = useProjects();
  const { user } = useAuth();
  const { data: models = [] } = useModels();
  const qc = useQueryClient();
  const [title, setTitle] = useState(defaultTitle || "");
  const [type, setType] = useState("research_report");
  const [selModelId, setSelModelId] = useState<string>("");
  const [selRunId, setSelRunId] = useState<string>("");

  React.useEffect(() => {
    if (open) {
      setTitle(defaultTitle || "");
      setSelModelId(modelId || "");
      setSelRunId(runId || "");
    }
  }, [open, defaultTitle, modelId, runId]);

  const mut = useMutation({
    mutationFn: async () => {
      const resolvedModel = models.find((m) => m.id === (selModelId || modelId));
      return (await api.post("/artifacts", {
        project_id: activeId, title, artifact_type: type, content, source, source_ref: sourceRef,
        creator_name: user?.name || user?.email || "",
        model_id: selModelId || modelId || undefined,
        model_name: resolvedModel?.display_name || modelName || undefined,
        run_id: selRunId || runId || undefined,
      })).data;
    },
    onSuccess: () => {
      toast.success("Saved as artifact");
      qc.invalidateQueries({ queryKey: ["artifacts"] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="save-artifact-dialog">
        <DialogHeader><DialogTitle className="font-display">Save as artifact</DialogTitle>
        <DialogDescription>Preserve this response as a project artifact, optionally linked to its model and run for provenance.</DialogDescription></DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Title</label>
            <Input data-testid="artifact-title-input" value={title} onChange={(e) => setTitle(e.target.value)} className="mt-1" placeholder="Artifact title" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Type</label>
            <Select value={type} onValueChange={setType}>
              <SelectTrigger className="mt-1" data-testid="artifact-type-select"><SelectValue /></SelectTrigger>
              <SelectContent>
                {ARTIFACT_TYPES.map((t) => <SelectItem key={t.v} value={t.v}>{t.l}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Model (provenance, optional)</label>
              <Select value={selModelId || "__none"} onValueChange={(v) => setSelModelId(v === "__none" ? "" : v)}>
                <SelectTrigger className="mt-1" data-testid="artifact-model-select"><SelectValue placeholder="None" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none">None</SelectItem>
                  {models.filter((m) => m.enabled).map((m) => <SelectItem key={m.id} value={m.id}>{m.display_name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Run ID (optional)</label>
              <Input data-testid="artifact-run-input" value={selRunId} onChange={(e) => setSelRunId(e.target.value)} className="mt-1 font-mono text-xs" placeholder="run id" />
            </div>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Content preview</label>
            <Textarea value={content} readOnly rows={5} className="mt-1 font-mono text-xs" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button data-testid="artifact-save-submit" disabled={!title.trim() || !activeId || mut.isPending} onClick={() => mut.mutate()}>
            {mut.isPending ? "Saving…" : "Save artifact"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
