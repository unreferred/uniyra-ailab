import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import {
  apiError, createCustomTool, testTool,
  listToolCredentials,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ErrorNote } from "@/components/common/Common";

export interface AddHttpToolDialogProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  // Global manager: no project -> project_id omitted (owner scope).
  // Project callers pass their id -> project scope.
  projectId?: string | null;
  availableProjects: { id: string; name: string }[];
  credentials: any[];
  onCreated?: (tool: any) => void;
}

const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];
const RISKS = ["low", "medium", "high", "critical"];
const NAME_RE = /^[a-zA-Z0-9_\-]+$/;

const parseJsonField = (text: string, label: string, fallback: any) => {
  if (!text.trim()) return fallback;
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${label} must be valid JSON`);
  }
};

// Full V4 HTTP custom-tool form. Sections: GENERAL / REQUEST / INPUT /
// REQUEST MAPPING / SECURITY. Only HTTP tools can be created here — no
// arbitrary Python/code tool creation path exists.
export const AddHttpToolDialog: React.FC<AddHttpToolDialogProps> = ({
  open, onOpenChange, projectId, availableProjects, credentials, onCreated,
}) => {
  const qc = useQueryClient();
  const [scope, setScope] = useState<"project" | "global">(projectId ? "project" : "global");
  const [project, setProject] = useState<string>(projectId || "");
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [method, setMethod] = useState("GET");
  const [urlTemplate, setUrlTemplate] = useState("");
  const [timeout, setTimeout] = useState("15");
  const [inputSchema, setInputSchema] = useState('{\n  "type": "object",\n  "properties": {},\n  "additionalProperties": true\n}');
  const [pathMapping, setPathMapping] = useState("{}");
  const [queryMapping, setQueryMapping] = useState("{}");
  const [headersMapping, setHeadersMapping] = useState("{}");
  const [bodyMapping, setBodyMapping] = useState("");
  const [credentialId, setCredentialId] = useState("");
  const [permissions, setPermissions] = useState("");
  const [risk, setRisk] = useState("low");
  const [testArgs, setTestArgs] = useState("{}");
  const [testResult, setTestResult] = useState<any>(null);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  React.useEffect(() => {
    if (open) {
      setScope(projectId ? "project" : "global");
      setProject(projectId || "");
      setFormError(null);
      setTestResult(null);
      setCreatedId(null);
    }
  }, [open, projectId]);

  const buildPayload = () => {
    const trimmedName = name.trim();
    if (!trimmedName) throw new Error("Name is required");
    if (!NAME_RE.test(trimmedName)) throw new Error("Name must match ^[a-zA-Z0-9_-]+$");
    if (!urlTemplate.trim()) throw new Error("URL template is required");
    const schema = parseJsonField(inputSchema, "Input schema", {});
    if (typeof schema !== "object" || Array.isArray(schema)) throw new Error("Input schema must be a JSON object");
    const path = parseJsonField(pathMapping, "Path mapping", {});
    const query = parseJsonField(queryMapping, "Query mapping", {});
    const headers = parseJsonField(headersMapping, "Headers mapping", {});
    let body: any;
    if (bodyMapping.trim()) {
      try {
        body = JSON.parse(bodyMapping);
      } catch {
        body = bodyMapping;
      }
    }
    const timeoutInt = timeout.trim() === "" ? 15 : parseInt(timeout, 10);
    if (!Number.isFinite(timeoutInt) || timeoutInt < 1 || timeoutInt > 60)
      throw new Error("Timeout must be between 1 and 60 seconds");
    const perms = permissions.split(",").map((p) => p.trim()).filter(Boolean);
    return {
      ...(scope === "project" && project ? { project_id: project } : {}),
      name: trimmedName,
      display_name: displayName.trim() || trimmedName,
      description,
      version: {
        tool_type: "http",
        input_schema: schema,
        output_schema: {},
        permissions: perms,
        risk_level: risk,
        implementation_config: {
          kind: "http",
          method,
          url_template: urlTemplate.trim(),
          query_params: { ...path, ...query },
          headers,
          ...(body !== undefined ? { body } : {}),
          timeout: timeoutInt,
          ...(credentialId ? { credential_id: credentialId } : {}),
        },
        source: "manual",
      },
    };
  };

  const createMut = useMutation({
    mutationFn: async () => createCustomTool(buildPayload()),
    onSuccess: (tool: any) => {
      toast.success("Custom HTTP tool created");
      setCreatedId(tool?.id || null);
      qc.invalidateQueries({ queryKey: ["tools"] });
      qc.invalidateQueries({ queryKey: ["tool-registry"] });
      onCreated?.(tool);
      onOpenChange(false);
      reset();
    },
    onError: (e) => {
      const msg = apiError(e);
      setFormError(msg);
      toast.error(msg);
    },
  });

  const testMut = useMutation({
    mutationFn: async () => {
      let parsed: any = {};
      try {
        parsed = testArgs.trim() ? JSON.parse(testArgs) : {};
      } catch {
        throw new Error("Test arguments must be valid JSON");
      }
      const payload = buildPayload();
      // Dry-run shape check only when the tool is not saved yet: POST the
      // real backend only when we have an id.
      if (!createdId) return { dry_run: true, payload };
      return testTool(createdId, { arguments: parsed, ...(project ? { project_id: project } : {}) });
    },
    onSuccess: (r) => {
      setTestResult(r);
      if (!(r as any)?.dry_run) toast.success("Tool test completed");
    },
    onError: (e) => {
      const msg = apiError(e);
      setFormError(msg);
      toast.error(msg);
    },
  });

  const reset = () => {
    setName("");
    setDisplayName("");
    setDescription("");
    setMethod("GET");
    setUrlTemplate("");
    setTimeout("15");
    setInputSchema('{\n  "type": "object",\n  "properties": {},\n  "additionalProperties": true\n}');
    setPathMapping("{}");
    setQueryMapping("{}");
    setHeadersMapping("{}");
    setBodyMapping("");
    setCredentialId("");
    setPermissions("");
    setRisk("low");
    setTestArgs("{}");
    setTestResult(null);
    setCreatedId(null);
    setFormError(null);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl" data-testid="add-http-tool-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">Add HTTP tool</DialogTitle>
          <DialogDescription>
            POST /tools — creates the tool plus immutable version 1.
            Only HTTP tools; no arbitrary Python execution path.
          </DialogDescription>
        </DialogHeader>
        {formError && <ErrorNote message={formError} />}
        <div className="space-y-5">
          <section data-testid="add-tool-general">
            <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">General</h4>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Scope</label>
                <Select value={scope} onValueChange={(v) => setScope(v as any)}>
                  <SelectTrigger className="mt-1" data-testid="add-tool-scope"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="project">project</SelectItem>
                    <SelectItem value="global">project-global</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {scope === "project" && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Project</label>
                  <Select value={project} onValueChange={setProject}>
                    <SelectTrigger className="mt-1" data-testid="add-tool-project"><SelectValue placeholder="Select project" /></SelectTrigger>
                    <SelectContent>
                      {availableProjects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              )}
              <div>
                <label className="text-xs font-medium text-muted-foreground">Name (a-z, 0-9, _ -)</label>
                <Input data-testid="add-tool-name" value={name} onChange={(e) => setName(e.target.value)} className="mt-1 font-mono text-xs" placeholder="my_api" />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Display name</label>
                <Input data-testid="add-tool-display" value={displayName} onChange={(e) => setDisplayName(e.target.value)} className="mt-1" />
              </div>
              <div className="col-span-2">
                <label className="text-xs font-medium text-muted-foreground">Description</label>
                <Textarea data-testid="add-tool-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="mt-1" />
              </div>
            </div>
          </section>

          <section data-testid="add-tool-request">
            <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Request</h4>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Method</label>
                <Select value={method} onValueChange={setMethod}>
                  <SelectTrigger className="mt-1" data-testid="add-tool-method"><SelectValue /></SelectTrigger>
                  <SelectContent>{METHODS.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Timeout (1-60s)</label>
                <Input data-testid="add-tool-timeout" value={timeout} onChange={(e) => setTimeout(e.target.value)} className="mt-1 font-mono text-xs" placeholder="15" />
              </div>
              <div className="col-span-2">
                <label className="text-xs font-medium text-muted-foreground">URL template (use {"{param}"} placeholders)</label>
                <Input data-testid="add-tool-url" value={urlTemplate} onChange={(e) => setUrlTemplate(e.target.value)} className="mt-1 font-mono text-xs" placeholder="https://api.example.com/v1/things/{id}" />
              </div>
            </div>
          </section>

          <section data-testid="add-tool-input">
            <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Input (JSON schema)</h4>
            <Textarea data-testid="add-tool-schema" value={inputSchema} onChange={(e) => setInputSchema(e.target.value)} rows={5} className="font-mono text-[11px]" />
          </section>

          <section data-testid="add-tool-mapping">
            <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Request mapping</h4>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Path params (JSON)</label>
                <Textarea data-testid="add-tool-path" value={pathMapping} onChange={(e) => setPathMapping(e.target.value)} rows={2} className="mt-1 font-mono text-[11px]" />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Query params (JSON)</label>
                <Textarea data-testid="add-tool-query" value={queryMapping} onChange={(e) => setQueryMapping(e.target.value)} rows={2} className="mt-1 font-mono text-[11px]" />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Headers (JSON)</label>
                <Textarea data-testid="add-tool-headers" value={headersMapping} onChange={(e) => setHeadersMapping(e.target.value)} rows={2} className="mt-1 font-mono text-[11px]" />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Body template (JSON or text)</label>
                <Textarea data-testid="add-tool-body" value={bodyMapping} onChange={(e) => setBodyMapping(e.target.value)} rows={2} className="mt-1 font-mono text-[11px]" placeholder='{"q": "{query}"}' />
              </div>
            </div>
          </section>

          <section data-testid="add-tool-security">
            <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Security</h4>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Credential ref (optional)</label>
                <Select value={credentialId || "__none"} onValueChange={(v) => setCredentialId(v === "__none" ? "" : v)}>
                  <SelectTrigger className="mt-1" data-testid="add-tool-credential"><SelectValue placeholder="No credential" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none">No credential</SelectItem>
                    {credentials.map((c: any) => <SelectItem key={c.id} value={c.id}>{c.name} · {c.credential_type}</SelectItem>)}
                  </SelectContent>
                </Select>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Or embed {"{{secret:NAME}}"} in the URL/headers — resolved server-side only.
                </p>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Risk</label>
                <Select value={risk} onValueChange={setRisk}>
                  <SelectTrigger className="mt-1" data-testid="add-tool-risk"><SelectValue /></SelectTrigger>
                  <SelectContent>{RISKS.map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="col-span-2">
                <label className="text-xs font-medium text-muted-foreground">Permissions (comma-separated)</label>
                <Input data-testid="add-tool-permissions" value={permissions} onChange={(e) => setPermissions(e.target.value)} className="mt-1 font-mono text-xs" placeholder="http.api.get" />
                <div className="mt-1 flex flex-wrap gap-1">
                  {permissions.split(",").map((p) => p.trim()).filter(Boolean).map((p) => (
                    <Badge key={p} variant="outline" className="font-mono text-[10px]">{p}</Badge>
                  ))}
                </div>
              </div>
            </div>
          </section>

          <section data-testid="add-tool-test">
            <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Test</h4>
            <Textarea data-testid="add-tool-test-args" value={testArgs} onChange={(e) => setTestArgs(e.target.value)} rows={2} className="font-mono text-[11px]" placeholder='{"id": "123"}' />
            {testResult != null && (
              <pre data-testid="add-tool-test-result" className="mt-2 max-h-40 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[10px] scrollbar-thin">
                {JSON.stringify(testResult, null, 2)}
              </pre>
            )}
          </section>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="outline" data-testid="add-tool-test-btn" disabled={testMut.isPending} onClick={() => testMut.mutate()}>
            {testMut.isPending ? (<><Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> Testing…</>) : "Test"}
          </Button>
          <Button data-testid="add-tool-save" disabled={!name.trim() || !urlTemplate.trim() || createMut.isPending} onClick={() => { setFormError(null); createMut.mutate(); }}>
            {createMut.isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export const fetchCredsForProject = async (projectId: string) => {
  if (!projectId) return [];
  try {
    return (await listToolCredentials(projectId))?.items || [];
  } catch {
    return [];
  }
};
