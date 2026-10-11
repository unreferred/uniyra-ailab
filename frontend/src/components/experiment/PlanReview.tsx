import React from "react";
import { ClipboardList, Loader2, Play, RefreshCw, TriangleAlert } from "lucide-react";
import { BenchmarkPlan } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { CopyButton } from "@/components/common/Common";
import { nCost, nNumber, shortHash } from "./format";

export const PlanReview: React.FC<{
  plan: BenchmarkPlan | null;
  building: boolean;
  starting: boolean;
  confirmed: boolean;
  onConfirmedChange: (v: boolean) => void;
  onBuild: () => void;
  onStart: () => void;
  canBuild: boolean;
  canStart: boolean;
  disabledReason?: string | null;
}> = ({ plan, building, starting, confirmed, onConfirmedChange, onBuild, onStart, canBuild, canStart, disabledReason }) => {
  const requiresConfirmation = Boolean(plan?.requires_confirmation);
  const startDisabled = !canStart || (requiresConfirmation && !confirmed) || starting;

  return (
    <Card className="space-y-4 p-4" data-testid="benchmark-plan">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ClipboardList className="h-4 w-4 text-brand" />
          <span className="font-display font-semibold">Benchmark plan</span>
          {plan && <Badge variant="outline" className="text-[10px] uppercase">{plan.status}</Badge>}
        </div>
        {canBuild && (
          <Button size="sm" variant="outline" data-testid="benchmark-plan-build" onClick={onBuild} disabled={building}>
            {building ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1.5 h-4 w-4" />}
            {plan ? "Rebuild plan" : "Build plan"}
          </Button>
        )}
      </div>

      {!plan ? (
        <p className="text-sm text-muted-foreground" data-testid="benchmark-plan-empty">
          {disabledReason || "No benchmark plan yet. Build a plan to preview the exact call matrix before starting."}
        </p>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Metric label="Cases" value={nNumber(plan.case_ids?.length ?? null)} testId="plan-case-count" />
            <Metric label="Configs" value={nNumber(plan.configuration_ids?.length ?? null)} testId="plan-config-count" />
            <Metric label="Repetitions" value={nNumber(plan.repetitions ?? null)} testId="plan-repetitions" />
            <Metric label="Planned calls" value={nNumber(plan.total_planned_calls ?? null)} testId="plan-total-calls" />
            <Metric
              label="Est. cost"
              value={
                plan.estimated_cost?.available
                  ? `${nCost(plan.estimated_cost.min)} – ${nCost(plan.estimated_cost.max)}`
                  : "estimate unavailable"
              }
              testId="plan-estimated-cost"
            />
          </div>

          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[11px] text-muted-foreground">
            <span>
              Cost method:{" "}
              {plan.estimated_cost?.available
                ? plan.estimated_cost.method || "n/a"
                : `unavailable${plan.estimated_cost?.unknown_pricing_config_count ? ` · ${plan.estimated_cost.unknown_pricing_config_count} config(s) missing pricing` : ""}`}
            </span>
            <span className="flex items-center gap-1">
              Plan hash: <span className="font-mono">{shortHash(plan.plan_hash)}</span>
              {plan.plan_hash && <CopyButton value={plan.plan_hash} testId="plan-hash-copy" />}
            </span>
          </div>

          {requiresConfirmation && (
            <div className="rounded-md border border-[hsl(var(--brand-amber))]/50 bg-[hsl(var(--brand-amber))]/10 p-3" data-testid="benchmark-confirm">
              <label className="flex items-start gap-2 text-sm">
                <Checkbox
                  checked={confirmed}
                  onCheckedChange={(v) => onConfirmedChange(v === true)}
                  className="mt-0.5"
                  data-testid="benchmark-confirm-checkbox"
                />
                <span>
                  <span className="flex items-center gap-1.5 font-medium">
                    <TriangleAlert className="h-3.5 w-3.5" /> Confirm large run
                  </span>
                  This plan will make <strong>{plan.total_planned_calls}</strong> model calls. Confirm before starting.
                </span>
              </label>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" data-testid="benchmark-start-panel" onClick={onStart} disabled={startDisabled}>
              {starting ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Play className="mr-1.5 h-4 w-4" />}
              Start benchmark
            </Button>
            {disabledReason && <span className="text-xs text-muted-foreground">{disabledReason}</span>}
            {requiresConfirmation && !confirmed && !disabledReason && (
              <span className="text-xs text-muted-foreground">Tick the confirmation checkbox to enable starting.</span>
            )}
          </div>
        </div>
      )}
    </Card>
  );
};

const Metric: React.FC<{ label: string; value: string; testId?: string }> = ({ label, value, testId }) => (
  <div className="flex flex-col" data-testid={testId}>
    <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <span className="font-mono text-sm font-semibold tabular">{value}</span>
  </div>
);
