import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiError, importOpenAPI, generateFromOpenAPI } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ErrorNote } from "@/components/common/Common";

export interface OpenApiImportDialogProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  projectId?: string | null;
  availableProjects: { id: string; name: string }[];
  credentials: any[];
  defaultRisk?: string;
  onImported?: (items: any[]) => void;
}

const RISK_BY_METHOD: Record<string, string> = {
  GET: "low",
  POST: "high",
  PUT: "high",
  PATCH: "high",
  DELETE: "critical",
};

const DEFAULT_CHECKED = new Set(["GET"]);

// Flow: parse -> metadata -> operations checklist -> preview -> save.
// Only checked operations are generated. Risk/permission/credentials are
// set per import before save; never auto-enables every endpoint.
export const OpenApiImportDialog: React.FC<OpenApiImportDialogProps> = ({
  open, onOpenChange, projectId, availableProjects, credentials, defaultRisk, onImported,
}) => {
  const qc = useQueryClient();
  const [project, setProject] = useState(projectId || "");
  const [spec, setSpec] = useState("");
  const [specUrl, setSpecUrl] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [risk, setRisk] = useState(defaultRisk || "");
  const [permissions, setPermissions] = useState("");
  const [credentialId, setCredentialId] = useState("");
  const [genErrors, setGenErrors] = useState<any[]>([]);
  const [stepError, setStepError] = useState<string | null>(null);

  React.useEffect(() => {
    if (open) {
      setProject(projectId || "");
      setStepError(null);
      setGenErrors([]);
    }
  }, [open, projectId]);

  const targetProject = projectId || project;

  const previewMut = useMutation({
    mutationFn: async () =>
      importOpenAPI({
        project_id: targetProject,
        ...(spec.trim() ? { spec } : {}),
        ...(specUrl.trim() ? { spec_url: specUrl.trim() } : {}),
        ...(baseUrl.trim() ? { base_url_override: baseUrl.trim() } : {}),
      }),
    onSuccess: (pv: any) => {
      setPreview(pv);
      setGenErrors([]);
      setStepError(null);
      // Safe default: only GET operations pre-checked. Destructive methods
      // stay unchecked until the user opts in.
      const ops = pv?.operations || [];
      setSelected(ops.filter((o: any) => DEFAULT_CHECKED.has(String(o.method).toUpperCase())).map((o: any) => o.operation_id));
      toast.success(`${pv?.operation_count ?? ops.length} operations found`);
    },
    onError: (e) => {
      const msg = apiError(e);
      setStepError(msg);
      toast.error(msg);
    },
  });

  const genMut = useMutation({
    mutationFn: async () =>
      generateFromOpenAPI({
        project_id: targetProject,
        spec: preview?.raw_spec || spec,
        ...(baseUrl.trim() ? { base_url_override: baseUrl.trim() } : {}),
        operations: (preview?.operations || [])
          .filter((o: any) => selected.includes(o.operation_id))
          .map((o: any) => ({
            operation_id: o.operation_id,
            include: true,
            ...(risk ? { risk_level: risk } : {}),
            ...(permissions.trim() ? { permissions: permissions.split(",").map((x) => x.trim()).filter(Boolean) } : {}),
            ...(credentialId ? { credential_id: credentialId } : {}),
          })),
      }),
    onSuccess: (r: any) => {
      const n = r?.items?.length || 0;
      const errs = r?.errors || [];
      setGenErrors(errs);
      toast.success(`Created ${n} tool${n === 1 ? "" : "s"}${errs.length ? ` (${errs.length} failed)` : ""}`);
      qc.invalidateQueries({ queryKey: ["tools"] });
      qc.invalidateQueries({ queryKey: ["tool-registry"] });
      if (errs.length === 0) {
        onImported?.(r?.items || []);
        close();
      }
    },
    onError: (e) => {
      const msg = apiError(e);
      setStepError(msg);
      toast.error(msg);
    },
  });

  const toggle = (id: string) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  const setAll = (on: boolean) =>
    setSelected(on ? (preview?.operations || []).map((o: any) => o.operation_id) : []);
  const close = () => {
    onOpenChange(false);
    setPreview(null);
    setGenErrors([]);
    setStepError(null);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl" data-testid="openapi-import-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">Import from OpenAPI</DialogTitle>
          <DialogDescription>
            Step 1: paste a spec or URL to preview. Step 2: check operations, set
            credentials/permissions/risk, then save. Only checked operations are created.
          </DialogDescription>
        </DialogHeader>
        {stepError && <ErrorNote message={stepError} />}
        {!projectId && !preview && (
          <div>
            <label className="text-xs font-medium text-muted-foreground">Project (import target)</label>
            <Select value={project} onValueChange={setProject}>
              <SelectTrigger className="mt-1" data-testid="openapi-project-select"><SelectValue placeholder="Select project" /></SelectTrigger>
              <SelectContent>
                {availableProjects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        )}
        {!preview ? (
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Upload JSON/YAML (paste spec text)</label>
              <Textarea data-testid="openapi-spec-input" value={spec} onChange={(e) => setSpec(e.target.value)} rows={6} className="mt-1 font-mono text-[11px]" placeholder='{"openapi":"3.0.0",…}' />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Import from URL (server-side fetch)</label>
              <Input data-testid="openapi-url-input" value={specUrl} onChange={(e) => setSpecUrl(e.target.value)} className="mt-1 font-mono text-xs" placeholder="https://" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Base URL override (optional)</label>
              <Input data-testid="openapi-baseurl-input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="mt-1 font-mono text-xs" placeholder="https://api.example.com" />
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="rounded-md border border-border/60 p-2.5" data-testid="openapi-metadata">
              <p className="text-xs font-medium">{preview.title || "Spec"}</p>
              <p className="font-mono text-[11px] text-muted-foreground">
                v{preview.version || "?"} · {(preview.operations || []).length} operations
                {preview.base_url ? ` · ${preview.base_url}` : ""}
              </p>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-muted-foreground" data-testid="openapi-selection-count">
                {selected.length} of {(preview.operations || []).length} selected
              </p>
              <div className="flex gap-2 text-[11px]">
                <button type="button" className="text-muted-foreground hover:text-foreground" data-testid="openapi-select-all" onClick={() => setAll(true)}>Select all</button>
                <button type="button" className="text-muted-foreground hover:text-foreground" data-testid="openapi-select-none" onClick={() => setAll(false)}>None</button>
              </div>
            </div>
            <div className="max-h-56 space-y-1 overflow-y-auto scrollbar-thin" data-testid="openapi-operations">
              {(preview.operations || []).map((o: any) => (
                <label key={o.operation_id} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-accent/50" data-testid={`openapi-op-${o.operation_id}`}>
                  <input type="checkbox" checked={selected.includes(o.operation_id)} onChange={() => toggle(o.operation_id)} className="h-3.5 w-3.5" />
                  <Badge variant="outline" className="font-mono text-[10px]">{o.method}</Badge>
                  <span className="truncate font-mono">{o.path}</span>
                  {o.summary && <span className="truncate text-muted-foreground">{o.summary}</span>}
                  <Badge variant="secondary" className="ml-auto shrink-0 text-[9px]" title="Risk suggestion derived from the HTTP method">
                    {o.risk_suggestion || RISK_BY_METHOD[o.method] || "low"}
                  </Badge>
                </label>
              ))}
            </div>
            {(preview.operations || []).length === 0 && (
              <p className="text-xs text-muted-foreground">No operations found in this spec.</p>
            )}
            <div className="grid grid-cols-2 gap-3 rounded-md border border-border/60 p-2.5" data-testid="openapi-save-options">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Credentials (optional)</label>
                <Select value={credentialId || "__none"} onValueChange={(v) => setCredentialId(v === "__none" ? "" : v)}>
                  <SelectTrigger className="mt-1" data-testid="openapi-credential-select"><SelectValue placeholder="No credential" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none">No credential</SelectItem>
                    {credentials.map((c: any) => <SelectItem key={c.id} value={c.id}>{c.name} · {c.credential_type}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Risk override (optional)</label>
                <Select value={risk || "__auto"} onValueChange={(v) => setRisk(v === "__auto" ? "" : v)}>
                  <SelectTrigger className="mt-1" data-testid="openapi-risk-select"><SelectValue placeholder="Auto by method" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__auto">Auto by method</SelectItem>
                    {["low", "medium", "high", "critical"].map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="col-span-2">
                <label className="text-xs font-medium text-muted-foreground">Permissions override (comma-separated, optional)</label>
                <Input data-testid="openapi-permissions-input" value={permissions} onChange={(e) => setPermissions(e.target.value)} className="mt-1 font-mono text-xs" placeholder="http.api.get" />
              </div>
            </div>
            <div data-testid="openapi-preview">
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                Preview — {selected.length} tool{selected.length === 1 ? "" : "s"} will be created
              </p>
              <ul className="mt-1 space-y-0.5 font-mono text-[11px] text-muted-foreground">
                {(preview.operations || []).filter((o: any) => selected.includes(o.operation_id)).map((o: any) => (
                  <li key={o.operation_id}>{o.method} {o.url_template || o.path}</li>
                ))}
              </ul>
            </div>
            {genErrors.length > 0 && (
              <div className="space-y-1" data-testid="openapi-generate-errors">
                {genErrors.map((e: any, i: number) => (
                  <p key={i} className="text-[11px] text-destructive">{e?.name ? `${e.name}: ` : ""}{e?.message || "Failed"}</p>
                ))}
              </div>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={close}>Cancel</Button>
          {!preview ? (
            <Button data-testid="openapi-preview-submit" disabled={(!spec.trim() && !specUrl.trim()) || !targetProject || previewMut.isPending} onClick={() => previewMut.mutate()}>
              {previewMut.isPending ? "Loading…" : "Preview operations"}
            </Button>
          ) : (
            <>
              <Button variant="outline" onClick={() => { setPreview(null); setGenErrors([]); }}>Back</Button>
              <Button data-testid="openapi-generate-submit" disabled={selected.length === 0 || genMut.isPending} onClick={() => genMut.mutate()}>
                {genMut.isPending ? "Creating…" : `Create ${selected.length} tool${selected.length === 1 ? "" : "s"}`}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
