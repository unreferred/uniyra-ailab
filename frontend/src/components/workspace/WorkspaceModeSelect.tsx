import React from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const WORKSPACE_MODE_OPTIONS: { value: string; label: string; description: string }[] = [
  { value: "chat", label: "Chat", description: "Freeform conversation" },
  { value: "plan", label: "Plan", description: "Structured plan only" },
  { value: "research", label: "Research", description: "Evidence-first investigation" },
  { value: "audit", label: "Audit", description: "Adversarial review" },
];

export type WorkspaceModeOption = { value: string; label: string; description: string };

export function WorkspaceModeSelect(props: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}): React.JSX.Element {
  const { value, onChange, disabled } = props;
  const current = WORKSPACE_MODE_OPTIONS.find((option) => option.value === value);
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground">Mode</span>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger
          data-testid="workspace-mode-select"
          className="h-9 w-[9.5rem] text-xs"
          title={current?.description}
        >
          <SelectValue placeholder="Mode">{current?.label ?? value}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {WORKSPACE_MODE_OPTIONS.map((option) => (
            <SelectItem
              key={option.value}
              value={option.value}
              data-testid={`workspace-mode-option-${option.value}`}
              className="text-xs"
              title={option.description}
            >
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export default WorkspaceModeSelect;
