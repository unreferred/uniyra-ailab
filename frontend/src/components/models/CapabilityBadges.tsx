import React from "react";
import { Badge } from "@/components/ui/badge";
import {
  CapabilityKey,
  CapabilityStatus,
  modelCapabilityStatus,
  normalizeCapabilitySource,
} from "@/lib/capabilities";

/** Workspace inspector + Models page share this ordering. */
export const CAPABILITY_KEYS: CapabilityKey[] = [
  "text",
  "vision",
  "tools",
  "streaming",
  "structured_output",
  "embeddings",
];

export const CAPABILITY_LABELS: Record<CapabilityKey, string> = {
  text: "Text",
  vision: "Vision",
  tools: "Tool calling",
  streaming: "Streaming",
  structured_output: "Structured output",
  embeddings: "Embeddings",
};

export const capabilitySymbol = (status: CapabilityStatus): string =>
  status === "SUPPORTED" ? "✓" : status === "UNSUPPORTED" ? "✕" : "?";

const badgeStyle = (status: CapabilityStatus): string =>
  status === "SUPPORTED"
    ? "text-[10px] text-emerald-600 border-emerald-500/30 bg-emerald-500/10"
    : status === "UNSUPPORTED"
      ? "text-[10px] text-destructive/80"
      : "text-[10px] text-muted-foreground/60 border-dashed";

/**
 * Real normalized capability badges for one model: text in/out, vision,
 * tool/function calling, streaming, JSON/structured output, embeddings.
 * Provenance (`capability_source` PROVIDER/VERIFIED/MANUAL/UNKNOWN) renders
 * alongside; a Supported·Manual override marker appears when the source is
 * MANUAL. Unknown metadata renders as "?" — never inferred.
 */
export const CapabilityBadges: React.FC<{
  model: any;
  showProvenance?: boolean;
  className?: string;
}> = ({ model, showProvenance = false, className }) => {
  const source = normalizeCapabilitySource(model?.capability_source);
  return (
    <div
      className={`flex flex-wrap items-center gap-1 ${className || ""}`}
      data-testid={`model-caps-${model?.id || "unknown"}`}
    >
      {CAPABILITY_KEYS.map((key) => {
        const status = modelCapabilityStatus(model, key);
        const label = CAPABILITY_LABELS[key];
        return (
          <Badge
            key={key}
            variant={status === "SUPPORTED" ? "secondary" : "outline"}
            className={badgeStyle(status)}
            title={`${label}: ${status}`}
            data-testid={`cap-${key}`}
          >
            {label} {capabilitySymbol(status)}
          </Badge>
        );
      })}
      {showProvenance && (
        <Badge
          variant="outline"
          className="font-mono text-[9px]"
          title={`capability_source: ${source}`}
          data-testid="cap-source"
        >
          {source}
        </Badge>
      )}
      {showProvenance && source === "MANUAL" && (
        <Badge
          variant="secondary"
          className="text-[9px]"
          title="Capabilities set by manual override"
          data-testid="cap-manual-marker"
        >
          Manual override
        </Badge>
      )}
    </div>
  );
};

export default CapabilityBadges;
