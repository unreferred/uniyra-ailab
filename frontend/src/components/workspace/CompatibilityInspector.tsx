import React, { useMemo } from "react";
import { Link } from "react-router-dom";
import { useCodeInterpreterStatus, useTools } from "@/lib/hooks";
import {
  CapabilityKey,
  CapabilityStatus,
  GATE_LABELS,
  isCodeInterpreterTool,
  modelCapabilityStatus,
  resolveToolGate,
  toolCallingStatusOf,
  workspaceGroupOf,
} from "@/lib/capabilities";
import { CapabilityBadges, CAPABILITY_KEYS } from "@/components/models/CapabilityBadges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ErrorNote } from "@/components/common/Common";

const symbol = (s: CapabilityStatus) => (s === "SUPPORTED" ? "✓" : s === "UNSUPPORTED" ? "✕" : "?");

const MODEL_PANEL_KEYS: { key: CapabilityKey; short: string }[] = [
  { key: "text", short: "Text" },
  { key: "streaming", short: "Streaming" },
  { key: "tools", short: "Tool calling" },
  { key: "vision", short: "Vision" },
];

/**
 * Tool compatibility inspector (V4.1): for the selected workspace model,
 * shows a MODEL panel (normalized capability symbols) plus a TOOLS list
 * (per-tool gate: AVAILABLE / UNAVAILABLE / MODEL_UNSUPPORTED /
 * MODEL_CAPABILITY_UNKNOWN / SANDBOX_UNAVAILABLE / PERMISSION_DENIED /
 * DISABLED_BY_PROJECT) with an "Inspect model capabilities" action that
 * opens /models. Sandbox state is read live; policy is never changed.
 */
export const CompatibilityInspector: React.FC<{
  projectId: string | null;
  model?: any | null;
  enabledToolIds?: string[];
}> = ({ projectId, model, enabledToolIds = [] }) => {
  const { data: toolsData, isLoading, isError, error } = useTools(projectId);
  const { data: ciStatus, isLoading: ciLoadingRaw } = useCodeInterpreterStatus();
  const ciLoading = ciLoadingRaw && ciStatus === undefined;
  const toolsCap = toolCallingStatusOf(model);

  const rows = useMemo(() => {
    const tools = toolsData?.items || [];
    return [...tools]
      .sort((a: any, b: any) => {
        const ga = workspaceGroupOf(a);
        const gb = workspaceGroupOf(b);
        if (ga !== gb) return ga === "BUILT-IN" ? -1 : 1;
        return String(a.display_name || a.name || "").localeCompare(
          String(b.display_name || b.name || ""),
        );
      })
      .map((t: any) => ({
        tool: t,
        group: workspaceGroupOf(t),
        gate: resolveToolGate({
          tool: t,
          model,
          ciStatus,
          ciLoading,
          projectId,
        }),
      }));
  }, [toolsData, model, ciStatus, ciLoading, projectId]);

  const selectableCount = rows.filter((r) => r.gate.selectable).length;
  const enabledSelectable = enabledToolIds.filter((id) =>
    rows.some((r) => r.tool.id === id && r.gate.selectable),
  ).length;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="outline"
          data-testid="workspace-compatibility-button"
          title={
            model
              ? `Tool compatibility for ${model.display_name || model.model_identifier}`
              : "Select a model to inspect tool compatibility"
          }
        >
          Compatibility
          {model ? (
            <span className="ml-1.5 font-mono text-[10px] text-muted-foreground">
              {enabledSelectable}/{selectableCount} tools
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="max-h-[70vh] w-[26rem] overflow-y-auto p-0 scrollbar-thin"
        align="start"
        data-testid="workspace-compatibility"
      >
        <div className="border-b border-border px-3 py-2">
          <p className="text-xs font-semibold">Tool compatibility</p>
          <p className="text-[11px] text-muted-foreground">
            {model
              ? `${model.display_name || model.model_identifier} · tool calling ${toolsCap}`
              : "No model selected"}
          </p>
        </div>
        <div className="space-y-3 p-3">
          {!model ? (
            <p className="text-xs text-muted-foreground">
              Select a model above to see which tools it can call.
            </p>
          ) : (
            <div data-testid="compatibility-model-panel">
              <p className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                Model
              </p>
              <p
                className="text-xs font-medium"
                data-testid="compatibility-model-name"
              >
                {model.display_name || model.model_identifier}
              </p>
              <p
                className="mt-0.5 font-mono text-[11px] text-muted-foreground"
                data-testid="compatibility-model-symbols"
              >
                {MODEL_PANEL_KEYS.map(({ key, short }) => (
                  <span key={key} className="mr-2">
                    {short} {symbol(modelCapabilityStatus(model, key))}
                  </span>
                ))}
              </p>
              <CapabilityBadges model={model} showProvenance className="mt-1.5" />
              {toolsCap === "UNKNOWN" && (
                <p
                  className="mt-1.5 text-[11px] text-amber-600"
                  data-testid="compatibility-capability-unknown-note"
                >
                  Tool calling capability unknown — tools stay disabled until
                  the model declares support.{" "}
                  <Link
                    to="/models"
                    className="text-primary hover:underline"
                    data-testid="compatibility-inspect-link"
                  >
                    Inspect model capabilities
                  </Link>
                </p>
              )}
            </div>
          )}

          <div data-testid="compatibility-tools-list">
            <p className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
              Tools
            </p>
            {isLoading ? (
              <p className="text-xs text-muted-foreground">Loading tools…</p>
            ) : isError ? (
              <ErrorNote
                message={
                  (error as any)?.message || "Could not load tools."
                }
              />
            ) : rows.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No tools registered.
              </p>
            ) : (
              <ul className="space-y-1">
                {rows.map(({ tool: t, group, gate }) => (
                  <li
                    key={t.id}
                    data-testid={`compat-tool-${t.id}`}
                    data-gate={gate.gate}
                    title={gate.reason || GATE_LABELS[gate.gate]}
                    className="flex items-start justify-between gap-2 rounded-md border border-border/60 px-2 py-1.5 text-xs"
                  >
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate font-medium">
                          {t.display_name || t.name}
                        </span>
                        {isCodeInterpreterTool(t) && (
                          <span className="font-mono text-[9px] text-muted-foreground">
                            sandboxed
                          </span>
                        )}
                      </span>
                      <span className="mt-0.5 block font-mono text-[10px] text-muted-foreground">
                        {group} ·{" "}
                        {gate.gate === "AVAILABLE"
                          ? "available"
                          : gate.reason ||
                            GATE_LABELS[gate.gate].toLowerCase()}
                      </span>
                    </span>
                    <Badge
                      variant={
                        gate.gate === "AVAILABLE" ? "secondary" : "outline"
                      }
                      className={`shrink-0 font-mono text-[9px] ${
                        gate.gate === "AVAILABLE"
                          ? ""
                          : "text-muted-foreground"
                      }`}
                      data-testid={`compat-gate-${t.id}`}
                    >
                      {gate.gate}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex items-center justify-between border-t border-border pt-2">
            <p className="font-mono text-[10px] text-muted-foreground">
              {CAPABILITY_KEYS.length} capability dimensions · sandbox read-only
            </p>
            <Link
              to="/models"
              className="text-xs text-primary hover:underline"
              data-testid="compatibility-inspect-models"
            >
              Inspect model capabilities
            </Link>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
};

export default CompatibilityInspector;
