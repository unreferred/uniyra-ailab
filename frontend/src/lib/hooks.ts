import { useQuery } from "@tanstack/react-query";
import {
  api,
  getAgentCatalog,
  getAgent,
  getArenaV6Catalog,
  getArenaV6Summary,
  getDriftCatalog,
  getInvariantCatalog,
  getPolicyCatalog,
  getPromptCaptureCatalog,
  getPrompt,
  getPromptBlockCatalog,
  getPromptCompilerCatalog,
  getPromptUsage,
  getWorkspaceModes,
  listContextBudgetPolicies,
  listContextCatalog,
  listDriftAnalyses,
  listContextPackVersions,
  listContextPacks,
  listAgentVersions,
  listAgents,
  listArenasV6,
  listFetchProviders,
  listProjectInvariants,
  listProjectPolicies,
  listPromptCaptures,
  listCompiledPrompts,
  listPromptBlocks,
  listPromptVersions,
  listPrompts,
  listSearchProviders,
  listToolValves,
} from "@/lib/api";
import {
  Agent,
  AgentCatalog,
  AgentVersion,
  ArenaV6Catalog,
  ArenaV6List,
  ArenaV6Summary,
  Attachment,
  CompiledPromptCatalog,
  CompiledPromptList,
  ContextBudgetPolicy,
  ContextCatalog,
  ContextPack,
  ContextPackVersion,
  ContextResponse,
  DriftAnalysisList,
  DriftCatalog,
  Failure,
  FailureAggregate,
  FailureDetail,
  ModelRecord,
  ProjectInvariant,
  ProjectInvariantCatalog,
  ProjectPolicy,
  ProjectPolicyCatalog,
  PromptCaptureRecord,
  PromptBlock,
  PromptBlockCatalog,
  PromptSpec,
  PromptUsage,
  PromptVersion,
  Provider,
  ProviderModelCatalog,
  Rubric,
  RunCompare,
  ToolRecord,
  TraceDetail,
  WorkspaceModesCatalog,
} from "@/lib/types";

export function useModels() {
  return useQuery<ModelRecord[]>({ queryKey: ["models"], queryFn: async () => (await api.get("/models")).data });
}
export function useProviders() {
  return useQuery<Provider[]>({ queryKey: ["providers"], queryFn: async () => (await api.get("/providers")).data });
}
export function useProviderModelCatalog(providerId: string, enabled = true) {
  return useQuery<ProviderModelCatalog>({
    queryKey: ["provider-model-catalog", providerId],
    queryFn: async () => (await api.get(`/providers/${providerId}/models`)).data,
    enabled: enabled && Boolean(providerId),
    retry: false,
  });
}
export function useRubrics() {
  return useQuery<Rubric[]>({ queryKey: ["rubrics"], queryFn: async () => (await api.get("/rubrics")).data });
}

// ---------------------------------------------------------------------------
// Phase 3: tools, attachments, traces, run context, run comparison
// ---------------------------------------------------------------------------

export interface ToolListResponse {
  items: ToolRecord[];
  total: number;
}

export interface AttachmentListResponse {
  items: Attachment[];
  total: number;
  limit: number;
  offset: number;
}

export function useTools(projectId?: string | null) {
  return useQuery<ToolListResponse>({
    queryKey: ["tools", projectId],
    queryFn: async () =>
      (await api.get("/tools", { params: projectId ? { project_id: projectId } : {} })).data,
    enabled: !!projectId,
  });
}

export function useAttachments(projectId?: string | null, conversationId?: string | null) {
  return useQuery<AttachmentListResponse>({
    queryKey: ["attachments", projectId, conversationId],
    queryFn: async () =>
      (
        await api.get("/attachments", {
          params: {
            project_id: projectId,
            ...(conversationId ? { conversation_id: conversationId } : {}),
          },
        })
      ).data,
    enabled: !!projectId,
  });
}

export function useTrace(runId?: string | null) {
  return useQuery<TraceDetail>({
    queryKey: ["run-trace", runId],
    queryFn: async () => (await api.get(`/runs/${runId}/trace`)).data,
    enabled: !!runId,
  });
}

export function useRunContext(runId?: string | null) {
  return useQuery<ContextResponse>({
    queryKey: ["run-context", runId],
    queryFn: async () => (await api.get(`/runs/${runId}/context`)).data,
    enabled: !!runId,
  });
}

export function useRunCompare(runA?: string | null, runB?: string | null) {
  return useQuery<RunCompare>({
    queryKey: ["run-compare", runA, runB],
    queryFn: async () =>
      (await api.get("/runs/compare", { params: { run_a: runA, run_b: runB } })).data,
    enabled: !!runA && !!runB,
  });
}

// ---------------------------------------------------------------------------
// Failure Lab: failures, aggregates, failure detail and the tool registry
// ---------------------------------------------------------------------------

export interface FailureListResponse {
  items: Failure[];
  total: number;
  limit: number;
  offset: number;
}

export function useFailures(params: Record<string, any> = {}) {
  return useQuery<FailureListResponse>({
    queryKey: ["failures", params],
    queryFn: async () => (await api.get("/failures", { params })).data,
  });
}

export function useFailureAggregate(projectId?: string | null) {
  return useQuery<FailureAggregate>({
    queryKey: ["failure-aggregate", projectId ?? null],
    queryFn: async () =>
      (
        await api.get("/failures/aggregate", {
          params: projectId ? { project_id: projectId } : {},
        })
      ).data,
  });
}

export function useFailure(id?: string | null) {
  return useQuery<FailureDetail>({
    queryKey: ["failure", id],
    queryFn: async () => (await api.get(`/failures/${id}`)).data,
    enabled: !!id,
  });
}

export function useToolRegistry(projectId?: string | null) {
  return useQuery<ToolListResponse>({
    queryKey: ["tools", projectId ?? null],
    queryFn: async () =>
      (await api.get("/tools", { params: projectId ? { project_id: projectId } : {} })).data,
  });
}

export function useTool(id?: string | null) {
  return useQuery<ToolRecord>({
    queryKey: ["tool", id],
    queryFn: async () => (await api.get(`/tools/${id}`)).data,
    enabled: !!id,
  });
}

// ---------------------------------------------------------------------------
// OmniLab V4: project workspace platform hooks (additive, same useQuery style)
// ---------------------------------------------------------------------------

export function useProjectOverview(projectId?: string | null) {
  return useQuery({
    queryKey: ["project-overview", projectId],
    queryFn: async () => (await api.get(`/projects/${projectId}/overview`)).data,
    enabled: !!projectId,
  });
}

export function useProjectActivity(projectId?: string | null, limit = 50) {
  return useQuery({
    queryKey: ["project-activity", projectId, limit],
    queryFn: async () =>
      (await api.get(`/projects/${projectId}/activity`, { params: { limit } })).data,
    enabled: !!projectId,
  });
}

export function useProjectFiles(
  projectId?: string | null,
  params: { search?: string; kind?: string; tag?: string } = {}
) {
  return useQuery({
    queryKey: ["project-files", projectId, params.search || "", params.kind || "", params.tag || ""],
    queryFn: async () =>
      (
        await api.get(`/projects/${projectId}/files`, {
          params: {
            ...(params.search ? { search: params.search } : {}),
            ...(params.kind ? { kind: params.kind } : {}),
            ...(params.tag ? { tag: params.tag } : {}),
          },
        })
      ).data,
    enabled: !!projectId,
  });
}

export function useKnowledgeCollections(projectId?: string | null) {
  return useQuery({
    queryKey: ["knowledge-collections", projectId],
    queryFn: async () =>
      (await api.get("/knowledge", { params: projectId ? { project_id: projectId } : {} })).data,
    enabled: !!projectId,
  });
}

export function useKnowledgeCollection(collectionId?: string | null) {
  return useQuery({
    queryKey: ["knowledge-collection", collectionId],
    queryFn: async () => (await api.get(`/knowledge/${collectionId}`)).data,
    enabled: !!collectionId,
  });
}

export function useKnowledgeDocuments(collectionId?: string | null) {
  return useQuery({
    queryKey: ["knowledge-documents", collectionId],
    queryFn: async () => (await api.get(`/knowledge/${collectionId}/documents`)).data,
    enabled: !!collectionId,
  });
}

export function useKnowledgeChunks(collectionId?: string | null, documentId?: string | null) {
  return useQuery({
    queryKey: ["knowledge-chunks", collectionId, documentId ?? null],
    queryFn: async () => {
      if (!collectionId) return [];
      return (
        await api.get(`/knowledge/${collectionId}/chunks`, {
          params: documentId ? { document_id: documentId } : {},
        })
      ).data;
    },
    enabled: !!collectionId,
  });
}

export function useToolVersions(toolId?: string | null) {
  return useQuery<{ items: any[]; builtin: boolean }>({
    queryKey: ["tool-versions", toolId],
    queryFn: async () => (await api.get(`/tools/${toolId}/versions`)).data,
    enabled: !!toolId,
  });
}

export function useToolValves(projectId?: string | null) {
  return useQuery({
    queryKey: ["tool-valves", projectId ?? null],
    queryFn: async () => listToolValves(projectId || undefined),
    staleTime: 15_000,
  });
}

export function useToolCredentials(projectId?: string | null) {
  return useQuery<{ items: any[] }>({
    queryKey: ["tool-credentials", projectId],
    queryFn: async () =>
      (await api.get("/tool-credentials", { params: { project_id: projectId } })).data,
    enabled: !!projectId,
  });
}

export function useCodeInterpreterStatus() {
  return useQuery({
    queryKey: ["code-interpreter-status"],
    queryFn: async () => (await api.get("/code-interpreter/status")).data,
  });
}

export interface SearchFetchProvidersResponse {
  items: any[];
}

export function useSearchProviders() {
  return useQuery({
    queryKey: ["search-providers"],
    queryFn: async () => listSearchProviders(),
    staleTime: 30_000,
  });
}

export function useFetchProviders() {
  return useQuery({
    queryKey: ["fetch-providers"],
    queryFn: async () => listFetchProviders(),
    staleTime: 30_000,
  });
}

export function useCodeExecutions(projectId?: string | null) {
  return useQuery({
    queryKey: ["code-executions", projectId],
    queryFn: async () =>
      (
        await api.get("/code-interpreter/executions", {
          params: projectId ? { project_id: projectId } : {},
        })
      ).data,
  });
}

// ---------------------------------------------------------------------------
// OmniLab V6.0: Prompt Lab query hooks (resource-first keys, additive).
// ---------------------------------------------------------------------------

export function usePromptSpecs(projectId?: string | null) {
  return useQuery<PromptSpec[]>({
    queryKey: ["prompt-specs", projectId],
    queryFn: async () => listPrompts(projectId as string),
    enabled: !!projectId,
  });
}

export function usePromptBlocks(projectId?: string | null, category?: string) {
  return useQuery<PromptBlock[]>({
    queryKey: ["prompt-blocks", projectId, category || ""],
    queryFn: async () => listPromptBlocks(projectId as string, category || undefined),
    enabled: !!projectId,
  });
}

export function usePromptBlockCatalog() {
  return useQuery<PromptBlockCatalog>({
    queryKey: ["prompt-block-catalog"],
    queryFn: async () => getPromptBlockCatalog(),
    staleTime: 60_000,
  });
}

export function useProjectPolicies(projectId?: string | null, domain?: string) {
  return useQuery<ProjectPolicy[]>({
    queryKey: ["project-policies", projectId, domain || ""],
    queryFn: async () => listProjectPolicies(projectId as string, domain || undefined),
    enabled: !!projectId,
  });
}

export function usePolicyCatalog() {
  return useQuery<ProjectPolicyCatalog>({
    queryKey: ["policy-catalog"],
    queryFn: async () => getPolicyCatalog(),
    staleTime: 60_000,
  });
}

export function useProjectInvariants(projectId?: string | null, status?: string) {
  return useQuery<ProjectInvariant[]>({
    queryKey: ["project-invariants", projectId, status || ""],
    queryFn: async () => listProjectInvariants(projectId as string, status || undefined),
    enabled: !!projectId,
  });
}

export function useInvariantCatalog() {
  return useQuery<ProjectInvariantCatalog>({
    queryKey: ["invariant-catalog"],
    queryFn: async () => getInvariantCatalog(),
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------------------
// OmniLab V6.1: Prompt Registry hooks (prompt detail, versions, usage).
// ---------------------------------------------------------------------------

export function usePromptDetail(promptId?: string | null) {
  return useQuery<PromptSpec>({
    queryKey: ["prompt-detail", promptId],
    queryFn: async () => getPrompt(promptId as string),
    enabled: !!promptId,
  });
}

export function usePromptVersions(promptId?: string | null) {
  return useQuery<PromptVersion[]>({
    queryKey: ["prompt-versions", promptId],
    queryFn: async () => listPromptVersions(promptId as string),
    enabled: !!promptId,
  });
}

export function usePromptUsage(promptId?: string | null, enabled = true) {
  return useQuery<PromptUsage>({
    queryKey: ["prompt-usage", promptId],
    queryFn: async () => getPromptUsage(promptId as string),
    enabled: enabled && !!promptId,
  });
}


// ---------------------------------------------------------------------------
// OmniLab V6.2: Context Lab hooks — catalog, context packs/versions and
// budget policies (resource-first query keys, additive).
// ---------------------------------------------------------------------------

export function useContextCatalog() {
  return useQuery<ContextCatalog>({
    queryKey: ["context-catalog"],
    queryFn: async () => listContextCatalog(),
    staleTime: 60_000,
  });
}

export function useContextPacks(projectId?: string | null) {
  return useQuery<ContextPack[]>({
    queryKey: ["context-packs", projectId],
    queryFn: async () => listContextPacks(projectId as string),
    enabled: !!projectId,
  });
}

export function useContextPackVersions(packId?: string | null) {
  return useQuery<ContextPackVersion[]>({
    queryKey: ["context-pack-versions", packId],
    queryFn: async () => listContextPackVersions(packId as string),
    enabled: !!packId,
  });
}

export function useContextBudgetPolicies(projectId?: string | null) {
  return useQuery<ContextBudgetPolicy[]>({
    queryKey: ["context-budget-policies", projectId],
    queryFn: async () => listContextBudgetPolicies(projectId as string),
    enabled: !!projectId,
  });
}


// ---------------------------------------------------------------------------
// OmniLab V6.3: Prompt Compiler hooks — catalog + compiled prompt history
// (resource-first query keys, additive).
// ---------------------------------------------------------------------------

export function usePromptCompilerCatalog(enabled = true) {
  return useQuery<CompiledPromptCatalog>({
    queryKey: ["prompt-compiler-catalog"],
    queryFn: async () => getPromptCompilerCatalog(),
    enabled,
    staleTime: 60_000,
  });
}

export function useCompiledPrompts(promptVersionId?: string | null, projectId?: string | null) {
  return useQuery<CompiledPromptList>({
    queryKey: ["compiled-prompts", projectId ?? null, promptVersionId ?? null],
    queryFn: async () =>
      listCompiledPrompts({
        ...(projectId ? { project_id: projectId } : {}),
        ...(promptVersionId ? { prompt_version_id: promptVersionId } : {}),
      }),
    enabled: !!promptVersionId,
  });
}

// ---------------------------------------------------------------------------
// OmniLab V6.4: Agent Lab hooks — catalog, project agents, agent detail and
// append-only versions (resource-first query keys, additive).
// ---------------------------------------------------------------------------

export function useAgentCatalog() {
  return useQuery<AgentCatalog>({
    queryKey: ["agent-catalog"],
    queryFn: async () => getAgentCatalog(),
    staleTime: 60_000,
  });
}

export function useAgents(projectId?: string | null, includeArchived = false) {
  return useQuery<Agent[]>({
    queryKey: ["agents", projectId, includeArchived],
    queryFn: async () => listAgents(projectId as string, includeArchived),
    enabled: !!projectId,
  });
}

export function useAgentDetail(agentId?: string | null) {
  return useQuery<Agent>({
    queryKey: ["agent-detail", agentId],
    queryFn: async () => getAgent(agentId as string),
    enabled: !!agentId,
  });
}

export function useAgentVersions(agentId?: string | null) {
  return useQuery<AgentVersion[]>({
    queryKey: ["agent-versions", agentId],
    queryFn: async () => listAgentVersions(agentId as string),
    enabled: !!agentId,
  });
}

// ---------------------------------------------------------------------------
// OmniLab V6.8: Arena V6 hooks — catalog, arena experiment list and per-config
// summary (resource-first query keys, additive).
// ---------------------------------------------------------------------------

export function useArenaV6Catalog() {
  return useQuery<ArenaV6Catalog>({
    queryKey: ["arena-v6-catalog"],
    queryFn: async () => getArenaV6Catalog(),
    staleTime: 60_000,
  });
}

export function useArenasV6(projectId?: string | null) {
  return useQuery<ArenaV6List>({
    queryKey: ["arenas-v6", projectId],
    queryFn: async () => listArenasV6(projectId ? { project_id: projectId } : {}),
    enabled: !!projectId,
  });
}

export function useArenaV6Summary(experimentId?: string | null) {
  return useQuery<ArenaV6Summary>({
    queryKey: ["arena-v6-summary", experimentId],
    queryFn: async () => getArenaV6Summary(experimentId as string),
    enabled: !!experimentId,
  });
}

// ---------------------------------------------------------------------------
// OmniLab V6.9: Drift Analysis hooks — catalog + project analysis history
// (resource-first query keys, additive).
// ---------------------------------------------------------------------------

export function useDriftCatalog() {
  return useQuery<DriftCatalog>({
    queryKey: ["drift-catalog"],
    queryFn: async () => getDriftCatalog(),
    staleTime: 60_000,
  });
}

export function useDriftAnalyses(projectId?: string | null) {
  return useQuery<DriftAnalysisList>({
    queryKey: ["drift-analyses", projectId],
    queryFn: async () => listDriftAnalyses(projectId ? { project_id: projectId } : {}),
    enabled: !!projectId,
  });
}


// ---------------------------------------------------------------------------
// OmniLab V6.14: Workspace Prompt Capture hooks — workspace mode catalog,
// capture catalog and per-project capture history (resource-first query keys,
// additive).
// ---------------------------------------------------------------------------

export function useWorkspaceModes(enabled = true) {
  return useQuery<WorkspaceModesCatalog>({
    queryKey: ["workspace-modes"],
    queryFn: async () => getWorkspaceModes(),
    enabled,
    staleTime: 60_000,
  });
}

export function usePromptCaptureCatalog(enabled = true) {
  return useQuery({
    queryKey: ["prompt-capture-catalog"],
    queryFn: async () => getPromptCaptureCatalog(),
    enabled,
    staleTime: 60_000,
  });
}

export function usePromptCaptures(projectId?: string | null) {
  return useQuery<PromptCaptureRecord[]>({
    queryKey: ["prompt-captures", projectId],
    queryFn: async () => listPromptCaptures(projectId as string),
    enabled: !!projectId,
  });
}
