import React from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PulseDot } from "@/components/common/Common";
import { RunActivityTimeline } from "@/components/workspace/RunActivityTimeline";
import { MarkdownView } from "@/components/common/Markdown";
import { cn } from "@/lib/utils";
import type { RunStreamState } from "@/hooks/useRunStream";

export interface RunStreamPanelProps {
  state: RunStreamState;
  onStop?: () => void;
  onRetry?: () => void;
  onContinue?: () => void;
  isRetrying?: boolean;
  isContinuing?: boolean;
  answerText?: string;
  className?: string;
}

const PHASE_LABEL: Record<string, string> = {
  thinking: "Thinking…",
  searching: "Searching web…",
  fetching: "Fetching…",
  analyzing: "Analyzing evidence…",
  finalizing: "Finalizing…",
  waiting: "Waiting for model…",
};

export const RunStreamPanel: React.FC<RunStreamPanelProps> = ({
  state,
  onStop,
  onRetry,
  onContinue,
  isRetrying,
  isContinuing,
  answerText,
  className,
}) => {
  const running = state.status === "running";
  const outputLimit = state.status === "output_limit";
  const visibleAnswer = answerText ?? state.answerText;
  const showError = state.status === "error" || state.status === "cancelled";
  const isCancelled = state.status === "cancelled";
  const isFailed = state.status === "error";
  const hasPartialText = Boolean(visibleAnswer && visibleAnswer.trim().length > 0);
  const errorMessage =
    (state.error && (state.error.message || state.error.code)) ||
    (isCancelled ? "Run cancelled." : "Run failed.");

  // Recovery actions eligibility:
  // - Provider/backend failure without partial text: [Retry]
  // - Transport interrupted after partial answer: [Continue] [Retry]
  // - Cancelled by user with partial answer: [Continue] [Retry]
  // - Output limit reached: [Continue]
  const showRetry = (isFailed || isCancelled) && onRetry;
  const showContinue = (outputLimit || ((isFailed || isCancelled) && hasPartialText)) && onContinue;


  return (
    <Card
      className={cn("w-full max-w-[85%] p-3.5 fade-in-up", className)}
      data-testid="message-streaming"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          {running && <PulseDot />}
          <span className="text-xs font-medium text-muted-foreground">
            {state.continuationStatusText || (outputLimit ? "Output limit reached" : PHASE_LABEL[state.phase] || "Working…")}
          </span>
          {state.partial && <span className="text-[10px] text-amber-500">partial</span>}
          {state.toolOutcome && state.toolOutcome !== "success" && (
            <span className="text-[10px] text-muted-foreground">{state.toolOutcome}</span>
          )}
        </div>
        {running && onStop && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px]"
            data-testid="run-stop"
            onClick={onStop}
          >
            Stop
          </Button>
        )}
      </div>

      <RunActivityTimeline activities={state.activities} phase={state.phase} running={running} />

      {state.activities.length === 0 && running && (
        <div className="flex items-center gap-2">
          <PulseDot />
          <span className="text-sm text-muted-foreground">Working…</span>
        </div>
      )}

      {outputLimit && (
        <p className="mt-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-[11px] text-amber-700 dark:text-amber-300">
          Output limit reached{state.outputLimitReason ? ` (${state.outputLimitReason})` : ""}.
        </p>
      )}
      {showError && (
        <p className="mt-1.5 rounded-md border border-destructive/40 bg-destructive/10 px-2.5 py-1.5 text-[11px] text-destructive">
          {errorMessage}
        </p>
      )}
      {visibleAnswer ? (
        <div className="mt-3 border-t border-border/40 pt-3" data-testid="run-answer">
          <MarkdownView content={visibleAnswer} />
        </div>
      ) : running ? (
        <div className="mt-3 border-t border-border/40 pt-3 text-sm text-muted-foreground">
          Waiting for model…
        </div>
      ) : null}

      {(showRetry || showContinue) && (
        <div className="mt-3 flex items-center gap-2 border-t border-border/40 pt-2.5" data-testid="run-recovery-actions">
          {showContinue && (
            <Button
              type="button"
              size="sm"
              variant="default"
              className="h-7 px-2.5 text-xs"
              data-testid="workspace-continue"
              disabled={isContinuing || isRetrying}
              onClick={onContinue}
            >
              {isContinuing ? (
                <>
                  <PulseDot className="mr-1.5" />
                  Continuing…
                </>
              ) : (
                "Continue"
              )}
            </Button>
          )}
          {showRetry && (
            <Button
              type="button"
              size="sm"
              variant={showContinue ? "outline" : "default"}
              className="h-7 px-2.5 text-xs"
              data-testid="workspace-retry"
              disabled={isRetrying || isContinuing}
              onClick={onRetry}
            >
              {isRetrying ? (
                <>
                  <PulseDot className="mr-1.5" />
                  Retrying…
                </>
              ) : (
                "Retry"
              )}
            </Button>
          )}
        </div>
      )}
    </Card>
  );
};
