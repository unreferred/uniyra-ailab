import React from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { LoadingSpinner, ErrorNote } from "@/components/common/Common";
import { nCost, nLatency, nNumber, nTokens } from "./format";
import { RunDetail } from "./types";

export const RunDetailDialog: React.FC<{ runId: string | null; onOpenChange: (open: boolean) => void }> = ({ runId, onOpenChange }) => {
  const { data, isLoading, isError, error } = useQuery<RunDetail>({
    queryKey: ["obs-run", runId],
    queryFn: async () => (await api.get(`/observability/runs/${runId}`)).data,
    enabled: Boolean(runId),
  });

  return (
    <Dialog open={runId != null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl" data-testid="run-detail-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">{data?.model_name || "Run detail"}</DialogTitle>
          <DialogDescription>
            {runId ? `Run ${runId}` : "Run details"}
            {data?.created_at ? ` · ${data.created_at}` : ""}
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <LoadingSpinner label="Loading run…" />
        ) : isError ? (
          <ErrorNote message={(error as any)?.message || "Could not load this run."} />
        ) : data ? (
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={data.status === "success" ? "default" : "destructive"} className="text-[10px] uppercase">{data.status}</Badge>
              {data.model_identifier && <Badge variant="outline" className="text-[10px]">{data.model_identifier}</Badge>}
              {data.provider_name && <Badge variant="secondary" className="text-[10px]">{data.provider_name}</Badge>}
              {data.context && <Badge variant="outline" className="text-[10px]">{data.context}</Badge>}
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <Field label="Latency" value={nLatency(data.latency_ms)} />
              <Field label="Input tokens" value={nTokens(data.input_tokens)} />
              <Field label="Output tokens" value={nTokens(data.output_tokens)} />
              <Field label="Cached" value={nTokens(data.cached_tokens)} />
              <Field label="Reasoning" value={nTokens(data.reasoning_tokens)} />
              <Field label="Cost" value={nCost(data.estimated_cost, data.cost_available)} />
              <Field label="Execution" value={data.execution_kind || "n/a"} />
              <Field label="Created" value={data.created_at || "n/a"} />
              <Field label="Run ID" value={data.run_id || data.id || "n/a"} mono />
            </div>
            {(() => {
              const rid = data.run_id || data.id;
              if (!rid) return null;
              return (
                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <Link
                    to={`/trace?run=${rid}`}
                    data-testid="run-detail-trace-link"
                    className="inline-flex h-8 items-center rounded-md border border-border px-3 text-xs font-medium transition-colors hover:bg-accent"
                  >
                    Trace
                  </Link>
                  <Link
                    to={`/context-lab?run=${rid}`}
                    data-testid="run-detail-context-link"
                    className="inline-flex h-8 items-center rounded-md border border-border px-3 text-xs font-medium transition-colors hover:bg-accent"
                  >
                    Context
                  </Link>
                </div>
              );
            })()}
            {data.error_message && <ErrorNote message={data.error_message} />}
            <p className="text-[11px] text-muted-foreground">
              {nNumber(data.input_tokens) === "n/a" || nNumber(data.output_tokens) === "n/a"
                ? "Token usage is partially unavailable for this run."
                : ""}
            </p>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
};

const Field: React.FC<{ label: string; value: string; mono?: boolean }> = ({ label, value, mono }) => (
  <div className="flex flex-col">
    <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <span className={mono ? "break-all font-mono text-xs" : "text-sm"}>{value}</span>
  </div>
);
