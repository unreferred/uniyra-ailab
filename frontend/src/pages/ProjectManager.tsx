import React, { useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, apiError } from "@/lib/api";
import { Conversation } from "@/lib/types";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { SectionHeader, EmptyState, LoadingSpinner, ErrorNote } from "@/components/common/Common";
import { OverviewTab } from "@/components/project/OverviewTab";
import { FilesTab } from "@/components/project/FilesTab";
import { KnowledgeTab } from "@/components/project/KnowledgeTab";
import { ToolsTab } from "@/components/project/ToolsTab";
import { SettingsTab } from "@/components/project/SettingsTab";
import { EntityListTab } from "@/components/project/EntityListTab";
import { relTime } from "@/lib/format";

const TABS = [
  "overview", "conversations", "files", "knowledge", "prompts",
  "tools", "experiments", "datasets", "findings", "artifacts", "settings",
] as const;

const ConversationsPanel: React.FC<{ projectId: string }> = ({ projectId }) => {
  const { data, isLoading, isError, error } = useQuery<Conversation[]>({
    queryKey: ["conversations", projectId],
    queryFn: async () => (await api.get(`/conversations?project_id=${projectId}`)).data,
    enabled: !!projectId,
  });
  if (isLoading) return <LoadingSpinner label="Loading conversations…" />;
  if (isError) return <ErrorNote message={apiError(error)} />;
  const items = data || [];
  if (items.length === 0) {
    return (
      <EmptyState
        title="No conversations"
        description="Start one from the Workspace — it will appear here scoped to this project."
        testId="pm-conversations-empty"
        action={<Link to="/workspace"><Button size="sm">Open Workspace</Button></Link>}
      />
    );
  }
  return (
    <div className="space-y-2" data-testid="pm-conversations">
      {items.map((c) => (
        <Card key={c.id} className="flex items-center justify-between p-3" data-testid={`pm-conversation-${c.id}`}>
          <div className="min-w-0">
            <Link to={`/workspace?conv=${c.id}`} className="block truncate text-sm font-medium text-primary hover:underline">{c.title || c.id}</Link>
            <p className="text-[11px] text-muted-foreground">{relTime(c.updated_at)}</p>
          </div>
          <Link to={`/workspace?conv=${c.id}`}><Button size="sm" variant="outline">Open</Button></Link>
        </Card>
      ))}
    </div>
  );
};

export default function ProjectManager() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [tab, setTab] = useState<string>("overview");

  const { data: project, isLoading, isError, error } = useQuery({
    queryKey: ["project", id],
    queryFn: async () => (await api.get(`/projects/${id}`)).data,
    enabled: !!id,
  });

  if (!id) return <div className="p-6"><ErrorNote message="Missing project id." /></div>;
  if (isLoading) return <div className="p-6"><LoadingSpinner label="Loading project…" /></div>;
  if (isError) return <div className="p-6"><ErrorNote message={apiError(error)} /></div>;
  if (!project) return <div className="p-6"><ErrorNote message="Project not found." /></div>;

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 lg:p-6" data-testid="project-manager">
      <SectionHeader
        title={project.name}
        caption={project.description || `Project workspace · ${project.status || "active"}`}
        action={<Button size="sm" variant="outline" onClick={() => navigate("/workspace")}>Open Workspace</Button>}
      />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList data-testid="project-tabs" className="flex flex-wrap">
          {TABS.map((t) => (
            <TabsTrigger key={t} value={t} data-testid={`project-tab-${t}`} className="capitalize">{t}</TabsTrigger>
          ))}
        </TabsList>

        <div className="pt-4">
          <TabsContent value="overview"><OverviewTab projectId={id} /></TabsContent>
          <TabsContent value="conversations"><ConversationsPanel projectId={id} /></TabsContent>
          <TabsContent value="files"><FilesTab projectId={id} /></TabsContent>
          <TabsContent value="knowledge"><KnowledgeTab projectId={id} /></TabsContent>
          <TabsContent value="prompts">
            <EntityListTab
              projectId={id}
              endpoint="prompts"
              testId="pm-prompts"
              emptyTitle="No prompts"
              emptyDescription="Prompts are managed globally. Create prompt versions from the global Prompts view."
              globalHref="/models"
              globalLabel="Models & Prompts"
              titleOf={(p) => p.name || p.id}
              detailHrefOf={(p) => `/models`}
              subtitleOf={(p) => `${p.version_count ?? (p.versions || []).length ?? "?"} versions`}
            />
          </TabsContent>
          <TabsContent value="tools"><ToolsTab projectId={id} /></TabsContent>
          <TabsContent value="experiments">
            <EntityListTab
              projectId={id}
              endpoint="experiments"
              testId="pm-experiments"
              emptyTitle="No experiments"
              emptyDescription="Experiments run globally and are filtered to this project here."
              globalHref="/experiments"
              globalLabel="Experiments"
              titleOf={(e) => e.title || e.id}
              detailHrefOf={(e) => `/experiments/${e.id}`}
              subtitleOf={(e) => e.status || null}
            />
          </TabsContent>
          <TabsContent value="datasets">
            <EntityListTab
              projectId={id}
              endpoint="datasets"
              testId="pm-datasets"
              emptyTitle="No datasets"
              emptyDescription="Datasets are stored globally; this view filters to the project."
              globalHref="/datasets"
              globalLabel="Datasets"
              titleOf={(d) => d.name || d.id}
              detailHrefOf={(d) => `/datasets/${d.id}`}
              subtitleOf={(d) => d.case_count != null ? `${d.case_count} cases` : null}
            />
          </TabsContent>
          <TabsContent value="findings">
            <EntityListTab
              projectId={id}
              endpoint="findings"
              testId="pm-findings"
              emptyTitle="No findings"
              emptyDescription="Findings linked to this project will appear here."
              globalHref="/findings"
              globalLabel="Findings"
              titleOf={(f) => f.title || f.id}
              detailHrefOf={() => `/findings`}
              subtitleOf={(f) => f.status || null}
            />
          </TabsContent>
          <TabsContent value="artifacts">
            <EntityListTab
              projectId={id}
              endpoint="artifacts"
              testId="pm-artifacts"
              emptyTitle="No artifacts"
              emptyDescription="Artifacts saved from workspace runs land here."
              globalHref="/artifacts"
              globalLabel="Artifacts"
              titleOf={(a) => a.title || a.id}
              detailHrefOf={() => `/artifacts`}
              subtitleOf={(a) => a.artifact_type || null}
            />
          </TabsContent>
          <TabsContent value="settings"><SettingsTab projectId={id} /></TabsContent>
        </div>
      </Tabs>
    </div>
  );
}
