import React, { useMemo } from "react";
import { Link } from "react-router-dom";
import { Wrench, Loader2 } from "lucide-react";
import { useCodeInterpreterStatus, useModels, useTools } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import {
  GATE_LABELS,
  isCodeInterpreterTool,
  resolveToolGate,
  workspaceGroupOf,
} from "@/lib/capabilities";

const RISK_VARIANT: Record<string, string> = {
  low: "secondary",
  medium: "outline",
  high: "destructive",
  critical: "destructive",
};

const CODE_INTERPRETER_SANDBOX_NOTE = "Sandboxed Docker execution, network off";

const GATE_STYLE: Record<string, string> = {
  AVAILABLE: "text-emerald-600",
  UNAVAILABLE: "text-amber-600",
  MODEL_UNSUPPORTED: "text-destructive",
  MODEL_CAPABILITY_UNKNOWN: "text-amber-600",
  SANDBOX_UNAVAILABLE: "text-destructive",
  PERMISSION_DENIED: "text-destructive",
  DISABLED_BY_PROJECT: "text-muted-foreground",
};

/**
 * Workspace tool picker (V4.1): BUILT-IN (calculator, date_time, json_transform,
 * code_interpreter) vs PROJECT TOOLS (everything else). Every disabled tool
 * shows its explicit gate reason — AVAILABLE / UNAVAILABLE / MODEL_UNSUPPORTED /
 * MODEL_CAPABILITY_UNKNOWN / SANDBOX_UNAVAILABLE / PERMISSION_DENIED /
 * DISABLED_BY_PROJECT — never a bare grey checkbox.
 *
 * Gating combines backend `available`/`availability_reason`, live sandbox
 * state (useCodeInterpreterStatus, read-only), and the selected model's
 * normalized tool-calling capability (caps.tools). Code Interpreter is
 * selectable only when sandbox available + tool enabled + permission allows +
 * the selected model supports tool/function calling. Native provider-side
 * code-interpreter support is never required — only tool calling.
 */
export const ToolsPopover: React.FC<{
  projectId: string | null;
  value: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  /** Selected workspace model id; gates tool calling against caps.tools. */
  modelId?: string | null;
}> = ({ projectId, value, onChange, disabled, modelId }) => {
  const { data, isLoading, isError } = useTools(projectId);
  const { data: models = [] } = useModels();
  const { data: ciStatus, isLoading: ciLoadingRaw } = useCodeInterpreterStatus();
  // Concrete status only: while the query is unresolved, sandbox-gated tools
  // stay disabled instead of guessing.
  const ciLoading = ciLoadingRaw && ciStatus === undefined;
  const model = useMemo(
    () => (modelId ? models.find((m: any) => m.id === modelId) || null : null),
    [models, modelId],
  );
  const tools = data?.items || [];
  const toggle = (id: string) =>
    value.includes(id) ? onChange(value.filter((x) => x !== id)) : onChange([...value, id]);

  const groups: ("BUILT-IN" | "PROJECT TOOLS")[] = ["BUILT-IN", "PROJECT TOOLS"];
  const byGroup = (g: string) => tools.filter((t: any) => workspaceGroupOf(t) === g);

  const renderTool = (t: any) => {
    const gate = resolveToolGate({
      tool: t,
      model,
      ciStatus,
      ciLoading,
      projectId,
    });
    const selectable = gate.selectable;
    const isCodeInterpreter = isCodeInterpreterTool(t);
    const selected = value.includes(t.id);
    const needsCapabilityLink =
      !selectable && gate.gate === "MODEL_CAPABILITY_UNKNOWN";
    return (
      <label
        key={t.id}
        data-testid={`tool-${t.id}`}
        data-gate={gate.gate}
        title={
          (isCodeInterpreter && selected ? `${CODE_INTERPRETER_SANDBOX_NOTE}. ` : "") +
          (gate.reason || GATE_LABELS[gate.gate])
        }
        className={cn(
          "flex items-start gap-2 rounded-md px-2 py-1.5 text-xs",
          selectable ? "cursor-pointer hover:bg-accent/50" : "opacity-70",
        )}
      >
        <Checkbox
          className="mt-0.5"
          checked={selected}
          disabled={!selectable}
          onCheckedChange={() => selectable && toggle(t.id)}
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate font-medium">{t.display_name || t.name}</span>
            {isCodeInterpreter && selected && (
              <span className="text-[10px] text-muted-foreground" title={CODE_INTERPRETER_SANDBOX_NOTE}>sandboxed</span>
            )}
            {t.version && <span className="font-mono text-[10px] text-muted-foreground">v{t.version}</span>}
            {t.current_version_id && (
              <span className="font-mono text-[10px] text-muted-foreground" title={`current version ${t.current_version_id}`}>
                {String(t.current_version_id).slice(0, 8)}
              </span>
            )}
            {t.risk_level && (
              <Badge
                variant={RISK_VARIANT[t.risk_level] || "outline"}
                className="px-1 py-0 text-[9px]"
              >
                {t.risk_level}
              </Badge>
            )}
          </span>
          {t.description && (
            <span className="mt-0.5 block line-clamp-2 text-muted-foreground">
              {t.description}
            </span>
          )}
          {!selectable && (
            <span
              className={cn("mt-0.5 block text-[10px]", GATE_STYLE[gate.gate] || "text-amber-500")}
              data-testid={`tool-reason-${t.id}`}
            >
              {gate.reason || GATE_LABELS[gate.gate]}{" "}
              <span className="font-mono">· {gate.gate}</span>
              {needsCapabilityLink && (
                <>
                  {" · "}
                  <Link
                    to="/models"
                    className="text-primary hover:underline"
                    data-testid={`tool-inspect-${t.id}`}
                    onClick={(e) => e.stopPropagation()}
                  >
                    Inspect model capabilities
                  </Link>
                </>
              )}
            </span>
          )}
        </span>
      </label>
    );
  };

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={disabled || !projectId}
          data-testid="workspace-tools-button"
        >
          <Wrench className="mr-1.5 h-3.5 w-3.5" /> Tools: {value.length}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-96 p-0" align="start" data-testid="workspace-tools">
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <span className="text-xs font-semibold">
            Tools{model ? ` · ${model.display_name || model.model_identifier}` : ""}
          </span>
          {value.length > 0 && (
            <button
              type="button"
              className="text-[11px] text-muted-foreground hover:text-foreground"
              onClick={() => onChange([])}
            >
              Clear
            </button>
          )}
        </div>
        <div className="max-h-80 overflow-y-auto p-2 scrollbar-thin">
          {isLoading && (
            <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading tools…
            </div>
          )}
          {isError && <p className="px-2 py-3 text-xs text-destructive">Could not load tools.</p>}
          {!isLoading && !isError && tools.length === 0 && (
            <p className="px-2 py-3 text-xs text-muted-foreground">No tools available.</p>
          )}
          {groups.map((g) => {
            const items = byGroup(g);
            if (items.length === 0) return null;
            return (
              <div key={g} data-testid={`tools-group-${g}`}>
                <p className="px-2 pb-1 pt-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{g}</p>
                {items.map(renderTool)}
              </div>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
};
