import React from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftRight, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ErrorNote } from "@/components/common/Common";
import { nCost, nLatency, nRate, nScore } from "./format";
import { CompareResponse, ConfigurationRecord } from "./types";

const NONE = "__none__";

export const ComparePanel: React.FC<{
  experimentId: string;
  configs: ConfigurationRecord[];
  enabled: boolean;
}> = ({ experimentId, configs, enabled }) => {
  const [baseline, setBaseline] = React.useState<string>(NONE);
  const [candidate, setCandidate] = React.useState<string>(NONE);

  React.useEffect(() => {
    if (configs.length >= 2 && baseline === NONE && candidate === NONE) {
      setBaseline(configs[0].id);
      setCandidate(configs[1].id);
    }
  }, [configs]); // eslint-disable-line

  const ready = baseline !== NONE && candidate !== NONE && baseline !== candidate;

  const { data, isLoading, isError, error } = useQuery<CompareResponse>({
    queryKey: ["experiment-compare", experimentId, baseline, candidate],
    queryFn: async () => (await api.get(`/experiments/${experimentId}/compare?baseline=${baseline}&candidate=${candidate}`)).data,
    enabled: enabled && ready,
  });

  const caseRows = data?.changed_cases && data.changed_cases.length > 0 ? data.changed_cases : data?.cases || [];

  return (
    <Card className="space-y-4 p-4" data-testid="compare-panel">
      <div className="flex items-center gap-2">
        <ArrowLeftRight className="h-4 w-4 text-brand" />
        <span className="font-display font-semibold">Compare configurations</span>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-52">
          <label className="text-xs text-muted-foreground">Baseline</label>
          <Select value={baseline} onValueChange={setBaseline}>
            <SelectTrigger className="mt-1 h-8" data-testid="compare-baseline"><SelectValue placeholder="Baseline" /></SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>Select…</SelectItem>
              {configs.map((c) => <SelectItem key={c.id} value={c.id}>{c.label || c.id}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="w-52">
          <label className="text-xs text-muted-foreground">Candidate</label>
          <Select value={candidate} onValueChange={setCandidate}>
            <SelectTrigger className="mt-1 h-8" data-testid="compare-candidate"><SelectValue placeholder="Candidate" /></SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>Select…</SelectItem>
              {configs.map((c) => <SelectItem key={c.id} value={c.id}>{c.label || c.id}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        {baseline !== NONE && baseline === candidate && (
          <span className="text-xs text-destructive">Baseline and candidate must differ.</span>
        )}
      </div>

      {!ready ? (
        <p className="text-sm text-muted-foreground">Select two different configurations to compare their benchmark results.</p>
      ) : isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Comparing…</div>
      ) : isError ? (
        <ErrorNote message={(error as any)?.message || "Could not compare these configurations."} />
      ) : data ? (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <Badge variant={data.comparable ? "secondary" : "outline"} className="text-[10px]">
              {data.comparable ? "comparable" : "not comparable"}
            </Badge>
            <span className="text-[11px] text-muted-foreground">No statistical significance is computed.</span>
          </div>

          <div className="grid gap-3 sm:grid-cols-4">
            <Delta label="Δ avg score" value={data.deltas?.avg_score} fmt={nScore} testId="delta-score" />
            <Delta label="Δ success rate" value={data.deltas?.success_rate} fmt={nRate} testId="delta-success" />
            <Delta label="Δ avg latency" value={data.deltas?.avg_latency_ms} fmt={nLatency} testId="delta-latency" />
            <Delta label="Δ known cost" value={data.deltas?.known_cost_total} fmt={(v) => nCost(v)} testId="delta-cost" />
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="compare-counts">
            <Count label="Improved" value={data.case_counts?.improved} />
            <Count label="Regressed" value={data.case_counts?.regressed} />
            <Count label="Unchanged" value={data.case_counts?.unchanged} />
            <Count label="Incomparable" value={data.case_counts?.incomparable_error} />
          </div>

          {caseRows.length === 0 ? (
            <p className="text-sm text-muted-foreground">No case-level changes to show.</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/50">
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Case</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Change</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Baseline</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Candidate</TableHead>
                    <TableHead className="text-[10px] font-mono uppercase tracking-wider">Δ</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {caseRows.map((c) => (
                    <TableRow key={c.case_id} data-testid="compare-case-row">
                      <TableCell className="max-w-[320px] text-xs">
                        <div className="line-clamp-2">{c.input_snippet || c.case_id}</div>
                        <div className="font-mono text-[10px] text-muted-foreground">{c.case_id}</div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={c.classification === "improved" ? "default" : c.classification === "regressed" ? "destructive" : "outline"}
                          className="text-[10px]"
                        >
                          {c.classification}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{nScore(c.baseline_score)}</TableCell>
                      <TableCell className="font-mono text-xs">{nScore(c.candidate_score)}</TableCell>
                      <TableCell className="font-mono text-xs">{nScore(c.delta ?? null)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      ) : null}
    </Card>
  );
};

const Delta: React.FC<{ label: string; value: number | null | undefined; fmt: (v: number | null | undefined) => string; testId: string }> = ({ label, value, fmt, testId }) => (
  <div className="rounded-md border border-border bg-muted/30 p-3" data-testid={testId}>
    <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</div>
    <div className="font-mono text-sm font-semibold tabular">{fmt(value)}</div>
  </div>
);

const Count: React.FC<{ label: string; value?: number }> = ({ label, value }) => (
  <div className="flex flex-col">
    <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <span className="font-mono text-sm font-semibold tabular">{value ?? "n/a"}</span>
  </div>
);
