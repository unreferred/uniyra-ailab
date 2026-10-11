import React from "react";
import { ChevronLeft, ChevronRight, ExternalLink, Gavel } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EvaluationDetailDialog, EvaluationOutcome, outcomeClasses, outcomeLabel } from "@/components/evaluation";
import { nCost, nLatency, nScore, nTokens } from "./format";
import { AggregateRow, CaseRunCell, CasesPage, EvaluateTarget } from "./types";

type EvalCellLike = CaseRunCell & {
  outcome?: string | null;
  parsed_score?: number | null;
  threshold?: number | null;
  evaluation_status?: string | null;
  evaluation?: {
    result?: { outcome?: string | null; threshold?: number | null };
    status?: string | null;
    rubric?: { threshold?: number | null };
  } | null;
  result?: { outcome?: string | null } | null;
};

const KNOWN_OUTCOMES: EvaluationOutcome[] = ["PASS", "FAIL", "ERROR", "NOT_EVALUATED"];

function normalizeOutcome(value: unknown): EvaluationOutcome | null {
  return typeof value === "string" && (KNOWN_OUTCOMES as string[]).includes(value)
    ? (value as EvaluationOutcome)
    : null;
}

/**
 * Derive the outcome for a case-run cell defensively. An explicit
 * result.outcome always wins; otherwise we fall back to the evaluation status
 * (error -> ERROR) and only infer PASS/FAIL when both a score and a threshold
 * are actually present. A missing evaluation is NEVER rendered as PASS.
 */
function cellOutcome(cell: CaseRunCell): EvaluationOutcome {
  const c = cell as EvalCellLike;
  const explicit =
    normalizeOutcome(c.outcome) ??
    normalizeOutcome(c.evaluation?.result?.outcome) ??
    normalizeOutcome(c.result?.outcome);
  const status = c.evaluation?.status ?? c.evaluation_status ?? cell.status ?? null;
  const fromLabel = outcomeLabel({
    id: cell.evaluation_id || "",
    evaluation_type: "",
    status: status ?? undefined,
    result: explicit ? { outcome: explicit } : undefined,
  } as any);
  if (fromLabel !== "NOT_EVALUATED") return fromLabel;
  const score = c.parsed_score ?? cell.score ?? null;
  const threshold = c.threshold ?? c.evaluation?.result?.threshold ?? c.evaluation?.rubric?.threshold ?? null;
  if (typeof score === "number" && typeof threshold === "number") {
    return score >= threshold ? "PASS" : "FAIL";
  }
  return "NOT_EVALUATED";
}

export const CaseTable: React.FC<{
  cases: CasesPage | null;
  configs: AggregateRow[];
  loading: boolean;
  onPage: (offset: number) => void;
  onEvaluate: (target: EvaluateTarget) => void;
  onOpenRun: (runId: string) => void;
}> = ({ cases, configs, loading, onPage, onEvaluate, onOpenRun }) => {
  const [evalDialog, setEvalDialog] = React.useState<{ evaluationId: string | null; open: boolean }>({
    evaluationId: null,
    open: false,
  });
  const openEval = (evaluationId: string) => setEvalDialog({ evaluationId, open: true });

  if (!cases) return <p className="px-1 py-3 text-sm text-muted-foreground">{loading ? "Loading cases…" : "No case data."}</p>;
  const items = cases.items || [];
  if (items.length === 0) return <p className="px-1 py-3 text-sm text-muted-foreground" data-testid="cases-empty">No case runs to display yet.</p>;

  const from = cases.offset + 1;
  const to = cases.offset + items.length;

  return (
    <div className="space-y-2" data-testid="case-table">
      <div className="overflow-x-auto rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/50">
              <TableHead className="min-w-[220px] text-[10px] font-mono uppercase tracking-wider">Case</TableHead>
              {configs.map((c) => (
                <TableHead key={c.configuration_id} className="min-w-[210px] text-[10px] font-mono uppercase tracking-wider">
                  {c.label || c.configuration_id}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((row) => (
              <TableRow key={row.dataset_case_id} data-testid="case-row">
                <TableCell className="align-top">
                  <p className="line-clamp-3 max-w-[280px] text-xs" title={row.input_text || ""}>{row.input_text || "n/a"}</p>
                  <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">{row.dataset_case_id}</p>
                </TableCell>
                {configs.map((c) => (
                  <TableCell key={c.configuration_id} className="align-top">
                    <CaseCell
                      cell={row.configurations?.[c.configuration_id] || null}
                      onEvaluate={onEvaluate}
                      onOpenRun={onOpenRun}
                      onOpenEval={openEval}
                    />
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>Showing {from}–{to} of {cases.total}</span>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" disabled={cases.offset <= 0 || loading} onClick={() => onPage(Math.max(0, cases.offset - cases.limit))}>
            <ChevronLeft className="h-3.5 w-3.5" /> Prev
          </Button>
          <Button size="sm" variant="outline" disabled={to >= cases.total || loading} onClick={() => onPage(cases.offset + cases.limit)}>
            Next <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <EvaluationDetailDialog
        evaluationId={evalDialog.evaluationId}
        open={evalDialog.open}
        onOpenChange={(open) => setEvalDialog((prev) => ({ ...prev, open }))}
      />
    </div>
  );
};

const CaseCell: React.FC<{
  cell: CaseRunCell | null;
  onEvaluate: (target: EvaluateTarget) => void;
  onOpenRun: (runId: string) => void;
  onOpenEval: (evaluationId: string) => void;
}> = ({ cell, onEvaluate, onOpenRun, onOpenEval }) => {
  if (!cell || !cell.run_id) {
    return <span className="text-xs text-muted-foreground">n/a</span>;
  }
  const outcome = cell.evaluation_id ? cellOutcome(cell) : null;
  return (
    <div className="space-y-1 text-[11px]">
      <div className="flex items-center gap-1.5">
        <span className={cell.status === "success" ? "font-medium text-[hsl(var(--brand-emerald))]" : cell.status === "error" ? "font-medium text-destructive" : "font-medium"}>
          {cell.status || "n/a"}
        </span>
        <button type="button" className="text-muted-foreground hover:text-foreground" title="Open run detail" onClick={() => onOpenRun(cell.run_id as string)} data-testid={`case-run-${cell.run_id}`}>
          <ExternalLink className="h-3 w-3" />
        </button>
      </div>
      {outcome && (
        <div className="flex items-center gap-1.5">
          <span
            className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium ${outcomeClasses(outcome)}`}
            data-testid={`case-eval-outcome-${cell.run_id}`}
          >
            {outcome}
          </span>
          <button
            type="button"
            className="text-[10px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            data-testid={`case-eval-details-${cell.run_id}`}
            onClick={() => onOpenEval(cell.evaluation_id as string)}
          >
            Details
          </button>
        </div>
      )}
      <div className="grid grid-cols-2 gap-x-2 font-mono text-muted-foreground">
        <span>{nLatency(cell.latency_ms)}</span>
        <span>{`${nTokens(cell.input_tokens)} in / ${nTokens(cell.output_tokens)} out`}</span>
        <span>{nCost(cell.cost, cell.cost_known === false ? false : cell.cost !== null && cell.cost !== undefined)}</span>
        <span data-testid={`case-score-${cell.run_id}`}>score {nScore(cell.score)}</span>
      </div>
      <Button
        size="sm"
        variant="ghost"
        className="h-6 px-1.5 text-[10px]"
        data-testid={`case-evaluate-${cell.run_id}`}
        onClick={() => onEvaluate({ run_id: cell.run_id as string, label: cell.label, status: cell.status })}
      >
        <Gavel className="mr-1 h-3 w-3" /> Evaluate
      </Button>
    </div>
  );
};
