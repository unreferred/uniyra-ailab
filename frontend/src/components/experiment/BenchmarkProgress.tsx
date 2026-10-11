import React from "react";
import { Ban, Loader2, Play, RotateCcw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { nNumber } from "./format";
import { BenchmarkResponse } from "./types";

const TERMINAL = ["completed", "failed", "cancelled"];

function Count({ label, value, testId }: { label: string; value: number; testId?: string }) {
  return (
    <div className="flex flex-col" data-testid={testId}>
      <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className="font-mono text-sm font-semibold tabular">{value}</span>
    </div>
  );
}

export const BenchmarkProgress: React.FC<{
  benchmark: BenchmarkResponse;
  onCancel: () => void;
  onResume: () => void;
  cancelling: boolean;
  resuming: boolean;
}> = ({ benchmark, onCancel, onResume, cancelling, resuming }) => {
  const plan = benchmark.plan;
  const progress = benchmark.progress || plan?.progress;

  if (!plan || !progress) {
    return (
      <Card className="p-4" data-testid="benchmark-progress">
        <div className="flex items-center gap-2">
          <Play className="h-4 w-4 text-muted-foreground" />
          <span className="font-display font-semibold">Benchmark progress</span>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">No plan has been started yet.</p>
      </Card>
    );
  }

  const total = progress.total || plan.total_planned_calls || 0;
  const done = progress.completed + progress.failed;
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const status = (plan.status || "").toLowerCase();
  const terminal = TERMINAL.includes(status);
  const running = status === "running";
  const cancelled = status === "cancelled";

  return (
    <Card className="space-y-4 p-4" data-testid="benchmark-progress">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Play className="h-4 w-4 text-brand" />
          <span className="font-display font-semibold">Benchmark progress</span>
          <Badge variant={running ? "default" : "outline"} className="text-[10px] uppercase">{plan.status}</Badge>
          {running && <span className="text-[11px] text-muted-foreground">live · refreshes every 2s</span>}
        </div>
        <div className="flex items-center gap-2">
          {running && (
            <Button size="sm" variant="outline" onClick={onCancel} disabled={cancelling} data-testid="benchmark-cancel">
              {cancelling ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Ban className="mr-1.5 h-4 w-4" />} Cancel
            </Button>
          )}
          {cancelled && (
            <Button size="sm" variant="outline" onClick={onResume} disabled={resuming} data-testid="benchmark-resume">
              {resuming ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-1.5 h-4 w-4" />} Resume
            </Button>
          )}
        </div>
      </div>

      <div className="space-y-1.5">
        <Progress value={pct} data-testid="benchmark-progress-bar" />
        <div className="flex justify-between text-[11px] text-muted-foreground">
          <span>{done} of {total} calls settled</span>
          <span>{pct}%</span>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
        <Count label="completed" value={progress.completed} testId="progress-completed" />
        <Count label="failed" value={progress.failed} testId="progress-failed" />
        <Count label="running" value={progress.running} testId="progress-running" />
        <Count label="pending" value={progress.pending} testId="progress-pending" />
        <Count label="skipped" value={progress.skipped} testId="progress-skipped" />
        <Count label="cancelled" value={progress.cancelled} testId="progress-cancelled" />
      </div>

      {plan.error_message && (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {plan.error_message}
        </p>
      )}
      {terminal && !plan.error_message && (
        <p className="text-[11px] text-muted-foreground">
          Plan finished with status <strong>{plan.status}</strong>. Aggregates below reflect the settled runs.
        </p>
      )}

      {benchmark.configurations && benchmark.configurations.length > 0 && (
        <div className="space-y-1.5 border-t border-border pt-3" data-testid="progress-per-config">
          {benchmark.configurations.map((c) => (
            <div key={c.configuration_id} className="flex items-center justify-between text-xs">
              <span className="truncate text-muted-foreground">{c.label || c.configuration_id}</span>
              <span className="font-mono tabular text-muted-foreground">
                {nNumber(c.completed)}/{nNumber(c.total)} completed · {nNumber(c.pending)} pending
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
};
