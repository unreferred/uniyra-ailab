import React, { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { Eye, Gavel, Trophy, Loader2, ShieldQuestion, Scale, BadgeCheck } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { Experiment, Run, Evaluation } from "@/lib/types";
import { useModels, useRubrics } from "@/lib/hooks";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { MarkdownView } from "@/components/common/Markdown";
import { CopyButton, TelemetryRow, ErrorNote, FamilyDot, Metric } from "@/components/common/Common";
import { fmtLatency, fmtTokens } from "@/lib/format";
import { cn } from "@/lib/utils";

export const ArenaResults: React.FC<{ experiment: Experiment; onRefetch: () => Promise<unknown> | void }> = ({ experiment, onRefetch }) => {
  const runs = experiment.runs || [];
  const evaluations = experiment.evaluations || [];
  const { data: models = [] } = useModels();
  const { data: rubrics = [] } = useRubrics();

  // Blind identity is server-driven: `revealed` only becomes true after an
  // explicit POST /reveal persists a reveal event. Non-blind experiments always
  // show identity.
  const isBlind = !!experiment.blind;
  const revealed = experiment.revealed === true;
  const showIdentity = !isBlind || revealed;
  const blindActive = isBlind && !revealed;

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [winnerConflict, setWinnerConflict] = useState<string | null>(null);
  const [judgeModel, setJudgeModel] = useState("");
  const [rubricId, setRubricId] = useState("");

  React.useEffect(() => {
    const judgeTagged = models.find((m) => m.enabled && m.tags.includes("judge"));
    if (!judgeModel && (judgeTagged || models[0])) setJudgeModel((judgeTagged || models.find((m) => m.enabled) || models[0])?.id || "");
    if (!rubricId && rubrics[0]) setRubricId(rubrics[0].id);
  }, [models, rubrics]); // eslint-disable-line

  const latestJudge = [...evaluations].reverse().find((e) => e.eval_type === "ai_judge");
  const humanEvals = evaluations.filter((e) => e.eval_type === "human");
  const judgeScoreFor = (runId: string) => latestJudge?.result?.evaluations?.find((x) => x.run_id === runId);

  const revealMut = useMutation({
    mutationFn: async () => (await api.post(`/experiments/${experiment.id}/reveal`)).data,
    onSuccess: async () => { toast.success("Model identities revealed"); await onRefetch(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const judgeMut = useMutation({
    mutationFn: async () => (await api.post(`/experiments/${experiment.id}/judge`, { judge_model_id: judgeModel, rubric_id: rubricId })).data,
    onSuccess: async (ev: Evaluation) => {
      if (ev.status === "error") toast.error(ev.error || "Judge failed to produce a valid verdict");
      else toast.success("Judge verdict recorded");
      await onRefetch();
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const humanMut = useMutation({
    mutationFn: async ({ runId, replace }: { runId: string; replace?: boolean }) =>
      (await api.post(`/experiments/${experiment.id}/human-eval`, { run_id: runId, winner: true, replace: !!replace })).data,
    onSuccess: async () => {
      setWinnerConflict(null);
      toast.success("Your pick was recorded");
      await onRefetch();
    },
    onError: (e: any, vars) => {
      if (e?.response?.status === 409) {
        setWinnerConflict(vars.runId);
        toast.warning("A winner is already recorded for this experiment.");
        return;
      }
      toast.error(apiError(e));
    },
  });

  const cols = runs.length >= 4 ? "xl:grid-cols-4" : runs.length === 3 ? "xl:grid-cols-3" : "xl:grid-cols-2";
  const winnerRunId = latestJudge?.result?.winner_run_id;
  const rubric = latestJudge?.rubric_snapshot;

  const runText = (r: Run) => r.normalized_response ?? r.content ?? "";
  const runLabel = (r: Run, idx: number) => r.label || `Response ${String.fromCharCode(65 + idx)}`;
  const runError = (r: Run) => r.error_message || (typeof r.error === "string" ? r.error : r.error?.message) || "This run failed";

  return (
    <div className="space-y-5">
      {/* task + controls */}
      <Card className="p-4" data-testid="arena-task-card">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Task</span>
            <p className="mt-1 whitespace-pre-wrap text-sm">{experiment.user_prompt}</p>
            {experiment.system_prompt ? <p className="mt-2 text-xs text-muted-foreground"><span className="font-medium">System:</span> {experiment.system_prompt}</p> : null}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-2">
            {isBlind && (
              <Badge variant="outline" className="gap-1" data-testid="blind-badge"><ShieldQuestion className="h-3 w-3" /> Blind arena</Badge>
            )}
            {isBlind && revealed && (
              <Badge variant="secondary" className="gap-1" data-testid="arena-revealed-badge"><BadgeCheck className="h-3 w-3" /> Revealed</Badge>
            )}
            {blindActive && (
              <Button size="sm" variant="outline" data-testid="arena-reveal" onClick={() => setConfirmOpen(true)} disabled={revealMut.isPending}>
                {revealMut.isPending ? <><Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> Revealing…</> : <><Eye className="mr-1.5 h-4 w-4" /> Reveal models</>}
              </Button>
            )}
          </div>
        </div>
      </Card>

      {winnerConflict && (
        <Card className="flex flex-wrap items-center justify-between gap-3 border-destructive/40 bg-destructive/5 p-3" data-testid="winner-conflict">
          <p className="text-sm text-destructive">A winner is already recorded for this experiment. Replace the previous winner?</p>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => setWinnerConflict(null)}>Cancel</Button>
            <Button size="sm" data-testid="replace-winner" disabled={humanMut.isPending} onClick={() => humanMut.mutate({ runId: winnerConflict, replace: true })}>
              Replace previous winner
            </Button>
          </div>
        </Card>
      )}

      {/* response panels */}
      <div className={cn("grid gap-4 md:grid-cols-2", cols)} data-testid="arena-panels">
        {runs.map((r: Run, idx: number) => {
          const text = runText(r);
          const js = judgeScoreFor(r.id);
          const isWinner = winnerRunId === r.id;
          const humanWin = humanEvals.some((h) => h.run_id === r.id && h.winner);
          return (
            <Card key={r.id} className={cn("flex flex-col p-4 fade-in-up", isWinner && "ring-1 ring-[hsl(var(--brand-amber))]")} data-testid={`arena-panel-${r.id}`}>
              <div className="mb-2 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {blindActive ? (
                    <Badge variant="outline" className="gap-1 font-display" data-testid={`arena-label-${r.id}`}>
                      <ShieldQuestion className="h-3 w-3" /> {runLabel(r, idx)}
                    </Badge>
                  ) : (
                    <span className="flex items-center gap-1.5 font-medium"><FamilyDot family={r.family} /> {r.model_name || "Unknown model"}</span>
                  )}
                  {isWinner && <Badge className="gap-1 bg-[hsl(var(--brand-amber))] text-black hover:bg-[hsl(var(--brand-amber))]"><Trophy className="h-3 w-3" /> Judge pick</Badge>}
                  {humanWin && <Badge variant="secondary" className="gap-1"><Trophy className="h-3 w-3" /> Your pick</Badge>}
                </div>
                {r.status === "success" && text && <CopyButton value={text} />}
              </div>

              <div className="min-h-[80px] flex-1">
                {r.status === "error" ? <ErrorNote message={runError(r)} /> : <MarkdownView content={text} />}
              </div>

              {js && (
                <div className="mt-3 rounded-md border border-border bg-muted/40 p-2.5" data-testid={`judge-score-${r.id}`}>
                  <div className="mb-1 flex items-center justify-between text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                    <span>Judge score</span><span className="font-semibold text-foreground">{js.total}</span>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(js.scores || {}).map(([k, v]) => (
                      <span key={k} className="rounded bg-background px-1.5 py-0.5 font-mono text-[10px]">{k}: {v}</span>
                    ))}
                  </div>
                  {js.rationale && <p className="mt-1.5 text-[11px] text-muted-foreground">{js.rationale}</p>}
                </div>
              )}

              <div className="mt-3 border-t border-border pt-2">
                {showIdentity ? (
                  <TelemetryRow t={{ latency_ms: r.latency_ms, input_tokens: r.input_tokens, output_tokens: r.output_tokens, estimated_cost: r.estimated_cost, cost_available: r.cost_available }} />
                ) : (
                  <div className="flex flex-wrap items-center gap-x-5 gap-y-1" data-testid="blind-telemetry-row">
                    <Metric label="latency" value={fmtLatency(r.latency_ms)} />
                    <Metric label="in tok" value={fmtTokens(r.input_tokens)} />
                    <Metric label="out tok" value={fmtTokens(r.output_tokens)} />
                  </div>
                )}
              </div>

              {r.status === "success" && (
                <Button size="sm" variant="ghost" className="mt-2 self-start" data-testid="arena-winner" disabled={humanMut.isPending} onClick={() => humanMut.mutate({ runId: r.id })}>
                  <Trophy className="mr-1.5 h-3.5 w-3.5" /> Pick as winner
                </Button>
              )}
            </Card>
          );
        })}
      </div>

      {/* judge controls */}
      <Card className="p-4" data-testid="judge-panel">
        <div className="mb-3 flex items-center gap-2"><Gavel className="h-4 w-4 text-brand" /><span className="font-display font-semibold">AI Judge</span></div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-56">
            <label className="text-xs text-muted-foreground">Judge model</label>
            <Select value={judgeModel} onValueChange={setJudgeModel}>
              <SelectTrigger className="mt-1 h-8" data-testid="judge-model-select"><SelectValue /></SelectTrigger>
              <SelectContent>{models.filter((m) => m.enabled).map((m) => <SelectItem key={m.id} value={m.id}><span className="flex items-center gap-2"><FamilyDot family={m.family} />{m.display_name}</span></SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="w-56">
            <label className="text-xs text-muted-foreground">Rubric</label>
            <Select value={rubricId} onValueChange={setRubricId}>
              <SelectTrigger className="mt-1 h-8" data-testid="judge-rubric-select"><SelectValue /></SelectTrigger>
              <SelectContent>{rubrics.map((r) => <SelectItem key={r.id} value={r.id}>{r.name}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <Button size="sm" data-testid="run-judge-button" disabled={!judgeModel || !rubricId || judgeMut.isPending} onClick={() => judgeMut.mutate()}>
            {judgeMut.isPending ? <><Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> Judging…</> : <><Scale className="mr-1.5 h-4 w-4" /> Run AI Judge</>}
          </Button>
        </div>

        {latestJudge && latestJudge.status !== "error" && latestJudge.result && (
          <div className="mt-4 space-y-2 rounded-md border border-border bg-muted/30 p-3" data-testid="judge-verdict">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="font-medium">{latestJudge.judge_model_name ? `Verdict by ${latestJudge.judge_model_name}` : "Judge verdict"}</span>
              {latestJudge.result.confidence != null && <Badge variant="outline">confidence {(latestJudge.result.confidence * 100).toFixed(0)}%</Badge>}
              {rubric && <Badge variant="secondary">{rubric.name}</Badge>}
            </div>
            {latestJudge.result.summary && <p className="text-sm text-muted-foreground">{latestJudge.result.summary}</p>}
          </div>
        )}
        {latestJudge && latestJudge.status === "error" && <ErrorNote className="mt-3" message={latestJudge.error} />}
      </Card>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reveal model identities?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently reveals which model produced each response and shows provider, cost, and pricing telemetry. Revealing cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction data-testid="arena-reveal-confirm" onClick={() => revealMut.mutate()}>
              Reveal models
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};
