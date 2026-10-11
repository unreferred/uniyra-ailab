import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle,
  Check,
  FileText,
  Loader2,
  Pencil,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  apiError,
  generateConsolidatedPlan,
  getPlanningState,
  ingestPlanningConversation,
  reopenPlanningItem,
  resolvePlanningConflict,
  updatePlanningItem,
  verifyPlanningItem,
} from "@/lib/api";
import { ConsolidatedPlan, PlanningItem, PlanningState } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { nText } from "@/components/experiment/format";
import { cn } from "@/lib/utils";

// `sheet` is not covered by the repo's `ui-jsx.d.ts` shim, so @types/react 19
// infers its forwardRef exports with empty props. Cast locally to keep TSX usage
// working without touching shared files.
const PanelSheetContent: React.ComponentType<any> = SheetContent;
const PanelSheetTitle: React.ComponentType<any> = SheetTitle;
const PanelSheetDescription: React.ComponentType<any> = SheetDescription;

export interface PlanningStatePanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  conversationId: string | null;
}

type ItemPatch = {
  topic_key?: string;
  category?: string;
  kind?: string;
  value?: string;
  status?: string;
  confidence?: number;
};

type PlanningGroup = {
  key: string;
  testid: string;
  label: string;
  items: PlanningItem[];
};

function shortId(value?: string | null): string {
  if (!value) return "";
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

function provenanceLine(item: PlanningItem): string | null {
  const parts: string[] = [];
  if (item.source_type) parts.push(`source: ${item.source_type}`);
  if (item.source_message_id) parts.push(`message ${shortId(item.source_message_id)}`);
  return parts.length ? parts.join(" · ") : null;
}

function statusVariant(status?: string | null): "default" | "secondary" | "destructive" | "outline" {
  if (!status) return "outline";
  if (status === "REJECTED" || status === "SUPERSEDED" || status === "CONFLICT" || status === "BLOCKED") {
    return "destructive";
  }
  if (status === "CONFIRMED_BY_USER" || status === "TECHNICALLY_VERIFIED" || status === "RESOLVED") {
    return "default";
  }
  return "secondary";
}

function isOpenQuestion(item: PlanningItem): boolean {
  return item.kind === "open_question" || item.category === "open_question" || item.status === "OPEN";
}

function canConfirm(item: PlanningItem): boolean {
  if (!item.id || item.status === "REJECTED" || item.superseded_by_id) return false;
  return (
    item.status === "ASSUMED_BY_MODEL" ||
    item.status === "ASSUMPTION" ||
    item.status === "NEEDS_VERIFICATION" ||
    item.verification_status === "NEEDS_VERIFICATION" ||
    isOpenQuestion(item)
  );
}

function canReject(item: PlanningItem): boolean {
  if (!item.id || item.superseded_by_id) return false;
  return item.status !== "REJECTED";
}

function canReopen(item: PlanningItem): boolean {
  if (!item.id) return false;
  const decisionish =
    item.category === "decision" ||
    item.category === "constraint" ||
    item.kind === "decision" ||
    item.kind === "constraint";
  return decisionish && (item.status === "RESOLVED" || item.status === "SUPERSEDED" || !!item.superseded_by_id);
}

function canRequestVerification(item: PlanningItem): boolean {
  if (!item.id || item.status === "REJECTED" || item.superseded_by_id) return false;
  return !item.verification_status || item.verification_status === "UNVERIFIED";
}

function canVerify(item: PlanningItem): boolean {
  if (!item.id) return false;
  return (
    item.verification_status === "NEEDS_VERIFICATION" ||
    item.kind === "verification" ||
    item.kind === "assumption" ||
    item.category === "assumption"
  );
}

export interface PlanningDecisionsViewProps {
  projectId: string;
  conversationId: string | null;
  className?: string;
}

export function PlanningDecisionsView({
  projectId,
  conversationId,
  className,
}: PlanningDecisionsViewProps): React.JSX.Element {
  const qc = useQueryClient();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftValue, setDraftValue] = useState("");
  const [editingConflictId, setEditingConflictId] = useState<string | null>(null);
  const [conflictValue, setConflictValue] = useState("");
  const [plan, setPlan] = useState<ConsolidatedPlan | null>(null);
  const [planOpen, setPlanOpen] = useState(false);

  const stateQuery = useQuery<PlanningState>({
    queryKey: ["planning-state", projectId, conversationId],
    queryFn: () => getPlanningState(projectId, conversationId as string),
    enabled: !!conversationId,
    retry: false,
  });

  const state = stateQuery.data;

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["planning-state", projectId, conversationId] });

  const rescan = useMutation({
    mutationFn: () => ingestPlanningConversation(projectId, conversationId as string),
    onSuccess: () => {
      invalidate();
      toast.success("Conversation re-scanned");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const updateItem = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: ItemPatch }) => updatePlanningItem(id, patch),
    onSuccess: () => {
      invalidate();
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const reopenItem = useMutation({
    mutationFn: (id: string) => reopenPlanningItem(id),
    onSuccess: () => {
      invalidate();
      toast.success("Item reopened");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const verifyItem = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => verifyPlanningItem(id, status),
    onSuccess: () => {
      invalidate();
      toast.success("Verification updated");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const resolveConflict = useMutation({
    mutationFn: ({ id, resolution, value }: { id: string; resolution: string; value?: string }) =>
      resolvePlanningConflict(id, resolution, value || ""),
    onSuccess: () => {
      invalidate();
      setEditingConflictId(null);
      toast.success("Conflict resolved");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const generatePlan = useMutation({
    mutationFn: () =>
      generateConsolidatedPlan(conversationId as string, { project_id: projectId }),
    onSuccess: (result) => {
      setPlan(result);
      setPlanOpen(true);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const groups = useMemo<PlanningGroup[]>(
    () => [
      {
        key: "confirmed",
        testid: "planning-group-confirmed",
        label: "Confirmed",
        items: state?.confirmed_decisions || [],
      },
      {
        key: "constraints",
        testid: "planning-group-constraints",
        label: "Constraints",
        items: state?.constraints || [],
      },
      {
        key: "rejected",
        testid: "planning-group-rejected",
        label: "Rejected",
        items: state?.rejected_options || [],
      },
      {
        key: "assumptions",
        testid: "planning-group-assumptions",
        label: "Assumptions",
        items: state?.assumptions || [],
      },
      {
        key: "open",
        testid: "planning-group-open",
        label: "Open Questions",
        items: state?.open_questions || [],
      },
      {
        key: "verification",
        testid: "planning-group-verification",
        label: "Needs Verification",
        items: state?.verification_items || [],
      },
    ],
    [state]
  );

  const conflicts = state?.conflicts || [];
  const hasAny = groups.some((g) => g.items.length > 0) || conflicts.length > 0;
  const itemActionPending = updateItem.isPending || reopenItem.isPending || verifyItem.isPending;

  const startEdit = (item: PlanningItem) => {
    setEditingId(item.id || null);
    setDraftValue(item.value || "");
  };

  const saveEdit = async () => {
    if (!editingId) return;
    try {
      await updateItem.mutateAsync({ id: editingId, patch: { value: draftValue } });
      toast.success("Item updated");
      setEditingId(null);
    } catch {
      // handled by mutation onError
    }
  };

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col", className)}>
      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-3 scrollbar-thin">
          <div className="flex items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1.5" data-testid="planning-counts">
              {state?.counts && Object.keys(state.counts).length > 0 ? (
                Object.entries(state.counts).map(([key, value]) => (
                  <Badge key={key} variant="outline" className="font-mono text-[10px]">
                    {key} {value}
                  </Badge>
                ))
              ) : (
                <span className="text-[10px] text-muted-foreground">No counts available.</span>
              )}
            </div>
            <Button
              variant="outline"
              size="sm"
              data-testid="planning-rescan"
              className="shrink-0"
              disabled={!conversationId || rescan.isPending}
              onClick={() => rescan.mutate()}
              title="Re-scan conversation"
            >
              {rescan.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              Re-scan conversation
            </Button>
          </div>

          {stateQuery.isLoading && <LoadingSpinner label="Loading planning state…" />}
          {stateQuery.isError && <ErrorNote message={apiError(stateQuery.error)} />}

          {!stateQuery.isLoading && state && (
            <>
              {!hasAny && (
                <p data-testid="planning-empty" className="py-8 text-center text-xs text-muted-foreground">
                  No planning decisions recorded yet.
                </p>
              )}

              {groups.map(
                (group) =>
                  group.items.length > 0 && (
                    <div key={group.key} data-testid={group.testid}>
                      <h4 className="mb-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                        {group.label} · {group.items.length}
                      </h4>
                      <div className="space-y-1.5">
                        {group.items.map((item, idx) => {
                          const editing = !!item.id && editingId === item.id;
                          const provenance = provenanceLine(item);
                          return (
                            <div
                              key={item.id || `${group.key}-${idx}`}
                              data-testid={item.id ? `planning-item-${item.id}` : undefined}
                              className="rounded-md border border-border bg-card p-2 text-[11px]"
                            >
                              <div className="flex items-start justify-between gap-2">
                                <div className="min-w-0 flex-1">
                                  {editing ? (
                                    <div className="flex items-center gap-1">
                                      <Input
                                        value={draftValue}
                                        onChange={(e) => setDraftValue(e.target.value)}
                                        className="h-7 text-xs"
                                        data-testid={`planning-item-edit-${item.id}`}
                                      />
                                      <Button
                                        size="icon"
                                        variant="ghost"
                                        className="h-6 w-6"
                                        title="Save"
                                        disabled={updateItem.isPending}
                                        onClick={saveEdit}
                                      >
                                        <Check className="h-3.5 w-3.5" />
                                      </Button>
                                      <Button
                                        size="icon"
                                        variant="ghost"
                                        className="h-6 w-6"
                                        title="Cancel"
                                        onClick={() => setEditingId(null)}
                                      >
                                        <X className="h-3.5 w-3.5" />
                                      </Button>
                                    </div>
                                  ) : (
                                    <p className="break-words text-xs">{nText(item.value)}</p>
                                  )}

                                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                                    {item.status && (
                                      <Badge
                                        variant={statusVariant(item.status)}
                                        className="px-1 py-0 text-[9px]"
                                      >
                                        {item.status}
                                      </Badge>
                                    )}
                                    {item.verification_status && (
                                      <Badge variant="outline" className="px-1 py-0 text-[9px]">
                                        {item.verification_status}
                                      </Badge>
                                    )}
                                    {item.topic_key && (
                                      <span className="font-mono text-[9px] text-muted-foreground">
                                        {item.topic_key}
                                      </span>
                                    )}
                                    {item.category && (
                                      <span className="text-[9px] text-muted-foreground">
                                        {item.category}
                                      </span>
                                    )}
                                    {typeof item.confidence === "number" && (
                                      <span className="text-[9px] text-muted-foreground">
                                        conf {nText(item.confidence)}
                                      </span>
                                    )}
                                  </div>

                                  {provenance && (
                                    <p
                                      className="mt-0.5 text-[9px] italic text-muted-foreground"
                                      data-testid={item.id ? `planning-provenance-${item.id}` : undefined}
                                    >
                                      {provenance}
                                    </p>
                                  )}
                                </div>

                                <div className="flex shrink-0 items-center gap-0.5">
                                  {canConfirm(item) && (
                                    <Button
                                      size="icon"
                                      variant="ghost"
                                      className="h-6 w-6"
                                      title="Confirm"
                                      data-testid={item.id ? `planning-confirm-${item.id}` : undefined}
                                      disabled={itemActionPending}
                                      onClick={() =>
                                        updateItem.mutate({
                                          id: item.id as string,
                                          patch: { status: "CONFIRMED_BY_USER" },
                                        })
                                      }
                                    >
                                      <Check className="h-3.5 w-3.5" />
                                    </Button>
                                  )}
                                  {canReject(item) && (
                                    <Button
                                      size="icon"
                                      variant="ghost"
                                      className="h-6 w-6"
                                      title="Reject"
                                      data-testid={item.id ? `planning-reject-${item.id}` : undefined}
                                      disabled={itemActionPending}
                                      onClick={() =>
                                        updateItem.mutate({
                                          id: item.id as string,
                                          patch: { status: "REJECTED", kind: "rejected_option" },
                                        })
                                      }
                                    >
                                      <X className="h-3.5 w-3.5" />
                                    </Button>
                                  )}
                                  {canReopen(item) && (
                                    <Button
                                      size="icon"
                                      variant="ghost"
                                      className="h-6 w-6"
                                      title="Reopen"
                                      data-testid={item.id ? `planning-reopen-${item.id}` : undefined}
                                      disabled={itemActionPending}
                                      onClick={() => reopenItem.mutate(item.id as string)}
                                    >
                                      <RotateCcw className="h-3.5 w-3.5" />
                                    </Button>
                                  )}
                                  {canRequestVerification(item) && (
                                    <Button
                                      size="icon"
                                      variant="ghost"
                                      className="h-6 w-6"
                                      title="Mark needs verification"
                                      data-testid={item.id ? `planning-needs-verification-${item.id}` : undefined}
                                      disabled={itemActionPending}
                                      onClick={() =>
                                        verifyItem.mutate({
                                          id: item.id as string,
                                          status: "NEEDS_VERIFICATION",
                                        })
                                      }
                                    >
                                      <AlertTriangle className="h-3.5 w-3.5" />
                                    </Button>
                                  )}
                                  {canVerify(item) && (
                                    <Button
                                      size="icon"
                                      variant="ghost"
                                      className="h-6 w-6"
                                      title="Mark verified"
                                      data-testid={item.id ? `planning-verified-${item.id}` : undefined}
                                      disabled={itemActionPending}
                                      onClick={() =>
                                        verifyItem.mutate({
                                          id: item.id as string,
                                          status: "TECHNICALLY_VERIFIED",
                                        })
                                      }
                                    >
                                      <ShieldCheck className="h-3.5 w-3.5" />
                                    </Button>
                                  )}
                                  {item.id && !editing && (
                                    <Button
                                      size="icon"
                                      variant="ghost"
                                      className="h-6 w-6"
                                      title="Edit"
                                      data-testid={`planning-edit-${item.id}`}
                                      disabled={itemActionPending}
                                      onClick={() => startEdit(item)}
                                    >
                                      <Pencil className="h-3.5 w-3.5" />
                                    </Button>
                                  )}
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )
              )}

              {conflicts.length > 0 && (
                <div data-testid="planning-group-conflicts">
                  <h4 className="mb-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                    Conflicts · {conflicts.length}
                  </h4>
                  <div className="space-y-1.5">
                    {conflicts.map((conflict, idx) => {
                      const editing = !!conflict.id && editingConflictId === conflict.id;
                      return (
                        <div
                          key={conflict.id || `conflict-${idx}`}
                          data-testid={conflict.id ? `planning-conflict-${conflict.id}` : undefined}
                          className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-[11px]"
                        >
                          <div className="flex items-start gap-1.5">
                            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
                            <div className="min-w-0 flex-1 space-y-0.5">
                              <p className="break-words">
                                <span className="text-muted-foreground line-through">
                                  {nText(conflict.previous_value)}
                                </span>{" "}
                                → <span className="font-medium">{nText(conflict.new_value)}</span>
                              </p>
                              {conflict.topic_key && (
                                <p className="font-mono text-[9px] text-muted-foreground">
                                  {conflict.topic_key}
                                </p>
                              )}
                            </div>
                          </div>

                          {editing ? (
                            <div className="mt-1.5 flex items-center gap-1">
                              <Input
                                value={conflictValue}
                                onChange={(e) => setConflictValue(e.target.value)}
                                className="h-7 text-xs"
                                data-testid={conflict.id ? `planning-conflict-edit-${conflict.id}` : undefined}
                              />
                              <Button
                                size="sm"
                                variant="secondary"
                                disabled={resolveConflict.isPending}
                                onClick={() =>
                                  conflict.id &&
                                  resolveConflict.mutate({
                                    id: conflict.id,
                                    resolution: "edit_manual",
                                    value: conflictValue,
                                  })
                                }
                              >
                                Save
                              </Button>
                              <Button size="sm" variant="ghost" onClick={() => setEditingConflictId(null)}>
                                Cancel
                              </Button>
                            </div>
                          ) : (
                            <div className="mt-1.5 flex flex-wrap gap-1">
                              <Button
                                size="sm"
                                variant="secondary"
                                disabled={resolveConflict.isPending}
                                onClick={() =>
                                  conflict.id &&
                                  resolveConflict.mutate({ id: conflict.id, resolution: "accept_new" })
                                }
                              >
                                Accept new
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={resolveConflict.isPending}
                                onClick={() =>
                                  conflict.id &&
                                  resolveConflict.mutate({ id: conflict.id, resolution: "keep_previous" })
                                }
                              >
                                Keep previous
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={resolveConflict.isPending}
                                onClick={() => {
                                  setEditingConflictId(conflict.id || null);
                                  setConflictValue(conflict.new_value || "");
                                }}
                              >
                                Edit manually
                              </Button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        <div className="border-t border-border px-4 py-3">
          <Button
            className="w-full"
            data-testid="planning-consolidated-plan"
            disabled={!conversationId || generatePlan.isPending}
            onClick={() => generatePlan.mutate()}
          >
            {generatePlan.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <FileText className="h-4 w-4" />
            )}
            Generate consolidated plan
          </Button>
        </div>

        <Dialog open={planOpen} onOpenChange={setPlanOpen}>
          <DialogContent
            data-testid="planning-consolidated-dialog"
            className="max-h-[85vh] max-w-2xl overflow-y-auto scrollbar-thin"
          >
            <DialogHeader>
              <DialogTitle className="font-display">Consolidated plan</DialogTitle>
              <DialogDescription>
                Linked policies: {plan?.policies?.length ?? 0} · invariants:{" "}
                {plan?.invariants?.length ?? 0}
              </DialogDescription>
            </DialogHeader>
            <pre
              data-testid="planning-consolidated-markdown"
              className="mt-2 max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-md border border-border bg-muted/40 p-3 font-mono text-xs scrollbar-thin"
            >
              {nText(plan?.markdown)}
            </pre>
          </DialogContent>
      </Dialog>
    </div>
  );
}

export function PlanningStatePanel({
  open,
  onOpenChange,
  projectId,
  conversationId,
}: PlanningStatePanelProps): React.JSX.Element {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <PanelSheetContent
        side="right"
        data-testid="planning-state-panel"
        className="flex w-full flex-col gap-0 p-0 sm:max-w-md"
      >
        <SheetHeader className="border-b border-border px-4 py-3 text-left">
          <PanelSheetTitle className="font-display text-base">Planning memory</PanelSheetTitle>
          <PanelSheetDescription className="text-xs">
            Decisions, constraints and open questions captured from this conversation.
          </PanelSheetDescription>
        </SheetHeader>
        <PlanningDecisionsView
          projectId={projectId}
          conversationId={conversationId}
          className="flex-1 min-h-0"
        />
      </PanelSheetContent>
    </Sheet>
  );
}

export default PlanningStatePanel;
