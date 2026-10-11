import axios from "axios";
import {
  Agent,
  AgentArenaCreate,
  AgentCatalog,
  AgentEvaluationCatalog,
  AgentEvaluationRequest,
  AgentEvaluationResult,
  AgentRunEvaluationList,
  AgentRunList,
  AgentRunRequest,
  AgentRunResult,
  AgentUsage,
  AgentVersion,
  AgentVersionCompare,
  ArenaV6Catalog,
  ArenaV6Experiment,
  ArenaV6List,
  ArenaV6Summary,
  Attachment,
  CompiledPrompt,
  CompiledPromptCatalog,
  CompiledPromptList,
  ConsolidatedPlan,
  DriftAnalysis,
  DriftAnalysisList,
  DriftAnalyzeRequest,
  DriftCatalog,
  DriftMaterializeResult,
  PlanningConflict,
  PlanningGraph,
  PlanningGraphNodeDetail,
  PlanningImpact,
  PlanningItem,
  PlanningState,
  PlanningStateCatalog,
  PrdDiff,
  PrdReadiness,
  PrdVersion,
  PrdVersionSummary,
  PromptArenaCreate,
  PromptCapturePreview,
  PromptCaptureRecord,
  PromptCaptureSaveRequest,
  PromptCaptureSaveResult,
  PromptCompileRequest,
  WorkspaceModesCatalog,
} from "./types";

function resolveBackendUrl(): string {
  const configured = (process.env.REACT_APP_BACKEND_URL || "").trim().replace(/\/$/, "");
  const fallback = configured || "http://localhost:8001";
  if (typeof window === "undefined") return fallback;
  const host = window.location.hostname;
  // Same-machine access: keep the configured URL (localhost workflow unchanged).
  if (host === "localhost" || host === "127.0.0.1" || !host) return fallback;
  // Remote access (Tailscale / LAN): the backend lives on the same host as the
  // page, only the port differs. Derive it so one build works for both
  // localhost and http://<tailscale-ip>:<port> without a rebuild per IP.
  let port = "8001";
  try {
    if (configured) {
      const parsed = new URL(configured);
      if (parsed.port) port = parsed.port;
      else if (parsed.protocol === "https:") port = "443";
      else if (parsed.protocol === "http:") port = "80";
    }
  } catch {
    // keep default port
  }
  const proto = window.location.protocol === "https:" ? "https:" : "http:";
  return `${proto}//${host}:${port}`;
}

const BACKEND_URL = resolveBackendUrl();
export const API_BASE = `${BACKEND_URL}/api`;

export const api = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  const parts = document.cookie ? document.cookie.split(";") : [];
  for (const part of parts) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      return decodeURIComponent(trimmed.slice(prefix.length));
    }
  }
  return null;
}

// Small helper for callers that use raw `fetch` (e.g. SSE streaming) and
// therefore bypass the axios request interceptor. Cookie-auth POSTs are
// rejected without this header.
export function csrfHeader(): Record<string, string> {
  const token = readCookie("csrf_token");
  return token ? { "X-CSRF-Token": token } : {};
}

export async function ensureCsrfToken(): Promise<void> {
  if (readCookie("csrf_token")) return;
  await api.get("/auth/csrf");
}

// Attach the CSRF token (from the non-httpOnly cookie) on unsafe methods.
// Cookie-authenticated requests need it; bearer-only clients are unaffected
// because they simply have no csrf_token cookie.
api.interceptors.request.use((config) => {
  const method = (config.method || "get").toLowerCase();
  if (!["get", "head", "options"].includes(method)) {
    const token = readCookie("csrf_token");
    if (token) {
      config.headers = config.headers || {};
      (config.headers as any)["X-CSRF-Token"] = token;
    }
  }
  return config;
});

// Transparent access-token refresh on 401.
let refreshing = false;
api.interceptors.response.use(
  (r) => r,
  async (error) => {
    const original = error.config;
    const status = error.response?.status;
    const url: string = original?.url || "";
    const isAuthCall = url.includes("/auth/login") || url.includes("/auth/register") || url.includes("/auth/refresh") || url.includes("/auth/me");
    if (status === 401 && !original._retry && !isAuthCall && !refreshing) {
      original._retry = true;
      try {
        refreshing = true;
        await api.post("/auth/refresh");
        // The server sets a fresh csrf cookie on refresh; the retried
        // request reads it at request time via the request interceptor.
        return api(original);
      } catch {
        // fall through
      } finally {
        refreshing = false;
      }
    }
    return Promise.reject(error);
  }
);

export function apiError(e: any): string {
  const detail = e?.response?.data?.detail;
  if (detail == null) return e?.message || "Something went wrong. Please try again.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail.map((d) => (d && typeof d.msg === "string" ? d.msg : JSON.stringify(d))).join(" ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  if (detail && typeof detail.message === "string") return detail.message;
  return String(detail);
}

// ---------------------------------------------------------------------------
// Phase 3: attachments (multipart upload + raw content as blob/object URL)
// ---------------------------------------------------------------------------

export async function uploadAttachments(
  projectId: string,
  files: File[],
  conversationId?: string
): Promise<Attachment[]> {
  const fd = new FormData();
  fd.append("project_id", projectId);
  if (conversationId) fd.append("conversation_id", conversationId);
  files.forEach((f) => fd.append("files", f, f.name));
  // The axios instance defaults to application/json; override so axios keeps
  // the FormData intact and lets the browser set the multipart boundary.
  const res = await api.post("/attachments", fd, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return res.data.attachments || [];
}

export interface AttachmentTextResponse {
  text: string;
  pages?: number | null;
  extraction_status?: string;
  char_count?: number;
  available?: boolean;
}

export async function fetchAttachmentText(id: string): Promise<AttachmentTextResponse> {
  return (await api.get(`/attachments/${id}/text`)).data;
}

export async function fetchAttachmentBlob(id: string): Promise<string> {
  const res = await api.get(`/attachments/${id}/content`, { responseType: "blob" });
  return URL.createObjectURL(res.data);
}

// ---------------------------------------------------------------------------
// OmniLab V4: project workspace platform (additive helpers — endpoints
// verified against backend/app/routers/*.py)
// ---------------------------------------------------------------------------

export async function projectOverview(id: string) {
  return (await api.get(`/projects/${id}/overview`)).data;
}

export async function projectSettings(id: string, patch: Record<string, any>) {
  return (await api.patch(`/projects/${id}/settings`, patch)).data;
}

export async function projectSearch(id: string, q: string, limit = 25) {
  return (await api.get(`/projects/${id}/search`, { params: { q, limit } })).data;
}

export async function projectActivity(id: string, limit = 50, offset = 0) {
  return (await api.get(`/projects/${id}/activity`, { params: { limit, offset } })).data;
}

// Project files: list returns a bare array; upload returns {files: [...]}.
export async function listProjectFiles(
  projectId: string,
  params: { search?: string; kind?: string; tag?: string; status?: string; limit?: number; offset?: number } = {}
) {
  return (await api.get(`/projects/${projectId}/files`, { params })).data;
}

export async function uploadProjectFiles(
  projectId: string,
  files: File[],
  extra: { display_name?: string; description?: string; tags?: string } = {}
) {
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f, f.name));
  if (extra.display_name) fd.append("display_name", extra.display_name);
  if (extra.description) fd.append("description", extra.description);
  if (extra.tags) fd.append("tags", extra.tags);
  const res = await api.post(`/projects/${projectId}/files/upload`, fd, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return res.data.files || [];
}

export async function createFileFromAttachment(
  projectId: string,
  body: { attachment_id: string; display_name?: string; description?: string }
) {
  return (await api.post(`/projects/${projectId}/files/from-attachment`, body)).data;
}

export async function getProjectFile(projectId: string, fileId: string) {
  return (await api.get(`/projects/${projectId}/files/${fileId}`)).data;
}

export async function updateProjectFile(projectId: string, fileId: string, patch: Record<string, any>) {
  return (await api.patch(`/projects/${projectId}/files/${fileId}`, patch)).data;
}

export async function deleteProjectFile(projectId: string, fileId: string) {
  return (await api.delete(`/projects/${projectId}/files/${fileId}`)).data;
}

// Knowledge: list/documents/chunks/search return bare arrays (or {items}
// for versions below); create/update return the collection document.
export async function listKnowledgeCollections(projectId?: string | null) {
  return (await api.get("/knowledge", { params: projectId ? { project_id: projectId } : {} })).data;
}

export async function createKnowledgeCollection(body: Record<string, any>) {
  return (await api.post("/knowledge", body)).data;
}

export async function getKnowledgeCollection(id: string) {
  return (await api.get(`/knowledge/${id}`)).data;
}

export async function updateKnowledgeCollection(id: string, patch: Record<string, any>) {
  return (await api.patch(`/knowledge/${id}`, patch)).data;
}

export async function deleteKnowledgeCollection(id: string) {
  return (await api.delete(`/knowledge/${id}`)).data;
}

export async function attachKnowledgeDocuments(collectionId: string, projectFileIds: string[]) {
  return (await api.post(`/knowledge/${collectionId}/documents`, { project_file_ids: projectFileIds })).data;
}

export async function listKnowledgeDocuments(collectionId: string, limit = 100, offset = 0) {
  return (await api.get(`/knowledge/${collectionId}/documents`, { params: { limit, offset } })).data;
}

export async function indexKnowledgeCollection(collectionId: string) {
  return (await api.post(`/knowledge/${collectionId}/index`)).data;
}

export async function reindexKnowledgeDocument(collectionId: string, documentId: string) {
  return (await api.post(`/knowledge/${collectionId}/documents/${documentId}/reindex`)).data;
}

export async function detachKnowledgeDocument(collectionId: string, documentId: string) {
  return (await api.delete(`/knowledge/${collectionId}/documents/${documentId}`)).data;
}

export async function listKnowledgeChunks(collectionId: string, params: { document_id?: string; limit?: number; offset?: number } = {}) {
  return (await api.get(`/knowledge/${collectionId}/chunks`, { params })).data;
}

export async function searchKnowledge(body: Record<string, any>) {
  return (await api.post("/knowledge/search", body)).data;
}

// Custom tools: POST /tools creates a versioned custom tool;
// GET /tools/:id/versions returns {items, builtin}.
export async function createCustomTool(body: Record<string, any>) {
  return (await api.post("/tools", body)).data;
}

export async function updateCustomTool(id: string, body: Record<string, any>) {
  return (await api.patch(`/tools/${id}`, body)).data;
}

export async function deleteCustomTool(id: string) {
  return (await api.delete(`/tools/${id}`)).data;
}

export async function listToolVersions(toolId: string) {
  return (await api.get(`/tools/${toolId}/versions`)).data;
}

export async function getToolVersion(versionId: string) {
  return (await api.get(`/tools/versions/${versionId}`)).data;
}

export async function testTool(toolId: string, body: Record<string, any>) {
  return (await api.post(`/tools/${toolId}/test`, body)).data;
}

export async function importOpenAPI(body: Record<string, any>) {
  return (await api.post("/tools/import-openapi", body)).data;
}

export async function generateFromOpenAPI(body: Record<string, any>) {
  return (await api.post("/tools/generate-from-openapi", body)).data;
}

// Credentials: list returns {items}; single GET takes ?project_id= guard.
export async function listToolCredentials(projectId: string) {
  return (await api.get("/tool-credentials", { params: { project_id: projectId } })).data;
}

export async function createToolCredential(body: Record<string, any>) {
  return (await api.post("/tool-credentials", body)).data;
}

export async function getToolCredential(id: string, projectId?: string) {
  return (await api.get(`/tool-credentials/${id}`, { params: projectId ? { project_id: projectId } : {} })).data;
}

export async function updateToolCredential(id: string, patch: Record<string, any>) {
  return (await api.patch(`/tool-credentials/${id}`, patch)).data;
}

export async function deleteToolCredential(id: string) {
  return (await api.delete(`/tool-credentials/${id}`)).data;
}

// Code interpreter: executions list returns {items, total, limit, offset};
// file download returns a blob URL (mirrors fetchAttachmentBlob).
export async function codeInterpreterStatus() {
  return (await api.get("/code-interpreter/status")).data;
}

export async function executeCode(body: { project_id: string; code: string; input_file_ids?: string[] }) {
  return (await api.post("/code-interpreter/execute", body)).data;
}

export async function listCodeExecutions(params: { project_id?: string; run_id?: string; limit?: number; offset?: number } = {}) {
  return (await api.get("/code-interpreter/executions", { params })).data;
}

export async function getCodeExecution(executionId: string) {
  return (await api.get(`/code-interpreter/executions/${executionId}`)).data;
}

export async function downloadExecutionFile(executionId: string, filename: string): Promise<string> {
  const res = await api.get(`/code-interpreter/executions/${executionId}/files/${filename}`, { responseType: "blob" });
  return URL.createObjectURL(res.data);
}

export async function deleteCodeExecution(executionId: string) {
  return (await api.delete(`/code-interpreter/executions/${executionId}`)).data;
}

// Spec-name aliases (same endpoints, canonical helpers above).
export const createFromAttachment = createFileFromAttachment;
export const executeCodeInterpreter = executeCode;
export const listExecutions = listCodeExecutions;
export const getExecution = getCodeExecution;
export const deleteExecution = deleteCodeExecution;

// Search providers
export async function listSearchProviders() {
  return (await api.get("/search-providers")).data;
}

export async function getSearchProvider(providerId: string) {
  return (await api.get(`/search-providers/${providerId}`)).data;
}

export async function updateSearchProvider(providerId: string, patch: Record<string, any>) {
  return (await api.patch(`/search-providers/${providerId}`, patch)).data;
}

export async function testSearchProvider(providerId: string) {
  return (await api.post(`/search-providers/${providerId}/test`)).data;
}

// Fetch providers
export async function listFetchProviders() {
  return (await api.get("/fetch-providers")).data;
}

export async function getFetchProvider(providerId: string) {
  return (await api.get(`/fetch-providers/${providerId}`)).data;
}

export async function updateFetchProvider(providerId: string, patch: Record<string, any>) {
  return (await api.patch(`/fetch-providers/${providerId}`, patch)).data;
}

export async function testFetchProvider(providerId: string) {
  return (await api.post(`/fetch-providers/${providerId}/test`)).data;
}

// Tool valves (V4.2.3.2): Tool/Provider settings, secrets stored server-side.
export async function listToolValves(projectId?: string) {
  return (await api.get("/tool-valves", { params: projectId ? { project_id: projectId } : {} })).data;
}
export async function getToolValves(toolId: string, projectId?: string) {
  return (await api.get(`/tool-valves/${toolId}`, { params: projectId ? { project_id: projectId } : {} })).data;
}
export async function updateToolValves(toolId: string, body: Record<string, any>) {
  return (await api.put(`/tool-valves/${toolId}`, body)).data;
}
export async function testToolValves(toolId: string, projectId?: string) {
  return (await api.post(`/tool-valves/${toolId}/test`, null, { params: projectId ? { project_id: projectId } : {} })).data;
}

// ---------------------------------------------------------------------------
// OmniLab V5 Marathon B: red team, scorecards, certification + finding
// promotions (additive helpers; owner-scoped, cookie+CSRF auth via `api`).
// ---------------------------------------------------------------------------

export async function listRedTeamExperiments(projectId?: string | null) {
  return (await api.get("/red-team", { params: projectId ? { project_id: projectId } : {} })).data;
}

export async function getRedTeamCatalog() {
  return (await api.get("/red-team/catalog")).data;
}

export async function materializeRedTeamFixtures(body: {
  project_id: string;
  name?: string;
  model_ids?: string[];
  include?: string[];
}) {
  return (await api.post("/red-team/fixtures", body)).data;
}

export async function createRedTeamExperiment(body: {
  project_id: string;
  dataset_id: string;
  dataset_version_id: string;
  model_ids?: string[];
  title?: string;
  objective?: string;
  repetitions?: number;
}) {
  return (await api.post("/red-team/experiments", body)).data;
}

export async function executeRedTeamExperiment(experimentId: string) {
  return (await api.post(`/red-team/experiments/${experimentId}/execute`)).data;
}

export async function getRedTeamSummary(experimentId: string) {
  return (await api.get(`/red-team/${experimentId}`)).data;
}

export async function getRunScorecard(runId: string) {
  return (await api.get(`/scorecards/run/${runId}`)).data;
}

export async function getExperimentScorecard(experimentId: string, configurationId?: string | null) {
  return (
    await api.get(`/scorecards/experiment/${experimentId}`, {
      params: configurationId ? { configuration_id: configurationId } : {},
    })
  ).data;
}

export async function compareExperimentScorecards(
  experimentId: string,
  baselineConfigurationId: string,
  candidateConfigurationId: string
) {
  return (
    await api.get(`/scorecards/experiment/${experimentId}/compare`, {
      params: {
        baseline_configuration_id: baselineConfigurationId,
        candidate_configuration_id: candidateConfigurationId,
      },
    })
  ).data;
}

export async function listScorecards(
  params: { project_id?: string; experiment_id?: string; run_id?: string; limit?: number; offset?: number } = {}
) {
  return (await api.get("/scorecards", { params })).data;
}

export async function buildScorecard(body: {
  project_id: string;
  scope: string;
  run_id?: string;
  experiment_id?: string;
  configuration_id?: string;
  baseline_configuration_id?: string;
  candidate_configuration_id?: string;
  notes?: string;
}) {
  return (await api.post("/scorecards", body)).data;
}

export async function getScorecard(id: string) {
  return (await api.get(`/scorecards/${id}`)).data;
}

export async function getCertificationPolicy() {
  return (await api.get("/certification/policy")).data;
}

export async function listCertificationCandidates(
  params: { project_id?: string; state?: string; subject_type?: string; limit?: number; offset?: number } = {}
) {
  return (await api.get("/certification/candidates", { params })).data;
}

export async function createCertificationCandidate(body: {
  project_id: string;
  label: string;
  subject?: Record<string, any>;
  evidence?: Record<string, any>;
  environment?: Record<string, any>;
  notes?: string;
}) {
  return (await api.post("/certification/candidates", body)).data;
}

export async function getCertificationCandidate(id: string) {
  return (await api.get(`/certification/candidates/${id}`)).data;
}

export async function evaluateCertificationReadiness(id: string) {
  return (await api.post(`/certification/candidates/${id}/readiness`)).data;
}

export async function updateCertificationCandidate(
  id: string,
  patch: { label?: string; notes?: string; evidence?: Record<string, any>; environment?: Record<string, any> }
) {
  return (await api.patch(`/certification/candidates/${id}`, patch)).data;
}

export async function promoteFindingToRegression(
  id: string,
  body: {
    dataset_id?: string;
    source_version_id?: string;
    name?: string;
    description?: string;
    case_overrides?: Record<string, any>;
  } = {}
) {
  return (await api.post(`/findings/${id}/promote/regression`, body)).data;
}

export async function promoteFindingToRedTeam(
  id: string,
  body: {
    dataset_id?: string;
    source_version_id?: string;
    name?: string;
    description?: string;
    case_overrides?: Record<string, any>;
    red_team_category?: string;
    expected_behavior?: string;
  } = {}
) {
  return (await api.post(`/findings/${id}/promote/red-team`, body)).data;
}

export async function linkFinding(id: string, body: { type: string; ref_id: string; label?: string }) {
  return (await api.post(`/findings/${id}/link`, body)).data;
}

// ---------------------------------------------------------------------------
// OmniLab V6.0: Prompt Lab — prompt specs, composable blocks, project policies
// and project invariants (additive; owner+project scoped via `api`).
// ---------------------------------------------------------------------------

// Prompt specs (extends the existing /prompts collection with purpose/status).
export async function listPrompts(projectId: string) {
  return (await api.get("/prompts", { params: { project_id: projectId } })).data;
}

export async function createPrompt(body: Record<string, any>) {
  return (await api.post("/prompts", body)).data;
}

export async function getPrompt(id: string) {
  return (await api.get(`/prompts/${id}`)).data;
}

export async function updatePrompt(id: string, patch: Record<string, any>) {
  return (await api.patch(`/prompts/${id}`, patch)).data;
}

export async function deletePrompt(id: string) {
  return (await api.delete(`/prompts/${id}`)).data;
}

// Prompt blocks.
export async function listPromptBlocks(
  projectId: string,
  category?: string,
  includeArchived?: boolean
) {
  return (
    await api.get("/prompt-blocks", {
      params: {
        project_id: projectId,
        ...(category ? { category } : {}),
        ...(includeArchived ? { include_archived: true } : {}),
      },
    })
  ).data;
}

export async function getPromptBlockCatalog() {
  return (await api.get("/prompt-blocks/catalog")).data;
}

export async function createPromptBlock(body: Record<string, any>) {
  return (await api.post("/prompt-blocks", body)).data;
}

export async function updatePromptBlock(id: string, patch: Record<string, any>) {
  return (await api.patch(`/prompt-blocks/${id}`, patch)).data;
}

export async function deletePromptBlock(id: string) {
  return (await api.delete(`/prompt-blocks/${id}`)).data;
}

export async function createStarterPromptBlocks(projectId: string) {
  return (await api.post("/prompt-blocks/starters", { project_id: projectId })).data;
}

// Project policies (latest version per family unless include_versions).
export async function listProjectPolicies(
  projectId: string,
  domain?: string,
  includeVersions?: boolean
) {
  return (
    await api.get("/project-policies", {
      params: {
        project_id: projectId,
        ...(domain ? { domain } : {}),
        ...(includeVersions ? { include_versions: true } : {}),
      },
    })
  ).data;
}

export async function getPolicyCatalog() {
  return (await api.get("/project-policies/catalog")).data;
}

export async function createProjectPolicy(body: Record<string, any>) {
  return (await api.post("/project-policies", body)).data;
}

export async function getProjectPolicy(id: string) {
  return (await api.get(`/project-policies/${id}`)).data;
}

export async function listProjectPolicyVersions(id: string) {
  return (await api.get(`/project-policies/${id}/versions`)).data;
}

export async function createProjectPolicyVersion(id: string, body: { content: string; description?: string }) {
  return (await api.post(`/project-policies/${id}/versions`, body)).data;
}

export async function updateProjectPolicyStatus(id: string, status: string) {
  return (await api.patch(`/project-policies/${id}`, { status })).data;
}

// Project invariants.
export async function listProjectInvariants(projectId: string, status?: string, domain?: string) {
  return (
    await api.get("/project-invariants", {
      params: {
        project_id: projectId,
        ...(domain ? { domain } : {}),
        ...(status ? { status } : {}),
      },
    })
  ).data;
}

export async function getInvariantCatalog() {
  return (await api.get("/project-invariants/catalog")).data;
}

export async function createProjectInvariant(body: Record<string, any>) {
  return (await api.post("/project-invariants", body)).data;
}

export async function updateProjectInvariant(id: string, patch: Record<string, any>) {
  return (await api.patch(`/project-invariants/${id}`, patch)).data;
}

export async function deleteProjectInvariant(id: string) {
  return (await api.delete(`/project-invariants/${id}`)).data;
}

// ---------------------------------------------------------------------------
// OmniLab V6.1: Prompt Registry — immutable version lifecycle, diff and usage
// (additive; owner+project scoped via `api`). `getPrompt` and
// `listPromptBlocks` are declared above (V6.0).
// ---------------------------------------------------------------------------

export async function listPromptVersions(promptId: string) {
  return (await api.get(`/prompts/${promptId}/versions`)).data;
}

export async function createPromptVersion(promptId: string, body: Record<string, any>) {
  return (await api.post(`/prompts/${promptId}/versions`, body)).data;
}

export async function updatePromptVersion(promptId: string, versionId: string, patch: Record<string, any>) {
  return (await api.patch(`/prompts/${promptId}/versions/${versionId}`, patch)).data;
}

export async function freezePromptVersion(promptId: string, versionId: string) {
  return (await api.post(`/prompts/${promptId}/versions/${versionId}/freeze`)).data;
}

export async function deprecatePromptVersion(promptId: string, versionId: string) {
  return (await api.post(`/prompts/${promptId}/versions/${versionId}/deprecate`)).data;
}

export async function clonePromptVersion(promptId: string, versionId: string) {
  return (await api.post(`/prompts/${promptId}/versions/${versionId}/clone`)).data;
}

export async function restorePromptVersion(promptId: string, versionId: string) {
  return (await api.post(`/prompts/${promptId}/versions/${versionId}/restore`)).data;
}

export async function comparePromptVersions(promptId: string, fromVersionId: string, toVersionId: string) {
  return (
    await api.get(`/prompts/${promptId}/versions/compare`, {
      params: { from_version_id: fromVersionId, to_version_id: toVersionId },
    })
  ).data;
}

export async function getPromptUsage(promptId: string) {
  return (await api.get(`/prompts/${promptId}/usage`)).data;
}

// ---------------------------------------------------------------------------
// OmniLab V6.2: Context Lab — context packs + versions, budget policies,
// run assembly, context preview and run diff (additive; owner+project scoped
// via `api`). Helpers mirror the /api/context-lab contract exactly.
// ---------------------------------------------------------------------------

export async function listContextCatalog() {
  return (await api.get("/context-lab/catalog")).data;
}

export async function listContextPacks(projectId: string, includeArchived = false) {
  return (
    await api.get("/context-lab/packs", {
      params: { project_id: projectId, ...(includeArchived ? { include_archived: true } : {}) },
    })
  ).data;
}

export async function createContextPack(body: { project_id: string; name: string; description?: string }) {
  return (await api.post("/context-lab/packs", body)).data;
}

export async function getContextPack(id: string) {
  return (await api.get(`/context-lab/packs/${id}`)).data;
}

export async function updateContextPack(id: string, patch: Record<string, any>) {
  return (await api.patch(`/context-lab/packs/${id}`, patch)).data;
}

export async function deleteContextPack(id: string) {
  return (await api.delete(`/context-lab/packs/${id}`)).data;
}

export async function listContextPackVersions(packId: string) {
  return (await api.get(`/context-lab/packs/${packId}/versions`)).data;
}

export async function createContextPackVersion(
  packId: string,
  body: {
    description?: string;
    items?: { key?: string; title?: string; source_type?: string; content?: string; order?: number }[];
  }
) {
  return (await api.post(`/context-lab/packs/${packId}/versions`, body)).data;
}

export async function updateContextPackVersion(packId: string, versionId: string, patch: Record<string, any>) {
  return (await api.patch(`/context-lab/packs/${packId}/versions/${versionId}`, patch)).data;
}

export async function freezeContextPackVersion(packId: string, versionId: string) {
  return (await api.post(`/context-lab/packs/${packId}/versions/${versionId}/freeze`)).data;
}

export async function deprecateContextPackVersion(packId: string, versionId: string) {
  return (await api.post(`/context-lab/packs/${packId}/versions/${versionId}/deprecate`)).data;
}

export async function compareContextPackVersions(packId: string, fromVersionId: string, toVersionId: string) {
  return (
    await api.get(`/context-lab/packs/${packId}/versions/compare`, {
      params: { from_version_id: fromVersionId, to_version_id: toVersionId },
    })
  ).data;
}

export async function createStarterContextPacks(projectId: string) {
  return (await api.post("/context-lab/packs/starters", { project_id: projectId })).data;
}

export async function getRunAssembly(runId: string) {
  return (await api.get(`/context-lab/runs/${runId}/assembly`)).data;
}

export async function previewContextAssembly(body: Record<string, any>) {
  return (await api.post("/context-lab/preview", body)).data;
}

export async function diffRunContexts(runA: string, runB: string) {
  return (await api.get("/context-lab/runs/diff", { params: { run_a: runA, run_b: runB } })).data;
}

export async function listContextBudgetPolicies(projectId: string, includeArchived = false) {
  return (
    await api.get("/context-lab/budget-policies", {
      params: { project_id: projectId, ...(includeArchived ? { include_archived: true } : {}) },
    })
  ).data;
}

export async function createContextBudgetPolicy(body: Record<string, any>) {
  return (await api.post("/context-lab/budget-policies", body)).data;
}

export async function getContextBudgetPolicy(id: string) {
  return (await api.get(`/context-lab/budget-policies/${id}`)).data;
}

export async function updateContextBudgetPolicy(id: string, patch: Record<string, any>) {
  return (await api.patch(`/context-lab/budget-policies/${id}`, patch)).data;
}

export async function deleteContextBudgetPolicy(id: string) {
  return (await api.delete(`/context-lab/budget-policies/${id}`)).data;
}

// ---------------------------------------------------------------------------
// OmniLab V6.3: Prompt Compiler — catalog, preview and compile. Preview is
// ephemeral (persisted:false, id:null); compile persists and returns an id.
// The compiled list is owner+project scoped (additive; via `api`).
// ---------------------------------------------------------------------------

export async function getPromptCompilerCatalog(): Promise<CompiledPromptCatalog> {
  return (await api.get("/prompt-compiler/catalog")).data;
}

export async function previewPromptCompilation(body: PromptCompileRequest): Promise<CompiledPrompt> {
  return (await api.post("/prompt-compiler/preview", body)).data;
}

export async function compilePrompt(body: PromptCompileRequest): Promise<CompiledPrompt> {
  return (await api.post("/prompt-compiler/compile", body)).data;
}

export async function listCompiledPrompts(
  params: { project_id?: string; prompt_version_id?: string; limit?: number; offset?: number } = {}
): Promise<CompiledPromptList> {
  return (await api.get("/prompt-compiler/compiled", { params })).data;
}

export async function getCompiledPrompt(id: string): Promise<CompiledPrompt> {
  return (await api.get(`/prompt-compiler/compiled/${id}`)).data;
}

// ---------------------------------------------------------------------------
// OmniLab V6.4: Agent Lab — agent identities, immutable configuration versions,
// lifecycle, diff and reverse usage (additive; owner+project scoped via `api`).
// ---------------------------------------------------------------------------

export async function getAgentCatalog(): Promise<AgentCatalog> {
  return (await api.get("/agents/catalog")).data;
}

export async function listAgents(projectId: string, includeArchived = false): Promise<Agent[]> {
  return (
    await api.get("/agents", {
      params: { project_id: projectId, ...(includeArchived ? { include_archived: true } : {}) },
    })
  ).data;
}

export async function createAgent(body: {
  project_id: string;
  name: string;
  description?: string;
  role?: string | null;
  purpose?: string | null;
  responsibilities?: string[];
  non_responsibilities?: string[];
  status?: string;
}): Promise<Agent> {
  return (await api.post("/agents", body)).data;
}

export async function getAgent(id: string): Promise<Agent> {
  return (await api.get(`/agents/${id}`)).data;
}

export async function updateAgent(id: string, patch: Record<string, any>): Promise<Agent> {
  return (await api.patch(`/agents/${id}`, patch)).data;
}

export async function deleteAgent(id: string): Promise<{ ok?: boolean }> {
  return (await api.delete(`/agents/${id}`)).data;
}

export async function listAgentVersions(agentId: string): Promise<AgentVersion[]> {
  return (await api.get(`/agents/${agentId}/versions`)).data;
}

export async function createAgentVersion(agentId: string, body: Record<string, any>): Promise<AgentVersion> {
  return (await api.post(`/agents/${agentId}/versions`, body)).data;
}

export async function getAgentVersion(agentId: string, versionId: string): Promise<AgentVersion> {
  return (await api.get(`/agents/${agentId}/versions/${versionId}`)).data;
}

export async function updateAgentVersion(
  agentId: string,
  versionId: string,
  patch: Record<string, any>
): Promise<AgentVersion> {
  return (await api.patch(`/agents/${agentId}/versions/${versionId}`, patch)).data;
}

export async function freezeAgentVersion(agentId: string, versionId: string): Promise<AgentVersion> {
  return (await api.post(`/agents/${agentId}/versions/${versionId}/freeze`)).data;
}

export async function deprecateAgentVersion(agentId: string, versionId: string): Promise<AgentVersion> {
  return (await api.post(`/agents/${agentId}/versions/${versionId}/deprecate`)).data;
}

export async function cloneAgentVersion(agentId: string, versionId: string): Promise<AgentVersion> {
  return (await api.post(`/agents/${agentId}/versions/${versionId}/clone`)).data;
}

export async function compareAgentVersions(
  agentId: string,
  fromVersionId: string,
  toVersionId: string
): Promise<AgentVersionCompare> {
  return (
    await api.get(`/agents/${agentId}/versions/compare`, {
      params: { from_version_id: fromVersionId, to_version_id: toVersionId },
    })
  ).data;
}

export async function getAgentUsage(agentId: string, versionId: string): Promise<AgentUsage> {
  return (await api.get(`/agents/${agentId}/versions/${versionId}/usage`)).data;
}

// ---------------------------------------------------------------------------
// OmniLab V6.5: Agent Runtime — execute a frozen/deprecated agent version and
// inspect the real runs. Additive; owner+project scoped via `api`. The run
// result carries runtime_policy_applied so callers can surface unsupported
// fields instead of pretending they were honoured.
// ---------------------------------------------------------------------------

export async function runAgentVersion(body: AgentRunRequest): Promise<AgentRunResult> {
  return (await api.post("/agent-runtime/run", body)).data;
}

export async function listAgentRuns(
  params: {
    agent_version_id?: string;
    agent_id?: string;
    project_id?: string;
    limit?: number;
    offset?: number;
  } = {}
): Promise<AgentRunList> {
  return (await api.get("/agent-runtime/runs", { params })).data;
}

export async function getAgentRun(runId: string): Promise<AgentRunResult> {
  return (await api.get(`/agent-runtime/runs/${runId}`)).data;
}

// ---------------------------------------------------------------------------
// OmniLab V6.6: Agent Evaluation — catalog + evaluate a real agent run and list
// the resulting evaluation documents (additive; owner+project scoped via `api`).
// ---------------------------------------------------------------------------

export async function getAgentEvaluationCatalog(): Promise<AgentEvaluationCatalog> {
  return (await api.get("/agent-evaluation/catalog")).data;
}

export async function evaluateAgentRun(
  runId: string,
  body: AgentEvaluationRequest
): Promise<AgentEvaluationResult> {
  return (await api.post(`/agent-evaluation/runs/${runId}`, body)).data;
}

export async function listAgentRunEvaluations(
  runId: string,
  params: { limit?: number; offset?: number } = {}
): Promise<AgentRunEvaluationList> {
  return (await api.get(`/agent-evaluation/runs/${runId}`, { params })).data;
}

// ---------------------------------------------------------------------------
// OmniLab V6.8: Arena V6 — catalog + agent/prompt arena creation, listing and
// per-configuration summary (additive; owner+project scoped via `api`). Planning
// and execution reuse the existing /experiments/{id}/benchmark endpoints.
// ---------------------------------------------------------------------------

export async function getArenaV6Catalog(): Promise<ArenaV6Catalog> {
  return (await api.get("/arenas/catalog")).data;
}

export async function createAgentArena(body: AgentArenaCreate): Promise<ArenaV6Experiment> {
  return (await api.post("/arenas/agent", body)).data;
}

export async function createPromptArena(body: PromptArenaCreate): Promise<ArenaV6Experiment> {
  return (await api.post("/arenas/prompt", body)).data;
}

export async function listArenasV6(
  params: { project_id?: string; limit?: number; offset?: number } = {}
): Promise<ArenaV6List> {
  return (await api.get("/arenas", { params })).data;
}

export async function getArenaV6Summary(experimentId: string): Promise<ArenaV6Summary> {
  return (await api.get(`/arenas/${experimentId}`)).data;
}

// ---------------------------------------------------------------------------
// OmniLab V6.9: Drift Analysis — catalog + real changed-file/contract drift
// analysis, analysis history/detail and finding materialization (additive;
// owner+project scoped via `api`).
// ---------------------------------------------------------------------------

export async function getDriftCatalog(): Promise<DriftCatalog> {
  return (await api.get("/drift/catalog")).data;
}

export async function analyzeDrift(body: DriftAnalyzeRequest): Promise<DriftAnalysis> {
  return (await api.post("/drift/analyze", body)).data;
}

export async function listDriftAnalyses(
  params: { project_id?: string; limit?: number; offset?: number } = {}
): Promise<DriftAnalysisList> {
  return (await api.get("/drift/analyses", { params })).data;
}

export async function getDriftAnalysis(id: string): Promise<DriftAnalysis> {
  return (await api.get(`/drift/analyses/${id}`)).data;
}

export async function materializeDriftFindings(
  id: string,
  body: Record<string, any> = {}
): Promise<DriftMaterializeResult> {
  return (await api.post(`/drift/analyses/${id}/findings`, body)).data;
}


// ---------------------------------------------------------------------------
// OmniLab V6.14: Workspace Prompt Capture / Composer — workspace modes plus
// capturing a conversation into a draft prompt that reuses the V6 Prompt Lab
// registry (additive; owner+project scoped via `api`).
// ---------------------------------------------------------------------------

export async function getWorkspaceModes(): Promise<WorkspaceModesCatalog> {
  return (await api.get("/workspace-modes")).data;
}

export async function getPromptCaptureCatalog() {
  return (await api.get("/prompt-capture/catalog")).data;
}

export async function analyzePromptCapture(body: {
  project_id: string;
  conversation_id: string;
  message_ids?: string[];
  mode?: string;
  prompt_id?: string | null;
}): Promise<PromptCapturePreview> {
  return (await api.post("/prompt-capture/analyze", body)).data;
}

export async function savePromptCapture(
  body: PromptCaptureSaveRequest
): Promise<PromptCaptureSaveResult> {
  return (await api.post("/prompt-capture/save", body)).data;
}

export async function listPromptCaptures(projectId: string): Promise<PromptCaptureRecord[]> {
  return (await api.get("/prompt-capture", { params: { project_id: projectId } })).data;
}

export async function getPromptCapture(id: string): Promise<PromptCaptureRecord> {
  return (await api.get(`/prompt-capture/${id}`)).data;
}

// ---------------------------------------------------------------------------
// OmniLab V6.14: Workspace Planning Decision Memory — planning state read,
// ingest, item/conflict mutations and consolidated plan generation (additive;
// owner+project scoped via `api`).
// ---------------------------------------------------------------------------

export async function getPlanningStateCatalog(): Promise<PlanningStateCatalog> {
  return (await api.get("/planning-state/catalog")).data;
}

export async function getPlanningState(projectId: string, conversationId: string): Promise<PlanningState> {
  return (await api.get("/planning-state", { params: { project_id: projectId, conversation_id: conversationId } })).data;
}

export async function ingestPlanningConversation(projectId: string, conversationId: string, messageIds?: string[]): Promise<any> {
  return (await api.post(`/planning-state/conversations/${conversationId}/ingest`, { project_id: projectId, message_ids: messageIds || [] })).data;
}

export async function createPlanningItem(body: {
  project_id: string; conversation_id: string; topic_key?: string; category?: string; kind?: string;
  value: string; status?: string; source_type?: string; confidence?: number; source_message_id?: string | null; excerpt?: string;
}): Promise<{ item: PlanningItem; conflict: PlanningConflict | null }> {
  return (await api.post("/planning-state/items", body)).data;
}

export async function updatePlanningItem(itemId: string, patch: {
  topic_key?: string; category?: string; kind?: string; value?: string; status?: string; confidence?: number;
}): Promise<PlanningItem> {
  return (await api.patch(`/planning-state/items/${itemId}`, patch)).data;
}

export async function reopenPlanningItem(itemId: string): Promise<PlanningItem> {
  return (await api.post(`/planning-state/items/${itemId}/reopen`, {})).data;
}

export async function verifyPlanningItem(itemId: string, verificationStatus: string, evidence = ""): Promise<PlanningItem> {
  return (await api.post(`/planning-state/items/${itemId}/verify`, { verification_status: verificationStatus, evidence })).data;
}

export async function listPlanningConflicts(projectId: string, conversationId: string, status?: string): Promise<PlanningConflict[]> {
  return (await api.get("/planning-state/conflicts", { params: { project_id: projectId, conversation_id: conversationId, ...(status ? { status } : {}) } })).data;
}

export async function resolvePlanningConflict(conflictId: string, resolution: string, value = ""): Promise<{ conflict: PlanningConflict; items: PlanningItem[]; state: PlanningState }> {
  return (await api.post(`/planning-state/conflicts/${conflictId}/resolve`, { resolution, value })).data;
}

export async function checkPlanningQuestions(body: { project_id: string; conversation_id: string; questions: string[] }): Promise<{ questions: string[]; allowed: string[]; blocked: { question: string; topic_key: string | null; reason: string }[]; resolved_topics: string[] }> {
  return (await api.post("/planning-state/check-questions", body)).data;
}

export async function generateConsolidatedPlan(conversationId: string, body: { project_id: string; include_policy_ids?: string[]; include_invariant_ids?: string[] }): Promise<ConsolidatedPlan> {
  return (await api.post(`/planning-state/conversations/${conversationId}/consolidated-plan`, body)).data;
}

// ---------------------------------------------------------------------------
// OmniLab V6.15: Structured Planning Graph & Living PRD — graph read/sync,
// node detail and review, impact analysis, PRD readiness, generation,
// versioning, diff and approval (additive; owner+project scoped via `api`).
// ---------------------------------------------------------------------------

export async function getPlanningGraph(projectId: string, conversationId?: string): Promise<PlanningGraph> {
  return (await api.get("/planning-state/graph", { params: { project_id: projectId, ...(conversationId ? { conversation_id: conversationId } : {}) } })).data;
}

export async function syncPlanningGraph(body: { project_id: string; conversation_id?: string | null }): Promise<PlanningGraph> {
  return (await api.post("/planning-state/graph/sync", body)).data;
}

export async function getPlanningGraphCatalog(): Promise<any> {
  return (await api.get("/planning-state/graph/catalog")).data;
}

export async function getPlanningGraphNode(nodeId: string): Promise<PlanningGraphNodeDetail> {
  return (await api.get(`/planning-state/graph/nodes/${nodeId}`)).data;
}

export async function clearPlanningGraphReview(nodeId: string, projectId: string): Promise<any> {
  return (await api.post(`/planning-state/graph/nodes/${nodeId}/clear-review`, { project_id: projectId })).data;
}

export async function analyzePlanningImpact(body: { project_id: string; decision_id?: string | null; node_id?: string | null }): Promise<PlanningImpact> {
  return (await api.post("/planning-state/impact", body)).data;
}

export async function getPrdReadiness(projectId: string, conversationId?: string): Promise<PrdReadiness> {
  return (await api.get("/planning-state/readiness", { params: { project_id: projectId, ...(conversationId ? { conversation_id: conversationId } : {}) } })).data;
}

export async function generatePrd(body: { project_id: string; conversation_id?: string | null }): Promise<PrdVersion> {
  return (await api.post("/planning-state/prd/generate", body)).data;
}

export async function listPrdVersions(projectId: string): Promise<{ versions: PrdVersionSummary[]; latest_version: number | null }> {
  return (await api.get("/planning-state/prd/versions", { params: { project_id: projectId } })).data;
}

export async function getPrdVersion(versionId: string, projectId?: string): Promise<PrdVersion> {
  return (await api.get(`/planning-state/prd/versions/${versionId}`, {
    params: projectId ? { project_id: projectId } : undefined,
  })).data;
}

export async function comparePrdVersions(projectId: string, fromVersionId: string, toVersionId: string): Promise<PrdDiff> {
  return (await api.get("/planning-state/prd/compare", { params: { project_id: projectId, from_version_id: fromVersionId, to_version_id: toVersionId } })).data;
}

export async function approvePrdVersion(versionId: string, projectId: string): Promise<PrdVersion> {
  return (await api.post(`/planning-state/prd/versions/${versionId}/approve`, { project_id: projectId })).data;
}
