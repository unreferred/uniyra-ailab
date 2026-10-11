import React, { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileJson, Plus, Settings2, Wrench } from "lucide-react";
import { apiError, deleteCustomTool } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { useCodeInterpreterStatus, useToolCredentials, useToolRegistry, useToolValves } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, ErrorNote, LoadingSpinner, SectionHeader } from "@/components/common/Common";
import {
  RiskBadge, ScopeBadge, StatusDot, ToolFilter, ToolStatus, filterKindOf,
  isCodeInterpreter, toolStatusOf, toolTypeLabel, versionLabelOf,
} from "@/components/tools/ToolShared";
import { AddHttpToolDialog } from "@/components/tools/AddHttpToolDialog";
import { OpenApiImportDialog } from "@/components/tools/OpenApiImportDialog";
import { ProvidersPanel } from "@/components/tools/ProvidersPanel";
import { ConnectionsPanel } from "@/components/tools/ConnectionsPanel";
import { ToolDetailDialog } from "@/components/tools/ToolDetailDialog";
import { ToolValveDialog } from "@/components/tools/ToolValveDialog";
import { ProjectToolsSection } from "@/components/tools/ProjectToolsSection";

const FILTERS: { key: ToolFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "builtin", label: "Built-in" },
  { key: "http", label: "Custom HTTP" },
  { key: "openapi", label: "OpenAPI" },
  { key: "code_interpreter", label: "Code Interpreter" },
];

const TOOLS_TABS = ["registry", "providers", "connections", "projects"] as const;

const ToolsPage: React.FC = () => {
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = TOOLS_TABS.includes(searchParams.get("tab") as any)
    ? (searchParams.get("tab") as string)
    : "registry";
  const [activeTab, setActiveTab] = useState<string>(initialTab);
  const { projects } = useProjects();
  const availableProjects = (projects || []).map((p) => ({ id: p.id, name: p.name }));
  const defaultProjectId = projects?.[0]?.id || null;

  const [filter, setFilter] = useState<ToolFilter>("all");
  const [selected, setSelected] = useState<any | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [configureTool, setConfigureTool] = useState<any | null>(null);

  // Global registry view (owner + global builtins). Project scoping is
  // surfaced per-row via GLOBAL/PROJECT badges; per-project CRUD stays in
  // Project Manager -> Tools.
  const { data, isLoading, isError, error, refetch } = useToolRegistry(undefined);
  const tools = useMemo(() => data?.items || [], [data]);
  const { data: ciStatus, isLoading: ciLoadingRaw } = useCodeInterpreterStatus();
  const ciLoading = ciLoadingRaw && !ciStatus;

  const credTarget = defaultProjectId || "";
  const credQuery = useToolCredentials(credTarget || null);

  const valveQuery = useToolValves(undefined);
  const valveMap = useMemo(() => {
    const map: Record<string, any> = {};
    for (const item of valveQuery.data?.items || []) {
      if (item?.tool_id) map[item.tool_id] = item;
    }
    return map;
  }, [valveQuery.data]);
  const [detailCreds, setDetailCreds] = useState<any[]>([]);
  React.useEffect(() => {
    let alive = true;
    if (selected && !isCodeInterpreter(selected)) {
      const pid = selected.project_id || defaultProjectId;
      if (pid) {
        import("@/lib/api").then((m) => m.listToolCredentials(pid).then(
          (r) => { if (alive) setDetailCreds(r?.items || []); },
          () => { if (alive) setDetailCreds([]); },
        ));
      } else setDetailCreds([]);
    } else setDetailCreds([]);
    return () => { alive = false; };
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo(
    () => (filter === "all" ? tools : tools.filter((t) => filterKindOf(t) === filter)),
    [tools, filter],
  );

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: tools.length, builtin: 0, http: 0, openapi: 0, code_interpreter: 0 };
    for (const t of tools) c[filterKindOf(t)] += 1;
    return c;
  }, [tools]);

  const delMut = useMutation({
    mutationFn: async (id: string) => deleteCustomTool(id),
    onSuccess: () => {
      toast.success("Tool deleted");
      qc.invalidateQueries({ queryKey: ["tools"] });
      qc.invalidateQueries({ queryKey: ["tool-registry"] });
      if (selected) setSelected(null);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const canDelete = (t: any) => !t?.builtin && (t?.tool_type === "http" || t?.tool_type === "openapi" || !!t?.current_version_id || !!t?.project_id);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6" data-testid="tools-page">
      <SectionHeader
        title="Tools"
        caption="Global registry — built-ins, custom HTTP, OpenAPI imports and the sandboxed code interpreter. Credentials live per project."
        action={
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" data-testid="tools-import-openapi" onClick={() => setImportOpen(true)}>
              <FileJson className="mr-1.5 h-4 w-4" /> Import OpenAPI
            </Button>
            <Button size="sm" data-testid="tools-add-tool" onClick={() => setAddOpen(true)}>
              <Plus className="mr-1.5 h-4 w-4" /> Add Tool
            </Button>
          </div>
        }
      />

      <Tabs
        value={activeTab}
        onValueChange={(v) => {
          setActiveTab(v);
          setSearchParams(v === "registry" ? {} : { tab: v }, { replace: true });
        }}
      >
        <TabsList data-testid="tools-tabs">
          <TabsTrigger value="registry" data-testid="tools-tab-registry"><Wrench className="mr-1.5 h-4 w-4" /> Registry</TabsTrigger>
          <TabsTrigger value="providers" data-testid="tools-tab-providers">Providers</TabsTrigger>
          <TabsTrigger value="connections" data-testid="tools-tab-connections">Connections</TabsTrigger>
          <TabsTrigger value="projects" data-testid="tools-tab-projects">Projects</TabsTrigger>
        </TabsList>

        <TabsContent value="registry" className="mt-4 space-y-4">
          <div className="flex flex-wrap gap-1.5" data-testid="tools-filters">
            {FILTERS.map((f) => (
              <Button
                key={f.key}
                size="sm"
                variant={filter === f.key ? "default" : "outline"}
                data-testid={`tools-filter-${f.key}`}
                onClick={() => setFilter(f.key)}
              >
                {f.label} · {counts[f.key] ?? 0}
              </Button>
            ))}
          </div>

          {isLoading ? (
            <LoadingSpinner label="Loading tools…" />
          ) : isError ? (
            <ErrorNote message={apiError(error)} />
          ) : filtered.length === 0 ? (
            <EmptyState
              icon={<Wrench className="h-6 w-6" />}
              title={tools.length === 0 ? "No tools registered" : "No tools match this filter"}
              description={tools.length === 0 ? "Import an OpenAPI spec or add a custom HTTP tool." : "Try a different filter."}
              testId="tools-empty"
            />
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border" data-testid="tools-table-wrap">
              <table className="w-full min-w-[880px] text-sm" data-testid="tools-table">
                <thead className="bg-muted/50 text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                  <tr>
                    <th className="px-4 py-2.5">Tool</th>
                    <th className="px-4 py-2.5">Type</th>
                    <th className="px-4 py-2.5">Scope</th>
                    <th className="px-4 py-2.5">Status</th>
                    <th className="px-4 py-2.5">Risk</th>
                    <th className="px-4 py-2.5">Version</th>
                    <th className="px-4 py-2.5">Permission</th>
                    <th className="px-4 py-2.5">Updated</th>
                    <th className="px-4 py-2.5">Config</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {filtered.map((t: any) => {
                    let st: ToolStatus;
                    if (isCodeInterpreter(t)) {
                      st = toolStatusOf(t, ciStatus, ciLoading || ciStatus === undefined);
                    } else {
                      const v = valveMap[t.id];
                      const vs = v?.status;
                      if (vs) {
                        if (!vs.enabled) st = { kind: "off", label: "Disabled" };
                        else if (!vs.configured) st = { kind: "missing", label: "Not configured" };
                        else if (vs.available) {
                          const providerBacked =
                            t.name === "web_search" || t.name === "web_fetch" ||
                            (t.tool_type === "function" && !!v.provider_kind);
                          st = { kind: "available", label: providerBacked ? (vs.healthy ? "Connected" : "Configured") : "Available" };
                        } else {
                          st = { kind: "off", label: vs.reason || "Unavailable" };
                        }
                      } else {
                        st = toolStatusOf(t, ciStatus, ciLoading || ciStatus === undefined);
                      }
                    }
                    return (
                      <tr
                        key={t.id}
                        className="cursor-pointer transition-colors hover:bg-accent/40"
                        data-testid={`tool-row-${t.id}`}
                        onClick={() => setSelected(t)}
                      >
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-2">
                            <span className="font-medium">{t.display_name || t.name}</span>
                            {t.builtin && <Badge variant="secondary" className="text-[10px]">built-in</Badge>}
                          </div>
                          <div className="font-mono text-[11px] text-muted-foreground">{t.name}</div>
                        </td>
                        <td className="px-4 py-2.5">
                          <Badge variant="outline" className="text-[10px]" data-testid={`tool-type-${t.id}`}>{toolTypeLabel(t.tool_type)}</Badge>
                        </td>
                        <td className="px-4 py-2.5"><ScopeBadge tool={t} /></td>
                        <td className="px-4 py-2.5">
                          <span className="flex items-center gap-1.5 text-[11px]" data-testid={`tool-status-${t.id}`} title={t.availability_reason || st.label}>
                            <StatusDot kind={st.kind} /> {st.label}
                          </span>
                          {isCodeInterpreter(t) && !ciLoading && ciStatus && (
                            <span className="mt-0.5 block font-mono text-[10px] text-muted-foreground" data-testid={`tool-ci-image-${t.id}`}>
                              {ciStatus.image || ""}
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-2.5"><RiskBadge risk={t.risk_level} testId={`tool-risk-${t.id}`} /></td>
                        <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground" data-testid={`tool-version-${t.id}`}>
                          {versionLabelOf(t)}
                          {t.current_version_id && (
                            <span className="ml-1.5 text-[10px]" title={`current version ${t.current_version_id}`}>
                              {String(t.current_version_id).slice(0, 8)}
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-2.5">
                          <div className="flex max-w-[220px] flex-wrap gap-1">
                            {(t.permissions || []).length === 0 ? (
                              <span className="text-[11px] text-muted-foreground">none</span>
                            ) : (
                              (t.permissions || []).slice(0, 3).map((perm: string) => (
                                <Badge key={perm} variant="outline" className="font-mono text-[9px]">{perm}</Badge>
                              ))
                            )}
                            {(t.permissions || []).length > 3 && (
                              <span className="text-[10px] text-muted-foreground">+{(t.permissions || []).length - 3}</span>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-2.5 font-mono text-[11px] text-muted-foreground" data-testid={`tool-updated-${t.id}`}>
                          {t.updated_at ? new Date(t.updated_at).toLocaleDateString() : "\u2014"}
                        </td>
                        <td className="px-4 py-2.5">
                          <Button
                            size="sm"
                            variant="outline"
                            data-testid={`tool-configure-${t.id}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              setConfigureTool(t);
                            }}
                          >
                            <Settings2 className="mr-1.5 h-3.5 w-3.5" /> Configure
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-[11px] text-muted-foreground" data-testid="tools-legend">
            TOOL = capability exposed to the model · PROVIDER = implementation supplying it · VALVE = configurable setting · CONNECTION = external endpoint/service
          </p>
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            <span>Project-scoped CRUD lives in Project Manager → Tools.</span>
            <Link to={defaultProjectId ? `/projects/${defaultProjectId}` : "/"} className="text-primary hover:underline" data-testid="tools-project-link">
              Open project tools
            </Link>
            <button type="button" className="ml-auto hover:text-foreground" onClick={() => { refetch(); }} data-testid="tools-refresh">Refresh</button>
          </div>
        </TabsContent>

        <TabsContent value="providers" className="mt-4 space-y-4">
          <ProvidersPanel />
        </TabsContent>

        <TabsContent value="connections" className="mt-4 space-y-4">
          <ConnectionsPanel />
        </TabsContent>

        <TabsContent value="projects" className="mt-4">
          <ProjectToolsSection projectId={defaultProjectId} availableProjects={availableProjects} />
        </TabsContent>
      </Tabs>

      <ToolValveDialog
        tool={configureTool}
        projectId={defaultProjectId}
        onClose={() => setConfigureTool(null)}
      />
      <ToolDetailDialog
        tool={selected}
        onClose={() => setSelected(null)}
        ciStatus={ciStatus}
        projectIdForTest={selected?.project_id || defaultProjectId}
        availableProjects={availableProjects}
      />
      <AddHttpToolDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        projectId={defaultProjectId}
        availableProjects={availableProjects}
        credentials={credQuery.data?.items || detailCreds}
      />
      <OpenApiImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        projectId={defaultProjectId}
        availableProjects={availableProjects}
        credentials={credQuery.data?.items || detailCreds}
      />
      <DeleteHelper selected={selected} canDelete={canDelete} onDelete={(id) => delMut.mutate(id)} pending={delMut.isPending} />
    </div>
  );
};

const DeleteHelper: React.FC<{ selected: any; canDelete: (t: any) => boolean; onDelete: (id: string) => void; pending: boolean }> = ({ selected, canDelete, onDelete, pending }) => {
  if (!selected || !canDelete(selected)) return null;
  return (
    <Card className="flex items-center justify-between p-3" data-testid="tool-delete-bar">
      <p className="text-xs text-muted-foreground">
        Custom tool <span className="font-mono">{selected.name}</span> — deleting removes the tool and its versions.
      </p>
      <Button size="sm" variant="destructive" data-testid={`tool-delete-${selected.id}`} disabled={pending} onClick={() => onDelete(selected.id)}>
        Delete tool
      </Button>
    </Card>
  );
};

export default ToolsPage;
