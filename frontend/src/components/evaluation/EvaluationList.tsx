import React from "react";
import { useQuery } from "@tanstack/react-query";
import { api, apiError } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { EvaluationDetail, formatScore, outcomeClasses, outcomeLabel } from "./outcome";
import { EvaluationDetailDialog } from "./EvaluationDetailDialog";

export interface EvaluationListProps {
  experimentId: string;
}

function normalizeList(data: any): EvaluationDetail[] {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.items)) return data.items;
  if (Array.isArray(data?.evaluations)) return data.evaluations;
  return [];
}

function evaluatorLabel(e: EvaluationDetail): string {
  const type = e.evaluator?.type || e.evaluator_type || e.eval_type || "n/a";
  const key = e.evaluator?.key;
  return key ? `${type} / ${key}` : type;
}

function passedText(e: EvaluationDetail): string {
  const passed = e.result?.passed;
  if (passed === null || passed === undefined) return "n/a";
  return passed ? "true" : "false";
}

function scoreOf(e: EvaluationDetail): number | null {
  if (e.result?.score !== undefined) return e.result.score;
  if (e.parsed_score !== undefined) return e.parsed_score;
  return null;
}

export const EvaluationList: React.FC<EvaluationListProps> = ({ experimentId }) => {
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = React.useState(false);

  const { data, isLoading, isError, error } = useQuery<EvaluationDetail[]>({
    queryKey: ["evaluations", experimentId],
    queryFn: async () =>
      normalizeList(
        (await api.get(`/experiments/${experimentId}/evaluations?limit=50`)).data
      ),
  });

  const rows = data || [];

  const openDetails = (id: string) => {
    setSelectedId(id);
    setDialogOpen(true);
  };

  return (
    <Card className="space-y-3 p-4" data-testid="evaluation-list-card">
      <div className="flex items-center justify-between">
        <span className="font-display font-semibold">Evaluations</span>
      </div>

      {isLoading ? (
        <LoadingSpinner label="Loading evaluations…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="evaluation-list-empty">
          No evaluations yet.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table data-testid="evaluation-list">
            <TableHeader>
              <TableRow className="bg-muted/50">
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Created</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Evaluator</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Outcome</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Score</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Passed</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((e) => {
                const outcome = outcomeLabel(e);
                return (
                  <TableRow key={e.id} data-testid="evaluation-list-row">
                    <TableCell className="font-mono text-[11px]">{e.created_at || "n/a"}</TableCell>
                    <TableCell className="text-xs">{evaluatorLabel(e)}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={outcomeClasses(outcome)}>
                        {outcome}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{formatScore(scoreOf(e))}</TableCell>
                    <TableCell className="font-mono text-xs">{passedText(e)}</TableCell>
                    <TableCell>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-7 text-xs"
                        data-testid={`evaluation-details-${e.id}`}
                        onClick={() => openDetails(e.id)}
                      >
                        Details
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      <EvaluationDetailDialog
        evaluationId={selectedId}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
      />
    </Card>
  );
};

export default EvaluationList;
