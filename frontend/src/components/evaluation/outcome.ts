// Shared evaluation types + pure null-safe helpers.
//
// Central rule enforced here: an absent value (null / undefined) is NEVER
// coerced to 0. Missing telemetry, scores and confidence all render "n/a".

export type EvaluationOutcome = "PASS" | "FAIL" | "ERROR" | "NOT_EVALUATED";

export interface EvaluationFinding {
  id?: string;
  category: string;
  severity: string;
  message: string;
  evidence?: any;
}

export interface EvaluationResult {
  outcome: EvaluationOutcome;
  status: "completed" | "error" | "not_evaluated";
  passed: boolean | null;
  score: number | null;
  threshold: number | null;
  confidence: number | null;
  reason: string | null;
}

export interface EvaluationDetail {
  id: string;
  schema_version?: number;
  evaluation_type: string;
  evaluator_type?: string;
  eval_type?: string;
  evaluator?: { type?: string; key?: string; implementation?: string; version?: string };
  run_id?: string;
  experiment_id?: string;
  project_id?: string;
  dataset_case_id?: string;
  dataset_version_id?: string;
  rubric?: { id?: string; version?: number; threshold?: number | null; snapshot?: any } | null;
  judge?: any | null;
  result?: EvaluationResult;
  scores?: Record<string, number>;
  findings?: EvaluationFinding[];
  metrics?: Record<string, number | string | null>;
  evidence?: any;
  provenance?: any;
  created_at?: string;
  // legacy compatibility
  parsed_score?: number | null;
  raw_result?: any;
  status?: string;
  method?: string | null;
}

/** True only when the value is actually present. */
export function isKnown(v: unknown): boolean {
  return v !== null && v !== undefined;
}

/**
 * Null-safe metric formatter. Missing values become "n/a" and everything else
 * is stringified as-is so numbers are never padded with fake precision.
 */
export function formatMetric(v: unknown): string {
  if (v === null || v === undefined) return "n/a";
  if (typeof v === "number") {
    if (!Number.isFinite(v)) return "n/a";
    return String(v);
  }
  if (typeof v === "string") {
    return v.trim() === "" ? "n/a" : v;
  }
  return String(v);
}

/** Derive the outcome, preferring the structured result over the legacy status. */
export function outcomeLabel(e: EvaluationDetail | null | undefined): EvaluationOutcome {
  const explicit = e?.result?.outcome;
  if (explicit === "PASS" || explicit === "FAIL" || explicit === "ERROR" || explicit === "NOT_EVALUATED") {
    return explicit;
  }
  if (e?.status === "error") return "ERROR";
  return "NOT_EVALUATED";
}

/** Tailwind classes for each outcome. Missing data is slate, never green. */
export function outcomeClasses(outcome: EvaluationOutcome): string {
  switch (outcome) {
    case "PASS":
      return "border-emerald-500/40 bg-emerald-500/15 text-emerald-600";
    case "FAIL":
      return "border-destructive/40 bg-destructive/15 text-destructive";
    case "ERROR":
      return "border-amber-500/40 bg-amber-500/15 text-amber-600";
    case "NOT_EVALUATED":
    default:
      return "border-border bg-muted text-muted-foreground";
  }
}

/** Null -> "n/a"; numbers rounded to 3 decimals. */
export function formatScore(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
  return v.toFixed(3);
}

/**
 * Render an arbitrary evidence/provenance value without ever leaking a
 * credential. Strings that look like an API key are replaced by "[redacted]".
 */
export function redactSecret(value: unknown): string {
  if (value === null || value === undefined) return "n/a";
  if (typeof value === "string") {
    if (/sk-|key/i.test(value)) return "[redacted]";
    return value.trim() === "" ? "n/a" : value;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "[object]";
}

/** True when a raw value appears to contain an API key. */
export function containsSecret(value: unknown): boolean {
  return typeof value === "string" && /sk-|key/i.test(value);
}
