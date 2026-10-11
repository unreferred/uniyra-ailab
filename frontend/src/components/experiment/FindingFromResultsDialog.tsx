import React from "react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Lightbulb, Loader2 } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNote } from "@/components/common/Common";
import { ConfigurationRecord } from "./types";

export const FindingFromResultsDialog: React.FC<{
  experimentId: string;
  configs: ConfigurationRecord[];
  runs: Array<{ id: string; model_name?: string | null; status?: string | null; dataset_case_id?: string | null }>;
  evaluations: Array<{ id: string; run_id?: string | null; evaluator_type?: string | null; eval_type?: string | null; parsed_score?: number | null }>;
  datasetVersionId?: string | null;
}> = ({ experimentId, configs, runs, evaluations, datasetVersionId }) => {
  const [open, setOpen] = React.useState(false);
  const [title, setTitle] = React.useState("");
  const [statement, setStatement] = React.useState("");
  const [confidence, setConfidence] = React.useState("medium");
  const [configIds, setConfigIds] = React.useState<string[]>([]);
  const [runIds, setRunIds] = React.useState<string[]>([]);
  const [evaluationIds, setEvaluationIds] = React.useState<string[]>([]);
  const [includeDatasetVersion, setIncludeDatasetVersion] = React.useState(false);
  const [createdId, setCreatedId] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setCreatedId(null);
  }, [open]);

  const toggle = (list: string[], setList: (v: string[]) => void, id: string) =>
    setList(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

  const create = useMutation({
    mutationFn: async () =>
      (await api.post(`/experiments/${experimentId}/findings`, {
        title: title.trim(),
        statement,
        description: "",
        confidence,
        notes: "",
        evidence: {
          run_ids: runIds,
          evaluation_ids: evaluationIds,
          configuration_ids: configIds,
          dataset_version_id: includeDatasetVersion ? datasetVersionId || null : null,
        },
      })).data,
    onSuccess: (finding) => {
      setCreatedId(finding?.id || "created");
      toast.success("Finding created from results");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const reset = () => {
    setTitle(""); setStatement(""); setConfidence("medium");
    setConfigIds([]); setRunIds([]); setEvaluationIds([]); setIncludeDatasetVersion(false);
    setCreatedId(null);
  };

  return (
    <>
      <Button size="sm" variant="outline" data-testid="finding-from-results" onClick={() => setOpen(true)}>
        <Lightbulb className="mr-1.5 h-4 w-4" /> Create finding from results
      </Button>

      <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) reset(); }}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl" data-testid="finding-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Create finding from results</DialogTitle>
            <DialogDescription>
              Attach only the evidence you select. Findings are saved as provisional and never claim a conclusion automatically.
            </DialogDescription>
          </DialogHeader>

          {createdId ? (
            <div className="space-y-3" data-testid="finding-created">
              <p className="text-sm">Finding created.</p>
              <Link to="/findings" className="text-sm font-medium text-primary underline underline-offset-4" data-testid="finding-success-link">
                View in Findings →
              </Link>
            </div>
          ) : (
            <div className="space-y-4">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Title</label>
                <Input value={title} onChange={(e) => setTitle(e.target.value)} className="mt-1" data-testid="finding-title" placeholder="Short finding title" />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Statement</label>
                <Textarea value={statement} onChange={(e) => setStatement(e.target.value)} rows={3} className="mt-1" data-testid="finding-statement" placeholder="State what the results show. No unsupported claims." />
              </div>
              <div className="w-40">
                <label className="text-xs font-medium text-muted-foreground">Confidence</label>
                <Select value={confidence} onValueChange={setConfidence}>
                  <SelectTrigger className="mt-1 h-8" data-testid="finding-confidence"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="low">Low</SelectItem>
                    <SelectItem value="medium">Medium</SelectItem>
                    <SelectItem value="high">High</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <EvidenceList
                title="Configurations"
                empty="No configurations."
                items={configs.map((c) => ({ id: c.id, label: c.label || c.id }))}
                selected={configIds}
                onToggle={(id) => toggle(configIds, setConfigIds, id)}
              />
              <EvidenceList
                title={`Runs (${runs.length})`}
                empty="No benchmark runs."
                items={runs.slice(0, 500).map((r) => ({ id: r.id, label: `${r.model_name || "run"} · ${r.status || "n/a"}${r.dataset_case_id ? ` · ${r.dataset_case_id.slice(0, 8)}` : ""}` }))}
                selected={runIds}
                onToggle={(id) => toggle(runIds, setRunIds, id)}
              />
              <EvidenceList
                title={`Evaluations (${evaluations.length})`}
                empty="No evaluations."
                items={evaluations.slice(0, 500).map((e) => ({ id: e.id, label: `${e.evaluator_type || e.eval_type || "evaluation"}${e.parsed_score != null ? ` · score ${e.parsed_score}` : ""}` }))}
                selected={evaluationIds}
                onToggle={(id) => toggle(evaluationIds, setEvaluationIds, id)}
              />

              {datasetVersionId && (
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox checked={includeDatasetVersion} onCheckedChange={(v) => setIncludeDatasetVersion(v === true)} data-testid="finding-dataset-version" />
                  Include dataset version {datasetVersionId}
                </label>
              )}

              {create.isError && <ErrorNote message={apiError(create.error)} />}

              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
                <Button data-testid="finding-submit" disabled={!title.trim() || create.isPending} onClick={() => create.mutate()}>
                  {create.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null} Create finding
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
};

const EvidenceList: React.FC<{
  title: string;
  empty: string;
  items: { id: string; label: string }[];
  selected: string[];
  onToggle: (id: string) => void;
}> = ({ title, empty, items, selected, onToggle }) => (
  <div>
    <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{title}</h4>
    {items.length === 0 ? (
      <p className="text-xs text-muted-foreground">{empty}</p>
    ) : (
      <div className="max-h-32 space-y-1 overflow-y-auto rounded-md border border-border p-2">
        {items.map((item) => (
          <label key={item.id} className="flex items-center gap-2 text-xs">
            <Checkbox checked={selected.includes(item.id)} onCheckedChange={() => onToggle(item.id)} />
            <span className="truncate">{item.label}</span>
          </label>
        ))}
      </div>
    )}
  </div>
);
