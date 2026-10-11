import React from "react";
import { Plus, Trash2 } from "lucide-react";
import { ModelRecord } from "@/lib/types";
import { SingleModelSelect } from "@/components/common/ModelPicker";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { ConfigurationRecord } from "./types";

export interface ConfigDraft {
  key: string;
  label: string;
  model_id: string;
  temperature: string;
  max_tokens: string;
  top_p: string;
  system_prompt: string;
  is_control: boolean;
}

let seq = 0;
export function newConfigKey(): string {
  seq += 1;
  return `cfg-${Date.now()}-${seq}`;
}

export function emptyConfig(order = 0): ConfigDraft {
  return {
    key: newConfigKey(),
    label: `Configuration ${order + 1}`,
    model_id: "",
    temperature: "",
    max_tokens: "",
    top_p: "",
    system_prompt: "",
    is_control: order === 0,
  };
}

export function configFromRecord(record: ConfigurationRecord, index: number): ConfigDraft {
  return {
    key: record.id || newConfigKey(),
    label: record.label || `Configuration ${index + 1}`,
    model_id: record.model_id || "",
    temperature: record.temperature === null || record.temperature === undefined ? "" : String(record.temperature),
    max_tokens: record.max_tokens === null || record.max_tokens === undefined ? "" : String(record.max_tokens),
    top_p: record.top_p === null || record.top_p === undefined ? "" : String(record.top_p),
    system_prompt: record.system_prompt || "",
    is_control: Boolean(record.is_control),
  };
}

function optionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

export function configToPayload(config: ConfigDraft, order: number) {
  return {
    label: config.label.trim(),
    model_id: config.model_id,
    temperature: optionalNumber(config.temperature),
    max_tokens: config.max_tokens.trim() ? Math.trunc(Number(config.max_tokens)) : null,
    top_p: optionalNumber(config.top_p),
    system_prompt: config.system_prompt,
    prompt_version_id: null,
    metadata: {},
    is_control: config.is_control,
    order,
  };
}

export function validateConfigs(configs: ConfigDraft[]): string | null {
  if (configs.length < 2) return "At least 2 configurations are required.";
  for (let i = 0; i < configs.length; i += 1) {
    const c = configs[i];
    if (!c.label.trim()) return `Configuration ${i + 1} needs a label.`;
    if (!c.model_id) return `Configuration ${i + 1} needs a model.`;
  }
  const labels = configs.map((c) => c.label.trim().toLowerCase());
  if (new Set(labels).size !== labels.length) return "Configuration labels must be unique.";
  return null;
}

export const ConfigEditor: React.FC<{
  models: ModelRecord[];
  configs: ConfigDraft[];
  onChange: (configs: ConfigDraft[]) => void;
  error?: string | null;
}> = ({ models, configs, onChange, error }) => {
  const update = (index: number, patch: Partial<ConfigDraft>) => {
    onChange(configs.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  };
  const remove = (index: number) => onChange(configs.filter((_, i) => i !== index));
  const add = () => onChange([...configs, emptyConfig(configs.length)]);

  return (
    <div className="space-y-3" data-testid="config-editor">
      {configs.map((config, index) => (
        <Card key={config.key} className="space-y-3 p-4" data-testid={`config-editor-row-${index}`}>
          <div className="flex items-center gap-3">
            <span className="flex h-6 w-6 items-center justify-center rounded bg-muted font-mono text-[11px] text-muted-foreground">
              {index + 1}
            </span>
            <Input
              data-testid={`config-label-${index}`}
              value={config.label}
              onChange={(e) => update(index, { label: e.target.value })}
              placeholder="Label"
              className="h-8 max-w-xs"
            />
            <label className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
              <Switch
                data-testid={`config-control-${index}`}
                checked={config.is_control}
                onCheckedChange={(v) => update(index, { is_control: v })}
              />
              Control
            </label>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className={cn("text-destructive", configs.length <= 2 && "opacity-40")}
              disabled={configs.length <= 2}
              title={configs.length <= 2 ? "At least 2 configurations are required" : "Remove configuration"}
              data-testid={`config-remove-${index}`}
              onClick={() => remove(index)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1fr_1fr]">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Model</label>
              <div className="mt-1">
                <SingleModelSelect
                  models={models.filter((m) => m.enabled)}
                  value={config.model_id}
                  onChange={(v) => update(index, { model_id: v })}
                  testId={`config-model-${index}`}
                  placeholder="Select model"
                />
              </div>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Temperature</label>
              <Input
                data-testid={`config-temperature-${index}`}
                type="number"
                step="0.1"
                min="0"
                max="2"
                value={config.temperature}
                onChange={(e) => update(index, { temperature: e.target.value })}
                className="mt-1 h-8"
                placeholder="default"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Max tokens</label>
              <Input
                data-testid={`config-max-tokens-${index}`}
                type="number"
                min="1"
                value={config.max_tokens}
                onChange={(e) => update(index, { max_tokens: e.target.value })}
                className="mt-1 h-8"
                placeholder="default"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Top P</label>
              <Input
                data-testid={`config-top-p-${index}`}
                type="number"
                step="0.05"
                min="0"
                max="1"
                value={config.top_p}
                onChange={(e) => update(index, { top_p: e.target.value })}
                className="mt-1 h-8"
                placeholder="default"
              />
            </div>
          </div>

          <div>
            <label className="text-xs font-medium text-muted-foreground">System prompt</label>
            <Textarea
              data-testid={`config-system-prompt-${index}`}
              value={config.system_prompt}
              onChange={(e) => update(index, { system_prompt: e.target.value })}
              rows={2}
              className="mt-1"
              placeholder="Optional system prompt"
            />
          </div>
        </Card>
      ))}

      <Button type="button" size="sm" variant="outline" data-testid="config-add" onClick={add}>
        <Plus className="mr-1.5 h-4 w-4" /> Add configuration
      </Button>
      {error && <p className="text-xs text-destructive" data-testid="config-error">{error}</p>}
    </div>
  );
};
