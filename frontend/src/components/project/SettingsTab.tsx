import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Save } from "lucide-react";
import { api, apiError, projectSettings } from "@/lib/api";
import { useModels, useTools, useKnowledgeCollections, useSearchProviders, useFetchProviders } from "@/lib/hooks";
import { providerItems } from "@/lib/providers";
import { Project } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SingleModelSelect } from "@/components/common/ModelPicker";
import { LoadingSpinner, ErrorNote } from "@/components/common/Common";
import { CONTEXT_STRATEGIES } from "@/components/workspace/ContextPopover";
import { Badge } from "@/components/ui/badge";

export const SettingsTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const qc = useQueryClient();
  const { data: project, isLoading, isError, error } = useQuery<Project>({
    queryKey: ["project", projectId],
    queryFn: async () => (await api.get(`/projects/${projectId}`)).data,
    enabled: !!projectId,
  });
  const { data: models = [] } = useModels();
  const { data: toolsData } = useTools(projectId);
  const { data: collectionsData } = useKnowledgeCollections(projectId);
  const { data: searchProvidersData } = useSearchProviders();
  const { data: fetchProvidersData } = useFetchProviders();
  const tools = toolsData?.items || [];
  const collections = Array.isArray(collectionsData) ? collectionsData : collectionsData?.items || [];
  const searchProviders = providerItems(searchProvidersData);
  const fetchProviders = providerItems(fetchProvidersData);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [status, setStatus] = useState("active");
  const [modelId, setModelId] = useState("");
  const [systemInstruction, setSystemInstruction] = useState("");
  const [temperature, setTemperature] = useState("");
  const [maxTokens, setMaxTokens] = useState("");
  const [topP, setTopP] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [strategy, setStrategy] = useState("FULL");
  const [enabledTools, setEnabledTools] = useState<string[]>([]);
  const [defaultCollections, setDefaultCollections] = useState<string[]>([]);
  const [topK, setTopK] = useState("");
  const [searchEnabled, setSearchEnabled] = useState(false);
  const [searchProviderId, setSearchProviderId] = useState("");
  const [fetchEnabled, setFetchEnabled] = useState(false);
  const [fetchProviderId, setFetchProviderId] = useState("");

  useEffect(() => {
    if (!project) return;
    setName(project.name || "");
    setDescription(project.description || "");
    setTags((project.tags || []).join(", "));
    setStatus(project.status || "active");
    setModelId(project.default_model_id || "");
    setSystemInstruction(project.default_system_instruction || "");
    setTemperature(project.default_temperature != null ? String(project.default_temperature) : "");
    setMaxTokens(project.default_max_tokens != null ? String(project.default_max_tokens) : "");
    setTopP(project.default_top_p != null ? String(project.default_top_p) : "");
    setStreaming(!!project.default_streaming);
    setStrategy(project.default_context_strategy || "FULL");
    setEnabledTools(project.default_enabled_tool_ids || []);
    setDefaultCollections(project.default_knowledge_collection_ids || []);
    setSearchEnabled(!!project.default_search_enabled);
    setSearchProviderId(project.default_search_provider_id || "");
    setFetchEnabled(!!project.default_fetch_enabled);
    setFetchProviderId(project.default_fetch_provider_id || "");
    const ro = project.default_retrieval_options || {};
    setTopK(ro.top_k != null ? String(ro.top_k) : "");
  }, [project?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: async () =>
      projectSettings(projectId, {
        name,
        description,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        status,
        default_model_id: modelId || null,
        default_system_instruction: systemInstruction,
        default_temperature: temperature === "" ? null : parseFloat(temperature),
        default_max_tokens: maxTokens === "" ? null : parseInt(maxTokens, 10),
        default_top_p: topP === "" ? null : parseFloat(topP),
        default_streaming: streaming,
        default_context_strategy: strategy,
        default_enabled_tool_ids: enabledTools,
        default_search_enabled: searchEnabled,
        default_search_provider_id: searchProviderId || null,
        default_fetch_enabled: fetchEnabled,
        default_fetch_provider_id: fetchProviderId || null,
        default_knowledge_collection_ids: defaultCollections,
        default_retrieval_options: topK === "" ? {} : { top_k: parseInt(topK, 10) },
      }),
    onSuccess: () => {
      toast.success("Project settings saved");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["project-overview", projectId] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  if (isLoading) return <LoadingSpinner label="Loading settings…" />;
  if (isError) return <ErrorNote message={apiError(error)} />;
  if (!project) return <ErrorNote message="Project not found." />;

  const toggleList = (list: string[], id: string, set: (v: string[]) => void) =>
    set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

  return (
    <div className="space-y-4" data-testid="project-settings">
      <Card className="space-y-3 p-4" data-testid="project-settings-general">
        <h3 className="font-display text-sm font-semibold">General</h3>
        <div>
          <label className="text-xs font-medium text-muted-foreground">Name</label>
          <Input data-testid="project-settings-name" value={name} onChange={(e) => setName(e.target.value)} className="mt-1" />
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground">Description</label>
          <Textarea data-testid="project-settings-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="mt-1" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Tags (comma-separated)</label>
            <Input data-testid="project-settings-tags" value={tags} onChange={(e) => setTags(e.target.value)} className="mt-1" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Status</label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="mt-1" data-testid="project-settings-status"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="active">active</SelectItem><SelectItem value="archived">archived</SelectItem></SelectContent>
            </Select>
          </div>
        </div>
      </Card>

      <Card className="space-y-3 p-4" data-testid="project-settings-ai">
        <h3 className="font-display text-sm font-semibold">AI defaults</h3>
        <div>
          <label className="text-xs font-medium text-muted-foreground">Default model</label>
          <div className="mt-1 w-[280px]">
            <SingleModelSelect models={models.filter((m) => m.enabled)} value={modelId} onChange={setModelId} testId="project-settings-model" placeholder="No default" />
          </div>
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground">System instruction</label>
          <Textarea data-testid="project-settings-system" value={systemInstruction} onChange={(e) => setSystemInstruction(e.target.value)} rows={3} className="mt-1" />
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div><label className="text-xs font-medium text-muted-foreground">Temperature</label><Input data-testid="project-settings-temp" value={temperature} onChange={(e) => setTemperature(e.target.value)} className="mt-1 font-mono text-xs" placeholder="e.g. 0.7" /></div>
          <div><label className="text-xs font-medium text-muted-foreground">Max tokens</label><Input data-testid="project-settings-maxtokens" value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} className="mt-1 font-mono text-xs" placeholder="e.g. 1024" /></div>
          <div><label className="text-xs font-medium text-muted-foreground">Top P</label><Input data-testid="project-settings-topp" value={topP} onChange={(e) => setTopP(e.target.value)} className="mt-1 font-mono text-xs" placeholder="e.g. 1" /></div>
        </div>
        <div className="flex flex-wrap items-center gap-6">
          <label className="flex items-center gap-2 text-xs">Streaming <Switch data-testid="project-settings-streaming" checked={streaming} onCheckedChange={setStreaming} /></label>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground">Context strategy</span>
            <Select value={strategy} onValueChange={setStrategy}>
              <SelectTrigger className="h-8 w-48" data-testid="project-settings-strategy"><SelectValue /></SelectTrigger>
              <SelectContent>{CONTEXT_STRATEGIES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
            </Select>
          </div>
        </div>
      </Card>

      <Card className="space-y-2 p-4" data-testid="project-settings-tools">
        <h3 className="font-display text-sm font-semibold">Default enabled tools</h3>
        <p className="text-[11px] text-muted-foreground">
          AVAILABLE TO PROJECT — checked tools are enabled by default for new conversations.
          Backend already filters by scope; GLOBAL tools are shared, PROJECT tools belong to this project.
        </p>
        {tools.length === 0 ? <p className="text-xs text-muted-foreground">No tools available.</p> : (
          <div className="grid gap-1 sm:grid-cols-2" data-testid="project-available-tools">
            {tools.map((t: any) => {
              const unavailable = t.available === false || t.enabled === false;
              const scope = t.scope ? String(t.scope).toUpperCase() : (t.builtin ? "GLOBAL" : (t.project_id ? "PROJECT" : "GLOBAL"));
              return (
                <label key={t.id} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-xs hover:bg-accent/50" data-testid={`default-tool-${t.id}`} title={t.availability_reason || undefined}>
                  <Checkbox checked={enabledTools.includes(t.id)} disabled={unavailable} onCheckedChange={() => !unavailable && toggleList(enabledTools, t.id, setEnabledTools)} />
                  <span className="min-w-0 flex-1 truncate">{t.display_name || t.name}</span>
                  <Badge variant="outline" className="font-mono text-[9px]">{scope}</Badge>
                  {unavailable ? (
                    <Badge variant="outline" className="text-[9px]">unavailable</Badge>
                  ) : (
                    <Badge variant="secondary" className="text-[9px]">available</Badge>
                  )}
                </label>
              );
            })}
          </div>
        )}
      </Card>

      <Card className="space-y-3 p-4" data-testid="project-settings-search-fetch">
        <h3 className="font-display text-sm font-semibold">Search & fetch defaults</h3>
        <p className="text-[11px] text-muted-foreground">
          Default web search / fetch behavior for new conversations in this project.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2 rounded-md border border-border/60 p-3">
            <label className="flex items-center gap-2 text-xs font-medium">
              Web search
              <Switch data-testid="project-settings-search-enabled" checked={searchEnabled} onCheckedChange={setSearchEnabled} />
            </label>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Search provider</label>
              <Select value={searchProviderId || "__none"} onValueChange={(v) => setSearchProviderId(v === "__none" ? "" : v)}>
                <SelectTrigger className="mt-1" data-testid="project-settings-search-provider"><SelectValue placeholder="Select provider" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none">No default</SelectItem>
                  {searchProviders.map((sp: any) => <SelectItem key={sp.id} value={sp.id}>{sp.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-2 rounded-md border border-border/60 p-3">
            <label className="flex items-center gap-2 text-xs font-medium">
              Web fetch
              <Switch data-testid="project-settings-fetch-enabled" checked={fetchEnabled} onCheckedChange={setFetchEnabled} />
            </label>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Fetch provider</label>
              <Select value={fetchProviderId || "__none"} onValueChange={(v) => setFetchProviderId(v === "__none" ? "" : v)}>
                <SelectTrigger className="mt-1" data-testid="project-settings-fetch-provider"><SelectValue placeholder="Select provider" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none">No default</SelectItem>
                  {fetchProviders.map((fp: any) => <SelectItem key={fp.id} value={fp.id}>{fp.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>
      </Card>

      <Card className="space-y-2 p-4" data-testid="project-settings-knowledge">
        <h3 className="font-display text-sm font-semibold">Default knowledge</h3>
        {collections.length === 0 ? <p className="text-xs text-muted-foreground">No knowledge collections. Create one in the Knowledge tab.</p> : (
          <div className="grid gap-1 sm:grid-cols-2">
            {collections.map((c: any) => (
              <label key={c.id} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-xs hover:bg-accent/50" data-testid={`default-collection-${c.id}`}>
                <Checkbox checked={defaultCollections.includes(c.id)} onCheckedChange={() => toggleList(defaultCollections, c.id, setDefaultCollections)} />
                <span className="truncate">{c.name}</span>
              </label>
            ))}
          </div>
        )}
        <div className="w-40">
          <label className="text-xs font-medium text-muted-foreground">Retrieval top_k</label>
          <Input data-testid="project-settings-topk" value={topK} onChange={(e) => setTopK(e.target.value)} className="mt-1 font-mono text-xs" placeholder="e.g. 5" />
        </div>
      </Card>

      <div className="flex items-center gap-3">
        <Button size="sm" data-testid="project-settings-save" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>
          <Save className="mr-1.5 h-4 w-4" /> {save.isPending ? "Saving…" : "Save settings"}
        </Button>
        <p className="text-[11px] text-muted-foreground">Applies to future conversations only — existing conversations and runs are never mutated.</p>
      </div>
    </div>
  );
};
