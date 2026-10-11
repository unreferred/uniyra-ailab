import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, FileJson, Trash2, FlaskConical, KeyRound, Loader2 } from "lucide-react";
import {
  api, apiError, createCustomTool, deleteCustomTool, testTool,
  importOpenAPI, generateFromOpenAPI,
  createToolCredential, updateToolCredential, deleteToolCredential,
} from "@/lib/api";
import { useTools, useToolVersions, useToolCredentials } from "@/lib/hooks";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { EmptyState, LoadingSpinner, ErrorNote } from "@/components/common/Common";
import { OpenAPIOperationPreview } from "@/lib/types";

const isCustom = (t: any) => !t.builtin && (t.tool_type === "http" || t.tool_type === "openapi" || t.tool_type === "code_interpreter" || !!t.current_version_id || t.scope === "project" || (!!t.project_id && !t.builtin));

const VersionsDialog: React.FC<{ toolId: string | null; toolName?: string | null; onClose: () => void }> = ({ toolId, toolName, onClose }) => {
  const { data, isLoading, isError, error } = useToolVersions(toolId);
  const items = data?.items || [];
  return (
    <Dialog open={!!toolId} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent data-testid="tool-versions-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">Versions{toolName ? ` · ${toolName}` : ""}</DialogTitle>
          <DialogDescription>GET /tools/{toolId}/versions — versions are immutable snapshots; edits create a new version.</DialogDescription>
        </DialogHeader>
        {isLoading ? <LoadingSpinner label="Loading versions…" /> : isError ? <ErrorNote message={apiError(error)} /> : (
          data?.builtin ? <p className="text-xs text-muted-foreground">Built-in tools have no versions.</p> :
          items.length === 0 ? <p className="text-xs text-muted-foreground">No versions recorded.</p> : (
            <div className="max-h-64 space-y-2 overflow-y-auto scrollbar-thin">
              {items.map((v: any) => (
                <div key={v.id} className="rounded-md border border-border/60 p-2 text-xs" data-testid={`tool-version-${v.id}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono font-medium">{v.version_label || `v${v.version ?? "?"}`}</span>
                    <Badge variant="outline" className="text-[10px]">{v.tool_type}</Badge>
                    <Badge variant="secondary" className="text-[10px]">risk: {v.risk_level || "low"}</Badge>
                    {v.created_at && <span className="font-mono text-[10px] text-muted-foreground">{v.created_at}</span>}
                    {v.immutable === true && <Badge variant="outline" className="text-[10px]">immutable</Badge>}
                  </div>
                  {(v.permissions || []).length > 0 && (
                    <p className="mt-1 font-mono text-[10px] text-muted-foreground">permissions: {(v.permissions || []).join(", ")}</p>
                  )}
                  <p className="mt-1 text-[11px] text-muted-foreground">Immutable — versions are never edited; new changes create a new version.</p>
                  <pre className="mt-1 max-h-24 overflow-auto rounded bg-muted/40 p-1.5 font-mono text-[10px] scrollbar-thin">{JSON.stringify(v.implementation_config || {}, null, 2)}</pre>
                </div>
              ))}
            </div>
          )
        )}
      </DialogContent>
    </Dialog>
  );
};

const TestDialog: React.FC<{ tool: any | null; projectId: string; onClose: () => void }> = ({ tool, projectId, onClose }) => {
  const [args, setArgs] = useState("{}");
  const [code, setCode] = useState('print("hello from sandbox")');
  const [result, setResult] = useState<any>(null);
  const isCode = tool?.tool_type === "code_interpreter";
  const mut = useMutation({
    mutationFn: async () => {
      let parsed: any = {};
      try { parsed = args.trim() ? JSON.parse(args) : {}; } catch { throw new Error("Arguments must be valid JSON"); }
      const body: any = { arguments: { ...parsed, ...(isCode ? { code } : {}) }, ...(isCode ? { code } : {}) };
      return testTool(tool.id, { ...body, project_id: projectId });
    },
    onSuccess: (r) => { setResult(r); toast.success("Tool test completed"); },
    onError: (e) => toast.error(apiError(e)),
  });
  const reset = () => { setResult(null); setArgs("{}"); setCode('print("hello from sandbox")'); };
  const call = result?.tool_call || null;
  return (
    <Dialog open={!!tool} onOpenChange={(o) => { if (!o) { onClose(); reset(); } }}>
      <DialogContent data-testid="tool-test-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">Test {tool?.display_name || tool?.name}</DialogTitle>
          <DialogDescription>POST /tools/{tool?.id}/test — real execution. Errors are sanitized server-side; no secrets returned.</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground">Arguments (JSON)</label>
          <Textarea data-testid="tool-test-args" value={args} onChange={(e) => setArgs(e.target.value)} rows={4} className="font-mono text-xs" />
          {isCode && (
            <>
              <label className="text-xs font-medium text-muted-foreground">Python code</label>
              <Textarea data-testid="tool-test-code" value={code} onChange={(e) => setCode(e.target.value)} rows={6} className="font-mono text-xs" />
            </>
          )}
          {result != null && (
            <div className="space-y-1.5" data-testid="tool-test-result">
              {(call?.duration_ms != null || call?.execution_status) && (
                <p className="font-mono text-[11px] text-muted-foreground">
                  {call?.duration_ms != null && <>latency: {call.duration_ms} ms</>}
                  {call?.execution_status && <> · status: {call.execution_status}</>}
                </p>
              )}
              <pre className="max-h-48 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[10px] scrollbar-thin">
                {JSON.stringify(result, null, 2)}
              </pre>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => { onClose(); reset(); }}>Close</Button>
          <Button data-testid="tool-test-submit" disabled={mut.isPending} onClick={() => mut.mutate()}>
            {mut.isPending ? (<><Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> Running…</>) : (<><FlaskConical className="mr-1 h-3.5 w-3.5" /> Run test</>)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const CreateToolDialog: React.FC<{ open: boolean; onOpenChange: (v: boolean) => void; projectId: string }> = ({ open, onOpenChange, projectId }) => {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [toolType, setToolType] = useState("http");
  const [baseUrl, setBaseUrl] = useState("");
  const [path, setPath] = useState("");
  const [method, setMethod] = useState("GET");
  const [inputSchema, setInputSchema] = useState('{"type":"object","properties":{}}');
  const mut = useMutation({
    mutationFn: async () => {
      let schema: any = {};
      try { schema = inputSchema.trim() ? JSON.parse(inputSchema) : {}; } catch { throw new Error("Input schema must be valid JSON"); }
      return createCustomTool({
        project_id: projectId,
        name: name.trim(),
        display_name: displayName.trim() || name.trim(),
        description,
        version: {
          tool_type: toolType,
          input_schema: schema,
          output_schema: {},
          permissions: [],
          risk_level: "low",
          implementation_config: {
            ...(baseUrl ? { base_url: baseUrl } : {}),
            ...(path ? { path } : {}),
            ...(toolType === "http" ? { method } : {}),
          },
          source: "manual",
        },
      });
    },
    onSuccess: () => { toast.success("Custom tool created"); qc.invalidateQueries({ queryKey: ["tools", projectId] }); onOpenChange(false); setName(""); setDisplayName(""); setDescription(""); setBaseUrl(""); setPath(""); setInputSchema('{"type":"object","properties":{}}'); },
    onError: (e) => toast.error(apiError(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="tool-create-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">Create custom HTTP tool</DialogTitle>
          <DialogDescription>POST /tools — creates the tool plus immutable version 1.</DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <div><label className="text-xs font-medium text-muted-foreground">Name (a-z, 0-9, _ -)</label><Input data-testid="tool-create-name" value={name} onChange={(e) => setName(e.target.value)} className="mt-1 font-mono text-xs" placeholder="my_api" /></div>
          <div><label className="text-xs font-medium text-muted-foreground">Display name</label><Input data-testid="tool-create-display" value={displayName} onChange={(e) => setDisplayName(e.target.value)} className="mt-1" /></div>
          <div className="col-span-2"><label className="text-xs font-medium text-muted-foreground">Description</label><Textarea data-testid="tool-create-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="mt-1" /></div>
          <div><label className="text-xs font-medium text-muted-foreground">Type</label>
            <Select value={toolType} onValueChange={setToolType}><SelectTrigger className="mt-1" data-testid="tool-create-type"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="http">http</SelectItem><SelectItem value="openapi">openapi</SelectItem><SelectItem value="code_interpreter">code_interpreter</SelectItem></SelectContent></Select></div>
          {toolType === "http" && (<div><label className="text-xs font-medium text-muted-foreground">Method</label>
            <Select value={method} onValueChange={setMethod}><SelectTrigger className="mt-1" data-testid="tool-create-method"><SelectValue /></SelectTrigger>
              <SelectContent>{["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}</SelectContent></Select></div>)}
          <div><label className="text-xs font-medium text-muted-foreground">Base URL</label><Input data-testid="tool-create-baseurl" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="mt-1 font-mono text-xs" placeholder="https://api.example.com" /></div>
          <div><label className="text-xs font-medium text-muted-foreground">Path</label><Input data-testid="tool-create-path" value={path} onChange={(e) => setPath(e.target.value)} className="mt-1 font-mono text-xs" placeholder="/v1/things" /></div>
          <div className="col-span-2"><label className="text-xs font-medium text-muted-foreground">Input schema (JSON)</label><Textarea data-testid="tool-create-schema" value={inputSchema} onChange={(e) => setInputSchema(e.target.value)} rows={3} className="mt-1 font-mono text-[11px]" /></div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button data-testid="tool-create-submit" disabled={!name.trim() || mut.isPending} onClick={() => mut.mutate()}>{mut.isPending ? "Creating…" : "Create"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const RISK_BY_METHOD_FALLBACK: Record<string, string> = {
  GET: "low",
  POST: "high",
  PUT: "high",
  PATCH: "high",
  DELETE: "critical",
};

const OpenAPIImportDialog: React.FC<{ open: boolean; onOpenChange: (v: boolean) => void; projectId: string }> = ({ open, onOpenChange, projectId }) => {
  const qc = useQueryClient();
  const [spec, setSpec] = useState("");
  const [specUrl, setSpecUrl] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [genErrors, setGenErrors] = useState<any[]>([]);
  const previewMut = useMutation({
    mutationFn: async () => importOpenAPI({ project_id: projectId, ...(spec.trim() ? { spec } : {}), ...(specUrl.trim() ? { spec_url: specUrl.trim() } : {}), ...(baseUrl.trim() ? { base_url_override: baseUrl.trim() } : {}) }),
    onSuccess: (p) => {
      setPreview(p);
      setGenErrors([]);
      setSelected((p?.operations || []).map((o: OpenAPIOperationPreview) => o.operation_id));
      toast.success(`${p?.operation_count ?? (p?.operations || []).length} operations found`);
    },
    onError: (e) => toast.error(apiError(e)),
  });
  const genMut = useMutation({
    mutationFn: async () =>
      generateFromOpenAPI({
        project_id: projectId,
        spec: preview?.raw_spec || spec,
        ...(baseUrl.trim() ? { base_url_override: baseUrl.trim() } : {}),
        operations: (preview?.operations || []).filter((o: any) => selected.includes(o.operation_id)).map((o: any) => ({ operation_id: o.operation_id, include: true })),
      }),
    onSuccess: (r: any) => {
      const n = r?.items?.length || 0;
      const errs = r?.errors || [];
      setGenErrors(errs);
      toast.success(`Created ${n} tool${n === 1 ? "" : "s"}${errs.length ? ` (${errs.length} failed)` : ""}`);
      qc.invalidateQueries({ queryKey: ["tools", projectId] });
      if (errs.length === 0) {
        onOpenChange(false);
        setPreview(null);
        setSpec("");
        setBaseUrl("");
      }
    },
    onError: (e) => toast.error(apiError(e)),
  });
  const toggle = (id: string) => setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  const setAll = (on: boolean) => setSelected(on ? (preview?.operations || []).map((o: any) => o.operation_id) : []);
  const close = () => { onOpenChange(false); setPreview(null); setGenErrors([]); };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl" data-testid="openapi-import-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">Import from OpenAPI</DialogTitle>
          <DialogDescription>Step 1: paste a spec or URL to preview. Step 2: select operations to generate. POST /tools/import-openapi then POST /tools/generate-from-openapi.</DialogDescription>
        </DialogHeader>
        {!preview ? (
          <div className="space-y-3">
            <div><label className="text-xs font-medium text-muted-foreground">Spec text (JSON/YAML)</label><Textarea data-testid="openapi-spec-input" value={spec} onChange={(e) => setSpec(e.target.value)} rows={6} className="mt-1 font-mono text-[11px]" placeholder='{"openapi":"3.0.0",…}' /></div>
            <div><label className="text-xs font-medium text-muted-foreground">…or spec URL (server-side fetch)</label><Input data-testid="openapi-url-input" value={specUrl} onChange={(e) => setSpecUrl(e.target.value)} className="mt-1 font-mono text-xs" placeholder="https://…" /></div>
            <div><label className="text-xs font-medium text-muted-foreground">Base URL override (optional)</label><Input data-testid="openapi-baseurl-input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="mt-1 font-mono text-xs" placeholder="https://api.example.com" /></div>
          </div>
        ) : (
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-muted-foreground">{preview.title || "Spec"} · {(preview.operations || []).length} operations{preview.base_url ? ` · ${preview.base_url}` : ""}</p>
              <div className="flex gap-2 text-[11px]">
                <button type="button" className="text-muted-foreground hover:text-foreground" data-testid="openapi-select-all" onClick={() => setAll(true)}>Select all</button>
                <button type="button" className="text-muted-foreground hover:text-foreground" data-testid="openapi-select-none" onClick={() => setAll(false)}>None</button>
              </div>
            </div>
            <div className="max-h-64 space-y-1 overflow-y-auto scrollbar-thin">
              {(preview.operations || []).map((o: OpenAPIOperationPreview) => (
                <label key={o.operation_id} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-accent/50" data-testid={`openapi-op-${o.operation_id}`}>
                  <input type="checkbox" checked={selected.includes(o.operation_id)} onChange={() => toggle(o.operation_id)} className="h-3.5 w-3.5" />
                  <Badge variant="outline" className="font-mono text-[10px]">{o.method}</Badge>
                  <span className="truncate font-mono">{o.path}</span>
                  {o.summary && <span className="truncate text-muted-foreground">{o.summary}</span>}
                  <Badge variant="secondary" className="ml-auto shrink-0 text-[9px]" title="Risk suggestion derived from the HTTP method">
                    {(o as any).risk_suggestion || RISK_BY_METHOD_FALLBACK[o.method] || "low"}
                  </Badge>
                </label>
              ))}
            </div>
            {(preview.operations || []).length === 0 && (
              <p className="text-xs text-muted-foreground">No operations found in this spec.</p>
            )}
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
            <Button data-testid="openapi-preview-submit" disabled={(!spec.trim() && !specUrl.trim()) || previewMut.isPending} onClick={() => previewMut.mutate()}>
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

const CredentialsManager: React.FC<{ projectId: string }> = ({ projectId }) => {
  const qc = useQueryClient();
  const { data, isLoading, isError, error } = useToolCredentials(projectId);
  const items = data?.items || [];
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [credType, setCredType] = useState("API_KEY");
  const [value, setValue] = useState("");
  const [editingValue, setEditingValue] = useState<Record<string, string>>({});

  const [credError, setCredError] = useState<string | null>(null);
  const createMut = useMutation({
    mutationFn: async () => createToolCredential({ project_id: projectId, name: name.trim(), credential_type: credType, value }),
    onSuccess: () => { toast.success("Credential stored"); setOpen(false); setName(""); setValue(""); setCredError(null); qc.invalidateQueries({ queryKey: ["tool-credentials", projectId] }); },
    onError: (e) => { setCredError(apiError(e)); toast.error(apiError(e)); },
  });
  const rotateMut = useMutation({
    mutationFn: async ({ id, v }: { id: string; v: string }) => updateToolCredential(id, { value: v }),
    onSuccess: () => { toast.success("Credential value rotated"); setEditingValue({}); setCredError(null); qc.invalidateQueries({ queryKey: ["tool-credentials", projectId] }); },
    onError: (e) => { setCredError(apiError(e)); toast.error(apiError(e)); },
  });
  const delMut = useMutation({
    mutationFn: async (id: string) => deleteToolCredential(id),
    onSuccess: () => { toast.success("Credential deleted"); setCredError(null); qc.invalidateQueries({ queryKey: ["tool-credentials", projectId] }); },
    onError: (e) => {
      // Backend refuses with 409 while tool versions reference the credential.
      const msg = apiError(e);
      setCredError(msg);
      toast.error(msg);
    },
  });

  return (
    <Card className="p-4" data-testid="tool-credentials">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="flex items-center gap-1.5 font-display text-sm font-semibold"><KeyRound className="h-4 w-4" /> Credentials</h3>
        <Button size="sm" variant="outline" data-testid="credential-create" onClick={() => setOpen(true)}><Plus className="mr-1 h-3.5 w-3.5" /> New</Button>
      </div>
      {isLoading ? <LoadingSpinner label="Loading credentials…" /> : isError ? <ErrorNote message={apiError(error)} /> : items.length === 0 ? (
        <p className="text-xs text-muted-foreground">No credentials stored. Values are never returned — only names and types.</p>
      ) : (
        <>
          {credError && <p className="mb-1.5 text-[11px] text-destructive" data-testid="credential-error">{credError}</p>}
          <div className="space-y-1.5">
          {items.map((c: any) => (
            <div key={c.id} className="flex items-center gap-2 rounded-md border border-border/60 px-2 py-1.5 text-xs" data-testid={`credential-${c.id}`}>
              <span className="font-mono font-medium">{c.name}</span>
              <Badge variant="outline" className="text-[10px]" title="Credential type">{c.credential_type}</Badge>
              {/* Secret values are never returned by the API — only a stored marker is shown. */}
              <Badge variant="secondary" className="text-[10px]" data-testid={`credential-stored-${c.id}`}>Stored</Badge>
              {editingValue[c.id] !== undefined ? (
                <>
                  <Input type="password" value={editingValue[c.id]} onChange={(e) => setEditingValue((p) => ({ ...p, [c.id]: e.target.value }))} className="h-7 font-mono text-xs" placeholder="new secret value" data-testid={`credential-rotate-input-${c.id}`} />
                  <Button size="sm" data-testid={`credential-rotate-save-${c.id}`} disabled={!editingValue[c.id] || rotateMut.isPending} onClick={() => rotateMut.mutate({ id: c.id, v: editingValue[c.id] })}>Save</Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditingValue((p) => { const n = { ...p }; delete n[c.id]; return n; })}>Cancel</Button>
                </>
              ) : (
                <>
                  <button type="button" className="ml-auto text-[11px] text-muted-foreground hover:text-foreground" data-testid={`credential-rotate-${c.id}`} onClick={() => setEditingValue((p) => ({ ...p, [c.id]: "" }))}>Rotate value</button>
                  <button type="button" className="text-[11px] text-destructive" data-testid={`credential-delete-${c.id}`} onClick={() => delMut.mutate(c.id)}>Delete</button>
                </>
              )}
            </div>
          ))}
          </div>
        </>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent data-testid="credential-create-dialog">
          <DialogHeader><DialogTitle className="font-display">New credential</DialogTitle><DialogDescription>POST /tool-credentials — the value is encrypted server-side and never returned.</DialogDescription></DialogHeader>
          <div className="space-y-3">
            <div><label className="text-xs font-medium text-muted-foreground">Name</label><Input data-testid="credential-name-input" value={name} onChange={(e) => setName(e.target.value)} className="mt-1 font-mono text-xs" placeholder="MY_API_KEY" /></div>
            <div><label className="text-xs font-medium text-muted-foreground">Type</label>
              <Select value={credType} onValueChange={setCredType}><SelectTrigger className="mt-1" data-testid="credential-type-select"><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="API_KEY">API_KEY</SelectItem><SelectItem value="BEARER_TOKEN">BEARER_TOKEN</SelectItem><SelectItem value="BASIC_AUTH">BASIC_AUTH</SelectItem><SelectItem value="CUSTOM_HEADER">CUSTOM_HEADER</SelectItem></SelectContent></Select></div>
            <div><label className="text-xs font-medium text-muted-foreground">Secret value</label><Input data-testid="credential-value-input" type="password" value={value} onChange={(e) => setValue(e.target.value)} className="mt-1 font-mono text-xs" /></div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button data-testid="credential-create-submit" disabled={!name.trim() || !value || createMut.isPending} onClick={() => createMut.mutate()}>{createMut.isPending ? "Storing…" : "Store"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
};

export const ToolsTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [versionsFor, setVersionsFor] = useState<string | null>(null);
  const [testFor, setTestFor] = useState<any | null>(null);

  const { data, isLoading, isError, error } = useTools(projectId);
  const tools = data?.items || [];
  const custom = tools.filter(isCustom);
  const versionsToolName =
    tools.find((t: any) => t.id === versionsFor)?.display_name ||
    tools.find((t: any) => t.id === versionsFor)?.name ||
    null;

  const delMut = useMutation({
    mutationFn: async (id: string) => deleteCustomTool(id),
    onSuccess: () => { toast.success("Tool deleted"); qc.invalidateQueries({ queryKey: ["tools", projectId] }); },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <div className="space-y-4" data-testid="project-tools">
      <div className="flex flex-wrap justify-end gap-2">
        <Button size="sm" variant="outline" data-testid="tools-import-openapi" onClick={() => setImportOpen(true)}>
          <FileJson className="mr-1.5 h-4 w-4" /> Import OpenAPI
        </Button>
        <Button size="sm" data-testid="tools-create-custom" onClick={() => setCreateOpen(true)}>
          <Plus className="mr-1.5 h-4 w-4" /> New custom tool
        </Button>
      </div>

      {isLoading ? (
        <LoadingSpinner label="Loading tools…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : custom.length === 0 ? (
        <EmptyState
          title="No project tools"
          description="Create a custom HTTP tool or import operations from an OpenAPI spec."
          testId="project-tools-empty"
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
              <tr><th className="px-4 py-2.5">Tool</th><th className="px-4 py-2.5">Type</th><th className="px-4 py-2.5">Version</th><th className="px-4 py-2.5 text-right">Actions</th></tr>
            </thead>
            <tbody className="divide-y divide-border">
              {custom.map((t: any) => (
                <tr key={t.id} className="transition-colors hover:bg-accent/40" data-testid={`project-tool-${t.id}`}>
                  <td className="px-4 py-2.5">
                    <div className="font-medium">{t.display_name || t.name}</div>
                    <div className="font-mono text-[11px] text-muted-foreground">{t.name}</div>
                  </td>
                  <td className="px-4 py-2.5"><Badge variant="outline" className="text-[10px]">{t.tool_type || "custom"}</Badge></td>
                  <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">{t.version || t.current_version_id?.slice(0, 8) || "—"}</td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      <Button size="sm" variant="ghost" data-testid={`project-tool-test-${t.id}`} title="Test" onClick={() => setTestFor(t)}><FlaskConical className="h-4 w-4" /></Button>
                      <Button size="sm" variant="ghost" data-testid={`project-tool-versions-${t.id}`} title="Versions" onClick={() => setVersionsFor(t.id)}>Versions</Button>
                      <Button size="sm" variant="ghost" className="text-destructive" data-testid={`project-tool-delete-${t.id}`} title="Delete" onClick={() => delMut.mutate(t.id)}><Trash2 className="h-4 w-4" /></Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <CredentialsManager projectId={projectId} />

      <CreateToolDialog open={createOpen} onOpenChange={setCreateOpen} projectId={projectId} />
      <OpenAPIImportDialog open={importOpen} onOpenChange={setImportOpen} projectId={projectId} />
      <VersionsDialog toolId={versionsFor} toolName={versionsToolName} onClose={() => setVersionsFor(null)} />
      <TestDialog tool={testFor} projectId={projectId} onClose={() => setTestFor(null)} />
    </div>
  );
};
