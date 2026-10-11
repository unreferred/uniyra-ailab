import React from "react";
import { AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { FinalizationTelemetryEntry, Run } from "@/lib/types";

const DASH = "\u2014";

function fmtValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return DASH;
  return String(value);
}

function fmtNum(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) return value.toLocaleString();
  if (typeof value === "string" && value.trim() !== "") return value;
  return DASH;
}

function fmtBool(value: unknown): string {
  if (value === true) return "yes";
  if (value === false) return "no";
  return DASH;
}

function recoveryLabel(value?: string | null): string {
  const raw = String(value || "").toUpperCase();
  if (raw.includes("LENGTH")) return "length";
  if (raw.includes("EMPTY")) return "empty";
  return value ? String(value) : DASH;
}

function isFailureOutcome(outcome?: string | null): boolean {
  if (!outcome) return false;
  const upper = String(outcome).toUpperCase();
  const markers = ["FAIL", "ERROR", "EXHAUST", "TIMEOUT", "INCOMPLETE", "PARTIAL", "TRUNC", "UNRECOVER"];
  return markers.some(function (m) {
    return upper.indexOf(m) >= 0;
  });
}

export const FinalizationDiagnostics: React.FC<{ run?: Run | null; className?: string }> = ({ run, className }) => {
  const telemetry: FinalizationTelemetryEntry[] =
    run && Array.isArray(run.finalization_telemetry) ? run.finalization_telemetry : [];

  const hasSignal = run
    ? Boolean(
        run.forced_finalization ||
          telemetry.length > 0 ||
          run.finalization_attempts != null ||
          run.finalization_outcome != null ||
          run.finalization_reason != null ||
          run.finalization_length_failure_kind != null ||
          run.finalization_recovery_mode != null ||
          run.finalization_context_mode != null
      )
    : false;

  if (!run || !hasSignal) return null;

  const attempts = typeof run.finalization_attempts === "number" ? run.finalization_attempts : null;
  const failureKind = run.finalization_length_failure_kind;
  const showWarning =
    (Boolean(failureKind) && attempts !== null && attempts >= 2) || isFailureOutcome(run.finalization_outcome);

  const original = run.original_context_estimated_tokens;
  const finalTokens = run.finalization_context_estimated_tokens;
  let reduction = "";
  if (typeof original === "number" && typeof finalTokens === "number" && original > 0 && original > finalTokens) {
    const saved = original - finalTokens;
    const pct = Math.round((saved / original) * 100);
    reduction = " \u00b7 reduction " + saved.toLocaleString() + " (" + pct + "%)";
  }

  return (
    <section
      data-testid="finalization-diagnostics"
      data-run-id={run.id || undefined}
      className={cn("space-y-2 rounded-md border border-border bg-muted/10 p-3", className)}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Finalization</h4>
        <Badge variant="outline" className="px-1.5 py-0 text-[9px]">
          attempts {fmtNum(run.finalization_attempts)}
        </Badge>
        <Badge variant="outline" className="px-1.5 py-0 text-[9px]">
          recovered {fmtBool(run.finalization_recovered)}
        </Badge>
        <Badge variant="outline" className="px-1.5 py-0 text-[9px]">
          outcome {fmtValue(run.finalization_outcome)}
        </Badge>
        <Badge variant="outline" className="px-1.5 py-0 text-[9px]">
          recovery_mode {fmtValue(run.finalization_recovery_mode)}
        </Badge>
        <Badge variant="outline" className="px-1.5 py-0 text-[9px]">
          finish_reason {fmtValue(run.provider_finish_reason)}
        </Badge>
      </div>

      {showWarning && (
        <div
          data-testid="finalization-warning"
          className="flex items-center gap-1.5 rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1 text-[10px] text-destructive"
        >
          <AlertTriangle className="h-3 w-3 shrink-0" />
          <span>
            Finalization issue: {failureKind ? failureKind : fmtValue(run.finalization_outcome)}
            {attempts !== null && attempts >= 2 ? " after " + attempts + " attempts" : ""}
          </span>
        </div>
      )}

      {(run.finalization_partial_answer || run.finalization_truncated) && (
        <div className="flex flex-wrap items-center gap-1">
          {run.finalization_partial_answer && (
            <Badge variant="destructive" className="px-1.5 py-0 text-[9px]">
              partial answer
            </Badge>
          )}
          {run.finalization_truncated && (
            <Badge variant="destructive" className="px-1.5 py-0 text-[9px]">
              truncated
            </Badge>
          )}
        </div>
      )}

      {run.finalization_reason && (
        <div className="break-words font-mono text-[10px] text-muted-foreground">reason: {run.finalization_reason}</div>
      )}

      {telemetry.length === 0 ? (
        <div className="font-mono text-[10px] text-muted-foreground" data-testid="finalization-telemetry-empty">
          No finalization telemetry recorded
        </div>
      ) : (
        <div className="overflow-x-auto scrollbar-thin" data-testid="finalization-telemetry">
          <table className="w-full min-w-[720px] border-collapse text-left font-mono text-[10px] text-muted-foreground">
            <thead>
              <tr className="border-b border-border">
                <th className="py-1 pr-2 font-medium">#</th>
                <th className="py-1 pr-2 font-medium">recovery</th>
                <th className="py-1 pr-2 font-medium">finish</th>
                <th className="py-1 pr-2 font-medium">max out req/eff</th>
                <th className="py-1 pr-2 font-medium">tokens in/out/reason/vis</th>
                <th className="py-1 pr-2 font-medium">ctx win/est/rem</th>
                <th className="py-1 pr-2 font-medium">length kind</th>
                <th className="py-1 pr-2 font-medium">effort</th>
                <th className="py-1 font-medium">reasoning/content</th>
              </tr>
            </thead>
            <tbody>
              {telemetry.map(function (entry, index) {
                const effort = fmtValue(entry.reasoning_effort);
                const effortText = effort === DASH ? DASH : effort + (entry.reasoning_reduction_applied ? " (applied)" : "");
                return (
                  <tr key={index} className="border-b border-border/40 align-top">
                    <td className="py-1 pr-2">{fmtNum(entry.finalization_attempt)}</td>
                    <td className="py-1 pr-2">
                      {fmtValue(entry.finalization_recovery) === DASH
                        ? recoveryLabel(entry.recovery_mode)
                        : fmtValue(entry.finalization_recovery)}
                    </td>
                    <td className="py-1 pr-2">{fmtValue(entry.finish_reason)}</td>
                    <td className="py-1 pr-2">
                      {fmtNum(entry.requested_max_output_tokens)} / {fmtNum(entry.effective_max_output_tokens)}
                    </td>
                    <td className="py-1 pr-2">
                      {fmtNum(entry.input_tokens)}/{fmtNum(entry.output_tokens)}/{fmtNum(entry.reasoning_tokens)}/
                      {fmtNum(entry.visible_content_tokens)}
                    </td>
                    <td className="py-1 pr-2">
                      {fmtNum(entry.context_window)}/{fmtNum(entry.estimated_context_tokens)}/
                      {fmtNum(entry.remaining_context_capacity)}
                    </td>
                    <td className="py-1 pr-2">{fmtValue(entry.length_failure_kind)}</td>
                    <td className="py-1 pr-2">{effortText}</td>
                    <td className="py-1">
                      {fmtBool(entry.had_reasoning)}/{fmtBool(entry.had_visible_content)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] text-muted-foreground" data-testid="finalization-context">
        <span>context_mode: {fmtValue(run.finalization_context_mode)}</span>
        <span>original: {fmtNum(run.original_context_estimated_tokens)}</span>
        <span>finalization: {fmtNum(run.finalization_context_estimated_tokens)}</span>
        {reduction !== "" && <span>{reduction}</span>}
        <span>evidence: {fmtNum(run.evidence_item_count)}</span>
        <span>sources: {fmtNum(run.source_count)}</span>
        <span>deduplicated: {fmtNum(run.deduplicated_item_count)}</span>
      </div>
    </section>
  );
};

export default FinalizationDiagnostics;
