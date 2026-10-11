import React, { useState } from "react";
import { Loader2, Check, Copy, Circle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { fmtLatency, fmtTokens, fmtCost, familyColor } from "@/lib/format";
import { Telemetry } from "@/lib/types";

export const LoadingSpinner: React.FC<{ label?: string; className?: string }> = ({ label, className }) => (
  <div className={cn("flex items-center gap-2 text-sm text-muted-foreground", className)} data-testid="loading-state">
    <Loader2 className="h-4 w-4 animate-spin" />
    {label && <span>{label}</span>}
  </div>
);

export const PulseDot: React.FC<{ color?: string; className?: string }> = ({ color = "hsl(var(--brand-emerald))", className }) => (
  <span className={cn("pulse-dot inline-block h-2 w-2 rounded-full", className)} style={{ background: color }} />
);

export const EmptyState: React.FC<{
  icon?: React.ReactNode; title: string; description?: string; action?: React.ReactNode; testId?: string;
}> = ({ icon, title, description, action, testId }) => (
  <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border/70 bg-card/40 px-6 py-16 text-center fade-in-up" data-testid={testId || "empty-state"}>
    {icon && <div className="mb-4 rounded-lg bg-muted/60 p-3 text-muted-foreground">{icon}</div>}
    <h3 className="font-display text-lg font-semibold tracking-tight">{title}</h3>
    {description && <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>}
    {action && <div className="mt-5">{action}</div>}
  </div>
);

export const ErrorNote: React.FC<{ message?: string | null; className?: string }> = ({ message, className }) =>
  message ? (
    <div className={cn("rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive", className)} data-testid="error-note">
      {message}
    </div>
  ) : null;

export const SectionHeader: React.FC<{ title: string; caption?: string; action?: React.ReactNode }> = ({ title, caption, action }) => (
  <div className="flex items-end justify-between gap-4">
    <div>
      <h2 className="font-display text-xl font-semibold tracking-tight lg:text-2xl">{title}</h2>
      {caption && <p className="mt-0.5 text-sm text-muted-foreground">{caption}</p>}
    </div>
    {action}
  </div>
);

export const CopyButton: React.FC<{ value: string; testId?: string; className?: string }> = ({ value, testId, className }) => {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      data-testid={testId || "copy-button"}
      className={cn("inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground", className)}
      onClick={() => { navigator.clipboard.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1400); }}
      title="Copy"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-[hsl(var(--brand-emerald))]" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
};

export const FamilyDot: React.FC<{ family?: string | null }> = ({ family }) => (
  <Circle className="h-2 w-2" style={{ fill: familyColor[family || ""] || "hsl(var(--muted-foreground))", color: familyColor[family || ""] || "hsl(var(--muted-foreground))" }} />
);

export const Metric: React.FC<{ label: string; value: string; testId?: string }> = ({ label, value, testId }) => (
  <div className="flex flex-col" data-testid={testId}>
    <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <span className="font-mono text-sm font-semibold tabular">{value}</span>
  </div>
);

export const TelemetryRow: React.FC<{ t?: Telemetry; className?: string }> = ({ t, className }) => {
  const reasoningTok = t?.reasoning_tokens ?? t?.token_usage?.reasoning_tokens ?? null;
  const cacheReadTok = t?.cache_read_tokens ?? t?.cache_telemetry?.cache_read_tokens ?? null;
  const hitRate = t?.cache_telemetry?.cache_hit_rate;

  return (
    <div className={cn("flex flex-wrap items-center gap-x-5 gap-y-1", className)} data-testid="telemetry-row">
      <Metric label="latency" value={fmtLatency(t?.latency_ms)} />
      <Metric label="in tok" value={fmtTokens(t?.input_tokens)} />
      <Metric label="out tok" value={fmtTokens(t?.output_tokens)} />
      {reasoningTok !== null && (
        <Metric label="reasoning" value={fmtTokens(reasoningTok)} testId="telemetry-reasoning" />
      )}
      {cacheReadTok !== null && (
        <Metric
          label="cached"
          value={hitRate != null ? `${fmtTokens(cacheReadTok)} (${(hitRate * 100).toFixed(0)}%)` : fmtTokens(cacheReadTok)}
          testId="telemetry-cache"
        />
      )}
      <Metric label="cost" value={fmtCost(t?.estimated_cost, t?.cost_available)} />
    </div>
  );
};
