import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, apiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { modelCapabilityStatus, normalizeCapabilitySource } from "@/lib/capabilities";
import type { CapabilityKey } from "@/lib/capabilities";

type TriState = "supported" | "unsupported" | "unknown";

const EDITABLE_KEYS: { key: CapabilityKey; label: string }[] = [
  { key: "tools", label: "Tool calling" },
  { key: "vision", label: "Vision" },
  { key: "streaming", label: "Streaming" },
  { key: "structured_output", label: "Structured output" },
];

const statusToTri = (status: string): TriState =>
  status === "SUPPORTED" ? "supported" : status === "UNSUPPORTED" ? "unsupported" : "unknown";

/**
 * Per-model manual capability override dialog. Each dimension offers
 * Supported / Unsupported / Unknown; saving PATCHes
 * `/models/{id}` with `{capabilities, capability_source: "MANUAL"}`.
 * Historical runs are untouched (append-only); compatibility views refresh
 * immediately after update via query invalidation.
 */
export const CapabilityOverrideDialog: React.FC<{
  model: any | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}> = ({ model, open, onOpenChange }) => {
  const qc = useQueryClient();
  const [values, setValues] = useState<Record<string, TriState> | null>(null);

  React.useEffect(() => {
    if (open && model) {
      const initial: Record<string, TriState> = {};
      for (const { key } of EDITABLE_KEYS) {
        initial[key] = statusToTri(modelCapabilityStatus(model, key));
      }
      setValues(initial);
    }
    if (!open) setValues(null);
  }, [open, model?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const mut = useMutation({
    mutationFn: async () => {
      const capabilities: Record<string, boolean | null> = {};
      for (const { key } of EDITABLE_KEYS) {
        const v = values?.[key] || "unknown";
        if (v === "supported") capabilities[key] = true;
        else if (v === "unsupported") capabilities[key] = false;
        else capabilities[key] = null;
      }
      return (
        await api.patch(`/models/${model.id}`, {
          capabilities,
          capability_source: "MANUAL",
        })
      ).data;
    },
    onSuccess: () => {
      toast.success("Model capabilities overridden (manual)");
      qc.invalidateQueries({ queryKey: ["models"] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const source = normalizeCapabilitySource(model?.capability_source);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="capability-override-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">
            Override capabilities · {model?.display_name || model?.model_identifier || "model"}
          </DialogTitle>
          <DialogDescription>
            Set Supported / Unsupported / Unknown per capability. Saved as a
            manual override (capability_source MANUAL); runs stay unchanged.
            {source !== "UNKNOWN" && (
              <span className="mt-1 block font-mono text-[11px]">
                current source: {source}
              </span>
            )}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3" data-testid="capability-override-form">
          {EDITABLE_KEYS.map(({ key, label }) => (
            <div
              key={key}
              className="flex items-center justify-between gap-3"
              data-testid={`capability-override-${key}`}
            >
              <span className="text-sm font-medium">{label}</span>
              <div
                className="flex items-center gap-3"
                role="radiogroup"
                aria-label={label}
                data-testid={`capability-override-${key}-options`}
              >
                {(["supported", "unsupported", "unknown"] as TriState[]).map(
                  (opt) => (
                    <label
                      key={opt}
                      className="flex cursor-pointer items-center gap-1.5 text-xs"
                    >
                      <input
                        type="radio"
                        name={`capability-override-${model?.id || "new"}-${key}`}
                        value={opt}
                        checked={(values?.[key] || "unknown") === opt}
                        onChange={() =>
                          setValues((prev) => ({ ...(prev || {}), [key]: opt }))
                        }
                        data-testid={`capability-override-${key}-${opt}`}
                        className="h-3.5 w-3.5 accent-primary"
                      />
                      <span className="capitalize">{opt}</span>
                    </label>
                  ),
                )}
              </div>
            </div>
          ))}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            data-testid="capability-override-submit"
            disabled={!model || mut.isPending}
            onClick={() => mut.mutate()}
          >
            {mut.isPending ? "Saving…" : "Save manual override"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default CapabilityOverrideDialog;
