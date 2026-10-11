import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Plus, Send, RefreshCw, Save, Trash2, MessageSquare, Loader2, Sparkles, Wand2, ListChecks, Columns2, RotateCcw, ArrowDown,
  CheckSquare, Square, X,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { api, apiError, uploadAttachments } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { useModels, useAttachments } from "@/lib/hooks";
import { Attachment, ContentBlock, Conversation, Message } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { SingleModelSelect, ModelCapabilityHint } from "@/components/common/ModelPicker";
import { MarkdownView } from "@/components/common/Markdown";
import { EmptyState, CopyButton, TelemetryRow, ErrorNote, PulseDot } from "@/components/common/Common";
import { SaveArtifactDialog } from "@/components/common/SaveArtifactDialog";
import { AttachmentKindIcon, AttachmentsBar } from "@/components/workspace/AttachmentsBar";
import { WorkspaceModeSelect } from "@/components/workspace/WorkspaceModeSelect";
import { PromptCaptureDialog } from "@/components/workspace/PromptCaptureDialog";
import { PlanningStatePanel, PlanningDecisionsView } from "@/components/workspace/PlanningStatePanel";
import { PlanMapView } from "@/components/workspace/PlanMapView";
import { PrdView } from "@/components/workspace/PrdView";
import { ToolsPopover } from "@/components/workspace/ToolsPopover";
import { CompatibilityInspector } from "@/components/workspace/CompatibilityInspector";
import { SearchControl } from "@/components/workspace/SearchControl";
import { FetchControl } from "@/components/workspace/FetchControl";
import { KnowledgePopover, RetrievalOptions } from "@/components/workspace/KnowledgePopover";
import { ContextPopover, DEFAULT_CONTEXT_OPTIONS } from "@/components/workspace/ContextPopover";
import type { ContextOptions } from "@/components/workspace/ContextPopover";
import { ParamsPopover } from "@/components/workspace/ParamsPopover";
import { TraceDrawer } from "@/components/workspace/TraceDrawer";
import { ContextDrawer } from "@/components/workspace/ContextDrawer";
import { AttachmentViewer } from "@/components/workspace/AttachmentViewer";
import { ReasoningPanel } from "@/components/workspace/ReasoningPanel";
import { RunStreamPanel } from "@/components/workspace/RunStreamPanel";
import { ReasoningSegmentList } from "@/components/workspace/RunActivityTimeline";
import { useRunStream } from "@/hooks/useRunStream";
import { cn } from "@/lib/utils";
import { relTime } from "@/lib/format";

const PLAN_TABS = [
  { key: "chat", label: "Chat" },
  { key: "plan-map", label: "Plan Map" },
  { key: "prd", label: "PRD" },
  { key: "decisions", label: "Decisions" },
];

interface MessageItemProps {
  message: Message;
  attachmentMap: Map<string, Attachment>;
  openViewer: (a: Attachment) => void;
  setInput: (text: string) => void;
  setCaptureTarget: (target: { messageIds: string[]; mode: "response" | "conversation" }) => void;
  setSaveContent: (content: string) => void;
  setSaveModelId: (modelId?: string) => void;
  setSaveRunId: (runId?: string) => void;
  setSaveOpen: (open: boolean) => void;
  setTraceRunId: (runId: string | null) => void;
  setContextRunId: (runId: string | null) => void;
  onContinue: (prompt?: string) => void;
  onRetry: () => void;
  isContinuing: boolean;
  isRetrying: boolean;
  busy: boolean;
}

const MessageItem: React.FC<MessageItemProps> = React.memo(({
  message: m,
  attachmentMap,
  openViewer,
  setInput,
  setCaptureTarget,
  setSaveContent,
  setSaveModelId,
  setSaveRunId,
  setSaveOpen,
  setTraceRunId,
  setContextRunId,
  onContinue,
  onRetry,
  isContinuing,
  isRetrying,
  busy,
}) => {
  const isOutputLimit = Boolean(m.output_limit || m.finish_reason === "length" || m.telemetry?.finish_reason === "length");

  const renderUserAttachments = () => {
    const blocks = (m.content_blocks || []) as ContentBlock[];
    const refs = blocks.filter((b) => b.attachment_id);
    if (refs.length === 0) return null;
    return (
      <div className="mt-2 flex flex-wrap gap-1.5">
        {refs.map((b, i) => {
          const att = b.attachment_id ? attachmentMap.get(b.attachment_id) : undefined;
          const name = att?.original_filename || (b.metadata?.filename as string) || b.attachment_id || "attachment";
          const kind = att?.kind || (b.metadata?.kind as string | undefined);
          return (
            <button
              key={b.attachment_id || i}
              type="button"
              data-testid={`message-attachment-${b.attachment_id || i}`}
              onClick={() => att && openViewer(att)}
              className={cn(
                "flex max-w-[200px] items-center gap-1.5 rounded-md border border-primary-foreground/25 bg-primary-foreground/10 px-2 py-1 text-[11px]",
                att && "hover:bg-primary-foreground/20"
              )}
              title={String(name)}
            >
              <AttachmentKindIcon kind={kind} className="h-3 w-3 shrink-0" />
              <span className="truncate">{String(name)}</span>
            </button>
          );
        })}
      </div>
    );
  };

  return (
    <div key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
      <Card
        className={cn(
          "max-w-[85%] p-3.5 fade-in-up",
          m.role === "user" ? "bg-primary text-primary-foreground" : "bg-card"
        )}
        data-testid={`message-${m.role}`}
      >
        {m.role === "user" && m.status !== "error" && m.status !== "failed" ? (
          <>
            {m.content && <p className="whitespace-pre-wrap text-sm">{m.content}</p>}
            {renderUserAttachments()}
          </>
        ) : m.status === "error" || m.status === "failed" ? (
          <>
            <p className="whitespace-pre-wrap text-sm">{m.content}</p>
            <ErrorNote message={m.error || "Send failed"} />
            {m.role === "user" && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="mt-2 h-7 px-2 text-[11px]"
                data-testid={`retry-message-${m.id}`}
                onClick={() => {
                  setInput(m.content);
                  requestAnimationFrame(() => (document.querySelector("[data-testid='workspace-input']") as HTMLTextAreaElement | null)?.focus());
                }}
              >
                <RotateCcw className="mr-1 h-3 w-3" /> Retry
              </Button>
            )}
          </>
        ) : (
          <>
            <div className="mb-1.5 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-[11px] font-medium text-muted-foreground">{m.model_name}</span>
                {Boolean(m.continuation_count && m.continuation_count > 0) && (
                  <span className="inline-flex items-center rounded-full bg-secondary/80 px-2 py-0.5 text-[10px] font-medium text-secondary-foreground" data-testid={`message-continuation-badge-${m.id}`}>
                    Completed · {m.continuation_count} continuation{(m.continuation_count || 0) > 1 ? "s" : ""}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                <CopyButton value={m.content} testId={`copy-message-${m.id}`} />
                <button
                  data-testid={`capture-prompt-${m.id}`}
                  title="Capture as prompt"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
                  onClick={() => setCaptureTarget({ messageIds: [m.id], mode: "response" })}
                >
                  <Wand2 className="h-3.5 w-3.5" />
                </button>
                <button
                  data-testid={`save-artifact-${m.id}`}
                  title="Save as artifact"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
                  onClick={() => {
                    setSaveContent(m.content);
                    setSaveModelId(m.model_id);
                    setSaveRunId(m.run_id);
                    setSaveOpen(true);
                  }}
                >
                  <Save className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
            {m.reasoning_segments?.length ? (
              <ReasoningSegmentList segments={m.reasoning_segments} />
            ) : (
              <ReasoningPanel
                reasoningTelemetry={m.telemetry?.reasoning_telemetry || m.reasoning_telemetry}
                reasoningContent={m.reasoning_content || m.telemetry?.reasoning_telemetry?.content}
                reasoningTokens={m.telemetry?.reasoning_tokens ?? m.telemetry?.token_usage?.reasoning_tokens}
              />
            )}
            {isOutputLimit && (
              <p className="my-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-[11px] text-amber-700 dark:text-amber-300">
                Output limit reached ({m.output_limit_reason || m.finish_reason || "length"}).
              </p>
            )}
            <MarkdownView content={m.content} />
            <div className="mt-2.5 border-t border-border pt-2">
              <TelemetryRow t={m.telemetry} />
              <div className="mt-2 flex flex-wrap items-center gap-1">
                {(m.run_id || m.trace_id) && (
                  <>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 px-2 text-[11px]"
                      data-testid={`message-trace-${m.id}`}
                      onClick={() => setTraceRunId(m.run_id || m.trace_id || null)}
                    >
                      Trace
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 px-2 text-[11px]"
                      data-testid={`message-context-${m.id}`}
                      onClick={() => setContextRunId(m.run_id || null)}
                    >
                      Context
                    </Button>
                  </>
                )}
                {isOutputLimit && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 px-2 text-[11px]"
                    data-testid={`message-continue-${m.id}`}
                    disabled={isContinuing || busy}
                    onClick={() => onContinue("Please continue from where you left off.")}
                  >
                    {isContinuing ? (
                      <>
                        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                        Continuing…
                      </>
                    ) : (
                      "Continue"
                    )}
                  </Button>
                )}
                {/* Recovery action on historical failed assistant messages */}
                {(m.status === "error" || m.status === "failed" || m.status === "cancelled") && (
                  <>
                    {m.content && m.content.trim().length > 0 && !isOutputLimit && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 px-2 text-[11px]"
                        data-testid={`message-continue-${m.id}`}
                        disabled={isContinuing || isRetrying || busy}
                        onClick={() => onContinue()}
                      >
                        {isContinuing ? (
                          <>
                            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                            Continuing…
                          </>
                        ) : (
                          "Continue"
                        )}
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 px-2 text-[11px]"
                      data-testid={`message-retry-${m.id}`}
                      disabled={isRetrying || isContinuing || busy}
                      onClick={onRetry}
                    >
                      {isRetrying ? (
                        <>
                          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                          Retrying…
                        </>
                      ) : (
                        "Retry"
                      )}
                    </Button>
                  </>
                )}
              </div>
            </div>
          </>
        )}
      </Card>
    </div>
  );
});
MessageItem.displayName = "MessageItem";

export default function Workspace() {
  const { activeId, activeProject } = useProjects();
  const { data: models = [] } = useModels();
  const enabledModels = models.filter((m) => m.enabled);
  const qc = useQueryClient();

  const invalidatePlanningQueries = useCallback(
    (conversationId: string | null | undefined) => {
      if (!activeId || !conversationId) return;
      qc.invalidateQueries({ queryKey: ["planning-state", activeId, conversationId] });
      qc.invalidateQueries({ queryKey: ["planning-graph", activeId, conversationId] });
      qc.invalidateQueries({ queryKey: ["prd-readiness", activeId, conversationId] });
      qc.invalidateQueries({ queryKey: ["prd-versions", activeId] });
    },
    [activeId, qc]
  );

  const [searchParams, setSearchParams] = useSearchParams();
  const [convId, setConvId] = useState<string | null>(() => searchParams.get("conv"));
  const convIdRef = React.useRef(convId);
  React.useEffect(() => { convIdRef.current = convId; }, [convId]);
  const [modelId, setModelId] = useState("");
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(1024);
  const [topP, setTopP] = useState(1);
  const [seed, setSeed] = useState("");
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveContent, setSaveContent] = useState("");
  const [saveModelId, setSaveModelId] = useState<string | undefined>(undefined);
  const [saveRunId, setSaveRunId] = useState<string | undefined>(undefined);
  const [supportsStream, setSupportsStream] = useState(false);
  const [streamOn, setStreamOn] = useState(false);
  const runStream = useRunStream();
  const doneHandledRef = useRef<string | null>(null);

  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [tools, setTools] = useState<string[]>([]);
  const [searchEnabled, setSearchEnabled] = useState<boolean>(false);
  const [searchProviderId, setSearchProviderId] = useState<string | null>(null);
  const [fetchEnabled, setFetchEnabled] = useState<boolean>(false);
  const [fetchProviderId, setFetchProviderId] = useState<string | null>(null);
  const [knowledgeEnabled, setKnowledgeEnabled] = useState<boolean>(false);
  const [knowledgeIds, setKnowledgeIds] = useState<string[]>([]);
  const [retrievalOptions, setRetrievalOptions] = useState<RetrievalOptions>({});
  const [contextOptions, setContextOptions] = useState<ContextOptions>(DEFAULT_CONTEXT_OPTIONS);
  const [contextStrategy, setContextStrategy] = useState("FULL");
  const [dragging, setDragging] = useState(false);
  const [viewerAttachment, setViewerAttachment] = useState<Attachment | null>(null);
  const [traceRunId, setTraceRunId] = useState<string | null>(null);
  const [contextRunId, setContextRunId] = useState<string | null>(null);
  const [workspaceMode, setWorkspaceMode] = useState("chat");
  const [captureTarget, setCaptureTarget] = useState<{ messageIds: string[]; mode: "response" | "conversation" } | null>(null);
  const [planningOpen, setPlanningOpen] = useState(false);
  const [planTab, setPlanTab] = useState("chat");
  const [planSplit, setPlanSplit] = useState(false);
  const [optimisticMessages, setOptimisticMessages] = useState<Message[]>([]);

  // Deletion state
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);
  const [bulkSelectMode, setBulkSelectMode] = useState(false);
  const [selectedConvIds, setSelectedConvIds] = useState<string[]>([]);
  const [bulkDeleteConfirmOpen, setBulkDeleteConfirmOpen] = useState(false);


  const scrollRef = useRef<HTMLDivElement>(null);
  const scrollContentRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inFlightRef = useRef(false);

  useEffect(() => {
    if (!modelId && enabledModels[0]) setModelId(enabledModels[0].id);
  }, [enabledModels, modelId]);

  const { data: conversations = [] } = useQuery<Conversation[]>({
    queryKey: ["conversations", activeId],
    queryFn: async () => (await api.get(`/conversations?project_id=${activeId}`)).data,
    enabled: !!activeId,
  });

  const { data: conversation } = useQuery<Conversation>({
    queryKey: ["conversation", convId],
    queryFn: async () => (await api.get(`/conversations/${convId}`)).data,
    enabled: !!convId,
  });

  const { data: convAttachments } = useAttachments(activeId, convId);
  const attachmentMap = useMemo(() => {
    const map = new Map<string, Attachment>();
    (convAttachments?.items || []).forEach((a) => map.set(a.id, a));
    return map;
  }, [convAttachments]);

  // Hydrate editor state from the conversation (system prompt + defaults).
  useEffect(() => {
    if (!conversation) {
      setWorkspaceMode("chat");
      if (activeProject) {
        setSearchEnabled(!!(activeProject as any).default_search_enabled);
        setSearchProviderId((activeProject as any).default_search_provider_id ?? null);
        setFetchEnabled(!!(activeProject as any).default_fetch_enabled);
        setFetchProviderId((activeProject as any).default_fetch_provider_id ?? null);
        const defaultKnowledgeIds = [...((activeProject as any).default_knowledge_collection_ids || [])];
        setKnowledgeIds(defaultKnowledgeIds);
        setKnowledgeEnabled(Boolean((activeProject as any).default_knowledge_enabled ?? true) && defaultKnowledgeIds.length > 0);
      }
      return;
    }
    setSystemPrompt(conversation.system_prompt || "");
    setWorkspaceMode(conversation.workspace_mode || "chat");
    const d = conversation.defaults || {};
    if (d.temperature != null) setTemperature(d.temperature);
    if (d.max_tokens != null) setMaxTokens(d.max_tokens);
    if (d.top_p != null) setTopP(d.top_p);
    if (d.streaming != null) setStreamOn(d.streaming);
    if (d.enabled_tool_ids) setTools([...d.enabled_tool_ids]);
    const conversationKnowledgeIds = [...((conversation as any).knowledge_collection_ids || [])];
    setKnowledgeIds(conversationKnowledgeIds);
    setKnowledgeEnabled(Boolean((conversation as any).knowledge_enabled ?? (activeProject as any)?.default_knowledge_enabled ?? true) && conversationKnowledgeIds.length > 0);
    if ((conversation as any).retrieval_options) setRetrievalOptions({ ...((conversation as any).retrieval_options as RetrievalOptions) });
    if (d.context_strategy) setContextStrategy(d.context_strategy);
    if (d.context_options) {
      setContextOptions((prev) => ({ ...prev, ...(d.context_options as Partial<ContextOptions>) }));
    }
    const sOn = (conversation as any).search_enabled ?? (activeProject as any)?.default_search_enabled ?? false;
    setSearchEnabled(!!sOn);
    setSearchProviderId((conversation as any).search_provider_id ?? (activeProject as any)?.default_search_provider_id ?? null);

    const fOn = (conversation as any).fetch_enabled ?? (activeProject as any)?.default_fetch_enabled ?? false;
    setFetchEnabled(!!fOn);
    setFetchProviderId((conversation as any).fetch_provider_id ?? (activeProject as any)?.default_fetch_provider_id ?? null);
  }, [conversation?.id, activeProject?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const [isDetached, setIsDetached] = useState(false);
  const lastScrollTopRef = useRef<number>(0);
  const isProgrammaticScrollRef = useRef<boolean>(false);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;

    if (isProgrammaticScrollRef.current) {
      lastScrollTopRef.current = el.scrollTop;
      return;
    }

    const currentScrollTop = el.scrollTop;
    const scrollDelta = currentScrollTop - lastScrollTopRef.current;
    lastScrollTopRef.current = currentScrollTop;

    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 48;

    if (isNearBottom) {
      setIsDetached(false);
    } else if (scrollDelta < -2) {
      // User explicitly scrolled upward
      setIsDetached(true);
    }
  }, []);

  const scrollToBottom = useCallback(() => {
    if (scrollRef.current) {
      isProgrammaticScrollRef.current = true;
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      lastScrollTopRef.current = scrollRef.current.scrollTop;
      requestAnimationFrame(() => {
        isProgrammaticScrollRef.current = false;
      });
    }
  }, []);

  useEffect(() => {
    runStream.reset();
    setOptimisticMessages([]);
    setIsDetached(false);
    lastScrollTopRef.current = 0;
    isProgrammaticScrollRef.current = false;
    // runStream is a stable hook owner; the conversation id is the reset boundary.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId]);

  useEffect(() => {
    const content = scrollContentRef.current;
    if (!content || typeof ResizeObserver === "undefined") return;
    let frame: number | null = null;
    const align = () => {
      if (frame !== null) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        if (!isDetached) scrollToBottom();
      });
    };
    const observer = new ResizeObserver(align);
    observer.observe(content);
    return () => {
      observer.disconnect();
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [isDetached, scrollToBottom]);

  // When run starts, if near bottom, ensure follow is active and pin
  useEffect(() => {
    if (runStream.state.status === "running") {
      const el = scrollRef.current;
      if (el) {
        const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 80;
        if (isNearBottom) {
          setIsDetached(false);
          scrollToBottom();
        }
      }
    }
  }, [runStream.state.status, scrollToBottom]);

  // While follow is ON (not detached), keep viewport pinned to flushed content
  useEffect(() => {
    if (!isDetached) {
      scrollToBottom();
    }
  }, [
    conversation?.messages?.length,
    runStream.state.answerText,
    runStream.state.activities,
    runStream.state.continuationStatusText,
    isDetached,
    scrollToBottom,
  ]);

  useEffect(() => {
    if (workspaceMode !== "plan") setPlanTab("chat");
  }, [workspaceMode]);

  // stream-support lookup
  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      if (!convId || !modelId) {
        setSupportsStream(false);
        setStreamOn(false);
        return;
      }
      try {
        const { data } = await api.get(`/conversations/${convId}/stream-support?model_id=${modelId}`);
        if (!cancelled) {
          const ok = !!data.supports_stream;
          setSupportsStream(ok);
          setStreamOn(ok);
        }
      } catch {
        if (!cancelled) {
          setSupportsStream(true);
          setStreamOn(true);
        }
      }
    };
    check();
    return () => {
      cancelled = true;
    };
  }, [convId, modelId]);

  // Best-effort persistence of generation params to the conversation.
  useEffect(() => {
    if (!convId) return;
    const t = setTimeout(() => {
      Promise.resolve(
        api.patch(`/conversations/${convId}`, {
          temperature,
          max_tokens: maxTokens,
          top_p: topP,
          streaming: streamOn,
        })
      ).catch(() => {});
    }, 700);
    return () => clearTimeout(t);
  }, [convId, temperature, maxTokens, topP, streamOn]);

  const createConv = useMutation({
    mutationFn: async () =>
      (await api.post("/conversations", { project_id: activeId, title: "New conversation", system_prompt: systemPrompt, workspace_mode: workspaceMode })).data,
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["conversations", activeId] });
      selectConv(c.id);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  React.useEffect(() => {
    const q = searchParams.get("conv");
    if (q && q !== convIdRef.current) setConvId(q);
    if (!q && convIdRef.current) setConvId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const selectConv = React.useCallback((id: string | null) => {
    setConvId(id);
    setSearchParams(id ? { conv: id } : {}, { replace: true });
  }, [setSearchParams]);

  const delConv = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/conversations/${id}`)).data,
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: ["conversations", activeId] });
      if (convId === id) selectConv(null);
      setDeleteTarget(null);
      toast.success("Conversation deleted");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const bulkDelConv = useMutation({
    mutationFn: async (ids: string[]) =>
      (await api.post("/conversations/bulk-delete", { conversation_ids: ids, project_id: activeId })).data,
    onSuccess: (data, ids) => {
      qc.invalidateQueries({ queryKey: ["conversations", activeId] });
      if (convId && ids.includes(convId)) selectConv(null);
      setSelectedConvIds([]);
      setBulkSelectMode(false);
      setBulkDeleteConfirmOpen(false);
      toast.success(`Deleted ${data.deleted_count || ids.length} conversation(s)`);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const send = useMutation({
    mutationFn: async (args: { id: string; payload: Record<string, any> }) =>
      (await api.post(`/conversations/${args.id}/messages`, args.payload)).data,
    onSuccess: (_d, vars) => {
      setInput("");
      setAttachments([]);
      qc.invalidateQueries({ queryKey: ["conversation", vars.id] });
      qc.invalidateQueries({ queryKey: ["conversations", activeId] });
      qc.invalidateQueries({ queryKey: ["attachments", activeId, vars.id] });
      invalidatePlanningQueries(vars.id);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const regenerate = useMutation({
    mutationFn: async () =>
      (
        await api.post(`/conversations/${convId}/regenerate`, {
          model_id: modelId,
          workspace_mode: workspaceMode,
          system_prompt: systemPrompt,
          temperature,
          max_tokens: maxTokens,
          top_p: topP,
          ...(seed.trim() !== "" && !Number.isNaN(Number(seed)) ? { seed: Number(seed) } : {}),
          enabled_tool_ids: tools,
          context_strategy: contextStrategy,
          context_options: contextOptions,
          knowledge_enabled: knowledgeEnabled,
          knowledge_collection_ids: knowledgeEnabled ? knowledgeIds : [],
          retrieval_options: knowledgeEnabled ? retrievalOptions : {},
        })
      ).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversation", convId] });
      invalidatePlanningQueries(convId);
      runStream.reset();
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const continueRun = useMutation({
    mutationFn: async (vars?: string | { continuation_prompt?: string } | void) => {
      const continuationPrompt = typeof vars === "string" ? vars : vars?.continuation_prompt;
      return (
        await api.post(`/conversations/${convId}/continue`, {
          model_id: modelId,
          continuation_prompt: continuationPrompt || undefined,
          workspace_mode: workspaceMode,
          system_prompt: systemPrompt,
          temperature,
          max_tokens: maxTokens,
          top_p: topP,
          ...(seed.trim() !== "" && !Number.isNaN(Number(seed)) ? { seed: Number(seed) } : {}),
          enabled_tool_ids: tools,
          context_strategy: contextStrategy,
          context_options: contextOptions,
          knowledge_enabled: knowledgeEnabled,
          knowledge_collection_ids: knowledgeEnabled ? knowledgeIds : [],
          retrieval_options: knowledgeEnabled ? retrievalOptions : {},
        })
      ).data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversation", convId] });
      invalidatePlanningQueries(convId);
      runStream.reset();
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const ensureConversation = useCallback(
    async (title: string): Promise<string> => {
      if (convId) return convId;
      const c = (
        await api.post("/conversations", {
          project_id: activeId,
          title: title.slice(0, 40) || "New conversation",
          workspace_mode: workspaceMode,
          system_prompt: systemPrompt,
          knowledge_enabled: knowledgeEnabled,
          knowledge_collection_ids: knowledgeEnabled ? knowledgeIds : [],
          retrieval_options: knowledgeEnabled ? retrievalOptions : {},
        })
      ).data;
      selectConv(c.id);
      qc.invalidateQueries({ queryKey: ["conversations", activeId] });
      return c.id as string;
    },
    [convId, activeId, systemPrompt, workspaceMode, knowledgeEnabled, knowledgeIds, retrievalOptions, qc, selectConv]
  );

  const buildPayload = (text: string) => ({
    model_id: modelId,
    content: text,
    workspace_mode: workspaceMode,
    system_prompt: systemPrompt,
    temperature,
    max_tokens: maxTokens,
    top_p: topP,
    ...(seed.trim() !== "" && !Number.isNaN(Number(seed)) ? { seed: Number(seed) } : {}),
    streaming: streamOn,
    attachment_ids: attachments.map((a) => a.id),
    enabled_tool_ids: tools,
    context_strategy: contextStrategy,
    context_options: contextOptions,
    knowledge_enabled: knowledgeEnabled,
    knowledge_collection_ids: knowledgeEnabled ? knowledgeIds : [],
    retrieval_options: knowledgeEnabled ? retrievalOptions : {},
    search_enabled: searchEnabled,
    search_provider_id: searchProviderId,
    fetch_enabled: fetchEnabled,
    fetch_provider_id: fetchProviderId,
  });

  const handleFiles = useCallback(
    async (files: File[]) => {
      if (!activeId || files.length === 0) return;
      setUploading(true);
      try {
        const created = await uploadAttachments(activeId, files, convId || undefined);
        setAttachments((prev) => [...prev, ...created]);
        qc.invalidateQueries({ queryKey: ["attachments", activeId, convId] });
      } catch (e) {
        toast.error(apiError(e));
      } finally {
        setUploading(false);
      }
    },
    [activeId, convId, qc]
  );

  const dragHasFiles = (e: React.DragEvent) =>
    Array.from(e.dataTransfer?.types || []).includes("Files");

  const onPickFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length) void handleFiles(files);
    e.target.value = "";
  };

  const removeAttachment = (id: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
    api.delete(`/attachments/${id}`).catch(() => {});
    qc.invalidateQueries({ queryKey: ["attachments", activeId, convId] });
  };

  const onPaste = (e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData?.files || []).filter((f) => f.type.startsWith("image/"));
    if (files.length) {
      e.preventDefault();
      void handleFiles(files);
    }
  };

  const onToolsChange = (ids: string[]) => {
    setTools(ids);
    if (convId) api.patch(`/conversations/${convId}`, { enabled_tool_ids: ids }).catch(() => {});
  };
  const onKnowledgeChange = (ids: string[]) => {
    setKnowledgeIds(ids);
    const enabled = ids.length > 0;
    setKnowledgeEnabled(enabled);
    if (convId) {
      api.patch(`/conversations/${convId}`, {
        knowledge_enabled: enabled,
        knowledge_collection_ids: enabled ? ids : [],
        retrieval_options: enabled ? retrievalOptions : {},
      }).catch(() => {});
    }
  };
  const onKnowledgeEnabledChange = (enabled: boolean) => {
    const nextEnabled = enabled && knowledgeIds.length > 0;
    setKnowledgeEnabled(nextEnabled);
    if (convId) api.patch(`/conversations/${convId}`, {
      knowledge_enabled: nextEnabled,
      knowledge_collection_ids: nextEnabled ? knowledgeIds : [],
      retrieval_options: nextEnabled ? retrievalOptions : {},
    }).catch(() => {});
  };
  const onRetrievalChange = (o: RetrievalOptions) => {
    setRetrievalOptions(o);
    if (convId) api.patch(`/conversations/${convId}`, { retrieval_options: o }).catch(() => {});
  };
  const onContextOptionsChange = (o: ContextOptions) => {
    setContextOptions(o);
    if (convId) api.patch(`/conversations/${convId}`, { context_options: o }).catch(() => {});
  };
  const onStrategyChange = (s: string) => {
    setContextStrategy(s);
    if (convId) api.patch(`/conversations/${convId}`, { context_strategy: s }).catch(() => {});
  };
  const onWorkspaceModeChange = (mode: string) => {
    setWorkspaceMode(mode);
    if (convId) api.patch(`/conversations/${convId}`, { workspace_mode: mode }).catch(() => {});
  };

  const busy = send.isPending || runStream.state.status === "running";
  const streamSupported = !!modelId && (supportsStream || !convId);

  const doSend = async () => {
    if (inFlightRef.current || busy) return;
    if ((!input.trim() && attachments.length === 0) || !modelId) return;
    inFlightRef.current = true;
    const text = input;
    const optimisticId = `optimistic-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const optimisticMessage: Message = {
      id: optimisticId,
      conversation_id: convId || "pending",
      role: "user",
      content: text,
      attachment_ids: attachments.map((a) => a.id),
      status: "sending",
      created_at: new Date().toISOString(),
    };
    setOptimisticMessages((prev) => [...prev, optimisticMessage]);
    setInput("");
    setAttachments([]);
    try {
      const id = await ensureConversation(text || "Attachments");
      const payload = buildPayload(text);
      doneHandledRef.current = null;
      setOptimisticMessages((prev) =>
        prev.map((message) => message.id === optimisticId ? { ...message, conversation_id: id, status: "sent" } : message)
      );
      try {
        // Tool-enabled, reasoning and plain runs all execute through the
        // streaming endpoint. `streaming:false` still projects a compact
        // single-shot event set rather than forcing the legacy path.
        await runStream.start(id, payload);
      } catch (streamErr) {
        // Transport-level failure only: fall back ONCE to the non-streaming
        // endpoint so the user still gets an answer.
        if (runStream.state.status === "cancelled") return;
        void streamErr;
        runStream.reset();
        toast.message("Streaming unavailable — sent without streaming");
        try {
          await send.mutateAsync({ id, payload: { ...payload, streaming: false } });
        } catch (err) {
          toast.error(apiError(err));
          setOptimisticMessages((prev) =>
            prev.map((message) => message.id === optimisticId ? { ...message, status: "failed", error: apiError(err) } : message)
          );
        }
      }
    } catch (err) {
      // Conversation creation errors are surfaced by the mutation handlers.
      setOptimisticMessages((prev) =>
        prev.map((message) => message.id === optimisticId ? { ...message, status: "failed", error: apiError(err) } : message)
      );
    } finally {
      inFlightRef.current = false;
    }
  };

  const openViewer = useCallback((a: Attachment) => setViewerAttachment(a), []);

  // Project a completed run into the react-query cache immediately, then drop
  // the ephemeral live panel once the persisted assistant message is visible.
  useEffect(() => {
    const st = runStream.state;
    if (st.status !== "done" && st.status !== "output_limit") return;
    const key = st.runId || st.assistantMessage?.id || "done";
    if (doneHandledRef.current === key) return;
    doneHandledRef.current = key;
    const msg = st.assistantMessage;
    const messageConversationId = msg?.conversation_id || st.conversationId;
    if (messageConversationId && msg) {
      qc.setQueryData(["conversation", messageConversationId], (old: any) => {
        if (!old) return old;
        const list = old.messages || [];
        if (msg.id && list.some((m: any) => m.id === msg.id)) return old;
        return { ...old, messages: [...list, msg] };
      });
    }
    if (messageConversationId) {
      qc.invalidateQueries({ queryKey: ["conversation", messageConversationId] });
      qc.invalidateQueries({ queryKey: ["conversations", activeId] });
      qc.invalidateQueries({ queryKey: ["attachments", activeId, messageConversationId] });
      invalidatePlanningQueries(messageConversationId);
    }
  }, [runStream.state.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const persistedAssistantId = runStream.state.assistantMessage?.id;
  const persistedRunId = runStream.state.runId;
  useEffect(() => {
    if (runStream.state.status !== "done" && runStream.state.status !== "output_limit") return;
    const persisted = (conversation?.messages || []).some(
      (m) =>
        (persistedAssistantId && m.id === persistedAssistantId) ||
        (persistedRunId && m.run_id === persistedRunId)
    );
    if (persisted) runStream.reset();
  }, [runStream.state.status, persistedAssistantId, persistedRunId, conversation?.messages]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!activeId) {
    return (
      <div className="p-6">
        <EmptyState
          icon={<MessageSquare className="h-6 w-6" />}
          title="No project selected"
          description="Create or select a project from the top bar to start a workspace conversation."
        />
      </div>
    );
  }

  const messages = conversation?.messages || [];
  const visibleMessages = [
    ...messages,
    ...optimisticMessages.filter((optimistic) =>
      !messages.some((message) =>
        message.role === "user" && message.content === optimistic.content &&
        new Date(message.created_at).getTime() >= new Date(optimistic.created_at).getTime()
      )
    ),
  ];

  const renderUserAttachments = (m: Message) => {
    const blocks = (m.content_blocks || []) as ContentBlock[];
    const refs = blocks.filter((b) => b.attachment_id);
    if (refs.length === 0) return null;
    return (
      <div className="mt-2 flex flex-wrap gap-1.5">
        {refs.map((b, i) => {
          const att = b.attachment_id ? attachmentMap.get(b.attachment_id) : undefined;
          const name = att?.original_filename || (b.metadata?.filename as string) || b.attachment_id || "attachment";
          const kind = att?.kind || (b.metadata?.kind as string | undefined);
          return (
            <button
              key={b.attachment_id || i}
              type="button"
              data-testid={`message-attachment-${b.attachment_id || i}`}
              onClick={() => att && openViewer(att)}
              className={cn(
                "flex max-w-[200px] items-center gap-1.5 rounded-md border border-primary-foreground/25 bg-primary-foreground/10 px-2 py-1 text-[11px]",
                att && "hover:bg-primary-foreground/20"
              )}
              title={String(name)}
            >
              <AttachmentKindIcon kind={kind} className="h-3 w-3 shrink-0" />
              <span className="truncate">{String(name)}</span>
            </button>
          );
        })}
      </div>
    );
  };

  const isPlanMode = workspaceMode === "plan";
  const planChatOnly = !isPlanMode || planTab === "chat";
  const planPanelTestId = `workspace-plan-${planTab}`;

  const renderPlanPanel = () => {
    if (planTab === "plan-map") {
      return <PlanMapView projectId={activeId} conversationId={convId ?? undefined} className="h-full" />;
    }
    if (planTab === "prd") {
      return <PrdView projectId={activeId} conversationId={convId ?? undefined} className="h-full" />;
    }
    if (planTab === "decisions") {
      return (
        <div data-testid="workspace-plan-decisions-view" className="flex min-h-0 flex-1 flex-col">
          <PlanningDecisionsView
            projectId={activeId}
            conversationId={convId}
            className="flex-1 min-h-0"
          />
        </div>
      );
    }
    return null;
  };

  const chatColumn = (
    <>
        <div ref={scrollRef} onScroll={handleScroll} className="relative flex-1 overflow-y-auto p-4 lg:p-6">
          <div ref={scrollContentRef} className="space-y-4">
          {messages.length === 0 && !busy && (
            <EmptyState
              icon={<Sparkles className="h-6 w-6" />}
              title="Start experimenting"
              description={`Send a prompt to a model in "${activeProject?.name}". Every response is captured as a Run with full telemetry.`}
              testId="workspace-empty"
            />
          )}
          {visibleMessages.map((m: Message) => (
            <MessageItem
              key={m.id}
              message={m}
              attachmentMap={attachmentMap}
              openViewer={openViewer}
              setInput={setInput}
              setCaptureTarget={setCaptureTarget}
              setSaveContent={setSaveContent}
              setSaveModelId={setSaveModelId}
              setSaveRunId={setSaveRunId}
              setSaveOpen={setSaveOpen}
              setTraceRunId={setTraceRunId}
              setContextRunId={setContextRunId}
              onContinue={(prompt) => continueRun.mutate(prompt ? { continuation_prompt: prompt } : undefined)}
              onRetry={() => regenerate.mutate()}
              isContinuing={continueRun.isPending}
              isRetrying={regenerate.isPending}
              busy={busy}
            />
          ))}
          {send.isPending && (
            <div className="flex justify-start">
              <Card className="flex items-center gap-2 p-3.5">
                <PulseDot />
                <span className="text-sm text-muted-foreground">Generating response…</span>
              </Card>
            </div>
          )}
          {runStream.state.status !== "idle" && (
            <div className="flex justify-start">
              <RunStreamPanel
                state={runStream.state}
                onStop={runStream.stop}
                answerText={runStream.state.answerText}
                onRetry={() => {
                  if (regenerate.isPending || continueRun.isPending || busy) return;
                  regenerate.mutate();
                }}
                onContinue={() => {
                  if (continueRun.isPending || regenerate.isPending || busy) return;
                  continueRun.mutate();
                }}
                isRetrying={regenerate.isPending}
                isContinuing={continueRun.isPending}
              />
            </div>
          )}
          </div>
          {isDetached && (
            <div className="sticky bottom-2 flex justify-center pointer-events-none z-10">
              <Button
                type="button"
                size="sm"
                variant="secondary"
                className="shadow-md pointer-events-auto text-xs gap-1.5 h-7 px-3 animate-in fade-in"
                data-testid="jump-to-latest"
                onClick={() => {
                  scrollToBottom();
                  setIsDetached(false);
                }}
              >
                <ArrowDown className="h-3.5 w-3.5" /> Follow latest
              </Button>
            </div>
          )}
        </div>

        {/* composer */}
        <div className="border-t border-border p-3">
          <AttachmentsBar
            attachments={attachments}
            onRemove={removeAttachment}
            onOpen={openViewer}
            uploading={uploading}
            className="mb-2"
          />

          <div className="mb-2 flex flex-wrap items-center gap-2">
            <WorkspaceModeSelect value={workspaceMode} onChange={onWorkspaceModeChange} />
            <Button
              type="button"
              size="sm"
              variant="outline"
              data-testid="workspace-attach-button"
              disabled={uploading}
              onClick={() => fileInputRef.current?.click()}
            >
              <Plus className="mr-1 h-3.5 w-3.5" /> Attach
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              data-testid="workspace-file-input"
              onChange={onPickFiles}
            />
            <ToolsPopover projectId={activeId} value={tools} onChange={onToolsChange} modelId={modelId} />
            <CompatibilityInspector projectId={activeId} model={enabledModels.find((m) => m.id === modelId) || null} enabledToolIds={tools} />
            <SearchControl
              enabled={searchEnabled}
              onEnabledChange={(on) => {
                setSearchEnabled(on);
                if (convId) api.patch(`/conversations/${convId}`, { search_enabled: on }).catch(() => {});
              }}
              providerId={searchProviderId}
              onProviderChange={(pid) => {
                setSearchProviderId(pid);
                if (convId) api.patch(`/conversations/${convId}`, { search_provider_id: pid }).catch(() => {});
              }}
            />
            <FetchControl
              enabled={fetchEnabled}
              onEnabledChange={(on) => {
                setFetchEnabled(on);
                if (convId) api.patch(`/conversations/${convId}`, { fetch_enabled: on }).catch(() => {});
              }}
              providerId={fetchProviderId}
              onProviderChange={(pid) => {
                setFetchProviderId(pid);
                if (convId) api.patch(`/conversations/${convId}`, { fetch_provider_id: pid }).catch(() => {});
              }}
            />
            <KnowledgePopover projectId={activeId} value={knowledgeIds} knowledgeEnabled={knowledgeEnabled} onChange={onKnowledgeChange} onKnowledgeEnabledChange={onKnowledgeEnabledChange} retrievalOptions={retrievalOptions} onRetrievalChange={onRetrievalChange} />
            <ContextPopover
              options={contextOptions}
              onChange={onContextOptionsChange}
              strategy={contextStrategy}
              onStrategyChange={onStrategyChange}
            />
            <ParamsPopover
              systemPrompt={systemPrompt}
              onSystemPrompt={setSystemPrompt}
              temperature={temperature}
              onTemperature={setTemperature}
              maxTokens={maxTokens}
              onMaxTokens={setMaxTokens}
              topP={topP}
              onTopP={setTopP}
              seed={seed}
              onSeed={setSeed}
              streaming={streamOn}
              onStreaming={setStreamOn}
              streamSupported={streamSupported}
              selectedModel={enabledModels.find((m) => m.id === modelId) || null}
            />
          </div>

          <div className="flex items-end gap-2">
            <Textarea
              data-testid="workspace-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPaste={onPaste}
              onKeyDown={(e) => {
                const isComposing = (e.nativeEvent as any)?.isComposing || e.keyCode === 229;
                if (e.key === "Enter") {
                  if (isComposing) {
                    return;
                  }
                  if (e.shiftKey) {
                    return; // Shift+Enter inserts newline
                  }
                  e.preventDefault();
                  if ((!input.trim() && attachments.length === 0) || !modelId || busy || inFlightRef.current) {
                    return;
                  }
                  void doSend();
                }
              }}
              placeholder="Send a prompt… (Enter to send, Shift+Enter for newline, paste images to attach)"
              rows={2}
              className="flex-1 resize-none"
            />
            <Button
              data-testid="workspace-send"
              disabled={(!input.trim() && attachments.length === 0) || !modelId || busy}
              onClick={() => void doSend()}
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </Button>
          </div>
        </div>
    </>
  );

  return (
    <div className="flex h-full">
      {/* conversation list */}
      <div className="hidden w-[260px] shrink-0 flex-col border-r border-border bg-card md:flex">
        <div className="flex items-center justify-between border-b border-border p-3">
          <span className="font-display text-sm font-semibold">Conversations</span>
          <div className="flex items-center gap-1">
            {conversations.length > 0 && (
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs"
                data-testid="conversation-select-mode-toggle"
                onClick={() => {
                  setBulkSelectMode(!bulkSelectMode);
                  setSelectedConvIds([]);
                }}
              >
                {bulkSelectMode ? "Cancel" : "Select"}
              </Button>
            )}
            <Button size="sm" variant="ghost" className="h-7 w-7 p-0" data-testid="new-conversation-button" onClick={() => createConv.mutate()}>
              <Plus className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {bulkSelectMode && (
          <div className="flex items-center justify-between border-b border-border bg-muted/30 px-3 py-1.5 text-xs">
            <span className="text-muted-foreground font-medium" data-testid="conversation-selected-count">
              {selectedConvIds.length} selected
            </span>
            <div className="flex items-center gap-1">
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-1.5 text-[11px]"
                data-testid="conversation-select-all"
                onClick={() => {
                  if (selectedConvIds.length === conversations.length) {
                    setSelectedConvIds([]);
                  } else {
                    setSelectedConvIds(conversations.map((c) => c.id));
                  }
                }}
              >
                {selectedConvIds.length === conversations.length ? "Deselect all" : "Select all"}
              </Button>
              <Button
                size="sm"
                variant="destructive"
                className="h-6 px-2 text-[11px]"
                data-testid="conversation-bulk-delete-btn"
                disabled={selectedConvIds.length === 0 || bulkDelConv.isPending}
                onClick={() => setBulkDeleteConfirmOpen(true)}
              >
                Delete
              </Button>
            </div>
          </div>
        )}

        <div className="flex-1 space-y-1 overflow-y-auto p-2 scrollbar-thin">
          {conversations.length === 0 && (
            <p className="px-2 py-4 text-xs text-muted-foreground">No conversations yet.</p>
          )}
          {conversations.map((c) => {
            const isSelected = selectedConvIds.includes(c.id);
            return (
              <div
                key={c.id}
                data-testid={`conversation-${c.id}`}
                onClick={() => {
                  if (bulkSelectMode) {
                    setSelectedConvIds((prev) =>
                      prev.includes(c.id) ? prev.filter((id) => id !== c.id) : [...prev, c.id]
                    );
                  } else {
                    selectConv(c.id);
                  }
                }}
                className={cn(
                  "group flex w-full cursor-pointer items-center justify-between rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                  convId === c.id && !bulkSelectMode ? "bg-accent" : "hover:bg-accent/50",
                  isSelected && "bg-accent/70"
                )}
              >
                {bulkSelectMode && (
                  <div
                    className="mr-2 shrink-0 flex items-center"
                    onClick={(e) => {
                      e.stopPropagation();
                    }}
                  >
                    <Checkbox
                      checked={isSelected}
                      data-testid={`conversation-select-${c.id}`}
                      onCheckedChange={() => {
                        setSelectedConvIds((prev) =>
                          prev.includes(c.id) ? prev.filter((id) => id !== c.id) : [...prev, c.id]
                        );
                      }}
                    />
                  </div>
                )}
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{c.title}</span>
                  <span className="block text-[10px] text-muted-foreground">{relTime(c.updated_at)}</span>
                </span>
                {!bulkSelectMode && (
                  <Trash2
                    data-testid={`delete-conversation-${c.id}`}
                    className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                    onClick={(e) => {
                      e.stopPropagation();
                      setDeleteTarget(c);
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* chat area */}
      <div
        className="relative flex min-w-0 flex-1 flex-col"
        onDragEnter={(e) => {
          if (!dragHasFiles(e)) return;
          e.preventDefault();
          setDragging(true);
        }}
        onDragOver={(e) => {
          if (!dragHasFiles(e)) return;
          e.preventDefault();
          if (!dragging) setDragging(true);
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false);
        }}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const files = Array.from(e.dataTransfer?.files || []);
          if (files.length) void handleFiles(files);
        }}
      >
        {dragging && (
          <div className="pointer-events-none absolute inset-3 z-30 flex items-center justify-center rounded-lg border-2 border-dashed border-primary bg-background/80 text-sm font-medium">
            Drop files to attach
          </div>
        )}

        <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
          <div className="w-[240px]">
            <SingleModelSelect models={enabledModels} value={modelId} onChange={setModelId} testId="workspace-model-select" />
          </div>
          <ModelCapabilityHint model={enabledModels.find((m) => m.id === modelId) || null} />
          {messages.length > 0 && (
            <Button
              size="sm"
              variant="ghost"
              data-testid="workspace-regenerate"
              onClick={() => regenerate.mutate()}
              disabled={regenerate.isPending}
            >
              {regenerate.isPending ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-1.5 h-4 w-4" />
              )}{" "}
              Regenerate
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            data-testid="capture-conversation"
            disabled={messages.length === 0}
            onClick={() => setCaptureTarget({ messageIds: [], mode: "conversation" })}
          >
            <Wand2 className="mr-1.5 h-4 w-4" /> Capture prompt
          </Button>
          <Button
            size="sm"
            variant="ghost"
            data-testid="workspace-planning-state"
            disabled={!convId}
            onClick={() => setPlanningOpen(true)}
          >
            <ListChecks className="mr-1.5 h-4 w-4" /> Planning state
          </Button>
        </div>

        {isPlanMode && (
          <div
            data-testid="workspace-plan-tabs"
            className="flex items-center gap-1 border-b border-border px-3 py-1.5"
          >
            {PLAN_TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                data-testid={`workspace-plan-tab-${tab.key}`}
                aria-pressed={planTab === tab.key}
                onClick={() => setPlanTab(tab.key)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                  planTab === tab.key
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                )}
              >
                {tab.label}
              </button>
            ))}
            <Button
              size="sm"
              variant={planSplit && planTab !== "chat" ? "secondary" : "ghost"}
              className="ml-auto hidden h-7 gap-1.5 px-2 text-[11px] lg:inline-flex"
              data-testid="workspace-plan-split"
              title="Toggle split view (side-by-side on large screens)"
              aria-pressed={planSplit && planTab !== "chat"}
              disabled={planTab === "chat"}
              onClick={() => setPlanSplit((prev) => !prev)}
            >
              <Columns2 className="h-3.5 w-3.5" /> Split
            </Button>
          </div>
        )}

        {planChatOnly ? (
          <div
            data-testid={isPlanMode ? "workspace-plan-chat" : undefined}
            className="flex min-h-0 flex-1 flex-col"
          >
            {chatColumn}
          </div>
        ) : planSplit ? (
          <div
            data-testid="workspace-plan-split-active"
            className="flex min-h-0 flex-1 flex-col lg:flex-row"
          >
            <div
              data-testid="workspace-plan-chat"
              className="flex min-h-0 min-w-0 flex-1 flex-col"
            >
              {chatColumn}
            </div>
            <div
              data-testid={planPanelTestId}
              className="min-h-0 w-full overflow-auto border-t border-border p-4 lg:w-[45%] lg:min-w-[360px] lg:border-l lg:border-t-0"
            >
              {renderPlanPanel()}
            </div>
          </div>
        ) : (
          <div
            data-testid={planPanelTestId}
            className={cn(
              "min-h-0 flex-1",
              planTab === "plan-map" ? "flex flex-col p-3 lg:p-4 overflow-hidden" : "overflow-auto p-4 lg:p-6"
            )}
          >
            {renderPlanPanel()}
          </div>
        )}
      </div>

      <SaveArtifactDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        content={saveContent}
        defaultTitle="Workspace response"
        source="workspace"
        sourceRef={convId || undefined}
        modelId={saveModelId}
        runId={saveRunId}
      />
      <AttachmentViewer
        attachment={viewerAttachment}
        open={!!viewerAttachment}
        onOpenChange={(o) => {
          if (!o) setViewerAttachment(null);
        }}
      />
      <TraceDrawer
        runId={traceRunId}
        open={!!traceRunId}
        onOpenChange={(o) => {
          if (!o) setTraceRunId(null);
        }}
      />
      <ContextDrawer
        runId={contextRunId}
        open={!!contextRunId}
        onOpenChange={(o) => {
          if (!o) setContextRunId(null);
        }}
      />
      <PlanningStatePanel
        open={planningOpen}
        onOpenChange={setPlanningOpen}
        projectId={activeId}
        conversationId={convId}
      />
      <PromptCaptureDialog
        open={!!captureTarget}
        onOpenChange={(o) => { if (!o) setCaptureTarget(null); }}
        projectId={activeId}
        conversationId={convId}
        messageIds={captureTarget?.messageIds || []}
        mode={captureTarget?.mode || "response"}
        conversationTitle={conversation?.title}
        onUseOnce={(text) => {
          setInput(text);
          toast.success("Captured prompt inserted — it will be used once on your next send.");
        }}
        onSaved={() => {
          qc.invalidateQueries({ queryKey: ["prompt-specs", activeId] });
          qc.invalidateQueries({ queryKey: ["prompt-captures", activeId] });
        }}
      />

      {/* Single Conversation Delete Confirmation Dialog */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent data-testid="conversation-delete-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete conversation?</AlertDialogTitle>
            <AlertDialogDescription className="space-y-1">
              <span className="block font-medium text-foreground">
                "{deleteTarget?.title || "Untitled conversation"}"
              </span>
              <span>
                This will permanently delete this conversation and its chat history.
              </span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="conversation-delete-cancel">Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid="conversation-delete-confirm"
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => {
                if (deleteTarget) {
                  delConv.mutate(deleteTarget.id);
                }
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Bulk Delete Confirmation Dialog */}
      <AlertDialog open={bulkDeleteConfirmOpen} onOpenChange={setBulkDeleteConfirmOpen}>
        <AlertDialogContent data-testid="conversation-bulk-delete-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete {selectedConvIds.length} conversation{selectedConvIds.length === 1 ? "" : "s"}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently remove the selected conversation histories.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="conversation-bulk-delete-cancel">Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid="conversation-bulk-delete-confirm"
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => {
                if (selectedConvIds.length > 0) {
                  bulkDelConv.mutate(selectedConvIds);
                }
              }}
            >
              Delete {selectedConvIds.length} conversation{selectedConvIds.length === 1 ? "" : "s"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
