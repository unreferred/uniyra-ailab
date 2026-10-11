import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { streamRun } from "@/lib/runStream";

export type RunPhase =
  | "thinking"
  | "searching"
  | "fetching"
  | "analyzing"
  | "finalizing"
  | "waiting"
  | "idle";

export type RunStatus = "idle" | "running" | "done" | "error" | "cancelled" | "output_limit";

export type ActivityStatus = "running" | "done" | "failed";

export interface ThoughtActivity {
  id: string;
  kind: "thought";
  modelCallId?: string;
  roundIndex?: number;
  phase: "initial" | "continuation" | "finalization";
  text: string;
  status: ActivityStatus;
  startedAt: number;
  finishedAt?: number;
  durationMs?: number;
  finalizationRecoveryMode?: string | null;
  finalizationRecoveryReason?: string | null;
}

export type FetchSourceStatus = "running" | "success" | "failed" | "timeout";

export interface FetchSourceActivity {
  url: string;
  host: string;
  title?: string;
  status: FetchSourceStatus;
  durationMs?: number;
  code?: string;
  message?: string;
  timeoutSeconds?: number;
  provider?: string;
}

export interface ToolRunActivity {
  id: string;
  kind: "tool";
  name: string;
  status: "requested" | "arguments-ready" | "running" | "completed" | "failed";
  arguments?: string;
  result?: any;
  error?: any;
  query?: string;
  provider?: string;
  durationMs?: number;
  searchCount?: number;
  searchResults?: Array<{ title?: string; url?: string; snippet?: string }>;
  sources?: FetchSourceActivity[];
  summary?: { searched?: number; selected?: number; fetched?: number; failed?: number };
  outcome?: string;
}

export type RunActivity = ThoughtActivity | ToolRunActivity;

export interface RunStreamState {
  status: RunStatus;
  phase: RunPhase;
  activities: RunActivity[];
  answerText: string;
  runId?: string;
  traceId?: string;
  run?: any;
  assistantMessage?: any;
  error?: any;
  partial?: boolean;
  outputLimit?: boolean;
  outputLimitReason?: string;
  toolOutcome?: string;
  sawDelta: boolean;
  continuationStatusText?: string;
  continuationIndex?: number;
  maxContinuations?: number;
  conversationId?: string;
}

export const initialRunStreamState: RunStreamState = {
  status: "idle",
  phase: "idle",
  activities: [],
  answerText: "",
  sawDelta: false,
};

export function acceptsRunStreamEvent(
  event: any,
  conversationId: string,
  generation: number,
  activeGeneration: number,
): boolean {
  return (
    generation === activeGeneration &&
    (!event?.conversation_id || event.conversation_id === conversationId)
  );
}

function eventType(evt: any): string {
  if (!evt || typeof evt !== "object") return "";
  if (typeof evt.type === "string") return evt.type;
  if (typeof evt.event === "string") return evt.event;
  return "";
}

function hostOf(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url || "";
  }
}

function asText(value: any): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function isOutputLimit(run: any, evt: any): boolean {
  if (evt?.output_limit === true || run?.output_limit === true) return true;
  if (run?.finalization_truncated === true) return true;
  return ["length", "max_tokens", "max_output_tokens", "token_limit", "output_limit"].includes(
    String(run?.finish_reason || "").toLowerCase()
  );
}

function toolKey(evt: any): string {
  return (
    evt?.provider_tool_call_id ||
    evt?.tool_call_id ||
    `${evt?.tool_name || evt?.name || "tool"}#${evt?.index ?? 0}`
  );
}

function toolName(evt: any): string {
  return evt?.tool_name || evt?.name || "tool";
}

function thoughtKey(evt: any): string {
  return evt?.model_call_id || `round:${evt?.round_index ?? 0}`;
}

function thoughtPhase(evt: any): ThoughtActivity["phase"] {
  if (evt?.type === "FINALIZATION_STARTED" || evt?.kind === "finalization") return "finalization";
  if ((evt?.round_index ?? 0) > 0) return "continuation";
  return "initial";
}

function updateThought(
  activities: RunActivity[],
  id: string,
  create: () => ThoughtActivity,
  patch: Partial<ThoughtActivity>
): RunActivity[] {
  const idx = activities.findIndex((a) => a.kind === "thought" && a.id === id);
  if (idx < 0) return [...activities, { ...create(), ...patch }];
  const copy = activities.slice();
  copy[idx] = { ...(copy[idx] as ThoughtActivity), ...patch };
  return copy;
}

function updateTool(
  activities: RunActivity[],
  key: string,
  createName: string,
  patchFn: (tool: ToolRunActivity) => Partial<ToolRunActivity>
): RunActivity[] {
  const idx = activities.findIndex((a) => a.kind === "tool" && a.id === key);
  if (idx < 0) {
    const base: ToolRunActivity = {
      id: key,
      kind: "tool",
      name: createName || "tool",
      status: "running",
    };
    return [...activities, { ...base, ...patchFn(base) }];
  }
  const copy = activities.slice();
  const current = copy[idx] as ToolRunActivity;
  copy[idx] = { ...current, ...patchFn(current) };
  return copy;
}

function upsertSource(
  sources: FetchSourceActivity[] | undefined,
  url: string,
  patch: Partial<FetchSourceActivity>
): FetchSourceActivity[] {
  const list = sources ? sources.slice() : [];
  const target = patch.url || url;
  const idx = list.findIndex((s) => (target && s.url === target) || (patch.url && s.url === patch.url));
  if (idx < 0) {
    list.push({
      url: target,
      host: patch.host || hostOf(target),
      status: "running",
      ...patch,
    });
    return list;
  }
  const prev = list[idx];
  list[idx] = {
    ...prev,
    ...patch,
    url: patch.url || prev.url,
    host: patch.host || prev.host || hostOf(patch.url || prev.url),
  };
  return list;
}

function mapSearchResults(raw: any): ToolRunActivity["searchResults"] | undefined {
  if (!Array.isArray(raw)) return undefined;
  return raw.map((r: any) => {
    if (typeof r === "string") return { url: r };
    return {
      title: r?.title,
      url: r?.url || r?.link || r?.source_url,
      snippet: r?.snippet || r?.content || r?.text,
    };
  });
}

function mapEvidenceSources(raw: any): FetchSourceActivity[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  return raw.map((s: any) => {
    if (typeof s === "string") {
      return { url: s, host: hostOf(s), status: "success" as FetchSourceStatus };
    }
    const url = s?.url || s?.source_url || s?.link || "";
    const code = s?.code ?? s?.error?.code;
    const statusRaw = String(s?.status || s?.fetch_status || "").toLowerCase();
    let status: FetchSourceStatus = "success";
    if (statusRaw.includes("timeout")) status = "timeout";
    else if (statusRaw.includes("fail") || statusRaw.includes("error")) status = "failed";
    else if (statusRaw.includes("run") || statusRaw.includes("pending")) status = "running";
    if (code === "FETCH_TIMEOUT") status = "timeout";
    return {
      url,
      host: s?.host || hostOf(url),
      title: s?.title,
      status,
      durationMs: typeof (s?.duration_ms ?? s?.durationMs) === "number" ? s?.duration_ms ?? s?.durationMs : undefined,
      code,
      message: s?.message ?? s?.error?.message,
      timeoutSeconds:
        typeof (s?.timeout_seconds ?? s?.timeoutSeconds) === "number"
          ? s?.timeout_seconds ?? s?.timeoutSeconds
          : undefined,
      provider: s?.provider,
    };
  });
}

function parseResearchResult(_tool: ToolRunActivity, result: any): Partial<ToolRunActivity> {
  if (!result || typeof result !== "object") return {};
  const search = result.search || result.search_result || {};
  const evidence = result.evidence || {};
  const results = search.results ?? result.results ?? evidence.results;
  const sources = result.sources ?? evidence.sources;
  const summary = result.summary ?? evidence.summary;
  const outcome = result.outcome ?? evidence.outcome;
  const query = result.query ?? search.query ?? evidence.query;

  const patch: Partial<ToolRunActivity> = {};
  if (query != null) patch.query = String(query);
  const mappedResults = mapSearchResults(results);
  if (mappedResults) patch.searchResults = mappedResults;
  const mappedSources = mapEvidenceSources(sources);
  if (mappedSources) patch.sources = mappedSources;
  if (summary && typeof summary === "object") patch.summary = summary;
  if (outcome != null) patch.outcome = String(outcome);
  return patch;
}

/**
 * Pure projection of the canonical agentic stream protocol. Unknown event
 * types are ignored so a newer backend never breaks the UI.
 *
 * Reasonings are segmented per model call (never merged into one blob) and
 * tool activity is interleaved chronologically with thought activity.
 */
export function runStreamReducer(state: RunStreamState, evt: any): RunStreamState {
  if (!evt || typeof evt !== "object") return state;
  const type = eventType(evt);

  // `done: true` is legacy payload metadata, not a lifecycle event. Some
  // providers emit completion-shaped frames for a model call or finalization
  // round; only the canonical RUN_* events may finalize the assistant turn.

  switch (type) {
    case "RUN_STARTED":
      return {
        ...state,
        status: "running",
        phase: "thinking",
        runId: evt.run_id || state.runId,
        traceId: evt.trace_id || state.traceId,
      };

    case "MODEL_STARTED":
    case "FINALIZATION_STARTED": {
      const id = thoughtKey(evt);
      const phase = thoughtPhase(evt);
      const now = Date.now();
      const activities = updateThought(
        state.activities,
        id,
        () => ({
          id,
          kind: "thought",
          phase,
          text: "",
          status: "running",
          startedAt: now,
          modelCallId: evt.model_call_id,
          roundIndex: evt.round_index,
        }),
        {
          phase,
          status: "running",
          ...(evt.model_call_id ? { modelCallId: evt.model_call_id } : {}),
          ...(evt.round_index != null ? { roundIndex: evt.round_index } : {}),
          ...(type === "FINALIZATION_STARTED"
            ? {
                finalizationRecoveryMode: evt.recovery_mode ?? evt.recoveryMode ?? null,
                finalizationRecoveryReason: evt.recovery_reason ?? evt.recoveryReason ?? null,
              }
            : {}),
        }
      );
      const runPhase: RunPhase =
        type === "FINALIZATION_STARTED" || evt.kind === "finalization"
          ? "finalizing"
          : evt.streaming === false
          ? "waiting"
          : "thinking";
      return { ...state, phase: runPhase, activities };
    }

    case "MODEL_REASONING_DELTA": {
      const delta = evt.delta ?? evt.content ?? "";
      if (!delta) return state;
      const id = thoughtKey(evt);
      const idx = state.activities.findIndex((a) => a.kind === "thought" && a.id === id);
      if (idx < 0) {
        const now = Date.now();
        return {
          ...state,
          activities: [
            ...state.activities,
            {
              id,
              kind: "thought",
              phase: thoughtPhase(evt),
              text: delta,
              status: "running",
              startedAt: now,
              modelCallId: evt.model_call_id,
              roundIndex: evt.round_index,
            },
          ],
        };
      }
      const copy = state.activities.slice();
      const cur = copy[idx] as ThoughtActivity;
      copy[idx] = { ...cur, text: cur.text + delta };
      return { ...state, activities: copy };
    }

    case "MODEL_OUTPUT_DELTA": {
      const delta = evt.delta ?? evt.content ?? "";
      if (!delta) return { ...state, sawDelta: true };
      return { ...state, answerText: state.answerText + delta, sawDelta: true };
    }

    case "MODEL_COMPLETED":
    case "FINALIZATION_COMPLETED": {
      const id = thoughtKey(evt);
      const idx = state.activities.findIndex((a) => a.kind === "thought" && a.id === id);
      if (idx < 0) return state;
      const copy = state.activities.slice();
      const cur = copy[idx] as ThoughtActivity;
      const finishedAt = Date.now();
      copy[idx] = {
        ...cur,
        status: "done",
        finishedAt,
        durationMs: evt.duration_ms ?? Math.max(0, finishedAt - cur.startedAt),
      };
      return { ...state, activities: copy };
    }

    case "FINALIZATION_DELTA": {
      const delta = evt.delta ?? evt.content ?? "";
      if (!delta) return { ...state, phase: "finalizing" };
      return {
        ...state,
        phase: "finalizing",
        answerText: state.answerText + delta,
        sawDelta: true,
      };
    }

    case "TOOL_CALL_STARTED":
      return {
        ...state,
        activities: updateTool(state.activities, toolKey(evt), toolName(evt), () => ({
          name: toolName(evt),
          status: "requested",
        })),
      };

    case "TOOL_CALL_DELTA": {
      const key = toolKey(evt);
      return {
        ...state,
        activities: updateTool(state.activities, key, toolName(evt), (t) => ({
          ...(evt.name ? { name: evt.name } : {}),
          arguments: (t.arguments || "") + (evt.delta || ""),
        })),
      };
    }

    case "TOOL_CALL_ARGUMENTS": {
      const key = toolKey(evt);
      return {
        ...state,
        activities: updateTool(state.activities, key, toolName(evt), () => ({
          name: toolName(evt),
          arguments: asText(evt.arguments),
        })),
      };
    }

    case "TOOL_CALL_COMPLETED":
      return {
        ...state,
        activities: updateTool(state.activities, toolKey(evt), toolName(evt), () => ({
          name: toolName(evt),
          arguments: evt.arguments != null ? asText(evt.arguments) : undefined,
          status: "arguments-ready",
        })),
      };

    case "TOOL_RESULT_STARTED":
      return {
        ...state,
        activities: updateTool(state.activities, toolKey(evt), toolName(evt), () => ({
          name: toolName(evt),
          status: "running",
        })),
      };

    case "TOOL_RESULT_COMPLETED": {
      const key = toolKey(evt);
      const explicitName = evt.tool_name || evt.name;
      return {
        ...state,
        activities: updateTool(state.activities, key, explicitName || toolName(evt), (t) => {
          const patch: Partial<ToolRunActivity> = {
            ...(explicitName ? { name: explicitName } : {}),
            result: evt.result,
            status: "completed",
            ...(evt.duration_ms != null ? { durationMs: evt.duration_ms } : {}),
          };
          if (t.name === "web_research" || explicitName === "web_research") {
            Object.assign(patch, parseResearchResult(t, evt.result));
          }
          return patch;
        }),
      };
    }

    case "TOOL_RESULT_FAILED": {
      const key = toolKey(evt);
      const exists = state.activities.some((a) => a.kind === "tool" && a.id === key);
      if (!exists) return state; // benign sub-span failure — nothing to attach to
      return {
        ...state,
        activities: updateTool(state.activities, key, toolName(evt), () => ({
          error: evt.error,
          status: "failed",
        })),
      };
    }

    case "SEARCH_STARTED": {
      const key = toolKey(evt);
      const createName = evt.tool_name || evt.name || "web_search";
      return {
        ...state,
        phase: "searching",
        activities: updateTool(state.activities, key, createName, (t) => ({
          query: evt.query ?? t.query ?? "",
          provider: evt.provider ?? t.provider,
          status: "running",
        })),
      };
    }

    case "SEARCH_COMPLETED": {
      const key = toolKey(evt);
      const createName = evt.tool_name || evt.name || "web_search";
      return {
        ...state,
        phase: "analyzing",
        activities: updateTool(state.activities, key, createName, (t) => {
          const patch: Partial<ToolRunActivity> = {
            searchCount: typeof evt.count === "number" ? evt.count : t.searchCount,
            ...(evt.query != null ? { query: evt.query } : {}),
            ...(evt.duration_ms != null ? { durationMs: evt.duration_ms } : {}),
          };
          const results = mapSearchResults(evt.results ?? evt.search_results);
          if (results) patch.searchResults = results;
          return patch;
        }),
      };
    }

    case "FETCH_STARTED": {
      const key = toolKey(evt);
      const url = evt.url || "";
      return {
        ...state,
        phase: "fetching",
        activities: updateTool(state.activities, key, "web_fetch", (t) => ({
          status: t.status === "completed" || t.status === "failed" ? t.status : "running",
          sources: upsertSource(t.sources, url, {
            url,
            host: hostOf(url),
            status: "running",
            provider: evt.provider,
          }),
        })),
      };
    }

    case "FETCH_COMPLETED": {
      const key = toolKey(evt);
      const url = evt.url || "";
      return {
        ...state,
        activities: updateTool(state.activities, key, "web_fetch", (t) => ({
          sources: upsertSource(t.sources, url, {
            url,
            host: hostOf(url),
            status: "success",
            durationMs: evt.duration_ms,
            provider: evt.provider ?? t.provider,
          }),
        })),
      };
    }

    case "FETCH_FAILED": {
      const err = evt.error || {};
      const url = err.url || evt.url || "";
      return {
        ...state,
        activities: updateTool(state.activities, toolKey(evt), "web_fetch", (t) => ({
          sources: upsertSource(t.sources, url, {
            url: url || undefined,
            host: url ? hostOf(url) : undefined,
            provider: err.provider || evt.provider,
            status: err.code === "FETCH_TIMEOUT" ? "timeout" : "failed",
            code: err.code,
            message: err.message,
            timeoutSeconds: err.timeout_seconds,
          }),
        })),
      };
    }

    case "RUN_COMPLETED":
    case "RUN_COMPLETE": {
      const run = evt.run;
      const assistantMessage = evt.assistant_message || evt.assistantMessage || state.assistantMessage;
      const terminalText =
        evt.full_text || run?.normalized_response || assistantMessage?.content || run?.content || "";
      // Keep every streamed delta. The terminal payload may contain a more
      // complete canonical response, but it must never replace a longer live
      // projection with a truncated one.
      const answerText = !state.answerText
        ? terminalText
        : terminalText.length >= state.answerText.length && terminalText.startsWith(state.answerText)
        ? terminalText
        : state.answerText;
      const limit = isOutputLimit(run, evt);
      const limitReason = evt.output_limit_reason || run?.finish_reason || run?.provider_finish_reason || null;
      const finishReason = run?.finish_reason || (limit ? "length" : "stop");
      const reconciledAssistantMessage = assistantMessage
        ? {
            ...assistantMessage,
            content: answerText,
            finish_reason: finishReason,
            output_limit: limit,
            output_limit_reason: limit ? limitReason : null,
          }
        : assistantMessage;
      return {
        ...state,
        status: isOutputLimit(run, evt) ? "output_limit" : "done",
        run,
        assistantMessage: reconciledAssistantMessage,
        partial: evt.partial,
        toolOutcome: evt.tool_outcome,
        outputLimit: isOutputLimit(run, evt),
        outputLimitReason: evt.output_limit_reason || run?.finish_reason,
        answerText,
      };
    }

    case "continuation_started": {
      return {
        ...state,
        continuationStatusText: evt.status_text || `Output limit reached · Continuing automatically… (${evt.continuation_index || 1}/${evt.max_continuations || 3})`,
        continuationIndex: evt.continuation_index,
        maxContinuations: evt.max_continuations,
      };
    }

    case "RUN_FAILED":
      return { ...state, status: "error", error: evt.error, run: evt.run ?? state.run };

    case "RUN_CANCELLED":
      return { ...state, status: "cancelled", error: evt.error, run: evt.run ?? state.run };

    default:
      return state;
  }
}

export interface UseRunStreamResult {
  state: RunStreamState;
  start: (conversationId: string, payload: object) => Promise<void>;
  stop: () => void;
  reset: () => void;
}

export function useRunStream(): UseRunStreamResult {
  const [state, setState] = useState<RunStreamState>(initialRunStreamState);
  const abortRef = useRef<AbortController | null>(null);
  const stateRef = useRef<RunStreamState>(state);
  const canonicalStateRef = useRef<RunStreamState>(initialRunStreamState);
  const frameIdRef = useRef<any>(null);
  const generationRef = useRef(0);
  const conversationIdRef = useRef<string | null>(null);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const cancelScheduledFlush = useCallback(() => {
    if (frameIdRef.current !== null) {
      if (typeof cancelAnimationFrame === "function") {
        cancelAnimationFrame(frameIdRef.current);
      } else {
        clearTimeout(frameIdRef.current);
      }
      frameIdRef.current = null;
    }
  }, []);

  const flushNow = useCallback(() => {
    cancelScheduledFlush();
    setState(canonicalStateRef.current);
  }, [cancelScheduledFlush]);

  const scheduleFlush = useCallback(() => {
    if (frameIdRef.current !== null) return;
    if (typeof requestAnimationFrame === "function") {
      frameIdRef.current = requestAnimationFrame(() => {
        frameIdRef.current = null;
        setState(canonicalStateRef.current);
      });
    } else {
      frameIdRef.current = setTimeout(() => {
        frameIdRef.current = null;
        setState(canonicalStateRef.current);
      }, 0);
    }
  }, []);

  useEffect(() => {
    return () => {
      cancelScheduledFlush();
    };
  }, [cancelScheduledFlush]);

  const start = useCallback(async (conversationId: string, payload: object) => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    conversationIdRef.current = conversationId;
    abortRef.current?.abort();
    cancelScheduledFlush();
    const controller = new AbortController();
    abortRef.current = controller;
    const initialRunning = {
      ...initialRunStreamState,
      status: "running" as const,
      phase: "thinking" as const,
      conversationId,
    };
    canonicalStateRef.current = initialRunning;
    setState(initialRunning);
    let sawTerminalEvent = false;
    try {
      await streamRun(conversationId, payload, {
        signal: controller.signal,
        onEvent: (evt) => {
          if (!acceptsRunStreamEvent(evt, conversationId, generation, generationRef.current) || conversationIdRef.current !== conversationId) return;
          const type = eventType(evt);
          canonicalStateRef.current = runStreamReducer(canonicalStateRef.current, evt);

          const isTerminal =
            type === "RUN_COMPLETED" ||
            type === "RUN_COMPLETE" ||
            type === "RUN_FAILED" ||
            type === "RUN_CANCELLED";

          if (isTerminal) {
            sawTerminalEvent = true;
            flushNow();
            return;
          }

          const isDelta =
            type === "MODEL_OUTPUT_DELTA" ||
            type === "FINALIZATION_DELTA" ||
            type === "MODEL_REASONING_DELTA" ||
            type === "TOOL_CALL_DELTA";

          if (isDelta) {
            scheduleFlush();
          } else {
            flushNow();
          }
        },
      });
      if (generationRef.current !== generation || conversationIdRef.current !== conversationId) return;
      if (!sawTerminalEvent) {
        const error = "Stream ended before a terminal event";
        canonicalStateRef.current = { ...canonicalStateRef.current, status: "error", error };
        flushNow();
        throw new Error(error);
      }
    } catch (e: any) {
      if (generationRef.current !== generation || conversationIdRef.current !== conversationId) return;
      if (controller.signal.aborted || e?.name === "AbortError") return;
      canonicalStateRef.current = {
        ...canonicalStateRef.current,
        status: "error",
        error: e?.message || "Stream failed",
      };
      flushNow();
      throw e;
    }
  }, [cancelScheduledFlush, flushNow, scheduleFlush]);

  const stop = useCallback(() => {
    generationRef.current += 1;
    abortRef.current?.abort();
    cancelScheduledFlush();
    canonicalStateRef.current =
      canonicalStateRef.current.status === "running"
        ? { ...canonicalStateRef.current, status: "cancelled" }
        : canonicalStateRef.current;
    flushNow();
    const runId = stateRef.current?.runId;
    if (runId) {
      void api.post(`/runs/${runId}/cancel`).catch(() => {});
    }
  }, [cancelScheduledFlush, flushNow]);

  const reset = useCallback(() => {
    generationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    conversationIdRef.current = null;
    cancelScheduledFlush();
    canonicalStateRef.current = initialRunStreamState;
    setState(initialRunStreamState);
  }, [cancelScheduledFlush]);

  return { state, start, stop, reset };
}
