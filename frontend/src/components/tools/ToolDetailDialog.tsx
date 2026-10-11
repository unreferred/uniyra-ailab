import React, { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { FlaskConical, Loader2, Play } from "lucide-react";
import {
  api, apiError, codeInterpreterStatus, executeCode, testTool,
} from "@/lib/api";
import { useToolVersions } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { RiskBadge, ScopeBadge, isCodeInterpreter, toolTypeLabel, versionLabelOf } from "@/components/tools/ToolShared";

export interface ToolDetailDialogProps {
  tool: any | null;
  onClose: () => void;
  ciStatus: any;
  projectIdForTest?: string | null;
  availableProjects: { id: string; name: string }[];
}

// Versions tab: immutable snapshots with created date + method/url/schema/
// permission-risk changes vs the previous version.
const VersionsTab: React.FC<{ tool: any }> = ({ tool }) => {
  const { data, isLoading, isError, error } = useToolVersions(tool?.id);
  const items = [...(data?.items || [])].sort((a: any, b: any) => (a.version || 0) - (b.version || 0));
  if (isLoading) return <LoadingSpinner label="Loading versions…" />;
  if (isError) return <ErrorNote message={apiError(error)} />;
  if (data?.builtin) return <p className="text-xs text-muted-foreground">Built-in tools have no versions.</p>;
  if (items.length === 0) return <p className="text-xs text-muted-foreground">No versions recorded.</p>;
  const currentId = tool?.current_version_id;
  const diffKeys = (cur: any, prev: any | null): string[] => {
    if (!prev) return [];
    const out: string[] = [];
    const cfg = cur?.implementation_config || {};
    const pcfg = prev?.implementation_config || {};
    if ((cfg.method || "") !== (pcfg.method || "")) out.push("method");
    if ((cfg.url_template || "") !== (pcfg.url_template || "")) out.push("url");
    if (JSON.stringify(cur?.input_schema || {}) !== JSON.stringify(prev?.input_schema || {})) out.push("schema");
    const cp = [...(cur?.permissions || [])].sort().join(",");
    const pp = [...(prev?.permissions || [])].sort().join(",");
    if (cp !== pp) out.push("permissions");
    if ((cur?.risk_level || "") !== (prev?.risk_level || "")) out.push("risk");
    return out;
  };
  return (
    <div className="max-h-72 space-y-2 overflow-y-auto scrollbar-thin" data-testid="tool-detail-versions">
      {items.map((v: any, i: number) => {
        const changes = diffKeys(v, i > 0 ? items[i - 1] : null);
        return (
          <div key={v.id} className="rounded-md border border-border/60 p-2 text-xs" data-testid={`tool-version-${v.id}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono font-medium">{v.version_label || `v${v.version ?? "?"}`}</span>
              {v.id === currentId && <Badge variant="default" className="text-[10px]" data-testid={`tool-version-current-${v.id}`}>CURRENT</Badge>}
              <Badge variant="outline" className="text-[10px]">{v.tool_type}</Badge>
              <Badge variant="secondary" className="text-[10px]">risk: {v.risk_level || "low"}</Badge>
              {v.created_at && <span className="font-mono text-[10px] text-muted-foreground">{v.created_at}</span>}
              {v.immutable === true && <Badge variant="outline" className="text-[10px]">immutable</Badge>}
            </div>
            {(v.permissions || []).length > 0 && (
              <p className="mt-1 font-mono text-[10px] text-muted-foreground">permissions: {(v.permissions || []).join(", ")}</p>
            )}
            {i > 0 && (
              <p className="mt-1 text-[11px] text-muted-foreground" data-testid={`tool-version-changes-${v.id}`}>
                {changes.length === 0 ? "No method/url/schema/permission-risk changes vs previous." : `Changed vs previous: ${changes.join(", ")}.`}
              </p>
            )}
            {i === 0 && <p className="mt-1 text-[11px] text-muted-foreground">Initial version.</p>}
            <p className="mt-1 text-[11px] text-muted-foreground">Immutable — edits create a new version; this snapshot never changes.</p>
            <pre className="mt-1 max-h-24 overflow-auto rounded bg-muted/40 p-1.5 font-mono text-[10px] scrollbar-thin">{JSON.stringify(v.implementation_config || {}, null, 2)}</pre>
          </div>
        );
      })}
    </div>
  );
};

const ToolTestTab: React.FC<{ tool: any; projectIdForTest?: string | null; availableProjects: { id: string; name: string }[] }> = ({ tool, projectIdForTest, availableProjects }) => {
  const [args, setArgs] = useState("{}");
  const [result, setResult] = useState<any>(null);
  const [project, setProject] = useState(projectIdForTest || tool?.project_id || "");
  const mut = useMutation({
    mutationFn: async () => {
      let parsed: any = {};
      try {
        parsed = args.trim() ? JSON.parse(args) : {};
      } catch {
        throw new Error("Arguments must be valid JSON");
      }
      return testTool(tool.id, { arguments: parsed, ...(project ? { project_id: project } : {}) });
    },
    onSuccess: (r) => {
      setResult(r);
      toast.success("Tool test completed");
    },
    onError: (e) => toast.error(apiError(e)),
  });
  const call = result?.tool_call || null;
  return (
    <div className="space-y-2" data-testid="tool-detail-test">
      {!projectIdForTest && !tool?.project_id && (
        <div className="max-w-xs">
          <label className="text-xs font-medium text-muted-foreground">Project (test scope)</label>
          <select value={project} onChange={(e) => setProject(e.target.value)} className="mt-1 h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm" data-testid="tool-detail-test-project">
            <option value="">No project</option>
            {availableProjects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      )}
      <label className="text-xs font-medium text-muted-foreground">Arguments (JSON)</label>
      <Textarea data-testid="tool-test-args" value={args} onChange={(e) => setArgs(e.target.value)} rows={4} className="font-mono text-xs" />
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
      <Button size="sm" data-testid="tool-test-submit" disabled={mut.isPending} onClick={() => mut.mutate()}>
        {mut.isPending ? (<><Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> Running…</>) : (<><FlaskConical className="mr-1 h-3.5 w-3.5" /> Run test</>)}
      </Button>
      {mut.isError && <ErrorNote message={apiError(mut.error)} />}
    </div>
  );
};

// Code Interpreter detail: real runtime info from /code-interpreter/status +
// a [Test Code Interpreter] console wired to the actual sandbox service.
const CodeInterpreterTab: React.FC<{ tool: any; ciStatus: any; projectIdForTest?: string | null; availableProjects: { id: string; name: string }[] }> = ({ tool, ciStatus, projectIdForTest, availableProjects }) => {
  const statusQuery = useQuery({
    queryKey: ["code-interpreter-status"],
    queryFn: codeInterpreterStatus,
    initialData: ciStatus ?? undefined,
    retry: false,
  });
  const status = statusQuery.data ?? ciStatus;
  const [code, setCode] = useState("print(2+2)");
  const [project, setProject] = useState(projectIdForTest || tool?.project_id || "");
  const [result, setResult] = useState<any>(null);
  const runMut = useMutation({
    mutationFn: async () => executeCode({ project_id: project, code }),
    onSuccess: (r) => {
      setResult(r);
      if (r?.status === "success") toast.success("Code executed");
      else toast.message(`Execution ${r?.status || "finished"}`);
    },
    onError: (e) => toast.error(apiError(e)),
  });
  const limits = status?.limits || {};
  const packages: string[] = status?.policy?.packages || [];
  const files: string[] = Array.isArray(result?.files)
    ? result.files.map((f: any) => (typeof f === "string" ? f : String(f?.filename || f?.name || f?.path || "output")))
    : [];
  const online = status?.available === true;
  const needProject = !project;
  return (
    <div className="space-y-3" data-testid="tool-ci-detail">
      <Card className="space-y-2 p-3" data-testid="tool-ci-runtime">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-medium">Status</span>
          {online ? (
            <Badge variant="secondary" className="text-[10px]" data-testid="tool-ci-online">Online</Badge>
          ) : (
            <Badge variant="destructive" className="text-[10px]" data-testid="tool-ci-offline">Offline</Badge>
          )}
          {!online && status?.reason && <span className="text-muted-foreground">{status.reason}</span>}
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[11px] text-muted-foreground sm:grid-cols-3">
          <div><dt className="uppercase text-[10px]">Image</dt><dd className="text-foreground" data-testid="tool-ci-image">{status?.image || "—"}</dd></div>
          <div><dt className="uppercase text-[10px]">Network</dt><dd className="text-foreground" data-testid="tool-ci-network">{status?.policy?.network ? String(status.policy.network).toLowerCase() === "off" ? "Disabled" : String(status.policy.network) : "—"}</dd></div>
          <div><dt className="uppercase text-[10px]">CPU</dt><dd className="text-foreground" data-testid="tool-ci-cpu">{limits.cpus ?? limits.cpu ?? "—"}</dd></div>
          <div><dt className="uppercase text-[10px]">Memory</dt><dd className="text-foreground" data-testid="tool-ci-memory">{limits.memory_mb != null ? `${limits.memory_mb}MB` : "—"}</dd></div>
          <div><dt className="uppercase text-[10px]">Timeout</dt><dd className="text-foreground" data-testid="tool-ci-timeout">{limits.timeout_s != null ? `${limits.timeout_s}s` : "—"}</dd></div>
          <div><dt className="uppercase text-[10px]">PID limit</dt><dd className="text-foreground" data-testid="tool-ci-pids">{limits.pids_limit ?? "—"}</dd></div>
          <div><dt className="uppercase text-[10px]">Output limit</dt><dd className="text-foreground" data-testid="tool-ci-output-limit">{limits.max_output_bytes != null ? `${Math.round(limits.max_output_bytes / 1024)}KB` : "—"}</dd></div>
          <div><dt className="uppercase text-[10px]">File limit</dt><dd className="text-foreground" data-testid="tool-ci-file-limit">{limits.max_files ?? "—"}</dd></div>
          <div><dt className="uppercase text-[10px]">Permission</dt><dd className="text-foreground" data-testid="tool-ci-permission">{(status?.policy?.permissions || tool?.permissions || []).join(", ") || "—"}</dd></div>
        </dl>
        <div data-testid="tool-ci-packages">
          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Packages</span>
          <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">{packages.length > 0 ? packages.join(", ") : "Not reported"}</p>
        </div>
      </Card>

      <div data-testid="tool-ci-console">
        <h4 className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Test Code Interpreter</h4>
        {(!projectIdForTest && !tool?.project_id) && (
          <div className="mb-2 max-w-xs">
            <label className="text-xs font-medium text-muted-foreground">Project (execution scope)</label>
            <select value={project} onChange={(e) => setProject(e.target.value)} className="mt-1 h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm" data-testid="tool-ci-project">
              <option value="">Select project</option>
              {availableProjects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
        )}
        <Textarea data-testid="tool-ci-code" value={code} onChange={(e) => setCode(e.target.value)} rows={4} className="font-mono text-xs" />
        <div className="mt-2">
          <Button
            size="sm"
            data-testid="tool-ci-run"
            disabled={!online || needProject || !code.trim() || runMut.isPending}
            title={!online ? "Sandbox offline" : needProject ? "Select a project" : undefined}
            onClick={() => runMut.mutate()}
          >
            {runMut.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Play className="mr-1.5 h-4 w-4" />}
            {runMut.isPending ? "Running…" : "Run"}
          </Button>
        </div>
        {runMut.isError && <div className="mt-2"><ErrorNote message={apiError(runMut.error)} /></div>}
        {result != null && (
          <Card className="mt-2 space-y-2 p-3" data-testid="tool-ci-result">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge variant="outline" className="text-[10px]" data-testid="tool-ci-result-status">{result.status || "unknown"}</Badge>
              {result.exit_code != null && <span className="font-mono text-muted-foreground" data-testid="tool-ci-exit">exit {result.exit_code}</span>}
              {result.duration_ms != null && <span className="font-mono text-muted-foreground" data-testid="tool-ci-duration">{result.duration_ms} ms</span>}
            </div>
            <div>
              <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">stdout</span>
              <pre className="mt-0.5 max-h-40 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[11px] scrollbar-thin" data-testid="tool-ci-stdout">{result.stdout || "(no output)"}</pre>
            </div>
            <div>
              <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">stderr</span>
              <pre className="mt-0.5 max-h-40 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[11px] scrollbar-thin" data-testid="tool-ci-stderr">{result.stderr || "(empty)"}</pre>
            </div>
            {files.length > 0 && (
              <div data-testid="tool-ci-files">
                <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Generated files ({files.length})</span>
                <ul className="mt-1 space-y-0.5 font-mono text-[11px] text-muted-foreground">
                  {files.map((f) => <li key={f}>{f}</li>)}
                </ul>
              </div>
            )}
          </Card>
        )}
      </div>
    </div>
  );
};

export const ToolDetailDialog: React.FC<ToolDetailDialogProps> = ({ tool, onClose, ciStatus, projectIdForTest, availableProjects }) => {
  const [tab, setTab] = useState("overview");
  React.useEffect(() => {
    if (tool) setTab(isCodeInterpreter(tool) ? "runtime" : "overview");
  }, [tool?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const isCI = tool ? isCodeInterpreter(tool) : false;
  const impl: any = tool?.implementation_config || tool?.implementation || null;
  const method = impl?.method;
  const url = impl?.url_template || impl?.url;
  return (
    <Dialog open={tool != null} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl" data-testid="tool-detail-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">{tool?.display_name || tool?.name || "Tool"}</DialogTitle>
          <DialogDescription className="font-mono text-xs">
            {tool?.name}{tool?.version != null && tool.version !== "" ? ` · ${versionLabelOf(tool)}` : ""}
          </DialogDescription>
        </DialogHeader>
        {tool && (
          <div className="space-y-4 text-sm">
            <div className="flex flex-wrap items-center gap-2" data-testid="tool-detail-badges">
              <Badge variant="outline" className="text-[10px]">{toolTypeLabel(tool.tool_type)}</Badge>
              <ScopeBadge tool={tool} />
              <RiskBadge risk={tool.risk_level} testId="tool-detail-risk" />
              {(tool.permissions || []).map((perm: string) => <Badge key={perm} variant="outline" className="font-mono text-[10px]">{perm}</Badge>)}
            </div>
            {tool.description && <p className="text-xs text-muted-foreground">{tool.description}</p>}
            {(method || url) && (
              <div className="rounded-md border border-border/60 p-2.5" data-testid="tool-detail-endpoint">
                <p className="break-all font-mono text-xs">
                  {method && <span className="mr-2 rounded bg-muted px-1.5 py-0.5">{method}</span>}
                  {url || "—"}
                </p>
              </div>
            )}
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList data-testid="tool-detail-tabs">
                {!isCI && <TabsTrigger value="overview" data-testid="tool-detail-tab-overview">Overview</TabsTrigger>}
                {isCI && <TabsTrigger value="runtime" data-testid="tool-detail-tab-runtime">Runtime</TabsTrigger>}
                <TabsTrigger value="schema" data-testid="tool-detail-tab-schema">Schema</TabsTrigger>
                {!isCI && <TabsTrigger value="versions" data-testid="tool-detail-tab-versions">Versions</TabsTrigger>}
                {isCI && <TabsTrigger value="versions" data-testid="tool-detail-tab-versions">Versions</TabsTrigger>}
                {!isCI && <TabsTrigger value="test" data-testid="tool-detail-tab-test">Test</TabsTrigger>}
              </TabsList>
              {!isCI && (
                <TabsContent value="overview" className="mt-3 space-y-2">
                  <div>
                    <h4 className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Input schema</h4>
                    <pre className="max-h-48 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[10px] scrollbar-thin" data-testid="tool-detail-input-schema">{JSON.stringify(tool.input_schema || {}, null, 2)}</pre>
                  </div>
                  <div>
                    <h4 className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Output schema</h4>
                    <pre className="max-h-48 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[10px] scrollbar-thin">{JSON.stringify(tool.output_schema || {}, null, 2)}</pre>
                  </div>
                </TabsContent>
              )}
              {isCI && (
                <TabsContent value="runtime" className="mt-3">
                  <CodeInterpreterTab tool={tool} ciStatus={ciStatus} projectIdForTest={projectIdForTest} availableProjects={availableProjects} />
                </TabsContent>
              )}
              <TabsContent value="schema" className="mt-3 space-y-2">
                <div>
                  <h4 className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Input schema</h4>
                  <pre className="max-h-48 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[10px] scrollbar-thin">{JSON.stringify(tool.input_schema || {}, null, 2)}</pre>
                </div>
                <div>
                  <h4 className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Output schema</h4>
                  <pre className="max-h-48 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[10px] scrollbar-thin">{JSON.stringify(tool.output_schema || {}, null, 2)}</pre>
                </div>
              </TabsContent>
              <TabsContent value="versions" className="mt-3">
                <VersionsTab tool={tool} />
              </TabsContent>
              {!isCI && (
                <TabsContent value="test" className="mt-3">
                  <ToolTestTab tool={tool} projectIdForTest={projectIdForTest} availableProjects={availableProjects} />
                </TabsContent>
              )}
            </Tabs>
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

// Re-export the raw api helper for tests that stub `@/lib/api`.
export { api };
