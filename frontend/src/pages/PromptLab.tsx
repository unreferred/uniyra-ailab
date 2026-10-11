import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Archive, ChevronDown, ChevronRight, Eye, FileCode2, Pencil, Plus, Sparkles, Trash2,
} from "lucide-react";
import {
  apiError,
  createProjectInvariant,
  createProjectPolicy,
  createProjectPolicyVersion,
  createPrompt,
  createPromptBlock,
  createStarterPromptBlocks,
  deleteProjectInvariant,
  deletePrompt,
  deletePromptBlock,
  listProjectPolicyVersions,
  updateProjectInvariant,
  updateProjectPolicyStatus,
  updatePrompt,
  updatePromptBlock,
} from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import {
  useInvariantCatalog,
  usePolicyCatalog,
  useProjectInvariants,
  useProjectPolicies,
  usePromptBlockCatalog,
  usePromptBlocks,
  usePromptSpecs,
} from "@/lib/hooks";
import { CatalogOption, ProjectInvariant, ProjectPolicy, PromptBlock, PromptSpec } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState, ErrorNote, LoadingSpinner, SectionHeader } from "@/components/common/Common";
import { nText } from "@/components/experiment/format";

const ALL = "__all__";
const SPEC_STATUSES = ["draft", "active", "deprecated", "archived"];

type Opt = { value: string; label: string };

function asArray<T = any>(data: any): T[] {
  if (Array.isArray(data)) return data as T[];
  if (Array.isArray(data?.items)) return data.items as T[];
  if (Array.isArray(data?.results)) return data.results as T[];
  return [];
}

function toOptions(list?: CatalogOption[] | null): Opt[] {
  if (!Array.isArray(list)) return [];
  const out: Opt[] = [];
  for (const item of list) {
    if (item == null) continue;
    if (typeof item === "string") out.push({ value: item, label: item });
    else if (item.key) out.push({ value: item.key, label: item.label || item.key });
  }
  return out;
}

function domainOptions(list?: { key?: string | null; label?: string | null }[] | null): Opt[] {
  if (!Array.isArray(list)) return [];
  const out: Opt[] = [];
  for (const item of list) {
    if (item && item.key) out.push({ value: item.key, label: item.label || item.key });
  }
  return out;
}

function isoDate(value?: string | null): string {
  if (!value) return "n/a";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "n/a" : d.toLocaleDateString();
}

const StatusBadge: React.FC<{ value?: string | null }> = ({ value }) => (
  <Badge variant="outline" className="text-[10px]">{nText(value)}</Badge>
);

// ---------------------------------------------------------------------------
// Prompts (prompt specs)
// ---------------------------------------------------------------------------

const EMPTY_SPEC = { name: "", description: "", purpose: "", status: "draft" };

const PromptsTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data, isLoading, isError, error } = usePromptSpecs(projectId);
  const specs = asArray<PromptSpec>(data);

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<PromptSpec | null>(null);
  const [form, setForm] = useState(EMPTY_SPEC);
  const [deleteTarget, setDeleteTarget] = useState<PromptSpec | null>(null);

  const reset = () => { setForm(EMPTY_SPEC); setEditing(null); };
  const openCreate = () => { reset(); setOpen(true); };
  const openEdit = (s: PromptSpec) => {
    setEditing(s);
    setForm({
      name: s.name || "",
      description: s.description || "",
      purpose: s.purpose || "",
      status: s.status || "draft",
    });
    setOpen(true);
  };

  const invalidate = () => qc.invalidateQueries({ queryKey: ["prompt-specs", projectId] });

  const createMut = useMutation({
    mutationFn: async () => createPrompt({
      project_id: projectId,
      name: form.name.trim(),
      description: form.description || undefined,
      purpose: form.purpose || undefined,
      status: form.status,
    }),
    onSuccess: () => { toast.success("Prompt created"); invalidate(); setOpen(false); reset(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const updateMut = useMutation({
    mutationFn: async () => updatePrompt(editing!.id as string, {
      name: form.name.trim(),
      description: form.description,
      purpose: form.purpose,
      status: form.status,
    }),
    onSuccess: () => { toast.success("Prompt updated"); invalidate(); setOpen(false); reset(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const deleteMut = useMutation({
    mutationFn: async (id: string) => deletePrompt(id),
    onSuccess: () => { toast.success("Prompt deleted"); invalidate(); setDeleteTarget(null); },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">Named prompt specifications for this project.</p>
        <Button size="sm" data-testid="prompt-spec-create" onClick={openCreate}>
          <Plus className="mr-1.5 h-4 w-4" /> New prompt
        </Button>
      </div>

      {isLoading ? (
        <LoadingSpinner label="Loading prompts…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : specs.length === 0 ? (
        <EmptyState
          icon={<FileCode2 className="h-6 w-6" />}
          title="No prompt specs"
          description="Create a prompt specification to organise its purpose and lifecycle."
          testId="prompt-specs-empty"
        />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table data-testid="prompt-spec-table">
            <TableHeader>
              <TableRow className="bg-muted/50">
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Name</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Purpose</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Status</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Versions</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Updated</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {specs.map((s, i) => (
                <TableRow key={s.id || i} data-testid="prompt-spec-row">
                  <TableCell className="text-xs font-medium" data-testid="prompt-spec-name">{nText(s.name)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{nText(s.purpose)}</TableCell>
                  <TableCell><StatusBadge value={s.status} /></TableCell>
                  <TableCell className="font-mono text-xs tabular">{nText(s.versions_count)}</TableCell>
                  <TableCell className="font-mono text-[11px] text-muted-foreground">{isoDate(s.updated_at)}</TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        data-testid="prompt-spec-view"
                        disabled={!s.id}
                        onClick={() => s.id && navigate(`/prompt-lab/prompts/${s.id}`)}
                      >
                        <Eye className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" data-testid="prompt-spec-edit" onClick={() => openEdit(s)}>
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" className="text-destructive" data-testid="prompt-spec-delete" onClick={() => setDeleteTarget(s)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) reset(); }}>
        <DialogContent data-testid="prompt-spec-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">{editing ? "Edit prompt spec" : "New prompt spec"}</DialogTitle>
            <DialogDescription>Prompt specs carry a name, purpose and lifecycle status. Versions are managed server-side.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Name</label>
              <Input data-testid="prompt-spec-name-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Purpose</label>
              <Input data-testid="prompt-spec-purpose-input" value={form.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value })} className="mt-1" placeholder="e.g. research synthesis" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Description</label>
              <Textarea data-testid="prompt-spec-description-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={3} className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Status</label>
              <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
                <SelectTrigger className="mt-1" data-testid="prompt-spec-status-input"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {SPEC_STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => { setOpen(false); reset(); }}>Cancel</Button>
            {editing ? (
              <Button data-testid="prompt-spec-submit" disabled={!form.name.trim() || updateMut.isPending} onClick={() => updateMut.mutate()}>
                {updateMut.isPending ? "Saving…" : "Save changes"}
              </Button>
            ) : (
              <Button data-testid="prompt-spec-submit" disabled={!form.name.trim() || createMut.isPending} onClick={() => createMut.mutate()}>
                {createMut.isPending ? "Saving…" : "Create prompt"}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleteTarget} onOpenChange={(v) => !v && setDeleteTarget(null)}>
        <AlertDialogContent data-testid="prompt-spec-delete-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete prompt spec</AlertDialogTitle>
            <AlertDialogDescription>
              Delete "{nText(deleteTarget?.name)}"? This removes the specification and its versions.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid="prompt-spec-delete-confirm"
              onClick={(e) => { e.preventDefault(); if (deleteTarget?.id) deleteMut.mutate(deleteTarget.id); }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Blocks
// ---------------------------------------------------------------------------

const EMPTY_BLOCK = { name: "", category: "", description: "", content: "", variablesText: "", status: "active" };

const BlocksTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const qc = useQueryClient();
  const [filterCategory, setFilterCategory] = useState<string>(ALL);
  const { data, isLoading, isError, error } = usePromptBlocks(projectId, filterCategory === ALL ? undefined : filterCategory);
  const { data: catalog } = usePromptBlockCatalog();
  const blocks = asArray<PromptBlock>(data);
  const categoryOptions = domainOptions(catalog?.categories);
  const statusOptions = toOptions(catalog?.statuses);

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<PromptBlock | null>(null);
  const [form, setForm] = useState(EMPTY_BLOCK);
  const [variablesError, setVariablesError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PromptBlock | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["prompt-blocks", projectId] });

  const openCreate = () => {
    setEditing(null);
    setVariablesError(null);
    setForm({
      name: "", category: categoryOptions[0]?.value || "", description: "", content: "",
      variablesText: "", status: statusOptions[0]?.value || "active",
    });
    setOpen(true);
  };

  const openEdit = (b: PromptBlock) => {
    setEditing(b);
    setVariablesError(null);
    setForm({
      name: b.name || "",
      category: b.category || "",
      description: b.description || "",
      content: b.content || "",
      variablesText: b.variables ? JSON.stringify(b.variables, null, 2) : "",
      status: b.status || "active",
    });
    setOpen(true);
  };

  const step = "mt-1";
  const createMut = useMutation({
    mutationFn: async (variables: any) => createPromptBlock({
      project_id: projectId,
      name: form.name.trim(),
      category: form.category,
      description: form.description || undefined,
      content: form.content || undefined,
      variables,
      status: form.status,
    }),
    onSuccess: () => { toast.success("Block created"); invalidate(); setOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const updateMut = useMutation({
    mutationFn: async (variables: any) => updatePromptBlock(editing!.id as string, {
      name: form.name.trim(),
      category: form.category,
      description: form.description,
      content: form.content,
      variables,
      status: form.status,
    }),
    onSuccess: () => { toast.success("Block updated"); invalidate(); setOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const deleteMut = useMutation({
    mutationFn: async (id: string) => deletePromptBlock(id),
    onSuccess: () => { toast.success("Block deleted"); invalidate(); setDeleteTarget(null); },
    onError: (e) => toast.error(apiError(e)),
  });

  const starterMut = useMutation({
    mutationFn: async () => createStarterPromptBlocks(projectId),
    onSuccess: (res: any) => {
      const created = Array.isArray(res?.created) ? res.created.length : null;
      const existing = Array.isArray(res?.existing) ? res.existing.length : null;
      if (created === null && existing === null) toast.success("Starter blocks added");
      else toast.success(`Starter blocks: ${created ?? 0} created, ${existing ?? 0} already present`);
      invalidate();
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const submit = () => {
    const vt = form.variablesText.trim();
    let variables: any;
    if (vt) {
      try {
        variables = JSON.parse(vt);
      } catch {
        setVariablesError("Variables must be valid JSON.");
        return;
      }
      if (variables === null || typeof variables !== "object" || Array.isArray(variables)) {
        setVariablesError("Variables must be a JSON object.");
        return;
      }
    }
    setVariablesError(null);
    if (editing) updateMut.mutate(variables);
    else createMut.mutate(variables);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Select value={filterCategory} onValueChange={setFilterCategory}>
            <SelectTrigger className="h-8 w-56" data-testid="prompt-block-category"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All categories</SelectItem>
              {categoryOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" data-testid="prompt-block-starters" disabled={starterMut.isPending} onClick={() => starterMut.mutate()}>
            <Sparkles className="mr-1.5 h-4 w-4" /> Add starter blocks
          </Button>
          <Button size="sm" data-testid="prompt-block-create" onClick={openCreate}>
            <Plus className="mr-1.5 h-4 w-4" /> New block
          </Button>
        </div>
      </div>

      {isLoading ? (
        <LoadingSpinner label="Loading blocks…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : blocks.length === 0 ? (
        <EmptyState
          icon={<FileCode2 className="h-6 w-6" />}
          title="No blocks"
          description="Add reusable prompt blocks, or seed a starter set from the catalog."
          testId="prompt-blocks-empty"
        />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table data-testid="prompt-block-table">
            <TableHeader>
              <TableRow className="bg-muted/50">
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Name</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Category</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Version</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Status</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {blocks.map((b, i) => (
                <TableRow key={b.id || i} data-testid="prompt-block-row">
                  <TableCell className="text-xs font-medium" data-testid="prompt-block-name">{nText(b.name)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{nText(b.category)}</TableCell>
                  <TableCell className="font-mono text-xs tabular">{nText(b.version)}</TableCell>
                  <TableCell><StatusBadge value={b.status} /></TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button size="sm" variant="ghost" data-testid="prompt-block-edit" onClick={() => openEdit(b)}>
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" className="text-destructive" data-testid="prompt-block-delete" onClick={() => setDeleteTarget(b)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) { setEditing(null); setVariablesError(null); } }}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto" data-testid="prompt-block-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">{editing ? "Edit block" : "New block"}</DialogTitle>
            <DialogDescription>Blocks are composable prompt fragments with optional template variables.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Name</label>
              <Input data-testid="prompt-block-name-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={step} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Category</label>
              <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
                <SelectTrigger className={step} data-testid="prompt-block-category-input"><SelectValue placeholder="Select category" /></SelectTrigger>
                <SelectContent>
                  {categoryOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Description</label>
              <Textarea data-testid="prompt-block-description-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={2} className={step} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Content</label>
              <Textarea data-testid="prompt-block-content-input" value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} rows={5} className={step} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Variables (JSON)</label>
              <Textarea data-testid="prompt-block-variables-input" value={form.variablesText} onChange={(e) => setForm({ ...form, variablesText: e.target.value })} rows={3} className={`${step} font-mono text-xs`} placeholder='{"topic": "string"}' />
              <ErrorNote className="mt-1" message={variablesError} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Status</label>
              <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
                <SelectTrigger className={step} data-testid="prompt-block-status-input"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(statusOptions.length > 0 ? statusOptions : [{ value: "active", label: "active" }]).map((o) => (
                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => { setOpen(false); setEditing(null); }}>Cancel</Button>
            <Button
              data-testid="prompt-block-submit"
              disabled={!form.name.trim() || !form.category || createMut.isPending || updateMut.isPending}
              onClick={submit}
            >
              {editing ? "Save changes" : "Create block"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleteTarget} onOpenChange={(v) => !v && setDeleteTarget(null)}>
        <AlertDialogContent data-testid="prompt-block-delete-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete block</AlertDialogTitle>
            <AlertDialogDescription>Delete "{nText(deleteTarget?.name)}"? This cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid="prompt-block-delete-confirm"
              onClick={(e) => { e.preventDefault(); if (deleteTarget?.id) deleteMut.mutate(deleteTarget.id); }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Policies
// ---------------------------------------------------------------------------

const PoliciesTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const qc = useQueryClient();
  const { data, isLoading, isError, error } = useProjectPolicies(projectId);
  const { data: catalog } = usePolicyCatalog();
  const policies = asArray<ProjectPolicy>(data);
  const domainOpts = domainOptions(catalog?.domains);

  const [createOpen, setCreateOpen] = useState(false);
  const [versionTarget, setVersionTarget] = useState<ProjectPolicy | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [versions, setVersions] = useState<Record<string, ProjectPolicy[]>>({});
  const [loadingVersions, setLoadingVersions] = useState<string | null>(null);

  const [form, setForm] = useState({ name: "", domain: "", description: "", content: "" });
  const [versionForm, setVersionForm] = useState({ content: "", description: "" });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["project-policies", projectId] });

  const openCreate = () => {
    setForm({ name: "", domain: domainOpts[0]?.value || "", description: "", content: "" });
    setCreateOpen(true);
  };

  const createMut = useMutation({
    mutationFn: async () => createProjectPolicy({
      project_id: projectId,
      name: form.name.trim(),
      domain: form.domain,
      description: form.description || undefined,
      content: form.content || undefined,
    }),
    onSuccess: () => { toast.success("Policy created"); invalidate(); setCreateOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const openVersion = (p: ProjectPolicy) => {
    setVersionForm({ content: "", description: "" });
    setVersionTarget(p);
  };

  const versionMut = useMutation({
    mutationFn: async () => createProjectPolicyVersion(versionTarget!.id as string, {
      content: versionForm.content,
      description: versionForm.description || undefined,
    }),
    onSuccess: (_res: any) => {
      toast.success("New policy version created");
      invalidate();
      if (versionTarget?.id) {
        setVersions((v) => { const next = { ...v }; delete next[versionTarget.id as string]; return next; });
        void loadVersions(versionTarget.id, true);
        setExpandedId(versionTarget.id);
      }
      setVersionTarget(null);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const archiveMut = useMutation({
    mutationFn: async (id: string) => updateProjectPolicyStatus(id, "archived"),
    onSuccess: () => { toast.success("Policy archived"); invalidate(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const loadVersions = async (id: string, force = false) => {
    if (!force && (versions[id] || loadingVersions === id)) return;
    setLoadingVersions(id);
    try {
      const res = await listProjectPolicyVersions(id);
      setVersions((v) => ({ ...v, [id]: asArray<ProjectPolicy>(res) }));
    } catch (e) {
      toast.error(apiError(e));
    } finally {
      setLoadingVersions(null);
    }
  };

  const toggleVersions = (id: string) => {
    if (expandedId === id) { setExpandedId(null); return; }
    setExpandedId(id);
    void loadVersions(id);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">Project policies are versioned content; the list shows the latest version per family.</p>
        <Button size="sm" data-testid="prompt-policy-create" onClick={openCreate}>
          <Plus className="mr-1.5 h-4 w-4" /> New policy
        </Button>
      </div>

      {isLoading ? (
        <LoadingSpinner label="Loading policies…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : policies.length === 0 ? (
        <EmptyState
          icon={<FileCode2 className="h-6 w-6" />}
          title="No policies"
          description="Create a project policy to define rules that govern this project."
          testId="prompt-policies-empty"
        />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table data-testid="prompt-policy-table">
            <TableHeader>
              <TableRow className="bg-muted/50">
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Name</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Domain</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Version</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Status</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {policies.map((p, i) => {
                const id = p.id as string;
                return (
                  <TableRow key={p.id || i} data-testid="prompt-policy-row">
                    <TableCell className="text-xs font-medium" data-testid="prompt-policy-name">{nText(p.name)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{nText(p.domain)}</TableCell>
                    <TableCell className="font-mono text-xs tabular">{nText(p.version)}</TableCell>
                    <TableCell><StatusBadge value={p.status} /></TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        <Button size="sm" variant="outline" data-testid="prompt-policy-new-version" onClick={() => openVersion(p)}>
                          New version
                        </Button>
                        <Button size="sm" variant="ghost" data-testid="prompt-policy-versions-toggle" onClick={() => toggleVersions(id)}>
                          {expandedId === id ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                          <span className="ml-1 text-[11px]">Versions</span>
                        </Button>
                        {p.status !== "archived" && (
                          <Button size="sm" variant="ghost" className="text-muted-foreground" data-testid="prompt-policy-archive" onClick={() => archiveMut.mutate(id)}>
                            <Archive className="h-3.5 w-3.5" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {expandedId && (
        <Card className="space-y-2 p-4" data-testid="prompt-policy-versions">
          <h3 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Version history</h3>
          {loadingVersions === expandedId ? (
            <LoadingSpinner label="Loading versions…" />
          ) : (versions[expandedId] || []).length === 0 ? (
            <p className="text-xs text-muted-foreground">No versions returned.</p>
          ) : (
            <ul className="divide-y divide-border">
              {(versions[expandedId] || []).map((v, i) => (
                <li key={v.id || i} className="flex items-center gap-3 py-1.5 text-xs" data-testid="prompt-policy-version-row">
                  <span className="font-mono">v{nText(v.version)}</span>
                  <StatusBadge value={v.status} />
                  <span className="font-mono text-[10px] text-muted-foreground">{isoDate(v.created_at)}</span>
                  {v.description && <span className="min-w-0 flex-1 truncate text-muted-foreground">{v.description}</span>}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto" data-testid="prompt-policy-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">New policy</DialogTitle>
            <DialogDescription>Create a policy family. Subsequent content changes are new versions.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Name</label>
              <Input data-testid="prompt-policy-name-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Domain</label>
              <Select value={form.domain} onValueChange={(v) => setForm({ ...form, domain: v })}>
                <SelectTrigger className="mt-1" data-testid="prompt-policy-domain-input"><SelectValue placeholder="Select domain" /></SelectTrigger>
                <SelectContent>
                  {domainOpts.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Description</label>
              <Textarea data-testid="prompt-policy-description-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={2} className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Content</label>
              <Textarea data-testid="prompt-policy-content-input" value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} rows={5} className="mt-1" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>Cancel</Button>
            <Button data-testid="prompt-policy-submit" disabled={!form.name.trim() || !form.domain || createMut.isPending} onClick={() => createMut.mutate()}>
              {createMut.isPending ? "Saving…" : "Create policy"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!versionTarget} onOpenChange={(v) => !v && setVersionTarget(null)}>
        <DialogContent data-testid="prompt-policy-new-version-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">New version</DialogTitle>
            <DialogDescription>Append a new immutable version of "{nText(versionTarget?.name)}".</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Content</label>
              <Textarea data-testid="prompt-policy-version-content" value={versionForm.content} onChange={(e) => setVersionForm({ ...versionForm, content: e.target.value })} rows={5} className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Description (optional)</label>
              <Input data-testid="prompt-policy-version-description" value={versionForm.description} onChange={(e) => setVersionForm({ ...versionForm, description: e.target.value })} className="mt-1" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setVersionTarget(null)}>Cancel</Button>
            <Button data-testid="prompt-policy-new-version-submit" disabled={!versionForm.content.trim() || versionMut.isPending} onClick={() => versionMut.mutate()}>
              {versionMut.isPending ? "Saving…" : "Create version"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Invariants
// ---------------------------------------------------------------------------

const EMPTY_INVARIANT = {
  code: "", domain: "", title: "", description: "", severity: "", enforcement_mode: "", status: "active",
};

const InvariantsTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const qc = useQueryClient();
  const { data, isLoading, isError, error } = useProjectInvariants(projectId);
  const { data: catalog } = useInvariantCatalog();
  const invariants = asArray<ProjectInvariant>(data);

  const domainOpts = domainOptions(catalog?.domains);
  const severityOpts = toOptions(catalog?.severities);
  const enforcementOpts = toOptions(catalog?.enforcement_modes);
  const statusOpts = toOptions(catalog?.statuses);

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ProjectInvariant | null>(null);
  const [form, setForm] = useState(EMPTY_INVARIANT);
  const [deleteTarget, setDeleteTarget] = useState<ProjectInvariant | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["project-invariants", projectId] });

  const openCreate = () => {
    setEditing(null);
    setForm({
      ...EMPTY_INVARIANT,
      domain: domainOpts[0]?.value || "",
      severity: severityOpts[0]?.value || "",
      enforcement_mode: enforcementOpts[0]?.value || "",
      status: statusOpts[0]?.value || "active",
    });
    setOpen(true);
  };

  const openEdit = (inv: ProjectInvariant) => {
    setEditing(inv);
    setForm({
      code: inv.code || "",
      domain: inv.domain || "",
      title: inv.title || "",
      description: inv.description || "",
      severity: inv.severity || "",
      enforcement_mode: inv.enforcement_mode || "",
      status: inv.status || "active",
    });
    setOpen(true);
  };

  const createMut = useMutation({
    mutationFn: async () => createProjectInvariant({
      project_id: projectId,
      code: form.code.trim(),
      domain: form.domain,
      title: form.title.trim(),
      description: form.description || undefined,
      severity: form.severity || undefined,
      enforcement_mode: form.enforcement_mode || undefined,
      status: form.status || undefined,
    }),
    onSuccess: () => { toast.success("Invariant created"); invalidate(); setOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const updateMut = useMutation({
    mutationFn: async () => updateProjectInvariant(editing!.id as string, {
      code: form.code.trim(),
      domain: form.domain,
      title: form.title.trim(),
      description: form.description,
      severity: form.severity,
      enforcement_mode: form.enforcement_mode,
      status: form.status,
    }),
    onSuccess: () => { toast.success("Invariant updated"); invalidate(); setOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  const deleteMut = useMutation({
    mutationFn: async (id: string) => deleteProjectInvariant(id),
    onSuccess: () => { toast.success("Invariant deleted"); invalidate(); setDeleteTarget(null); },
    onError: (e) => toast.error(apiError(e)),
  });

  const canSubmit = form.code.trim() && form.domain && form.title.trim();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">Project invariants are enforced constraints with a severity and enforcement mode.</p>
        <Button size="sm" data-testid="prompt-invariant-create" onClick={openCreate}>
          <Plus className="mr-1.5 h-4 w-4" /> New invariant
        </Button>
      </div>

      {isLoading ? (
        <LoadingSpinner label="Loading invariants…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : invariants.length === 0 ? (
        <EmptyState
          icon={<FileCode2 className="h-6 w-6" />}
          title="No invariants"
          description="Define constraints the project must always satisfy."
          testId="prompt-invariants-empty"
        />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table data-testid="prompt-invariant-table">
            <TableHeader>
              <TableRow className="bg-muted/50">
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Code</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Domain</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Severity</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Enforcement</TableHead>
                <TableHead className="text-[10px] font-mono uppercase tracking-wider">Status</TableHead>
                <TableHead className="text-right text-[10px] font-mono uppercase tracking-wider">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invariants.map((inv, i) => (
                <TableRow key={inv.id || i} data-testid="prompt-invariant-row">
                  <TableCell className="font-mono text-xs" data-testid="prompt-invariant-code">{nText(inv.code)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{nText(inv.domain)}</TableCell>
                  <TableCell className="text-xs"><StatusBadge value={inv.severity} /></TableCell>
                  <TableCell className="text-xs"><StatusBadge value={inv.enforcement_mode} /></TableCell>
                  <TableCell className="text-xs"><StatusBadge value={inv.status} /></TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button size="sm" variant="ghost" data-testid="prompt-invariant-edit" onClick={() => openEdit(inv)}>
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" className="text-destructive" data-testid="prompt-invariant-delete" onClick={() => setDeleteTarget(inv)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) setEditing(null); }}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto" data-testid="prompt-invariant-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">{editing ? "Edit invariant" : "New invariant"}</DialogTitle>
            <DialogDescription>Invariants encode constraints with severity and enforcement mode.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Code</label>
                <Input data-testid="prompt-invariant-code-input" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} className="mt-1 font-mono" />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Domain</label>
                <Select value={form.domain} onValueChange={(v) => setForm({ ...form, domain: v })}>
                  <SelectTrigger className="mt-1" data-testid="prompt-invariant-domain-input"><SelectValue placeholder="Select domain" /></SelectTrigger>
                  <SelectContent>
                    {domainOpts.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Title</label>
              <Input data-testid="prompt-invariant-title-input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Description</label>
              <Textarea data-testid="prompt-invariant-description-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={3} className="mt-1" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Severity</label>
                <Select value={form.severity} onValueChange={(v) => setForm({ ...form, severity: v })}>
                  <SelectTrigger className="mt-1" data-testid="prompt-invariant-severity-input"><SelectValue placeholder="Select severity" /></SelectTrigger>
                  <SelectContent>
                    {severityOpts.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Enforcement mode</label>
                <Select value={form.enforcement_mode} onValueChange={(v) => setForm({ ...form, enforcement_mode: v })}>
                  <SelectTrigger className="mt-1" data-testid="prompt-invariant-enforcement-input"><SelectValue placeholder="Select mode" /></SelectTrigger>
                  <SelectContent>
                    {enforcementOpts.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Status</label>
              <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
                <SelectTrigger className="mt-1" data-testid="prompt-invariant-status-input"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {statusOpts.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => { setOpen(false); setEditing(null); }}>Cancel</Button>
            {editing ? (
              <Button data-testid="prompt-invariant-submit" disabled={!canSubmit || updateMut.isPending} onClick={() => updateMut.mutate()}>
                {updateMut.isPending ? "Saving…" : "Save changes"}
              </Button>
            ) : (
              <Button data-testid="prompt-invariant-submit" disabled={!canSubmit || createMut.isPending} onClick={() => createMut.mutate()}>
                {createMut.isPending ? "Saving…" : "Create invariant"}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleteTarget} onOpenChange={(v) => !v && setDeleteTarget(null)}>
        <AlertDialogContent data-testid="prompt-invariant-delete-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete invariant</AlertDialogTitle>
            <AlertDialogDescription>Delete "{nText(deleteTarget?.code)}"? This cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid="prompt-invariant-delete-confirm"
              onClick={(e) => { e.preventDefault(); if (deleteTarget?.id) deleteMut.mutate(deleteTarget.id); }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function PromptLab() {
  const { activeId, activeProject } = useProjects();

  if (!activeId) {
    return (
      <div className="mx-auto max-w-6xl p-4 lg:p-6" data-testid="prompt-lab-page">
        <EmptyState
          icon={<FileCode2 className="h-6 w-6" />}
          title="No project selected"
          description="Select a project to manage prompt specs, blocks, policies and invariants."
          testId="prompt-lab-no-project"
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="prompt-lab-page">
      <SectionHeader
        title="Prompt Lab"
        caption={`Prompt specs, composable blocks, project policies and invariants for "${activeProject?.name || "this project"}".`}
      />
      <Tabs defaultValue="specs">
        <TabsList data-testid="prompt-lab-tabs">
          <TabsTrigger value="specs" data-testid="prompt-lab-tab-specs">Prompts</TabsTrigger>
          <TabsTrigger value="blocks" data-testid="prompt-lab-tab-blocks">Blocks</TabsTrigger>
          <TabsTrigger value="policies" data-testid="prompt-lab-tab-policies">Policies</TabsTrigger>
          <TabsTrigger value="invariants" data-testid="prompt-lab-tab-invariants">Invariants</TabsTrigger>
        </TabsList>
        <TabsContent value="specs" className="mt-4"><PromptsTab projectId={activeId} /></TabsContent>
        <TabsContent value="blocks" className="mt-4"><BlocksTab projectId={activeId} /></TabsContent>
        <TabsContent value="policies" className="mt-4"><PoliciesTab projectId={activeId} /></TabsContent>
        <TabsContent value="invariants" className="mt-4"><InvariantsTab projectId={activeId} /></TabsContent>
      </Tabs>
    </div>
  );
}
