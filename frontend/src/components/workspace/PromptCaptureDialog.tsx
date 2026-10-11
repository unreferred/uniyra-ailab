import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { analyzePromptCapture, apiError, savePromptCapture } from "@/lib/api";
import {
  PromptCapturePreview,
  PromptCaptureSaveRequest,
  PromptCaptureSaveResult,
} from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { nText } from "@/components/experiment/format";

export interface PromptCaptureDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  conversationId: string | null;
  messageIds?: string[];
  mode?: "response" | "conversation";
  conversationTitle?: string;
  onUseOnce: (text: string) => void;
  onSaved?: (result: PromptCaptureSaveResult) => void;
}

type AssetRow = { id: string; name: string; meta: string };

function buildRows(
  candidates: any[] | null | undefined,
  linked: any[] | null | undefined,
  labelFor: (row: any) => string,
  metaFor: (row: any) => string
): AssetRow[] {
  const map = new Map<string, AssetRow>();
  for (const item of linked || []) {
    if (!item || !item.id) continue;
    map.set(item.id, { id: item.id, name: labelFor(item) || item.id, meta: metaFor(item) || "" });
  }
  for (const item of candidates || []) {
    if (!item || !item.id) continue;
    const existing = map.get(item.id);
    map.set(item.id, {
      id: item.id,
      name: labelFor(item) || existing?.name || item.id,
      meta: metaFor(item) || existing?.meta || "",
    });
  }
  return Array.from(map.values());
}

function hasItems(value?: string[] | null): boolean {
  return Array.isArray(value) && value.some((entry) => (entry || "").trim() !== "");
}

function ListSection({
  label,
  items,
  testid,
}: {
  label: string;
  items?: string[] | null;
  testid?: string;
}) {
  if (!hasItems(items)) return null;
  return (
    <div data-testid={testid}>
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <ul className="mt-0.5 list-disc space-y-0.5 pl-4 text-xs">
        {(items || []).filter((entry) => (entry || "").trim() !== "").map((entry, index) => (
          <li key={`${label}-${index}`}>{entry}</li>
        ))}
      </ul>
    </div>
  );
}

function AssetGroup({
  label,
  testid,
  idPrefix,
  rows,
  selected,
  onToggle,
  emptyLabel,
}: {
  label: string;
  testid: string;
  idPrefix: string;
  rows: AssetRow[];
  selected: string[];
  onToggle: (id: string, checked: boolean) => void;
  emptyLabel: string;
}) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <div className="mt-1 space-y-1.5 rounded-md border border-border p-2" data-testid={testid}>
        {rows.length === 0 ? (
          <p className="text-xs text-muted-foreground">{emptyLabel}</p>
        ) : (
          rows.map((row) => (
            <label key={row.id} className="flex items-center gap-2 text-xs">
              <Checkbox
                checked={selected.includes(row.id)}
                onCheckedChange={(checked) => onToggle(row.id, checked === true)}
                data-testid={`${idPrefix}-${row.id}`}
              />
              <span>
                {nText(row.name)}
                {row.meta ? <span className="text-muted-foreground"> ({nText(row.meta)})</span> : null}
              </span>
            </label>
          ))
        )}
      </div>
    </div>
  );
}

export function PromptCaptureDialog(props: PromptCaptureDialogProps): React.JSX.Element {
  const {
    open,
    onOpenChange,
    projectId,
    conversationId,
    messageIds,
    mode,
    conversationTitle,
    onUseOnce,
    onSaved,
  } = props;

  const qc = useQueryClient();
  const navigate = useNavigate();

  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [createBlocks, setCreateBlocks] = useState(false);
  const [blockIds, setBlockIds] = useState<string[]>([]);
  const [policyIds, setPolicyIds] = useState<string[]>([]);
  const [invariantIds, setInvariantIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const messageKey = (messageIds || []).join(",");

  const previewQuery = useQuery<PromptCapturePreview>({
    queryKey: ["prompt-capture-preview", projectId, conversationId, messageKey, mode],
    queryFn: async () =>
      analyzePromptCapture({
        project_id: projectId,
        conversation_id: conversationId as string,
        message_ids: messageIds || [],
        ...(mode ? { mode } : {}),
      }),
    enabled: open && !!conversationId && !!projectId,
    retry: false,
  });

  const preview = previewQuery.data;

  useEffect(() => {
    if (open) setError(null);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setName(preview?.title || conversationTitle || "");
    setPurpose(preview?.purpose || "");
  }, [open, preview?.title, preview?.purpose, conversationTitle]);

  useEffect(() => {
    if (!open || !preview) return;
    const linkedBlockIds = (preview.blocks?.linked || [])
      .map((entry) => entry?.id)
      .filter(Boolean) as string[];
    const linkedPolicyIds = (preview.policies?.linked || [])
      .map((entry) => entry?.id)
      .filter(Boolean) as string[];
    const linkedInvariantIds = (preview.invariants?.linked || [])
      .map((entry) => entry?.id)
      .filter(Boolean) as string[];
    setBlockIds(linkedBlockIds);
    setPolicyIds(linkedPolicyIds);
    setInvariantIds(linkedInvariantIds);
  }, [open, preview]);

  const blockRows = useMemo(
    () =>
      buildRows(
        preview?.blocks?.candidates ?? preview?.candidates?.blocks,
        preview?.blocks?.linked,
        (row) => row.name || "",
        (row) => row.category || ""
      ),
    [preview]
  );
  const policyRows = useMemo(
    () =>
      buildRows(
        preview?.policies?.candidates ?? preview?.candidates?.policies,
        preview?.policies?.linked,
        (row) => row.name || "",
        (row) => row.domain || ""
      ),
    [preview]
  );
  const invariantRows = useMemo(
    () =>
      buildRows(
        preview?.invariants?.candidates ?? preview?.candidates?.invariants,
        preview?.invariants?.linked,
        (row) => row.code || row.title || "",
        (row) => [row.domain, row.severity].filter(Boolean).join(" · ")
      ),
    [preview]
  );

  const toggleId =
    (setter: React.Dispatch<React.SetStateAction<string[]>>) => (id: string, checked: boolean) => {
      setter((prev) =>
        checked ? (prev.includes(id) ? prev : [...prev, id]) : prev.filter((value) => value !== id)
      );
    };

  const saveMutation = useMutation({
    mutationFn: async (body: PromptCaptureSaveRequest) => savePromptCapture(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["prompt-specs", projectId] });
      qc.invalidateQueries({ queryKey: ["prompt-captures", projectId] });
    },
    onError: (e) => {
      const message = apiError(e);
      setError(message);
      toast.error(message);
    },
  });

  const pending = saveMutation.isPending;
  const structure = preview?.structure;
  const compiled = preview?.preview;
  const suggestedBlocks = preview?.blocks?.suggested_created || [];
  const renderedText = compiled?.rendered_text || preview?.content || "";

  const captureFields = preview?.capture_fields ?? null;
  const structuredSpec = (preview?.structured_spec ?? null) as Record<string, any> | null;
  const planningState = preview?.decision_state ?? null;

  const decisionList = (key: string): string[] => {
    const clean = (values?: (string | null | undefined)[] | null): string[] =>
      (values || [])
        .map((entry) => (typeof entry === "string" ? entry.trim() : ""))
        .filter((entry) => entry !== "");
    const direct = clean((captureFields as any)?.[key]);
    if (direct.length) return direct;
    const spec = structuredSpec?.[key];
    if (Array.isArray(spec)) {
      const cleanedSpec = clean(spec as (string | null | undefined)[]);
      if (cleanedSpec.length) return cleanedSpec;
    }
    const items = (planningState as any)?.[key];
    if (Array.isArray(items)) {
      return clean(items.map((item: any) => (typeof item === "string" ? item : item?.value)));
    }
    return [];
  };

  const confirmedDecisions = decisionList("confirmed_decisions");
  const rejectedOptions = decisionList("rejected_options");
  const assumptionItems = decisionList("assumptions");
  const openQuestions = decisionList("open_questions");
  const verificationItems = decisionList("verification_items");
  const hasDecisionState = [
    confirmedDecisions,
    rejectedOptions,
    assumptionItems,
    openQuestions,
    verificationItems,
  ].some((items) => hasItems(items));

  const buildBody = (overrides: Partial<PromptCaptureSaveRequest>): PromptCaptureSaveRequest => ({
    project_id: projectId,
    conversation_id: conversationId as string,
    ...(messageIds && messageIds.length ? { message_ids: messageIds } : {}),
    ...(mode ? { mode } : {}),
    name,
    title: name,
    purpose,
    include_block_ids: blockIds,
    include_policy_ids: policyIds,
    include_invariant_ids: invariantIds,
    variables: {},
    ...overrides,
  });

  const handleUseOnce = () => {
    onUseOnce(renderedText);
    onOpenChange(false);
  };

  const handleSavePrompt = async () => {
    setError(null);
    try {
      const result = await saveMutation.mutateAsync(
        buildBody({ kind: "prompt", freeze_version: false })
      );
      toast.success("Prompt draft created");
      onSaved?.(result);
      onOpenChange(false);
    } catch {
      // handled by the mutation onError
    }
  };

  const handleSaveTemplate = async () => {
    setError(null);
    try {
      const result = await saveMutation.mutateAsync(
        buildBody({
          kind: "planning_template",
          create_missing_blocks: createBlocks,
          freeze_version: false,
        })
      );
      toast.success("Planning template saved");
      onSaved?.(result);
      onOpenChange(false);
    } catch {
      // handled by the mutation onError
    }
  };

  const handleCreateAgent = async () => {
    setError(null);
    try {
      const result = await saveMutation.mutateAsync(
        buildBody({ kind: "prompt", freeze_version: true })
      );
      toast.success("Prompt frozen — opening Agent Lab");
      onSaved?.(result);
      onOpenChange(false);
      const promptId = result?.prompt?.id;
      const versionId = result?.version?.id;
      if (promptId && versionId) {
        navigate(
          `/agents?capturePromptId=${promptId}&captureVersionId=${versionId}&captureName=${encodeURIComponent(name)}&capturePurpose=${encodeURIComponent(purpose)}`
        );
      }
    } catch {
      // handled by the mutation onError
    }
  };

  const shownError = error || (previewQuery.error ? apiError(previewQuery.error) : null);
  const canAct = !!conversationId && !!projectId && !!preview && !pending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="prompt-capture-dialog"
        className="max-h-[85vh] max-w-3xl overflow-y-auto"
      >
        <DialogHeader>
          <DialogTitle className="font-display">Capture prompt</DialogTitle>
          <DialogDescription>
            Extract a reusable draft prompt from this conversation. A draft is created first — it is
            never auto-frozen, and you must confirm every linked block, policy and invariant before
            saving.
          </DialogDescription>
        </DialogHeader>

        {shownError ? (
          <div data-testid="prompt-capture-error">
            <ErrorNote message={shownError} />
          </div>
        ) : null}

        {previewQuery.isLoading ? (
          <div data-testid="prompt-capture-loading">
            <LoadingSpinner label="Analyzing conversation…" />
          </div>
        ) : (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Name</label>
                <Input
                  data-testid="prompt-capture-name"
                  className="mt-1"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Prompt name"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Purpose</label>
                <Input
                  data-testid="prompt-capture-purpose"
                  className="mt-1"
                  value={purpose}
                  onChange={(e) => setPurpose(e.target.value)}
                  placeholder="What this prompt is for"
                />
              </div>
            </div>

            <div
              data-testid="prompt-capture-structure"
              className="space-y-2 rounded-md border border-border p-3"
            >
              <p className="text-xs font-medium text-muted-foreground">Extracted structure</p>
              {structure?.role ? (
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Role
                  </p>
                  <p className="mt-0.5 text-xs">{nText(structure.role)}</p>
                </div>
              ) : null}
              {structure?.objective ? (
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Objective
                  </p>
                  <p className="mt-0.5 text-xs">{nText(structure.objective)}</p>
                </div>
              ) : null}
              <ListSection label="Scope" items={structure?.scope} />
              <ListSection label="Constraints" items={structure?.constraints} />
              <ListSection label="Architecture rules" items={structure?.architecture_rules} />
              <ListSection label="Design rules" items={structure?.design_rules} />
              <ListSection label="Security rules" items={structure?.security_rules} />
              <ListSection label="Phases" items={structure?.phases} />
              <ListSection label="Validation" items={structure?.validation} />
              <ListSection label="Expected output" items={structure?.expected_output} />
              {!structure?.role &&
              !structure?.objective &&
              !hasItems(structure?.scope) &&
              !hasItems(structure?.constraints) &&
              !hasItems(structure?.architecture_rules) &&
              !hasItems(structure?.design_rules) &&
              !hasItems(structure?.security_rules) &&
              !hasItems(structure?.phases) &&
              !hasItems(structure?.validation) &&
              !hasItems(structure?.expected_output) ? (
                <p className="text-xs text-muted-foreground">n/a — no structure extracted.</p>
              ) : null}
            </div>

            {hasDecisionState ? (
              <div
                data-testid="prompt-capture-decision-state"
                className="space-y-2 rounded-md border border-border p-3"
              >
                <p className="text-xs font-medium text-muted-foreground">Decision state</p>
                <ListSection
                  label="Confirmed decisions"
                  items={confirmedDecisions}
                  testid="prompt-capture-confirmed-decisions"
                />
                <ListSection
                  label="Rejected options (explicit negative constraints)"
                  items={rejectedOptions}
                />
                <ListSection label="Assumptions (not facts)" items={assumptionItems} />
                <ListSection label="Open questions" items={openQuestions} />
                <ListSection label="Needs verification" items={verificationItems} />
              </div>
            ) : null}

            <div className="space-y-3">
              <p className="text-xs font-medium text-muted-foreground">
                Linked assets (confirm before saving)
              </p>
              <AssetGroup
                label="Blocks"
                testid="prompt-capture-linked-blocks"
                idPrefix="capture-block"
                rows={blockRows}
                selected={blockIds}
                onToggle={toggleId(setBlockIds)}
                emptyLabel="n/a — no blocks matched."
              />
              <AssetGroup
                label="Policies"
                testid="prompt-capture-linked-policies"
                idPrefix="capture-policy"
                rows={policyRows}
                selected={policyIds}
                onToggle={toggleId(setPolicyIds)}
                emptyLabel="n/a — no policies matched."
              />
              <AssetGroup
                label="Invariants"
                testid="prompt-capture-linked-invariants"
                idPrefix="capture-invariant"
                rows={invariantRows}
                selected={invariantIds}
                onToggle={toggleId(setInvariantIds)}
                emptyLabel="n/a — no invariants matched."
              />
              {suggestedBlocks.length > 0 ? (
                <div
                  data-testid="prompt-capture-suggested-blocks"
                  className="rounded-md border border-dashed border-border p-2"
                >
                  <p className="text-xs font-medium text-muted-foreground">
                    Suggested blocks — would create block (requires confirmation)
                  </p>
                  <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs">
                    {suggestedBlocks.map((block, index) => (
                      <li key={`suggested-${index}`}>
                        {nText(block?.name)}
                        {block?.category ? (
                          <span className="text-muted-foreground"> ({nText(block.category)})</span>
                        ) : null}
                        {block?.reason ? (
                          <span className="text-muted-foreground"> — {nText(block.reason)}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>

            {preview?.redaction?.redacted ? (
              <div
                data-testid="prompt-capture-redaction"
                className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300"
              >
                Sensitive values were redacted before previewing. ({nText(preview.redaction.matches)}{" "}
                match{(preview.redaction.matches ?? 0) === 1 ? "" : "es"})
              </div>
            ) : null}

            <div>
              <p className="text-xs font-medium text-muted-foreground">Compiled preview</p>
              <pre
                data-testid="prompt-capture-preview"
                className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-muted/40 p-3 font-mono text-xs"
              >
                {renderedText || "n/a"}
              </pre>
              <p className="mt-1 text-[11px] text-muted-foreground">
                Checksum: {nText(compiled?.checksum)} · Token estimate:{" "}
                {nText(compiled?.token_estimate)}
              </p>
            </div>

            <label className="flex items-center gap-2 text-xs">
              <Checkbox
                data-testid="prompt-capture-create-blocks"
                checked={createBlocks}
                onCheckedChange={(checked) => setCreateBlocks(checked === true)}
              />
              <span>Create blocks for unmapped constraints</span>
            </label>
          </div>
        )}

        <DialogFooter className="flex-wrap gap-2 sm:justify-between">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              data-testid="prompt-capture-use-once"
              disabled={!canAct}
              onClick={handleUseOnce}
            >
              Use once
            </Button>
            <Button
              variant="secondary"
              data-testid="prompt-capture-save-prompt"
              disabled={!canAct}
              onClick={handleSavePrompt}
            >
              Save prompt draft
            </Button>
            <Button
              variant="secondary"
              data-testid="prompt-capture-save-template"
              disabled={!canAct}
              onClick={handleSaveTemplate}
            >
              Save planning template
            </Button>
            <Button
              data-testid="prompt-capture-create-agent"
              disabled={!canAct}
              onClick={handleCreateAgent}
            >
              Create agent (freezes prompt)
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default PromptCaptureDialog;
