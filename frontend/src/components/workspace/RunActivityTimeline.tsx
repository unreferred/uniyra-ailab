import React, { useEffect, useState } from "react";
import {
  AlertTriangle,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Globe,
  Loader2,
  Search,
  Wrench,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  FetchSourceActivity,
  RunActivity,
  RunPhase,
  ThoughtActivity,
  ToolRunActivity,
} from "@/hooks/useRunStream";

export interface RunActivityTimelineProps {
  activities: RunActivity[];
  phase?: RunPhase;
  running?: boolean;
  className?: string;
}

function formatDuration(ms?: number | null): string | null {
  if (ms == null || Number.isNaN(ms)) return null;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function recoveryLabel(mode?: string | null): string | null {
  const raw = String(mode || "").toUpperCase();
  if (!raw) return null;
  if (raw.indexOf("LENGTH") >= 0) return "Recovering (length)";
  if (raw.indexOf("EMPTY") >= 0) return "Recovering (empty)";
  return "Recovering";
}

function humanize(name: string): string {
  const text = String(name || "").replace(/[_-]+/g, " ").trim();
  if (!text) return "Tool";
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function stringify(value: any): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function isToolRunning(tool: ToolRunActivity): boolean {
  return tool.status === "requested" || tool.status === "arguments-ready" || tool.status === "running";
}

function useAutoOpen(running: boolean): [boolean, React.Dispatch<React.SetStateAction<boolean>>] {
  const [open, setOpen] = useState(running);
  useEffect(() => {
    setOpen(running);
  }, [running]);
  return [open, setOpen];
}

const RowFrame: React.FC<{
  testId?: string;
  status?: string;
  children: React.ReactNode;
}> = ({ testId, status, children }) => (
  <div
    data-testid={testId}
    data-status={status}
    className="overflow-hidden rounded-md border border-border/50 bg-muted/20"
  >
    {children}
  </div>
);

const RowHeader: React.FC<{
  icon: React.ReactNode;
  label: React.ReactNode;
  open: boolean;
  onToggle: () => void;
  running: boolean;
  toggleTestId?: string;
}> = ({ icon, label, open, onToggle, running, toggleTestId }) => (
  <button
    type="button"
    data-testid={toggleTestId}
    onClick={onToggle}
    className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[11px] text-muted-foreground transition-colors hover:bg-muted/40"
  >
    {running ? (
      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
    ) : (
      <Check className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
    )}
    {icon}
    <span className="min-w-0 flex-1 truncate font-medium text-foreground/80">{label}</span>
    {open ? (
      <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
    ) : (
      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
    )}
  </button>
);

const RowBody: React.FC<{ testId?: string; children: React.ReactNode }> = ({ testId, children }) => (
  <div
    data-testid={testId}
    className="space-y-1.5 border-t border-border/40 px-2.5 py-2 text-[11px] text-muted-foreground"
  >
    {children}
  </div>
);

// ---------------------------------------------------------------------------
// Thought rows
// ---------------------------------------------------------------------------

const ThoughtRow: React.FC<{ activity: ThoughtActivity }> = ({ activity }) => {
  const running = activity.status === "running";
  const [open, setOpen] = useState(true);

  const duration = formatDuration(activity.durationMs);
  const recovery = running ? recoveryLabel(activity.finalizationRecoveryMode) : null;
  const label = running ? recovery || "Thinking…" : `Thought for ${duration || "0ms"}`;

  return (
    <RowFrame testId="run-thought" status={activity.status}>
      <RowHeader
        running={running}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        toggleTestId="run-thought-toggle"
        icon={<Brain className="h-3.5 w-3.5 shrink-0 text-amber-500/80" />}
        label={label}
      />
      {open && (
        <pre
          data-testid="run-thought-content"
          className="max-h-80 overflow-y-auto whitespace-pre-wrap break-words border-t border-border/40 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-muted-foreground"
        >
          {activity.text || "…"}
        </pre>
      )}
    </RowFrame>
  );
};

export interface ReasoningSegmentListItem {
  phase?: string | null;
  content?: string | null;
  duration_ms?: number | null;
  model_call_id?: string | null;
}

const ReasoningSegmentRow: React.FC<{ segment: ReasoningSegmentListItem }> = ({ segment }) => {
  const [open, setOpen] = useState(true);
  const duration = formatDuration(segment.duration_ms ?? undefined);
  const label = segment.phase === "finalization" ? "Final thought" : `Thought for ${duration || "0ms"}`;

  return (
    <RowFrame testId="run-thought" status="done">
      <RowHeader
        running={false}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        toggleTestId="run-thought-toggle"
        icon={<Brain className="h-3.5 w-3.5 shrink-0 text-amber-500/80" />}
        label={label}
      />
      {open && (
        <pre
          data-testid="run-thought-content"
          className="max-h-80 overflow-y-auto whitespace-pre-wrap break-words border-t border-border/40 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-muted-foreground"
        >
          {segment.content || "…"}
        </pre>
      )}
    </RowFrame>
  );
};

export const ReasoningSegmentList: React.FC<{ segments: ReasoningSegmentListItem[] }> = ({
  segments,
}) => {
  if (!segments || segments.length === 0) return null;
  return (
    <div className="space-y-1.5">
      {segments.map((segment, i) => (
        <ReasoningSegmentRow key={segment.model_call_id || `segment-${i}`} segment={segment} />
      ))}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Source rows
// ---------------------------------------------------------------------------

const SourceRow: React.FC<{ source: FetchSourceActivity; index: number }> = ({ source, index }) => {
  const duration = formatDuration(source.durationMs);
  let icon: React.ReactNode;
  let text: string;
  let tone: string;

  if (source.status === "success") {
    icon = <Check className="h-3.5 w-3.5 shrink-0 text-emerald-500" />;
    text = `\u2713 ${source.host}${duration ? ` ${duration}` : ""}`;
    tone = "text-muted-foreground";
  } else if (source.status === "timeout") {
    icon = <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" />;
    text = `\u26a0 ${source.host} timeout`;
    tone = "text-amber-600 dark:text-amber-400";
  } else if (source.status === "failed") {
    icon = <X className="h-3.5 w-3.5 shrink-0 text-destructive" />;
    text = `\u2717 ${source.host} failed`;
    tone = "text-destructive";
  } else {
    icon = <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />;
    text = `Fetching ${source.host}…`;
    tone = "text-muted-foreground";
  }

  return (
    <div
      data-testid={`run-fetch-${index}`}
      data-status={source.status}
      className={cn("flex items-center gap-1.5 text-[11px]", tone)}
      title={source.url}
    >
      {icon}
      <span className="truncate">{text}</span>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Tool rows
// ---------------------------------------------------------------------------

const WebSearchRow: React.FC<{ activity: ToolRunActivity }> = ({ activity }) => {
  const running = isToolRunning(activity);
  const [open, setOpen] = useAutoOpen(running);
  const duration = formatDuration(activity.durationMs);
  const results = activity.searchResults || [];
  const count = activity.searchCount ?? results.length;
  const label = running
    ? "Searching web…"
    : `Searched web · ${count} result${count === 1 ? "" : "s"}${duration ? ` · ${duration}` : ""}`;

  return (
    <RowFrame testId="run-search" status={activity.status}>
      <RowHeader
        running={running}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        toggleTestId="run-search-toggle"
        icon={<Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
        label={label}
      />
      {open && (
        <RowBody testId="run-search-body">
          {activity.query ? (
            <div>
              <span className="text-muted-foreground/70">Query: </span>
              <span className="text-foreground/80">{activity.query}</span>
            </div>
          ) : null}
          {activity.provider ? (
            <div>
              <span className="text-muted-foreground/70">Provider: </span>
              <span className="text-foreground/80">{activity.provider}</span>
            </div>
          ) : null}
          {results.length > 0 && (
            <div className="space-y-1">
              {results.map((result, i) => (
                <div key={i} data-testid={`run-search-result-${i}`} className="min-w-0">
                  {result.title ? <div className="truncate text-foreground/80">{result.title}</div> : null}
                  {result.url ? <div className="truncate text-muted-foreground/80">{result.url}</div> : null}
                  {result.snippet ? <div className="text-muted-foreground/70">{result.snippet}</div> : null}
                </div>
              ))}
            </div>
          )}
        </RowBody>
      )}
    </RowFrame>
  );
};

const WebFetchRow: React.FC<{ activity: ToolRunActivity }> = ({ activity }) => {
  const sources = activity.sources || [];
  const running = isToolRunning(activity) || sources.some((s) => s.status === "running");
  const [open, setOpen] = useAutoOpen(running);
  const first = sources[0];
  const duration = formatDuration(first?.durationMs ?? activity.durationMs);
  let label: string;
  if (sources.length === 0) {
    label = running ? "Fetching…" : "Fetched";
  } else if (running) {
    label = `Fetching ${sources.length > 1 ? `${sources.length} sources` : first?.host ?? "…"}…`;
  } else {
    label = `Fetched ${sources.length > 1 ? `${sources.length} sources` : first?.host ?? "…"}${duration ? ` · ${duration}` : ""}`;
  }

  return (
    <RowFrame testId="run-fetch" status={activity.status}>
      <RowHeader
        running={running}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        toggleTestId="run-fetch-toggle"
        icon={<Globe className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
        label={label}
      />
      {open && sources.length > 0 && (
        <RowBody testId="run-fetch-body">
          <div className="space-y-0.5">
            {sources.map((source, i) => (
              <SourceRow key={`${source.url}-${i}`} source={source} index={i} />
            ))}
          </div>
        </RowBody>
      )}
    </RowFrame>
  );
};

const WebResearchRow: React.FC<{ activity: ToolRunActivity }> = ({ activity }) => {
  const sources = activity.sources || [];
  const successful = sources.filter((s) => s.status === "success").length;
  const fetched = activity.summary?.fetched ?? successful;
  const selected = activity.summary?.selected ?? activity.summary?.searched ?? sources.length;
  const duration = formatDuration(activity.durationMs);
  const running = isToolRunning(activity) || sources.some((s) => s.status === "running");
  const failed = activity.status === "failed";
  const [open, setOpen] = useAutoOpen(running);

  let label: string;
  if (running) {
    label = sources.length === 0 ? "Researching…" : `Fetching ${fetched}/${selected}…`;
  } else {
    label = `${failed ? "" : "Researched the web · "}fetched ${fetched}/${selected} sources${duration ? ` · ${duration}` : ""}`;
  }
  const results = activity.searchResults || [];
  const resultCount = activity.searchCount ?? results.length;

  return (
    <RowFrame testId="run-research" status={activity.status}>
      <RowHeader
        running={running}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        toggleTestId="run-research-toggle"
        icon={<Globe className="h-3.5 w-3.5 shrink-0 text-sky-500/80" />}
        label={label}
      />
      {open && (
        <RowBody testId="run-research-body">
          {activity.query ? (
            <div>
              <span className="text-muted-foreground/70">Query: </span>
              <span className="text-foreground/80">{activity.query}</span>
            </div>
          ) : null}
          <div>
            <span className="text-muted-foreground/70">Results: </span>
            <span className="text-foreground/80">{resultCount}</span>
          </div>
          {sources.length > 0 && (
            <div className="space-y-0.5">
              {sources.map((source, i) => (
                <SourceRow key={`${source.url}-${i}`} source={source} index={i} />
              ))}
            </div>
          )}
        </RowBody>
      )}
    </RowFrame>
  );
};

const GenericToolRow: React.FC<{ activity: ToolRunActivity }> = ({ activity }) => {
  const running = isToolRunning(activity);
  const [open, setOpen] = useAutoOpen(running);
  const name = humanize(activity.name);

  return (
    <RowFrame testId="run-tool" status={activity.status}>
      <RowHeader
        running={running}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        toggleTestId="run-tool-toggle"
        icon={<Wrench className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
        label={running ? `${name}…` : name}
      />
      {open && (
        <RowBody testId="run-tool-body">
          {activity.arguments ? (
            <div>
              <div className="text-muted-foreground/70">Arguments</div>
              <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-foreground/80">
                {activity.arguments}
              </pre>
            </div>
          ) : null}
          {activity.result != null ? (
            <div>
              <div className="text-muted-foreground/70">Result</div>
              <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words font-mono text-[11px] text-foreground/80">
                {stringify(activity.result)}
              </pre>
            </div>
          ) : null}
          {activity.error != null ? (
            <div>
              <div className="text-destructive">Error</div>
              <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-destructive">
                {stringify(activity.error)}
              </pre>
            </div>
          ) : null}
        </RowBody>
      )}
    </RowFrame>
  );
};

const ToolActivityRow: React.FC<{ activity: ToolRunActivity }> = ({ activity }) => {
  const name = (activity.name || "").toLowerCase();
  if (name === "web_search") return <WebSearchRow activity={activity} />;
  if (name === "web_fetch") return <WebFetchRow activity={activity} />;
  if (name === "web_research") return <WebResearchRow activity={activity} />;
  return <GenericToolRow activity={activity} />;
};

export const RunActivityTimeline: React.FC<RunActivityTimelineProps> = ({
  activities,
  className,
}) => {
  if (!activities || activities.length === 0) return null;
  return (
    <div data-testid="run-activity-timeline" className={cn("space-y-1.5", className)}>
      {activities.map((activity) =>
        activity.kind === "thought" ? (
          <ThoughtRow key={`thought-${activity.id}`} activity={activity} />
        ) : (
          <ToolActivityRow key={`tool-${activity.id}`} activity={activity} />
        )
      )}
    </div>
  );
};

export default RunActivityTimeline;
