import React from "react";
import { Badge } from "@/components/ui/badge";

export const RISK_VARIANT: Record<string, string> = {
  low: "outline",
  medium: "secondary",
  high: "destructive",
  critical: "destructive",
};

export const riskVariant = (risk?: string | null) => {
  if (!risk) return "outline";
  return (RISK_VARIANT[risk.toLowerCase()] || "secondary") as any;
};

export const TOOL_TYPE_LABEL: Record<string, string> = {
  function: "Built-in",
  http: "Custom HTTP",
  openapi: "OpenAPI",
  code_interpreter: "Code Interpreter",
};

export const toolTypeLabel = (t?: string | null) => TOOL_TYPE_LABEL[t || ""] || t || "function";

// Group key for the manager filter tabs.
export type ToolFilter = "all" | "builtin" | "http" | "openapi" | "code_interpreter";

export const isBuiltin = (t: any) => !!t?.builtin || t?.tool_type === "function";
export const isCodeInterpreter = (t: any) =>
  t?.name === "code_interpreter" || t?.tool_type === "code_interpreter";

export const filterKindOf = (t: any): Exclude<ToolFilter, "all"> => {
  if (isCodeInterpreter(t)) return "code_interpreter";
  if (t?.tool_type === "openapi") return "openapi";
  if (t?.tool_type === "http") return "http";
  return "builtin";
};

export const scopeOf = (t: any): string => {
  if (t?.scope) return String(t.scope).toUpperCase();
  if (t?.builtin) return "GLOBAL";
  if (t?.project_id) return "PROJECT";
  return "GLOBAL";
};

export const versionLabelOf = (t: any): string => {
  if (t?.version != null && t.version !== "") {
    const v = String(t.version);
    return v.startsWith("v") ? v : `v${v}`;
  }
  return "\u2014";
};

export const StatusDot: React.FC<{ kind: "online" | "available" | "missing" | "unsupported" | "off" }> = ({ kind }) => {
  const color =
    kind === "online" || kind === "available"
      ? "hsl(var(--brand-emerald))"
      : kind === "missing"
        ? "hsl(var(--brand-amber))"
        : "hsl(var(--muted-foreground))";
  return (
    <span
      className="inline-block h-2 w-2 shrink-0 rounded-full"
      style={{ background: color }}
      aria-hidden
    />
  );
};

export interface ToolStatus {
  kind: "online" | "available" | "missing" | "unsupported" | "off";
  label: string;
}

// Single source of truth for the status column. Never reports sandbox state
// without consulting live /code-interpreter/status data (passed in).
export const toolStatusOf = (
  t: any,
  ciStatus: { available?: boolean } | null | undefined,
  ciLoading: boolean,
): ToolStatus => {
  if (isCodeInterpreter(t)) {
    if (ciLoading || ciStatus === undefined) return { kind: "off", label: "\u25cb Checking\u2026" };
    if (ciStatus?.available === true) return { kind: "online", label: "\u25cf Sandbox online" };
    return { kind: "off", label: "\u25cf Sandbox offline" };
  }
  if (t?.available === false) {
    const reason = String(t?.availability_reason || "");
    if (/credential|secret/i.test(reason)) return { kind: "missing", label: "\u25cf Credential missing" };
    if (/tool calling|capabilit|model/i.test(reason))
      return { kind: "unsupported", label: "\u25cb Model does not support tool calling" };
    return { kind: "off", label: `\u25cf ${reason || "Unavailable"}` };
  }
  if (t?.enabled === false) return { kind: "off", label: "\u25cf Disabled" };
  return { kind: "available", label: "\u25cf Available" };
};

export const RiskBadge: React.FC<{ risk?: string | null; testId?: string }> = ({ risk, testId }) => (
  <Badge variant={riskVariant(risk)} className="text-[10px]" data-testid={testId}>
    {(risk || "low").toUpperCase()}
  </Badge>
);

export const ScopeBadge: React.FC<{ tool: any }> = ({ tool }) => {
  const scope = scopeOf(tool);
  return (
    <Badge variant="outline" className="font-mono text-[10px]">
      {scope}
    </Badge>
  );
};
