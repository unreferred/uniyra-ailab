import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Loader2 } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ErrorNote } from "@/components/common/Common";
import { formatMetric, formatScore } from "./outcome";

export type MetricClassification = "improved" | "unchanged" | "degraded" | "unknown";
export type CaseClassification =
  | "improved"
  | "regressed"
  | "unchanged"
  | "incomparable"
  | "unknown";

export interface RegressionMetric {
  metric: string;
  baseline: number | string | null;
  candidate: number | string | null;
  delta: number | null;
  classification: MetricClassification;
}

export interface RegressionCase {
  case_id: string;
  baseline_score: number | null;
  candidate_score: number | null;
  delta?: number | null;
  classification: CaseClassification;
}

export interface RegressionResult {
  id?: string;
  experiment_id?: string;
  baseline_configuration_id?: string;
  candidate_configuration_id?: string;
  baseline_label?: string;
  candidate_label?: string;
  metrics?: RegressionMetric[];
  cases?: RegressionCase[];
  created_at?: string;
}

export interface RegressionPanelProps {
  experimentId: string;
  configurations: { id: string; label?: string }[];
}

function normalizeList(data: any): RegressionResult[] {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.items)) return data.items;
  if (data?.metrics || data?.cases) return [data];
  if (data?.result && (data.result.metrics || data.result.cases)) return [data.result];
  return [];
}

function pickLatest(results: RegressionResult[]): RegressionResult | null {
  if (results.length === 0) return null;
  const withDate = results.filter((r) => !!r.created_at);
  if (withDate.length > 0) {
    return [...withDate].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0];
  }
  return results[results.length - 1];
}

function metricClasses(c: MetricClassification): string {
  switch (c) {
    case "improved":
      return "border-emerald-500/40 bg-emerald-500/15 text-emerald-600";
    case "degraded":
      return "border-destructive/40 bg-destructive/15 text-destructive";
    case "unchanged":
      return "border-border bg-muted text-muted-foreground";
    case "unknown":
    default:
      return "border-amber-500/40 bg-amber-500/15 text-amber-600";
  }
}

function caseClasses(c: CaseClassification): string {
  switch (c) {
    case "improved":
      return "border-emerald-500/40 bg-emerald-500/15 text-emerald-600";
    case "regressed":
      return "border-destructive/40 bg-destructive/15 text-destructive";
    case "unchanged":
      return "border-border bg-muted text-muted-foreground";
    case "incomparable":
      return "border-sky-500/40 bg-sky-500/15 text-sky-600";
    case "unknown":
    default:
      return "border-amber-500/40 bg-amber-500/15 text-amber-600";
  }
}

export const RegressionPanel: React.FC<RegressionPanelProps> = ({ experimentId, configurations }) => {
  const queryClient = useQueryClient();
  const hasTwo = configurations.length >= 2;

  const [baseline, setBaseline] = React.useState<string>(() => configurations[0]?.id ?? "");
  const [candidate, setCandidate] = React.useState<string>(() => configurations[1]?.id ?? "");

  React.useEffect(() => {
    if (configurations.length >= 2) {
      setBaseline((prev) => (configurations.some((c) => c.id === prev) ? prev : configurations[0].id));
      setCandidate((prev) => (configurations.some((c) => c.id === prev) ? prev : configurations[1].id));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configurations]);

  const listQuery = useQuery<RegressionResult[]>({
    queryKey: ["regression", experimentId],
    queryFn: async () =>
      normalizeList((await api.get(`/experiments/${experimentId}/regression`)).data),
  });

  const mutation = useMutation({
    mutationFn: async () =>
      (
        await api.post(`/experiments/${experimentId}/regression`, {
          baseline_configuration_id: baseline,
          candidate_configuration_id: candidate,
        })
      ).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["regression", experimentId] });
    },
  });

  const result = (mutation.data as RegressionResult | undefined) || pickLatest(listQuery.data || []);

  const labelFor = (id: string, fallback?: string) =>
    configurations.find((c) => c.id === id)?.label || fallback || id || "n/a";

  const baselineLabel = labelFor(
    result?.baseline_configuration_id || baseline,
    result?.baseline_label
  );
  const candidateLabel = labelFor(
    result?.candidate_configuration_id || candidate,
    result?.candidate_label
  );

  const canRun = hasTwo && !!baseline && !!candidate && baseline !== candidate;

  const metrics = result?.metrics || [];
  const cases = result?.cases || [];

  return (
    <Card className="space-y-4 p-4" data-testid="regression-panel">
      <div className="flex items-center gap-2">
        <ArrowRight className="h-4 w-4 text-brand" />
        <span className="font-display font-semibold">Regression</span>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-52">
          <label className="text-xs text-muted-foreground">Baseline</label>
          <Select value={baseline} onValueChange={setBaseline} disabled={!hasTwo}>
            <SelectTrigger className="mt-1 h-8" data-testid="regression-baseline">
              <SelectValue placeholder="Baseline" />
            </SelectTrigger>
            <SelectContent>
              {configurations.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.label || c.id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="w-52">
          <label className="text-xs text-muted-foreground">Candidate</label>
          <Select value={candidate} onValueChange={setCandidate} disabled={!hasTwo}>
            <SelectTrigger className="mt-1 h-8" data-testid="regression-candidate">
              <SelectValue placeholder="Candidate" />
            </SelectTrigger>
            <SelectContent>
              {configurations.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.label || c.id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          type="button"
          size="sm"
          className="h-8"
          data-testid="run-regression"
          disabled={!canRun || mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          Run regression
        </Button>
      </div>

      {!hasTwo && (
        <p className="text-sm text-muted-foreground">
          At least two configurations are required to run a regression.
        </p>
      )}

      {mutation.isError && <ErrorNote message={apiError(mutation.error)} />}
      {listQuery.isError && <ErrorNote message={apiError(listQuery.error)} />}

      {result ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge variant="outline" className="text-[10px]" data-testid="regression-baseline-label">
              baseline: {baselineLabel}
            </Badge>
            <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
            <Badge variant="outline" className="text-[10px]" data-testid="regression-candidate-label">
              candidate: {candidateLabel}
            </Badge>
          </div>

          <section className="space-y-2">
            <h3 className="font-display text-sm font-semibold">Metric deltas</h3>
            {metrics.length === 0 ? (
              <p className="text-sm text-muted-foreground">No metric deltas available.</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border">
                <Table data-testid="regression-metrics">
                  <TableHeader>
                    <TableRow className="bg-muted/50">
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Metric</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Baseline</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Candidate</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Delta</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Change</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {metrics.map((m) => (
                      <TableRow key={m.metric} data-testid={`regression-metric-${m.metric}`}>
                        <TableCell className="text-xs">{m.metric}</TableCell>
                        <TableCell className="font-mono text-xs">{formatMetric(m.baseline)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatMetric(m.candidate)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatMetric(m.delta)}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className={metricClasses(m.classification)}>
                            {m.classification}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </section>

          <section className="space-y-2">
            <h3 className="font-display text-sm font-semibold">Case changes</h3>
            {cases.length === 0 ? (
              <p className="text-sm text-muted-foreground">No case-level changes available.</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border">
                <Table data-testid="regression-cases">
                  <TableHeader>
                    <TableRow className="bg-muted/50">
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Case</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Baseline</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Candidate</TableHead>
                      <TableHead className="text-[10px] font-mono uppercase tracking-wider">Change</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {cases.map((c) => (
                      <TableRow key={c.case_id} data-testid="regression-case-row">
                        <TableCell className="font-mono text-xs">{c.case_id}</TableCell>
                        <TableCell className="font-mono text-xs">{formatScore(c.baseline_score)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatScore(c.candidate_score)}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className={caseClasses(c.classification)}>
                            {c.classification}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </section>

          <p className="text-[11px] text-muted-foreground">
            No single aggregate score is computed. No statistical significance is computed. A
            classification of "unknown" means the telemetry is missing or incomparable — it is not
            a zero.
          </p>
        </div>
      ) : listQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading regression results…</p>
      ) : (
        <p className="text-sm text-muted-foreground">
          No regression has been run for this experiment yet.
        </p>
      )}
    </Card>
  );
};

export default RegressionPanel;
