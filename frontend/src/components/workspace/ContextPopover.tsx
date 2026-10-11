import React from "react";
import { Layers } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export interface ContextOptions {
  system_prompt: boolean;
  conversation_history: boolean;
  attachments: boolean;
  tool_definitions: boolean;
}

export const DEFAULT_CONTEXT_OPTIONS: ContextOptions = {
  system_prompt: true,
  conversation_history: true,
  attachments: true,
  tool_definitions: true,
};

export const CONTEXT_STRATEGIES = ["FULL", "RECENT_MESSAGES", "MANUAL_SELECTION", "AUTO_TRUNCATE"] as const;

const STRATEGY_HINT: Record<string, string> = {
  FULL: "Include every selected component.",
  RECENT_MESSAGES: "Include only the most recent messages.",
  MANUAL_SELECTION: "Keep only the selected component types.",
  AUTO_TRUNCATE: "Trim history to fit the context budget.",
};

const OPTIONS: { key: keyof ContextOptions; label: string }[] = [
  { key: "system_prompt", label: "System Prompt" },
  { key: "conversation_history", label: "Conversation History" },
  { key: "attachments", label: "Attachments" },
  { key: "tool_definitions", label: "Tool Definitions" },
];

export const ContextPopover: React.FC<{
  options: ContextOptions;
  onChange: (o: ContextOptions) => void;
  strategy: string;
  onStrategyChange: (s: string) => void;
}> = ({ options, onChange, strategy, onStrategyChange }) => (
  <Popover>
    <PopoverTrigger asChild>
      <Button type="button" size="sm" variant="outline" data-testid="workspace-context-button">
        <Layers className="mr-1.5 h-3.5 w-3.5" /> Context
      </Button>
    </PopoverTrigger>
    <PopoverContent className="w-80 space-y-4" align="start" data-testid="workspace-context">
      <div className="space-y-2">
        <span className="text-xs font-semibold">Include in context</span>
        {OPTIONS.map(({ key, label }) => (
          <label key={key} className="flex cursor-pointer items-center gap-2 text-xs">
            <Checkbox
              data-testid={`context-option-${key}`}
              checked={options[key]}
              onCheckedChange={(v: any) => onChange({ ...options, [key]: v === true })}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>
      <div className="space-y-1.5">
        <span className="text-xs font-semibold">Strategy</span>
        <Select value={strategy} onValueChange={onStrategyChange}>
          <SelectTrigger data-testid="workspace-context-strategy" className="h-8">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {CONTEXT_STRATEGIES.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-[10px] text-muted-foreground">{STRATEGY_HINT[strategy] || ""}</p>
      </div>
    </PopoverContent>
  </Popover>
);
