import React, { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { KeyRound, Plus } from "lucide-react";
import { apiError, createToolCredential, updateToolCredential, deleteToolCredential } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";

export interface CredentialsPanelProps {
  projectId?: string | null;
  availableProjects: { id: string; name: string }[];
  credentials: any[];
  isLoading: boolean;
  isError: boolean;
  error: any;
  tools: any[];
}

const TYPES = ["API_KEY", "BEARER_TOKEN", "BASIC_AUTH", "CUSTOM_HEADER"];

// Vault UI: create / enter-secret-once / update-replace / delete-unused /
// which-tools-reference. Saved secrets are never revealed — only the
// bullet mask plus a "Stored" marker and a "Used by N tools" count.
export const CredentialsPanel: React.FC<CredentialsPanelProps> = ({
  projectId, availableProjects, credentials, isLoading, isError, error, tools,
}) => {
  const qc = useQueryClient();
  const [project, setProject] = useState(projectId || "");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [credType, setCredType] = useState("API_KEY");
  const [value, setValue] = useState("");
  const [editingValue, setEditingValue] = useState<Record<string, string>>({});
  const [credError, setCredError] = useState<string | null>(null);

  React.useEffect(() => {
    if (projectId) setProject(projectId);
  }, [projectId]);

  const targetProject = projectId || project;

  // Count tool versions referencing each credential by id or {{secret:NAME}}.
  const usage = useMemo(() => {
    const counts: Record<string, { count: number; tools: string[] }> = {};
    for (const c of credentials) counts[c.id] = { count: 0, tools: [] };
    const nameToId: Record<string, string> = {};
    for (const c of credentials) nameToId[String(c.name)] = c.id;
    for (const t of tools || []) {
      const cfg = (t as any)?.implementation_config || (t as any)?.implementation || {};
      const hay = JSON.stringify(cfg);
      for (const c of credentials) {
        const hitId = typeof c.id === "string" && hay.includes(c.id);
        const hitName = hay.includes(`{{secret:${c.name}}}`) || hay.includes(`[secret:${c.name}]`);
        if (hitId || hitName) {
          if (!counts[c.id].tools.includes(t.display_name || t.name)) {
            counts[c.id].tools.push(t.display_name || t.name);
            counts[c.id].count += 1;
          }
        }
      }
    }
    void nameToId;
    return counts;
  }, [credentials, tools]);

  const createMut = useMutation({
    mutationFn: async () =>
      createToolCredential({ project_id: targetProject, name: name.trim(), credential_type: credType, value }),
    onSuccess: () => {
      toast.success("Credential stored");
      setOpen(false);
      setName("");
      setValue("");
      setCredError(null);
      qc.invalidateQueries({ queryKey: ["tool-credentials"] });
    },
    onError: (e) => {
      const msg = apiError(e);
      setCredError(msg);
      toast.error(msg);
    },
  });

  const rotateMut = useMutation({
    mutationFn: async ({ id, v }: { id: string; v: string }) => updateToolCredential(id, { value: v }),
    onSuccess: () => {
      toast.success("Credential value replaced");
      setEditingValue({});
      setCredError(null);
      qc.invalidateQueries({ queryKey: ["tool-credentials"] });
    },
    onError: (e) => {
      const msg = apiError(e);
      setCredError(msg);
      toast.error(msg);
    },
  });

  const delMut = useMutation({
    mutationFn: async (id: string) => deleteToolCredential(id),
    onSuccess: () => {
      toast.success("Credential deleted");
      setCredError(null);
      qc.invalidateQueries({ queryKey: ["tool-credentials"] });
    },
    onError: (e) => {
      const msg = apiError(e);
      setCredError(msg);
      toast.error(msg);
    },
  });

  return (
    <Card className="p-4" data-testid="tool-credentials">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 font-display text-sm font-semibold">
          <KeyRound className="h-4 w-4" /> Credentials
        </h3>
        <Button size="sm" variant="outline" data-testid="credential-create" onClick={() => setOpen(true)}>
          <Plus className="mr-1 h-3.5 w-3.5" /> New
        </Button>
      </div>
      {!projectId && (
        <div className="mb-2 max-w-xs">
          <label className="text-xs font-medium text-muted-foreground">Project scope</label>
          <Select value={project} onValueChange={setProject}>
            <SelectTrigger className="mt-1" data-testid="credentials-project-select"><SelectValue placeholder="Select project" /></SelectTrigger>
            <SelectContent>
              {availableProjects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      )}
      {isLoading ? (
        <LoadingSpinner label="Loading credentials…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : credentials.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="credentials-empty">
          {targetProject
            ? "No credentials stored. Values are entered once, encrypted server-side, and never returned."
            : "Select a project to view its credential vault."}
        </p>
      ) : (
        <>
          {credError && <p className="mb-1.5 text-[11px] text-destructive" data-testid="credential-error">{credError}</p>}
          <div className="space-y-1.5">
            {credentials.map((c: any) => {
              const used = usage[c.id];
              return (
                <div key={c.id} className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 px-2 py-1.5 text-xs" data-testid={`credential-${c.id}`}>
                  <span className="font-mono font-medium">{c.name}</span>
                  <Badge variant="outline" className="text-[10px]">{c.credential_type}</Badge>
                  {/* Masked value display — the real secret never leaves the server. */}
                  <span className="font-mono text-muted-foreground" data-testid={`credential-masked-${c.id}`}>&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;</span>
                  <Badge variant="secondary" className="text-[10px]" data-testid={`credential-stored-${c.id}`}>Stored</Badge>
                  <Badge variant="outline" className="text-[10px]" data-testid={`credential-usage-${c.id}`} title={(used?.tools || []).join(", ") || "No tool references detected"}>
                    Used by {used?.count || 0} tool{(used?.count || 0) === 1 ? "" : "s"}
                  </Badge>
                  {editingValue[c.id] !== undefined ? (
                    <>
                      <Input type="password" value={editingValue[c.id]} onChange={(e) => setEditingValue((p) => ({ ...p, [c.id]: e.target.value }))} className="h-7 font-mono text-xs" placeholder="new secret value" data-testid={`credential-rotate-input-${c.id}`} />
                      <Button size="sm" data-testid={`credential-rotate-save-${c.id}`} disabled={!editingValue[c.id] || rotateMut.isPending} onClick={() => rotateMut.mutate({ id: c.id, v: editingValue[c.id] })}>Save</Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditingValue((p) => { const n = { ...p }; delete n[c.id]; return n; })}>Cancel</Button>
                    </>
                  ) : (
                    <>
                      <button type="button" className="ml-auto text-[11px] text-muted-foreground hover:text-foreground" data-testid={`credential-rotate-${c.id}`} onClick={() => setEditingValue((p) => ({ ...p, [c.id]: "" }))}>Update value</button>
                      <button type="button" className="text-[11px] text-destructive" data-testid={`credential-delete-${c.id}`} onClick={() => delMut.mutate(c.id)}>Delete</button>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent data-testid="credential-create-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">New credential</DialogTitle>
            <DialogDescription>POST /tool-credentials — the secret is entered once, encrypted server-side, and never returned.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {!projectId && (
              <div>
                <label className="text-xs font-medium text-muted-foreground">Project</label>
                <Select value={project} onValueChange={setProject}>
                  <SelectTrigger className="mt-1" data-testid="credential-project-select"><SelectValue placeholder="Select project" /></SelectTrigger>
                  <SelectContent>
                    {availableProjects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            )}
            <div>
              <label className="text-xs font-medium text-muted-foreground">Name</label>
              <Input data-testid="credential-name-input" value={name} onChange={(e) => setName(e.target.value)} className="mt-1 font-mono text-xs" placeholder="MY_API_KEY" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Type</label>
              <Select value={credType} onValueChange={setCredType}>
                <SelectTrigger className="mt-1" data-testid="credential-type-select"><SelectValue /></SelectTrigger>
                <SelectContent>{TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Secret value (entered once)</label>
              <Input data-testid="credential-value-input" type="password" value={value} onChange={(e) => setValue(e.target.value)} className="mt-1 font-mono text-xs" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button data-testid="credential-create-submit" disabled={!name.trim() || !value || !targetProject || createMut.isPending} onClick={() => createMut.mutate()}>
              {createMut.isPending ? "Storing…" : "Store"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
};
