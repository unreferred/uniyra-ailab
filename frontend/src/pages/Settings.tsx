import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Archive, Trash2, Save, KeyRound, Server, Boxes, Wrench, Cpu } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useProjects } from "@/context/ProjectContext";
import { useCodeInterpreterStatus } from "@/lib/hooks";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { SectionHeader } from "@/components/common/Common";
import { SecretsSection } from "@/components/settings/SecretsSection";
import { initials } from "@/lib/format";
import { Link } from "react-router-dom";

export default function Settings() {
  const { user } = useAuth();
  const { activeProject, refresh, setActiveId, projects } = useProjects();
  const qc = useQueryClient();
  const [name, setName] = useState(activeProject?.name || "");
  const [description, setDescription] = useState(activeProject?.description || "");
  const [revealSecrets, setRevealSecrets] = useState(false);
  const { data: ciStatus } = useCodeInterpreterStatus();

  React.useEffect(() => { setName(activeProject?.name || ""); setDescription(activeProject?.description || ""); }, [activeProject?.id]); // eslint-disable-line

  const save = useMutation({
    mutationFn: async () => (await api.patch(`/projects/${activeProject!.id}`, { name, description })).data,
    onSuccess: () => { toast.success("Project updated"); refresh(); },
    onError: (e) => toast.error(apiError(e)),
  });
  const archive = useMutation({
    mutationFn: async () => (await api.patch(`/projects/${activeProject!.id}`, { status: activeProject!.status === "archived" ? "active" : "archived" })).data,
    onSuccess: () => { toast.success("Project status updated"); refresh(); },
    onError: (e) => toast.error(apiError(e)),
  });
  const del = useMutation({
    mutationFn: async () => (await api.delete(`/projects/${activeProject!.id}`)).data,
    onSuccess: () => { toast.success("Project deleted"); const rest = projects.filter((p) => p.id !== activeProject!.id); if (rest[0]) setActiveId(rest[0].id); refresh(); qc.invalidateQueries(); },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 lg:p-6">
      <SectionHeader title="Settings" caption="Manage your profile and the active project." />

      <Card className="p-5" data-testid="settings-profile">
        <h3 className="mb-4 font-display font-semibold">Profile</h3>
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">{initials(user?.name)}</div>
          <div>
            <div className="font-medium">{user?.name}</div>
            <div className="text-sm text-muted-foreground">{user?.email}</div>
          </div>
          <Badge variant="secondary" className="ml-auto">{user?.role}</Badge>
        </div>
      </Card>

      {/* Discoverability hub: every workspace dependency reachable without knowing API routes. */}
      <Card className="p-5" data-testid="settings-discover">
        <h3 className="mb-2 font-display font-semibold">Workspace setup</h3>
        <p className="text-sm text-muted-foreground">
          Everything the workspace needs — providers, models and their tool-calling
          capabilities, tool credentials, and the code-interpreter runtime.
        </p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2" data-testid="settings-links">
          <Link to="/models" data-testid="settings-link-providers" className="flex items-center gap-2 rounded-md border border-border/60 px-3 py-2 text-sm hover:bg-accent/50">
            <Server className="h-4 w-4 text-muted-foreground" /> Providers
            <span className="ml-auto text-[11px] text-muted-foreground">Models → Providers</span>
          </Link>
          <Link to="/models" data-testid="settings-link-models" className="flex items-center gap-2 rounded-md border border-border/60 px-3 py-2 text-sm hover:bg-accent/50">
            <Boxes className="h-4 w-4 text-muted-foreground" /> Models & capabilities
            <span className="ml-auto text-[11px] text-muted-foreground">Models → Models</span>
          </Link>
          <Link to="/settings" data-testid="settings-link-credentials" className="flex items-center gap-2 rounded-md border border-border/60 px-3 py-2 text-sm hover:bg-accent/50">
            <Wrench className="h-4 w-4 text-muted-foreground" /> Tool credentials
            <span className="ml-auto text-[11px] text-muted-foreground">Settings → Security & Secrets</span>
          </Link>
          <Link to="/tools" data-testid="settings-link-runtime" className="flex items-center gap-2 rounded-md border border-border/60 px-3 py-2 text-sm hover:bg-accent/50">
            <Cpu className="h-4 w-4 text-muted-foreground" /> Runtime status
            <span className="ml-auto font-mono text-[11px] text-muted-foreground" data-testid="settings-runtime-state">
              {ciStatus == null ? "checking…" : ciStatus.available === true ? "sandbox online" : "sandbox offline"}
            </span>
          </Link>
        </div>
      </Card>

      <Card className="p-5" data-testid="settings-tool-credentials">
        <h3 className="mb-2 font-display font-semibold">Security & Secrets (advanced)</h3>
        <p className="text-sm text-muted-foreground">
          Normal tool configuration happens with each tool (Tools → Configure). This is the
          encrypted vault for debugging and administration — secrets are entered once,
          encrypted server-side, and never shown again.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Link to="/tools" data-testid="settings-tools-link" className="text-sm text-primary hover:underline">
            Open Tools → Configure
          </Link>
          <Button size="sm" variant="outline" data-testid="settings-reveal-secrets" onClick={() => setRevealSecrets((v) => !v)}>
            {revealSecrets ? "Hide secrets vault" : "Reveal secrets vault"}
          </Button>
        </div>
        {revealSecrets && <div className="mt-3"><SecretsSection /></div>}
      </Card>

      <Card className="p-5" data-testid="settings-universal-key">
        <h3 className="mb-2 flex items-center gap-1.5 font-display font-semibold"><KeyRound className="h-4 w-4" /> Execution keys</h3>
        <p className="text-sm text-muted-foreground">
          Add your own OpenAI-compatible providers with BYOK keys from the <span className="font-medium">Models &rarr; Providers</span> tab. All keys are encrypted at rest and never exposed to the browser.
        </p>
      </Card>

      {activeProject && (
        <Card className="p-5" data-testid="settings-project">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="font-display font-semibold">Active project</h3>
            {activeProject.status === "archived" && <Badge variant="outline" className="text-destructive">archived</Badge>}
          </div>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Name</label>
              <Input data-testid="settings-project-name" value={name} onChange={(e) => setName(e.target.value)} className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Description</label>
              <Textarea data-testid="settings-project-description" value={description} onChange={(e) => setDescription(e.target.value)} rows={3} className="mt-1" />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" data-testid="settings-save-project" onClick={() => save.mutate()} disabled={!name.trim() || save.isPending}><Save className="mr-1.5 h-4 w-4" /> Save changes</Button>
              <Button size="sm" variant="outline" data-testid="settings-archive-project" onClick={() => archive.mutate()}><Archive className="mr-1.5 h-4 w-4" /> {activeProject.status === "archived" ? "Unarchive" : "Archive"}</Button>
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button size="sm" variant="ghost" className="text-destructive" data-testid="settings-delete-project"><Trash2 className="mr-1.5 h-4 w-4" /> Delete project</Button>
                </AlertDialogTrigger>
                <AlertDialogContent data-testid="delete-project-confirm">
                  <AlertDialogHeader>
                    <AlertDialogTitle>Delete "{activeProject.name}"?</AlertDialogTitle>
                    <AlertDialogDescription>This permanently removes the project and all its experiments, runs, conversations, evaluations and artifacts. This cannot be undone.</AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction data-testid="confirm-delete-project" className="bg-destructive text-destructive-foreground hover:bg-destructive/90" onClick={() => del.mutate()}>Delete</AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
