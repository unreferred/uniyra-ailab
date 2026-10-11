import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, apiError, projectSettings } from "@/lib/api";
import { useToolRegistry } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { ScopeBadge, isCodeInterpreter } from "@/components/tools/ToolShared";

// Project integration: AVAILABLE TO PROJECT checkboxes, project defaults,
// GLOBAL vs PROJECT badges. Backend already filters by scope — this view
// only displays the scoping correctly and persists project defaults.
export const ProjectToolsSection: React.FC<{ projectId?: string | null; availableProjects: { id: string; name: string }[] }> = ({
  projectId, availableProjects,
}) => {
  const qc = useQueryClient();
  const [project, setProject] = useState(projectId || "");
  const target = projectId || project;

  const { data: projectDoc, isLoading: projectLoading } = useQuery({
    queryKey: ["project", target],
    queryFn: async () => (await api.get(`/projects/${target}`)).data,
    enabled: !!target,
  });

  // Global registry view (no project filter) so PROJECT vs GLOBAL scoping is
  // visible side by side; the per-project ToolsTab stays authoritative for
  // project-scoped CRUD.
  const { data: registry, isLoading: toolsLoading, isError, error } = useToolRegistry(undefined);
  const tools = registry?.items || [];

  const [enabledIds, setEnabledIds] = useState<string[] | null>(null);
  React.useEffect(() => {
    if (projectDoc) setEnabledIds(projectDoc.default_enabled_tool_ids || []);
  }, [projectDoc?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: async () => projectSettings(target, { default_enabled_tool_ids: enabledIds || [] }),
    onSuccess: () => {
      toast.success("Project tool defaults saved");
      qc.invalidateQueries({ queryKey: ["project", target] });
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const toggle = (id: string) =>
    setEnabledIds((prev) => {
      const list = prev || [];
      return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
    });

  const current = enabledIds ?? projectDoc?.default_enabled_tool_ids ?? [];

  return (
    <Card className="p-4" data-testid="project-tools-section">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-display text-sm font-semibold">Available to project</h3>
          <p className="text-[11px] text-muted-foreground">
            Check tools to enable by default for new conversations. Applies to future conversations only.
          </p>
        </div>
        <Button size="sm" data-testid="project-tools-save" disabled={!target || save.isPending || enabledIds == null} onClick={() => save.mutate()}>
          {save.isPending ? "Saving…" : "Save defaults"}
        </Button>
      </div>
      {!projectId && (
        <div className="mb-2 max-w-xs">
          <label className="text-xs font-medium text-muted-foreground">Project</label>
          <select value={project} onChange={(e) => setProject(e.target.value)} className="mt-1 h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm" data-testid="project-tools-project">
            <option value="">Select project</option>
            {availableProjects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      )}
      {!target ? (
        <p className="text-xs text-muted-foreground">Select a project to manage its tool defaults.</p>
      ) : projectLoading || toolsLoading ? (
        <LoadingSpinner label="Loading project tools…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : tools.length === 0 ? (
        <p className="text-xs text-muted-foreground">No tools registered.</p>
      ) : (
        <div className="grid gap-1 sm:grid-cols-2" data-testid="project-tools-list">
          {tools.map((t: any) => {
            const unavailable = t.available === false || t.enabled === false;
            const checked = current.includes(t.id);
            return (
              <label
                key={t.id}
                className={`flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-accent/50 ${unavailable ? "opacity-60" : ""}`}
                data-testid={`project-available-tool-${t.id}`}
                title={isCodeInterpreter(t) ? "Sandboxed Docker execution, network off" : undefined}
              >
                <Checkbox checked={checked} disabled={unavailable} onCheckedChange={() => !unavailable && toggle(t.id)} />
                <span className="min-w-0 flex-1 truncate">{t.display_name || t.name}</span>
                <ScopeBadge tool={t} />
                {unavailable && (
                  <Badge variant="outline" className="text-[9px]" title={t.availability_reason || "Unavailable"}>
                    unavailable
                  </Badge>
                )}
              </label>
            );
          })}
        </div>
      )}
    </Card>
  );
};
