import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, apiError } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import {
  EvaluationDetail,
  formatMetric,
  formatScore,
  outcomeClasses,
  outcomeLabel,
  redactSecret,
} from "./outcome";

export interface EvaluationDetailDialogProps {
  evaluationId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// Always surface these keys; absent ones render "n/a" rather than disappearing.
const CANONICAL_METRICS = [
  "tool_call_count",
  "tool_failure_count",
  "latency_ms",
  "ttft_ms",
  "input_tokens",
  "output_tokens",
  "cost",
  "knowledge_marker_count",
  "knowledge_citation_failures",
];

function severityClasses(severity: string): string {
  const s = (severity || "").toLowerCase();
  if (s === "critical" || s === "high" || s === "error" || s === "fatal") {
    return "border-destructive/40 bg-destructive/15 text-destructive";
  }
  if (s === "medium" || s === "warning" || s === "warn") {
    return "border-amber-500/40 bg-amber-500/15 text-amber-600";
  }
  if (s === "low" || s === "info") {
    return "border-sky-500/40 bg-sky-500/15 text-sky-600";
  }
  return "border-border bg-muted text-muted-foreground";
}

function metricEntries(metrics?: Record<string, number | string | null>) {
  const provided = Object.entries(metrics || {});
  const seen = new Set(provided.map(([k]) => k));
  const extras = CANONICAL_METRICS.filter((k) => !seen.has(k)).map((k) => [k, null] as const);
  return [...provided, ...extras];
}

function Field({ label, value, testId }: { label: string; value: string; testId?: string }) {
  return (
    <div className="flex flex-col" data-testid={testId}>
      <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className="font-mono text-sm">{value}</span>
    </div>
  );
}

export const EvaluationDetailDialog: React.FC<EvaluationDetailDialogProps> = ({
  evaluationId,
  open,
  onOpenChange,
}) => {
  const { data, isLoading, isError, error } = useQuery<EvaluationDetail>({
    queryKey: ["evaluation", evaluationId],
    queryFn: async () => (await api.get(`/evaluations/${evaluationId}`)).data,
    enabled: open && !!evaluationId,
  });

  const outcome = outcomeLabel(data);
  const result = data?.result;
  const findings = data?.findings || [];
  const scoreEntries = Object.entries(data?.scores || {});
  const metrics = metricEntries(data?.metrics);

  const evaluatorType = data?.evaluator?.type || data?.evaluator_type || data?.eval_type || "n/a";
  const evaluatorKey = data?.evaluator?.key;
  const evaluatorVersion = data?.evaluator?.version;

  const contextSnapshotId =
    data?.provenance?.context_snapshot_id ?? data?.evidence?.context_snapshot_id ?? null;
  const judgeModel = data?.judge?.model ?? data?.judge?.model_id ?? null;
  const judgeProvider = data?.judge?.provider ?? data?.judge?.provider_id ?? null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-h-[85vh] max-w-3xl overflow-y-auto"
        data-testid="evaluation-detail-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Badge
              variant="outline"
              className={outcomeClasses(outcome)}
              data-testid="evaluation-outcome"
            >
              {outcome}
            </Badge>
            <span>Evaluation detail</span>
          </DialogTitle>
          <DialogDescription data-testid="evaluation-evaluator">
            {evaluatorType}
            {evaluatorKey ? ` / ${redactSecret(evaluatorKey)}` : ""}
            {evaluatorVersion ? ` · v${redactSecret(evaluatorVersion)}` : ""}
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <LoadingSpinner label="Loading evaluation…" />
        ) : isError ? (
          <ErrorNote message={apiError(error)} />
        ) : data ? (
          <div className="space-y-5">
            {/* Result */}
            <section className="space-y-2">
              <h3 className="font-display text-sm font-semibold">Result</h3>
              <div className="grid gap-3 sm:grid-cols-4" data-testid="evaluation-result">
                <Field
                  label="Passed"
                  value={result ? (result.passed === null ? "n/a" : result.passed ? "true" : "false") : "n/a"}
                />
                <Field label="Score" value={formatScore(result?.score)} />
                <Field label="Threshold" value={formatScore(result?.threshold)} />
                {result?.confidence !== null && result?.confidence !== undefined && (
                  <Field label="Confidence" value={formatScore(result.confidence)} />
                )}
              </div>
              {result?.reason ? (
                <p className="text-xs text-muted-foreground">{redactSecret(result.reason)}</p>
              ) : null}
            </section>

            {/* Rubric */}
            <section className="space-y-2">
              <h3 className="font-display text-sm font-semibold">Rubric</h3>
              <div className="text-sm" data-testid="evaluation-rubric">
                {data.rubric ? (
                  <span className="font-mono text-xs">
                    {redactSecret(data.rubric.id || "rubric")}
                    {data.rubric.version !== undefined ? ` · v${formatMetric(data.rubric.version)}` : ""}
                    {` · threshold ${formatScore(data.rubric.threshold)}`}
                  </span>
                ) : (
                  <span className="text-muted-foreground">No rubric</span>
                )}
              </div>
            </section>

            {/* Scores by dimension */}
            {scoreEntries.length > 0 && (
              <section className="space-y-2" data-testid="evaluation-scores">
                <h3 className="font-display text-sm font-semibold">Scores by dimension</h3>
                <div className="divide-y divide-border rounded-lg border border-border">
                  {scoreEntries.map(([dimension, value]) => (
                    <div
                      key={dimension}
                      className="flex items-center justify-between px-3 py-1.5"
                      data-testid="evaluation-score-row"
                    >
                      <span className="text-xs text-muted-foreground">{dimension}</span>
                      <span className="font-mono text-xs">{formatScore(value)}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Findings */}
            <section className="space-y-2">
              <h3 className="font-display text-sm font-semibold">Findings</h3>
              <div className="space-y-2" data-testid="evaluation-findings">
                {findings.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No findings</p>
                ) : (
                  findings.map((f, i) => (
                    <div
                      key={f.id || `${f.category}-${i}`}
                      className="rounded-md border border-border p-2"
                      data-testid="evaluation-finding-row"
                    >
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className={severityClasses(f.severity)}>
                          {f.severity || "unknown"}
                        </Badge>
                        <span className="text-xs font-medium">{f.category}</span>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">{redactSecret(f.message)}</p>
                    </div>
                  ))
                )}
              </div>
            </section>

            {/* Metrics */}
            <section className="space-y-2">
              <h3 className="font-display text-sm font-semibold">Metrics</h3>
              <div className="overflow-x-auto rounded-lg border border-border">
                <Table data-testid="evaluation-metrics">
                  <TableHeader>
                    <TableRow className="bg-muted/50">
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Metric</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Value</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {metrics.map(([key, value]) => (
                      <TableRow key={key} data-testid={`evaluation-metric-${key}`}>
                        <TableCell className="text-xs">{key}</TableCell>
                        <TableCell className="font-mono text-xs">{formatMetric(value)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </section>

            {/* Evidence / provenance */}
            <section className="space-y-2">
              <h3 className="font-display text-sm font-semibold">Evidence &amp; provenance</h3>
              <div className="grid gap-3 sm:grid-cols-2" data-testid="evaluation-evidence">
                <Field
                  label="Run"
                  testId="evaluation-run"
                  value={data.run_id ? data.run_id : "n/a"}
                />
                <Field
                  label="Dataset case"
                  testId="evaluation-dataset-case"
                  value={redactSecret(data.dataset_case_id)}
                />
                <Field
                  label="Context snapshot"
                  testId="evaluation-context-snapshot"
                  value={redactSecret(contextSnapshotId)}
                />
                <Field
                  label="Evaluator version"
                  testId="evaluation-evaluator-version"
                  value={redactSecret(evaluatorVersion ?? data.provenance?.evaluator_version)}
                />
                <Field
                  label="Judge model"
                  testId="evaluation-judge-model"
                  value={redactSecret(judgeModel)}
                />
                <Field
                  label="Judge provider"
                  testId="evaluation-judge-provider"
                  value={redactSecret(judgeProvider)}
                />
              </div>
              {data.run_id ? (
                <Link
                  className="text-xs text-primary underline-offset-4 hover:underline"
                  to={`/trace?run=${data.run_id}`}
                  data-testid="evaluation-run-link"
                >
                  Open run trace
                </Link>
              ) : null}
            </section>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
};

export default EvaluationDetailDialog;
