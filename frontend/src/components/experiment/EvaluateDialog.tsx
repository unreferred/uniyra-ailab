import React from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, Loader2, Scale, XCircle } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { CaseEvaluation, ModelRecord, Rubric } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNote, FamilyDot } from "@/components/common/Common";
import { nScore } from "./format";
import { EvaluateTarget } from "./types";

const METHODS = [
  { value: "exact_match", label: "Exact match" },
  { value: "contains", label: "Contains" },
  { value: "regex", label: "Regex" },
  { value: "token_overlap", label: "Token overlap" },
];

export const EvaluateDialog: React.FC<{
  experimentId: string;
  target: EvaluateTarget | null;
  models: ModelRecord[];
  rubrics: Rubric[];
  onOpenChange: (open: boolean) => void;
  onEvaluated: () => void;
}> = ({ experimentId, target, models, rubrics, onOpenChange, onEvaluated }) => {
  const [evaluatorType, setEvaluatorType] = React.useState<"deterministic" | "human" | "llm_judge">("deterministic");
  const [method, setMethod] = React.useState("exact_match");
  const [judgeModelId, setJudgeModelId] = React.useState("");
  const [rubricId, setRubricId] = React.useState("");
  const [score, setScore] = React.useState("");
  const [passed, setPassed] = React.useState<"unset" | "true" | "false">("unset");
  const [notes, setNotes] = React.useState("");
  const [result, setResult] = React.useState<CaseEvaluation | null>(null);

  React.useEffect(() => {
    if (target) {
      setResult(null);
      setNotes("");
      setScore("");
      setPassed("unset");
      const preferred = models.find((m) => m.enabled && m.tags?.includes("judge")) || models.find((m) => m.enabled);
      setJudgeModelId(preferred?.id || "");
      setRubricId(rubrics[0]?.id || "");
    }
  }, [target?.run_id]); // eslint-disable-line

  const evaluate = useMutation({
    mutationFn: async () => {
      if (!target) return null;
      const body: Record<string, any> = { run_id: target.run_id, evaluator_type: evaluatorType, notes };
      if (evaluatorType === "deterministic") body.method = method;
      if (evaluatorType === "human") {
        if (score.trim() !== "") body.score = Number(score);
        if (passed !== "unset") body.passed = passed === "true";
      }
      if (evaluatorType === "llm_judge") {
        body.judge_model_id = judgeModelId;
        if (rubricId) body.rubric_id = rubricId;
      }
      return (await api.post(`/experiments/${experimentId}/evaluate`, body)).data as CaseEvaluation;
    },
    onSuccess: (data) => {
      if (!data) return;
      setResult(data);
      if (data.status === "error") toast.error(data.error || "Evaluation failed");
      else toast.success("Evaluation recorded");
      onEvaluated();
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const humanInvalid = evaluatorType === "human" && score.trim() === "" && passed === "unset";
  const judgeInvalid = evaluatorType === "llm_judge" && !judgeModelId;

  return (
    <Dialog open={target != null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg" data-testid="evaluate-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">Evaluate run</DialogTitle>
          <DialogDescription>
            {target?.model_name || target?.label || "Selected run"}
            {target?.run_id ? ` · ${target.run_id}` : ""}
          </DialogDescription>
        </DialogHeader>

        <Tabs value={evaluatorType} onValueChange={(v) => setEvaluatorType(v as typeof evaluatorType)}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="deterministic" data-testid="evaluate-tab-deterministic">Deterministic</TabsTrigger>
            <TabsTrigger value="human" data-testid="evaluate-tab-human">Human</TabsTrigger>
            <TabsTrigger value="llm_judge" data-testid="evaluate-tab-judge">LLM judge</TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="space-y-3">
          {evaluatorType === "deterministic" && (
            <div>
              <label className="text-xs font-medium text-muted-foreground">Method</label>
              <Select value={method} onValueChange={setMethod}>
                <SelectTrigger className="mt-1 h-9" data-testid="evaluate-method"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {METHODS.map((m) => <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="mt-1 text-[11px] text-muted-foreground">Scored against the dataset case's expected output.</p>
            </div>
          )}

          {evaluatorType === "human" && (
            <div className="space-y-2">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Score (0–100, or 0–1)</label>
                <Input
                  type="number"
                  min="0"
                  max="100"
                  step="0.1"
                  value={score}
                  onChange={(e) => setScore(e.target.value)}
                  className="mt-1 h-9"
                  data-testid="evaluate-score"
                  placeholder="Optional"
                />
              </div>
              <div className="flex items-center gap-3">
                <label className="text-xs font-medium text-muted-foreground">Passed</label>
                <Select value={passed} onValueChange={(v) => setPassed(v as typeof passed)}>
                  <SelectTrigger className="h-8 w-32" data-testid="evaluate-passed"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="unset">Unset</SelectItem>
                    <SelectItem value="true">Passed</SelectItem>
                    <SelectItem value="false">Failed</SelectItem>
                  </SelectContent>
                </Select>
                <span className="text-[11px] text-muted-foreground">Provide a score, a pass/fail, or both.</span>
              </div>
            </div>
          )}

          {evaluatorType === "llm_judge" && (
            <div className="space-y-2">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Judge model</label>
                <Select value={judgeModelId} onValueChange={setJudgeModelId}>
                  <SelectTrigger className="mt-1 h-9" data-testid="evaluate-judge-model"><SelectValue placeholder="Select judge model" /></SelectTrigger>
                  <SelectContent>
                    {models.filter((m) => m.enabled).map((m) => (
                      <SelectItem key={m.id} value={m.id}>
                        <span className="flex items-center gap-2"><FamilyDot family={m.family} /> {m.display_name}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Rubric (optional)</label>
                <Select value={rubricId} onValueChange={setRubricId}>
                  <SelectTrigger className="mt-1 h-9" data-testid="evaluate-rubric"><SelectValue placeholder="No rubric" /></SelectTrigger>
                  <SelectContent>
                    {rubrics.map((r) => <SelectItem key={r.id} value={r.id}>{r.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}

          <div>
            <label className="text-xs font-medium text-muted-foreground">Notes</label>
            <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} className="mt-1" data-testid="evaluate-notes" />
          </div>

          <Button
            size="sm"
            data-testid="evaluate-submit"
            disabled={evaluate.isPending || humanInvalid || judgeInvalid}
            onClick={() => evaluate.mutate()}
          >
            {evaluate.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Scale className="mr-1.5 h-4 w-4" />}
            Run evaluation
          </Button>
          {humanInvalid && <p className="text-[11px] text-destructive">Enter a score or choose passed/failed.</p>}
          {judgeInvalid && <p className="text-[11px] text-destructive">Select a judge model.</p>}
        </div>

        {result && (
          <div className="rounded-md border border-border bg-muted/40 p-3" data-testid="evaluate-result">
            <div className="flex items-center gap-2">
              {result.status === "error" ? <XCircle className="h-4 w-4 text-destructive" /> : <CheckCircle2 className="h-4 w-4 text-[hsl(var(--brand-emerald))]" />}
              <span className="text-sm font-medium">{result.status === "error" ? "Evaluation error" : "Evaluation recorded"}</span>
              <span className="ml-auto font-mono text-xs">score: {nScore(result.parsed_score)}</span>
            </div>
            {result.error && <ErrorNote className="mt-2" message={result.error} />}
            <div className="mt-2">
              <Button size="sm" variant="outline" onClick={() => onOpenChange(false)} data-testid="evaluate-close">Close &amp; refresh</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};
