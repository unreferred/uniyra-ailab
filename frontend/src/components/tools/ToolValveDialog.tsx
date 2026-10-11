import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertCircle, CheckCircle2, ChevronRight, Loader2, RefreshCw } from "lucide-react";
import { apiError, getToolValves, testToolValves, updateToolValves } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { StatusDot } from "@/components/tools/ToolShared";
import { ValveForm } from "@/components/tools/ValveForm";

export interface ToolValveDialogProps {
  tool: any | null;
  projectId?: string | null;
  onClose: () => void;
}

type StatusKind = "online" | "available" | "missing" | "unsupported" | "off";

const statusViewOf = (status: any): { kind: StatusKind; label: string } => {
  if (!status || !status.enabled) return { kind: "off", label: "Disabled" };
  if (!status.configured) {
    return { kind: "missing", label: `Not configured — ${status.reason || "configuration required"}` };
  }
  if (status.available) {
    return { kind: "available", label: status.reason ? `Connected — ${status.reason}` : "Connected" };
  }
  return { kind: "off", label: `Unavailable — ${status.reason || "provider connection failed"}` };
};

// Tool configuration lives with the tool. The dialog renders the tool's valve
// schema plus, for provider-backed tools, the selected provider's valves.
export const ToolValveDialog: React.FC<ToolValveDialogProps> = ({ tool, projectId, onClose }) => {
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["tool-valves", tool?.id, projectId ?? null],
    queryFn: () => getToolValves(tool.id, projectId || undefined),
    enabled: !!tool?.id,
  });
  const data = q.data;

  const [enabled, setEnabled] = useState(true);
  const [providerId, setProviderId] = useState<string>("");
  const [providerChanges, setProviderChanges] = useState<Record<string, any>>({});
  const [toolChanges, setToolChanges] = useState<Record<string, any>>({});
  const [testResult, setTestResult] = useState<{ ok?: boolean; status?: string; message?: string } | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [formVersion, setFormVersion] = useState(0);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  React.useEffect(() => {
    setEnabled(true);
    setProviderId("");
    setProviderChanges({});
    setToolChanges({});
    setTestResult(null);
    setSaveError(null);
    setAdvancedOpen(false);
  }, [tool?.id]);

  React.useEffect(() => {
    if (data) {
      setEnabled(!!data.status?.enabled);
      setProviderId(data.provider_id ?? "");
    }
  }, [data]);

  const testMut = useMutation({
    mutationFn: async () => testToolValves(tool.id, projectId || undefined),
    onSuccess: (res) => setTestResult(res),
    onError: (e: any) => setTestResult({ ok: false, status: "ERROR", message: apiError(e) }),
  });

  const saveMut = useMutation({
    mutationFn: async () => {
      const body: Record<string, any> = {};
      if (data && enabled !== data.status?.enabled) body.enabled = enabled;
      if (providers.length > 0) body.provider_id = providerId || data?.provider_id || providers[0]?.id;
      if (Object.keys(toolChanges).length > 0) body.values = toolChanges;
      if (Object.keys(providerChanges).length > 0) body.provider_values = providerChanges;
      return updateToolValves(tool.id, body);
    },
    onSuccess: () => {
      toast.success("Configuration saved");
      setProviderChanges({});
      setFormVersion((v) => v + 1);
      setToolChanges({});
      setSaveError(null);
      qc.invalidateQueries({ queryKey: ["tool-valves"] });
      testMut.mutate();
    },
    onError: (e) => {
      const msg = apiError(e);
      setSaveError(msg);
      toast.error(msg);
    },
  });

  const providers = data?.providers || [];
  const selectedProvider =
    providers.find((p: any) => p.id === providerId) ||
    providers.find((p: any) => p.id === data?.provider_id) ||
    providers[0];
  const toolSchema = data?.schema || {};
  const providerSchema = selectedProvider?.valve_schema || {};

  // Tool-local valves are split into defaults and advanced (ceiling) groups
  // without any schema change: ceiling keys are "advanced", everything else is
  // a plain default. Both groups submit through the same toolChanges handler.
  const toolEntries = Object.entries(toolSchema);
  const defaultEntries = toolEntries.filter(([key]) => !key.endsWith("_ceiling"));
  const advancedEntries = toolEntries.filter(([key]) => key.endsWith("_ceiling"));
  const defaultsSchema = Object.fromEntries(defaultEntries) as Record<string, any>;
  const advancedSchema = Object.fromEntries(advancedEntries) as Record<string, any>;
  const hasAdvanced = advancedEntries.length > 0;
  const isResearch = (tool?.name || data?.name) === "web_research";
  const toolHeading = isResearch ? "Research settings" : "Tool settings";
  const busy = saveMut.isPending || testMut.isPending;
  const view = statusViewOf(data?.status);

  return (
    <Dialog open={!!tool} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-2xl" data-testid="tool-valve-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">
            Configure {tool?.display_name || tool?.name}
          </DialogTitle>
          <DialogDescription>
            Settings are saved server-side. Secrets are encrypted and never shown again.
          </DialogDescription>
        </DialogHeader>

        {q.isLoading ? (
          <LoadingSpinner label="Loading configuration…" />
        ) : q.isError ? (
          <ErrorNote message={apiError(q.error)} />
        ) : !data ? null : (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Switch
                checked={enabled}
                onCheckedChange={setEnabled}
                data-testid="tool-valve-enabled"
              />
              <span className="text-xs text-muted-foreground">Enabled</span>
            </div>

            {providers.length > 0 && (
              <div className="max-w-sm">
                <label className="text-xs font-medium text-muted-foreground">Provider</label>
                <Select
                  value={providerId || ""}
                  onValueChange={(v) => {
                    setProviderId(v);
                    setProviderChanges({});
                  }}
                >
                  <SelectTrigger className="mt-1" data-testid="tool-valve-provider">
                    <SelectValue placeholder="Select provider" />
                  </SelectTrigger>
                  <SelectContent>
                    {providers.map((p: any) => (
                      <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            {providers.length > 0 && selectedProvider && Object.keys(providerSchema).length > 0 && (
              <section>
                <h3 className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Provider settings
                </h3>
                <ValveForm
                  key={`${selectedProvider.id}-${formVersion}`}
                  prefix="tool-valve-provider"
                  schema={providerSchema}
                  values={{ ...(selectedProvider.valve_values || {}), ...providerChanges }}
                  onChange={(k, v) => setProviderChanges((p) => ({ ...p, [k]: v }))}
                  disabled={busy}
                />
              </section>
            )}

            {Object.keys(toolSchema).length > 0 && (
              <section data-testid="tool-valve-tool-settings">
                <h3 className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {toolHeading}
                </h3>
                {defaultEntries.length > 0 && (
                  <ValveForm
                    key={`tool-defaults-${formVersion}`}
                    prefix="tool-valve"
                    schema={defaultsSchema}
                    values={{ ...(data.values || {}), ...toolChanges }}
                    onChange={(k, v) => setToolChanges((p) => ({ ...p, [k]: v }))}
                    disabled={busy}
                  />
                )}
                {hasAdvanced && (
                  <div className="mt-3">
                    <button
                      type="button"
                      data-testid="tool-valve-advanced-toggle"
                      aria-expanded={advancedOpen}
                      onClick={() => setAdvancedOpen((v) => !v)}
                      className="flex items-center gap-1 font-mono text-[11px] font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground"
                    >
                      <ChevronRight
                        className={`h-3 w-3 transition-transform ${advancedOpen ? "rotate-90" : ""}`}
                      />
                      Advanced
                    </button>
                    {advancedOpen && (
                      <div className="mt-2" data-testid="tool-valve-advanced">
                        <ValveForm
                          key={`tool-advanced-${formVersion}`}
                          prefix="tool-valve"
                          schema={advancedSchema}
                          values={{ ...(data.values || {}), ...toolChanges }}
                          onChange={(k, v) => setToolChanges((p) => ({ ...p, [k]: v }))}
                          disabled={busy}
                        />
                      </div>
                    )}
                  </div>
                )}
              </section>
            )}

            <div className="flex items-center gap-1.5 text-[11px]" data-testid="tool-valve-status">
              <StatusDot kind={view.kind} />
              <span>{view.label}</span>
            </div>

            {saveError && <ErrorNote message={saveError} />}

            {testResult && (
              <div
                className={`flex items-start gap-1.5 rounded border p-2 text-[11px] ${
                  testResult.ok
                    ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                    : "border-destructive/20 bg-destructive/10 text-destructive"
                }`}
                data-testid="tool-valve-test-result"
              >
                {testResult.ok ? (
                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                ) : (
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                )}
                <div className="min-w-0 flex-1">
                  <span className="font-semibold">
                    {testResult.status || (testResult.ok ? "CONNECTED" : "FAILED")}:{" "}
                  </span>
                  <span>{testResult.message}</span>
                </div>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            data-testid="tool-valve-test"
            disabled={!data || busy}
            onClick={() => {
              setTestResult(null);
              testMut.mutate();
            }}
          >
            {testMut.isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            )}
            Test Connection
          </Button>
          <Button variant="ghost" data-testid="tool-valve-cancel" onClick={onClose}>
            Cancel
          </Button>
          <Button
            data-testid="tool-valve-save"
            disabled={!data || busy}
            onClick={() => saveMut.mutate()}
          >
            {saveMut.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            Save &amp; Test
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ToolValveDialog;
