import React from "react";
import { Link } from "react-router-dom";
import { ModelRecord } from "@/lib/types";
import { FamilyDot } from "@/components/common/Common";
import { cn } from "@/lib/utils";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Check } from "lucide-react";
import { toolCallingStatusOf } from "@/lib/capabilities";

const toolCapMark = (m: ModelRecord): string => {
  const status = toolCallingStatusOf(m);
  return status === "SUPPORTED" ? " · tools ✓" : status === "UNSUPPORTED" ? " · tools ✕" : " · tools ?";
};

export const SingleModelSelect: React.FC<{
  models: ModelRecord[]; value: string; onChange: (v: string) => void; testId?: string; placeholder?: string;
}> = ({ models, value, onChange, testId, placeholder }) => (
  <Select value={value} onValueChange={onChange}>
    <SelectTrigger data-testid={testId || "model-select"} className="h-8"><SelectValue placeholder={placeholder || "Select model"} /></SelectTrigger>
    <SelectContent>
      {models.map((m) => (
        <SelectItem key={m.id} value={m.id}>
          <span className="flex items-center gap-2" title={`Tool calling: ${toolCallingStatusOf(m)}`}>
            <FamilyDot family={m.family} /> {m.display_name}
            <span className="font-mono text-[10px] text-muted-foreground">{toolCapMark(m)}</span>
          </span>
        </SelectItem>
      ))}
    </SelectContent>
  </Select>
);

export const MultiModelSelect: React.FC<{
  models: ModelRecord[]; selected: string[]; onToggle: (id: string) => void; max?: number;
}> = ({ models, selected, onToggle, max = 4 }) => (
  <div className="grid gap-2 sm:grid-cols-2" data-testid="multi-model-select">
    {models.map((m) => {
      const active = selected.includes(m.id);
      const disabled = !active && selected.length >= max;
      return (
        <button
          key={m.id}
          type="button"
          data-testid={`model-pick-${m.id}`}
          disabled={disabled}
          onClick={() => onToggle(m.id)}
          title={`Tool calling: ${toolCallingStatusOf(m)}`}
          className={cn(
            "flex items-center justify-between rounded-md border px-3 py-2 text-left text-sm transition-colors",
            active ? "border-primary bg-accent" : "border-border hover:bg-accent/50",
            disabled && "cursor-not-allowed opacity-40"
          )}
        >
          <span className="flex items-center gap-2 truncate">
            <FamilyDot family={m.family} />
            <span className="truncate">{m.display_name}</span>
            <span className="font-mono text-[10px] text-muted-foreground">{toolCapMark(m)}</span>
          </span>
          {active && <Check className="h-4 w-4 text-primary" />}
        </button>
      );
    })}
  </div>
);

/** Inline hint linking to /models when the selected model's caps are unknown. */
export const ModelCapabilityHint: React.FC<{ model?: ModelRecord | null }> = ({ model }) => {
  if (!model || toolCallingStatusOf(model) !== "UNKNOWN") return null;
  return (
    <span className="text-[11px] text-amber-600" data-testid="model-capability-hint">
      Tool calling capability unknown ·{" "}
      <Link to="/models" className="text-primary hover:underline" data-testid="model-capability-inspect">
        Inspect model capabilities
      </Link>
    </span>
  );
};
