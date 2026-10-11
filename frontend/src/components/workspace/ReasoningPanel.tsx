import React, { useState } from "react";
import { ChevronDown, ChevronRight, Copy, Check, Brain } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ReasoningTelemetry } from "@/lib/types";

export interface ReasoningPanelProps {
  reasoningTelemetry?: ReasoningTelemetry | null;
  reasoningContent?: string | null;
  reasoningTokens?: number | null;
  defaultExpanded?: boolean;
  isStreaming?: boolean;
  className?: string;
}

export const ReasoningPanel: React.FC<ReasoningPanelProps> = ({
  reasoningTelemetry,
  reasoningContent,
  reasoningTokens,
  defaultExpanded = true,
  isStreaming = false,
  className = "",
}) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [copied, setCopied] = useState(false);

  // Derive available reasoning information
  const content = reasoningTelemetry?.content || reasoningContent || null;
  const tokens = reasoningTelemetry?.tokens ?? reasoningTokens ?? null;
  const summary = reasoningTelemetry?.summary || null;
  const available = Boolean(
    reasoningTelemetry?.available || content || tokens !== null || summary
  );

  // Strict invariant: NEVER fabricate reasoning when not reported by provider
  if (!available) {
    return null;
  }

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      toast.success("Reasoning copied to clipboard");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Failed to copy reasoning");
    }
  };

  return (
    <div
      data-testid="reasoning-panel"
      className={`my-2 rounded-md border border-border/60 bg-muted/30 text-xs overflow-hidden ${className}`}
    >
      <div className="w-full flex items-center justify-between px-3 py-2 text-left text-muted-foreground hover:bg-muted/50 transition-colors">
        <button
          type="button"
          data-testid="reasoning-panel-toggle"
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-2 flex-1 text-left"
        >
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          )}
          <Brain className="h-3.5 w-3.5 text-amber-500/80 shrink-0" />
          <span className="font-medium text-foreground/80">
            {summary ? `Reasoning: ${summary}` : "Provider Reasoning"}
          </span>
          {tokens !== null && (
            <Badge
              variant="outline"
              className="text-[10px] px-1.5 py-0 h-4 border-amber-500/30 text-amber-600 dark:text-amber-400"
              data-testid="reasoning-tokens-badge"
            >
              {tokens.toLocaleString()} tokens
            </Badge>
          )}
          {isStreaming && (
            <Badge
              variant="outline"
              className="text-[9px] px-1.5 py-0 h-4 border-amber-500/40 text-amber-600 dark:text-amber-400 animate-pulse flex items-center gap-1"
              data-testid="reasoning-streaming-badge"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-amber-500 animate-ping" />
              thinking…
            </Badge>
          )}
          {reasoningTelemetry?.streamed && !isStreaming && (
            <Badge
              variant="secondary"
              className="text-[9px] px-1 py-0 h-3.5 text-muted-foreground"
            >
              streamed
            </Badge>
          )}
        </button>

        {content && expanded && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleCopy}
            className="h-6 px-2 text-[10px] gap-1 text-muted-foreground hover:text-foreground shrink-0"
            data-testid="reasoning-copy-button"
          >
            {copied ? (
              <>
                <Check className="h-3 w-3 text-green-500" />
                <span>Copied</span>
              </>
            ) : (
              <>
                <Copy className="h-3 w-3" />
                <span>Copy</span>
              </>
            )}
          </Button>
        )}
      </div>

      {expanded && (
        <div
          data-testid="reasoning-panel-content"
          className="px-3 pb-3 pt-1 border-t border-border/40 text-muted-foreground"
        >
          {content ? (
            <pre className="font-mono text-[11px] whitespace-pre-wrap break-words leading-relaxed max-h-96 overflow-y-auto pr-1 select-text">
              {content}
            </pre>
          ) : (
            <p className="italic text-muted-foreground/70 text-[11px]">
              Provider reported reasoning token accounting ({tokens ?? 0} tokens) without textual chain-of-thought content.
            </p>
          )}
        </div>
      )}
    </div>
  );
};
