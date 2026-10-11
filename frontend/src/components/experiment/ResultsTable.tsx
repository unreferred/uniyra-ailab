import React from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart3, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { ModelRecord, Rubric } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Switch } from "@/components/ui/switch";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { CaseTable } from "./CaseTable";
import { EvaluateDialog } from "./EvaluateDialog";
import { RunDetailDialog } from "./RunDetailDialog";
import { nCost, nLatency, nNumber, nRate, nScore, nTokens, sumOrNull } from "./format";
import { ConfigurationRecord, EvaluateTarget, ResultsResponse } from "./types";

const CASE_PAGE_SIZE = 25;

export const ResultsTable: React.FC<{
  experimentId: string;
  configs: ConfigurationRecord[];
  models: ModelRecord[];
  rubrics: Rubric[];
  enabled: boolean;
}> = ({ experimentId, configs, models, rubrics, enabled }) => {
  const [includeCases, setIncludeCases] = React.useState(false);
  const [offset, setOffset] = React.useState(0);
  const [evalTarget, setEvalTarget] = React.useState<EvaluateTarget | null>(null);
  const [runId, setRunId] = React.useState<string | null>(null);

  const queryKey = ["experiment-results", experimentId, includeCases, offset];
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery<ResultsResponse>({
    queryKey,
    queryFn: async () => {
      const url = includeCases
        ? `/experiments/${experimentId}/results?include_cases=true&limit=${CASE_PAGE_SIZE}&offset=${offset}`
        : `/experiments/${experimentId}/results`;
      return (await api.get(url)).data;
    },
    enabled: enabled && Boolean(experimentId),
  });

  const rows = data?.configurations || [];
  const overallExecution = sumOrNull(rows.map((r) => r.execution_cost_total));
  const overallEvaluation = sumOrNull(rows.map((r) => r.evaluation_cost_total));
  const overallTotal = sumOrNull(rows.map((r) => r.total_experiment_cost));

  return (
    <Card className="space-y-4 p-4" data-testid="results-table">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-brand" />
          <span className="font-display font-semibold">Results</span>
          {data?.plan_status && <span className="text-[11px] text-muted-foreground">plan: {data.plan_status}</span>}
          {isFetching && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        </div>
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>Load cases</span>
          <Switch
            data-testid="load-cases-toggle"
            checked={includeCases}
            onCheckedChange={(v) => { setIncludeCases(v); setOffset(0); }}
          />
        </label>
      </div>

      {isError && <ErrorNote message={(error as any)?.message || "Could not load results."} />}

      {isLoading ? (
        <LoadingSpinner label="Loading results…" />
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="results-empty">
          {data?.note || "No benchmark aggregates yet. Start a benchmark to collect results."}
        </p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <CostSummary label="Execution cost" value={overallExecution} testId="overall-execution-cost" />
            <CostSummary label="Evaluation cost" value={overallEvaluation} testId="overall-evaluation-cost" />
            <CostSummary label="Total experiment cost" value={overallTotal} testId="overall-total-cost" />
          </div>

          <div className="overflow-x-auto rounded-lg border border-border">
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/50">
                  {[
                    "Label", "Model", "Cases", "Completed", "Failed", "Success", "Evaluated",
                    "Avg score", "Median score", "Avg latency", "Median latency", "P95 latency", "Latency n",
                    "In tokens", "Out tokens", "Cached", "Reasoning", "Known usage", "Unknown usage",
                    "Known cost", "Unknown cost runs", "Avg cost/task", "Eval cost", "Exec cost", "Total cost",
                  ].map((h) => (
                    <TableHead key={h} className="whitespace-nowrap text-[10px] font-mono uppercase tracking-wider">{h}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.configuration_id} data-testid={`result-row-${r.configuration_id}`}>
                    <TableCell className="whitespace-nowrap font-medium">{r.label || r.configuration_id}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {r.model_snapshot?.display_name || r.model_id || "n/a"}
                    </TableCell>
                    <Num v={r.total_cases} />
                    <Num v={r.completed} />
                    <Num v={r.failed} />
                    <Num v={r.success_rate} fmt={nRate} />
                    <Num v={r.evaluated_count} />
                    <Num v={r.avg_score} fmt={nScore} />
                    <Num v={r.median_score} fmt={nScore} />
                    <Num v={r.avg_latency_ms} fmt={nLatency} />
                    <Num v={r.median_latency_ms} fmt={nLatency} />
                    <Num v={r.p95_latency_ms} fmt={nLatency} />
                    <Num v={r.latency_sample_count} />
                    <Num v={r.total_input_tokens} fmt={nTokens} />
                    <Num v={r.total_output_tokens} fmt={nTokens} />
                    <Num v={r.cached_tokens} fmt={nTokens} />
                    <Num v={r.reasoning_tokens} fmt={nTokens} />
                    <Num v={r.known_usage_run_count} />
                    <Num v={r.unknown_usage_run_count} />
                    <Num v={r.known_cost_total} fmt={(v: number | null | undefined) => nCost(v)} />
                    <Num v={r.unknown_cost_run_count} />
                    <Num v={r.avg_known_cost_per_task} fmt={(v: number | null | undefined) => nCost(v)} />
                    <Num v={r.evaluation_cost_total} fmt={(v: number | null | undefined) => nCost(v)} />
                    <Num v={r.execution_cost_total} fmt={(v: number | null | undefined) => nCost(v)} />
                    <Num v={r.total_experiment_cost} fmt={(v: number | null | undefined) => nCost(v)} />
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Unknown values are shown as “n/a”. Costs are summed only when every run in the group has a known price.
          </p>
        </>
      )}

      {includeCases && (
        <CaseTable
          cases={data?.cases ?? null}
          configs={rows}
          loading={isFetching}
          onPage={setOffset}
          onEvaluate={setEvalTarget}
          onOpenRun={setRunId}
        />
      )}

      <EvaluateDialog
        experimentId={experimentId}
        target={evalTarget}
        models={models}
        rubrics={rubrics}
        onOpenChange={(open) => { if (!open) setEvalTarget(null); }}
        onEvaluated={() => { refetch(); }}
      />
      <RunDetailDialog runId={runId} onOpenChange={(open) => { if (!open) setRunId(null); }} />
    </Card>
  );
};

const Num: React.FC<{ v: number | null | undefined; fmt?: (v: number | null | undefined) => string }> = ({ v, fmt }) => (
  <TableCell className="whitespace-nowrap font-mono text-xs tabular">{fmt ? fmt(v) : nNumber(v)}</TableCell>
);

const CostSummary: React.FC<{ label: string; value: number | null; testId: string }> = ({ label, value, testId }) => (
  <div className="rounded-md border border-border bg-muted/30 p-3" data-testid={testId}>
    <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</div>
    <div className="font-mono text-sm font-semibold tabular">{nCost(value)}</div>
  </div>
);
