import React from "react";
import { Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ModelRecord } from "@/lib/types";

export const ParamsPopover: React.FC<{
  systemPrompt: string;
  onSystemPrompt: (v: string) => void;
  temperature: number;
  onTemperature: (v: number) => void;
  maxTokens: number;
  onMaxTokens: (v: number) => void;
  topP: number;
  onTopP: (v: number) => void;
  seed: string;
  onSeed: (v: string) => void;
  streaming: boolean;
  onStreaming: (v: boolean) => void;
  streamSupported: boolean;
  selectedModel?: ModelRecord | null;
}> = ({
  systemPrompt,
  onSystemPrompt,
  temperature,
  onTemperature,
  maxTokens,
  onMaxTokens,
  topP,
  onTopP,
  seed,
  onSeed,
  streaming,
  onStreaming,
  streamSupported,
  selectedModel,
}) => {
  const modelMaxOutput = selectedModel?.max_output_tokens ?? (selectedModel?.capabilities?.max_output_tokens as number | undefined) ?? null;
  const sliderMax = modelMaxOutput && modelMaxOutput > 0 ? Math.min(modelMaxOutput, 65536) : 8192;
  const isAuto = maxTokens === 0 || maxTokens === null || maxTokens === undefined;
  const effectiveMaxTokens = isAuto ? (modelMaxOutput || "Auto (Model default)") : maxTokens;
  const isClamped = Boolean(modelMaxOutput && maxTokens && maxTokens > modelMaxOutput);

  return (
  <Popover>
    <PopoverTrigger asChild>
      <Button type="button" size="sm" variant="outline" data-testid="workspace-params-button">
        <Settings2 className="mr-1.5 h-3.5 w-3.5" /> Parameters
      </Button>
    </PopoverTrigger>
    <PopoverContent
      className="max-h-[70vh] w-80 space-y-4 overflow-y-auto scrollbar-thin"
      align="start"
      data-testid="workspace-params"
    >
      <div>
        <label className="text-xs font-medium text-muted-foreground">System prompt</label>
        <Textarea
          data-testid="workspace-system-prompt"
          value={systemPrompt}
          onChange={(e) => onSystemPrompt(e.target.value)}
          rows={3}
          className="mt-1 text-xs"
          placeholder="You are a helpful research assistant."
        />
      </div>
      <div>
        <div className="flex justify-between text-xs">
          <span className="font-medium text-muted-foreground">Temperature</span>
          <span className="font-mono">{temperature.toFixed(2)}</span>
        </div>
        <Slider
          data-testid="workspace-temperature"
          value={[temperature]}
          min={0}
          max={2}
          step={0.05}
          onValueChange={(v: number[]) => onTemperature(v[0])}
          className="mt-2"
        />
      </div>
      <div>
        <div className="flex justify-between items-center text-xs">
          <span className="font-medium text-muted-foreground">Output tokens</span>
          <span className="font-mono text-[11px]">
            {isAuto ? "Auto / Model default" : `${maxTokens} tokens`}
          </span>
        </div>
        <div className="mt-1.5 flex items-center gap-2">
          <Slider
            data-testid="workspace-max-tokens"
            value={[maxTokens || sliderMax]}
            min={128}
            max={Math.max(sliderMax, 8192)}
            step={128}
            onValueChange={(v: number[]) => onMaxTokens(v[0])}
            className="flex-1"
          />
        </div>
        <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
          <span data-testid="params-model-max">{modelMaxOutput ? `Model max: ${modelMaxOutput}` : "Model max: Unknown"}</span>
          {isClamped ? (
            <span data-testid="params-clamped" className="text-amber-500 font-medium">Clamped to {modelMaxOutput}</span>
          ) : null}
        </div>
      </div>
      <div>
        <div className="flex justify-between text-xs">
          <span className="font-medium text-muted-foreground">Top P</span>
          <span className="font-mono">{topP.toFixed(2)}</span>
        </div>
        <Slider
          data-testid="workspace-top-p"
          value={[topP]}
          min={0}
          max={1}
          step={0.05}
          onValueChange={(v: number[]) => onTopP(v[0])}
          className="mt-2"
        />
      </div>
      <div>
        <label className="text-xs font-medium text-muted-foreground">Seed (optional)</label>
        <input
          type="number"
          data-testid="workspace-seed"
          value={seed}
          onChange={(e) => onSeed(e.target.value)}
          className="mt-1 h-8 w-full rounded-md border border-input bg-background px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          placeholder="e.g. 42"
        />
      </div>
      <label className="flex items-center justify-between text-xs">
        <span className="flex items-center gap-1.5 text-muted-foreground">Stream tokens</span>
        <Switch
          data-testid="workspace-stream-toggle"
          checked={streaming}
          disabled={!streamSupported}
          onCheckedChange={onStreaming}
        />
      </label>
      {!streamSupported && (
        <p className="text-[10px] text-muted-foreground">Streaming is not supported by this model.</p>
      )}
    </PopoverContent>
  </Popover>
  );
};
