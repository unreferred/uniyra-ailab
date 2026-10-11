import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronsUpDown, Plus, FolderKanban, Check } from "lucide-react";
import { toast } from "sonner";
import { api, apiError } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Popover, PopoverContent, PopoverTrigger,
} from "@/components/ui/popover";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter, DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

export const ProjectSwitcher: React.FC = () => {
  const { projects, activeProject, setActiveId, refresh } = useProjects();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const createMut = useMutation({
    mutationFn: async () => (await api.post("/projects", { name, description })).data,
    onSuccess: (proj) => {
      toast.success("Project created");
      qc.invalidateQueries({ queryKey: ["projects"] });
      setActiveId(proj.id);
      setDialogOpen(false); setName(""); setDescription("");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <div className="flex items-center gap-1">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            data-testid="project-switcher-trigger"
            className="flex min-w-[190px] items-center justify-between gap-2 rounded-md border border-border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-accent"
          >
            <span className="flex items-center gap-2 truncate">
              <FolderKanban className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="truncate font-medium">{activeProject?.name || "No project"}</span>
            </span>
            <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[260px] p-1.5" data-testid="project-switcher-menu">
          <div className="px-2 py-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Projects</div>
          <div className="max-h-64 overflow-y-auto scrollbar-thin">
            {projects.length === 0 && <div className="px-2 py-3 text-sm text-muted-foreground">No projects yet</div>}
            {projects.map((p) => (
              <button
                key={p.id}
                data-testid={`project-option-${p.id}`}
                onClick={() => { setActiveId(p.id); setOpen(false); }}
                className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent"
              >
                <span className="truncate">{p.name}</span>
                {p.id === activeProject?.id && <Check className="h-3.5 w-3.5 text-[hsl(var(--brand-emerald))]" />}
              </button>
            ))}
          </div>
          <div className="mt-1 border-t border-border pt-1">
            <button
              data-testid="project-switcher-new"
              onClick={() => { setOpen(false); setDialogOpen(true); }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-brand transition-colors hover:bg-accent"
            >
              <Plus className="h-3.5 w-3.5" /> New project
            </button>
          </div>
        </PopoverContent>
      </Popover>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent data-testid="new-project-dialog">
          <DialogHeader><DialogTitle className="font-display">Create project</DialogTitle>
            <DialogDescription>Projects are the top-level research boundary; all experiments, datasets and findings live inside one.</DialogDescription></DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Name</label>
              <Input data-testid="new-project-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Reasoning Benchmarks" className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Description</label>
              <Textarea data-testid="new-project-description" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What is this research about?" className="mt-1" rows={3} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button data-testid="new-project-submit" disabled={!name.trim() || createMut.isPending} onClick={() => createMut.mutate()}>
              {createMut.isPending ? "Creating…" : "Create project"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export const NewProjectDialogButton: React.FC<{ children?: React.ReactNode }> = () => null;
