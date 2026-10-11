// Domain types — mirror the backend API contracts (database-agnostic).

export interface User {
  id: string;
  email: string;
  name: string;
  role: string;
  created_at: string;
}

export interface StructuredError {
  code?: string;
  category?: string;
  message?: string;
  retryable?: boolean;
  source?: string | null;
  upstream_status?: number | null;
  details_safe?: Record<string, any> | null;
}

export interface Project {
  id: string;
  owner_id: string;
  name: string;
  description: string;
  tags: string[];
  status: string;
  created_at: string;
  updated_at: string;
  stats?: { experiments: number; conversations: number; artifacts: number; runs: number };
}

export interface Provider {
  id: string;
  owner_id: string;
  name: string;
  provider_type: "openai_compatible";
  base_url: string | null;
  enabled: boolean;
  has_api_key: boolean;
  seeded?: boolean;
  supports_model_discovery?: boolean;
  capabilities?: Record<string, any>;
  metadata: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface ModelRecord {
  id: string;
  owner_id: string;
  provider_id: string;
  display_name: string;
  model_identifier: string;
  family: string | null;
  enabled: boolean;
  context_window: number | null;
  max_output_tokens?: number | null;
  input_cost: number | null;
  output_cost: number | null;
  input_price_per_1m?: number | null;
  output_price_per_1m?: number | null;
  input_price_per_1m_tokens?: number | null;
  output_price_per_1m_tokens?: number | null;
  cache_read_price_per_1m?: number | null;
  cache_write_price_per_1m?: number | null;
  cache_creation_price_per_1m?: number | null;
  reasoning_price_per_1m?: number | null;
  raw_pricing?: Record<string, any> | null;
  capabilities: Record<string, any>;
  capability_source?: "PROVIDER" | "VERIFIED" | "MANUAL" | "UNKNOWN";
  modalities?: Record<string, string[]>;
  provider_metadata?: Record<string, any>;
  pricing_source?: string | null;
  pricing_updated_at?: string | null;
  tags: string[];
  notes: string;
  seeded?: boolean;
  created_at: string;
  updated_at: string;
}

export interface TokenUsage {
  input_tokens?: number | null;
  output_tokens?: number | null;
  reasoning_tokens?: number | null;
  cache_read_tokens?: number | null;
  cache_write_tokens?: number | null;
  cache_creation_tokens?: number | null;
  uncached_input_tokens?: number | null;
  total_tokens?: number | null;
  audio_tokens?: number | null;
  image_tokens?: number | null;
  raw_usage?: Record<string, any> | null;
}

export interface CacheTelemetry {
  cache_supported?: boolean | null;
  cache_read_tokens?: number | null;
  cache_write_tokens?: number | null;
  cache_creation_tokens?: number | null;
  uncached_input_tokens?: number | null;
  cache_hit_rate?: number | null;
  ttl_seconds?: number | null;
  raw_cache_details?: Record<string, any> | null;
}

export interface ReasoningTelemetry {
  available: boolean;
  content?: string | null;
  summary?: string | null;
  tokens?: number | null;
  streamed?: boolean;
  source?: string | null;
  raw?: Record<string, any>;
}

export interface ReasoningSegment {
  model_call_id?: string | null;
  round_index?: number | null;
  phase?: string | null;
  content?: string | null;
  duration_ms?: number | null;
}

export interface CostBreakdown {
  cost_available: boolean;
  input_cost?: number | null;
  uncached_input_cost?: number | null;
  cache_read_cost?: number | null;
  cache_write_cost?: number | null;
  cache_creation_cost?: number | null;
  output_cost?: number | null;
  reasoning_cost?: number | null;
  total_cost?: number | null;
  updated_at?: string;
}

export interface Telemetry {
  finish_reason?: string | null;
  latency_ms?: number | null;
  ttft_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  reasoning_tokens?: number | null;
  cache_read_tokens?: number | null;
  cache_write_tokens?: number | null;
  cache_creation_tokens?: number | null;
  uncached_input_tokens?: number | null;
  total_tokens?: number | null;
  estimated_cost?: number | null;
  cost_available?: boolean;
  trace_id?: string | null;
  token_usage?: TokenUsage | null;
  cache_telemetry?: CacheTelemetry | null;
  reasoning_telemetry?: ReasoningTelemetry | null;
  cost_breakdown?: CostBreakdown | null;
}

export interface ContentBlock {
  type: string;
  text?: string | null;
  attachment_id?: string | null;
  metadata?: Record<string, any>;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  finish_reason?: string | null;
  output_limit?: boolean;
  output_limit_reason?: string | null;
  continuation_count?: number;
  run_chain?: string[];
  output_limit_exhausted?: boolean;
  content_blocks?: ContentBlock[];
  attachment_ids?: string[];
  model_id?: string;
  model_name?: string;
  run_id?: string;
  status?: string;
  error?: string | null;
  telemetry?: Telemetry;
  trace_id?: string | null;
  enabled_tool_ids?: string[];
  tool_call_count?: number | null;
  reasoning_content?: string | null;
  reasoning_summary?: string | null;
  reasoning_telemetry?: ReasoningTelemetry | null;
  reasoning_segments?: ReasoningSegment[];
  created_at: string;
}

export interface ConversationDefaults {
  default_model_id?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
  top_p?: number | null;
  streaming?: boolean | null;
  enabled_tool_ids?: string[];
  context_strategy?: string;
  context_options?: Record<string, any>;
}

export interface Conversation {
  id: string;
  project_id: string;
  title: string;
  system_prompt: string;
  created_at: string;
  updated_at: string;
  messages?: Message[];
  defaults?: ConversationDefaults;
  workspace_mode?: string | null;
}

export interface FinalizationTelemetryEntry {
  finalization_attempt?: number | null;
  finalization_recovery?: string | null;
  recovery_mode?: string | null;
  finish_reason?: string | null;
  requested_max_output_tokens?: number | null;
  effective_max_output_tokens?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  reasoning_tokens?: number | null;
  visible_content_tokens?: number | null;
  total_completion_tokens?: number | null;
  context_window?: number | null;
  estimated_context_tokens?: number | null;
  remaining_context_capacity?: number | null;
  had_reasoning?: boolean | null;
  had_visible_content?: boolean | null;
  length_failure_kind?: string | null;
  reasoning_effort?: string | null;
  reasoning_reduction_applied?: boolean | null;
}

export interface Run {
  id: string;
  experiment_id?: string | null;
  project_id?: string | null;
  context: string;
  provider_name?: string;
  provider_type?: string;
  model_id?: string;
  model_name?: string;
  model_identifier?: string;
  family?: string | null;
  system_prompt?: string;
  params?: Record<string, any>;
  status: "success" | "error";
  latency_ms?: number;
  input_tokens?: number;
  output_tokens?: number;
  estimated_cost?: number | null;
  cost_available?: boolean;
  pricing_snapshot?: PricingSnapshot | null;
  raw_response?: string | null;
  normalized_response?: string | null;
  finish_reason?: string | null;
  error?: string | StructuredError | null;
  error_message?: string | null;
  content?: string | null;
  cached_tokens?: number | null;
  reasoning_tokens?: number | null;
  cache_read_tokens?: number | null;
  cache_write_tokens?: number | null;
  cache_creation_tokens?: number | null;
  uncached_input_tokens?: number | null;
  total_tokens?: number | null;
  reasoning_content?: string | null;
  reasoning_summary?: string | null;
  token_usage?: TokenUsage | null;
  cache_telemetry?: CacheTelemetry | null;
  reasoning_telemetry?: ReasoningTelemetry | null;
  reasoning_segments?: ReasoningSegment[];
  cost_breakdown?: CostBreakdown | null;
  streaming?: boolean;
  trace_id?: string | null;
  dataset_id?: string | null;
  root_run_id?: string | null;
  parent_run_id?: string | null;
  continuation_index?: number;
  continuation_count?: number;
  continuation_reason?: string | null;
  run_chain?: string[];
  output_limit_exhausted?: boolean;
  tool_call_count?: number | null;
  context_hash?: string | null;
  execution_kind?: string | null;
  configuration_id?: string | null;
  dataset_version_id?: string | null;
  dataset_case_id?: string | null;
  repetition_index?: number | null;
  attempt_index?: number | null;
  benchmark_plan_id?: string | null;
  created_at: string;
  finalization_telemetry?: FinalizationTelemetryEntry[];
  forced_finalization?: boolean | null;
  finalization_reason?: string | null;
  finalization_attempts?: number | null;
  finalization_recovered?: boolean | null;
  finalization_outcome?: string | null;
  provider_finish_reason?: string | null;
  had_reasoning?: boolean | null;
  had_content?: boolean | null;
  content_length?: number | null;
  finalization_recovery_mode?: string | null;
  finalization_context_mode?: "FULL" | "EVIDENCE_VIEW" | null;
  original_context_estimated_tokens?: number | null;
  finalization_context_estimated_tokens?: number | null;
  evidence_item_count?: number | null;
  source_count?: number | null;
  deduplicated_item_count?: number | null;
  finalization_length_failure_kind?: string | null;
  finalization_partial_answer?: boolean | null;
  finalization_truncated?: boolean | null;
  finalization_requested_max_output_tokens?: number | null;
  finalization_effective_max_output_tokens?: number | null;
  label?: string | null;
}

export interface ExperimentConfiguration {
  id?: string;
  label: string;
  model_id: string;
  temperature?: number | null;
  max_tokens?: number | null;
  top_p?: number | null;
  system_prompt?: string;
  prompt_version_id?: string | null;
  metadata?: Record<string, any>;
  is_control?: boolean;
  order?: number;
  configuration_hash?: string;
  model_snapshot?: Record<string, any>;
  prompt_version_snapshot?: Record<string, any> | null;
}

export interface ExperimentProtocol {
  objective?: string;
  hypothesis?: string;
  dataset_id?: string | null;
  dataset_version_id?: string | null;
  rubric_id?: string | null;
  judge_model_id?: string | null;
  judge_rubric_id?: string | null;
  variables?: Record<string, any>[];
  constants?: Record<string, any>;
  repetitions?: number;
  success_criteria?: string[];
  max_planned_calls?: number | null;
}

export interface BenchmarkEstimatedCost {
  available: boolean;
  method?: string | null;
  currency?: string;
  min?: number | null;
  max?: number | null;
  unknown_pricing_config_count?: number;
}

export interface BenchmarkProgress {
  total: number;
  completed: number;
  failed: number;
  running: number;
  pending: number;
  cancelled: number;
  skipped: number;
}

export interface BenchmarkPlan {
  id: string;
  experiment_id: string;
  dataset_id?: string | null;
  dataset_version_id?: string | null;
  configuration_ids: string[];
  case_ids: string[];
  repetitions: number;
  total_planned_calls: number;
  plan_hash?: string;
  status: string;
  progress: BenchmarkProgress;
  estimated_cost?: BenchmarkEstimatedCost;
  requires_confirmation?: boolean;
  created_at: string;
  updated_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
}

export interface Experiment {
  id: string;
  project_id: string;
  title: string;
  objective?: string;
  hypothesis?: string;
  experiment_type: string;
  system_prompt?: string;
  user_prompt?: string;
  params?: Record<string, any>;
  model_ids: string[];
  blind: boolean;
  blind_map?: { label: string; run_id: string; model_id: string; model_name: string }[];
  rubric_id?: string | null;
  status: string;
  run_count: number;
  parent_experiment_id?: string;
  cloned_from?: string;
  created_at: string;
  updated_at: string;
  runs?: Run[];
  evaluations?: Evaluation[];
  revealed?: boolean;
  protocol?: ExperimentProtocol;
  configurations?: ExperimentConfiguration[];
  configuration_hash?: string;
  protocol_hash?: string;
  dataset_version_hash?: string;
  runner_version?: string;
  schema_version?: number;
  dataset_version_id?: string | null;
  benchmark_plan?: BenchmarkPlan | null;
  legacy?: boolean;
}

export interface RubricDimension {
  key: string;
  label: string;
  description?: string;
  max_score: number;
}

export interface Rubric {
  id: string;
  name: string;
  description: string;
  dimensions: RubricDimension[];
  seeded?: boolean;
  created_at: string;
  updated_at: string;
}

export interface Evaluation {
  id: string;
  experiment_id: string;
  eval_type: "ai_judge" | "human";
  judge_model_name?: string;
  rubric_snapshot?: Rubric;
  result?: {
    evaluations?: { run_id: string; scores: Record<string, number>; total: number; rationale: string }[];
    ranking?: string[];
    winner_run_id?: string;
    confidence?: number;
    summary?: string;
  };
  raw_output?: string;
  status: string;
  error?: string | null;
  // human
  run_id?: string;
  model_name?: string;
  scores?: Record<string, number>;
  winner?: boolean;
  notes?: string;
  created_at: string;
}

export interface CaseEvaluation {
  id: string;
  experiment_id: string;
  run_id: string;
  dataset_case_id?: string | null;
  configuration_id?: string | null;
  repetition_index?: number | null;
  evaluator_type: string;
  method?: string | null;
  rubric_id?: string | null;
  rubric_snapshot?: any;
  judge_model_id?: string | null;
  judge_model_snapshot?: any;
  judge_run_id?: string | null;
  raw_result?: any;
  parsed_score: number | null;
  status: string;
  error?: string | null;
  error_message?: string | null;
  usage?: {
    input_tokens?: number | null;
    output_tokens?: number | null;
    cost?: number | null;
    cost_available?: boolean;
  };
  created_at: string;
}

export interface ConfigAggregate {
  configuration_id: string;
  label?: string;
  total_cases: number;
  completed: number;
  failed: number;
  success_rate: number | null;
  evaluated_count?: number;
  avg_score: number | null;
  median_score: number | null;
  avg_latency_ms: number | null;
  median_latency_ms: number | null;
  p95_latency_ms: number | null;
  latency_sample_count?: number;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  cached_tokens?: number | null;
  reasoning_tokens?: number | null;
  known_usage_run_count?: number;
  unknown_usage_run_count?: number;
  known_cost_total: number | null;
  unknown_cost_run_count: number;
  avg_known_cost_per_task: number | null;
  evaluation_cost_total: number | null;
  execution_cost_total: number | null;
  total_experiment_cost: number | null;
}

export interface CaseResultRow {
  dataset_case_id: string;
  input_text?: string;
  results: Record<string, {
    run_id?: string | null;
    status?: string | null;
    latency_ms?: number | null;
    input_tokens?: number | null;
    output_tokens?: number | null;
    cost_known?: boolean;
    cost?: number | null;
    score?: number | null;
    evaluation_id?: string | null;
  }>;
}

export interface ExperimentResults {
  legacy?: boolean;
  configurations: ConfigAggregate[];
  overall?: Record<string, any>;
  cases?: CaseResultRow[];
  total_cases_count?: number;
}

export interface CompareResult {
  experiment_id?: string;
  comparable: boolean;
  baseline?: any;
  candidate?: any;
  deltas: {
    avg_score: number | null;
    success_rate: number | null;
    avg_latency_ms: number | null;
    known_cost_total: number | null;
  };
  case_counts: {
    improved: number;
    regressed: number;
    unchanged: number;
    incomparable_error: number;
  };
  changed_cases: CompareResultCase[];
  cases?: CompareResultCase[];
}

export interface CompareResultCase {
  case_id: string;
  input_snippet?: string;
  classification: string;
  baseline_score: number | null;
  candidate_score: number | null;
  delta?: number | null;
}

export type ArtifactType =
  | "research_report" | "architecture" | "code" | "prompt"
  | "evaluation_report" | "experiment_result" | "decision_memo"
  | "dataset" | "generic_document"
  | "research_note" | "report" | "evaluation_report_legacy" | "text" | string;

export interface Artifact {
  id: string;
  project_id: string;
  title: string;
  artifact_type: ArtifactType;
  content: string;
  source: string;
  source_ref?: string | null;
  version: number;
  metadata: Record<string, any>;
  creator_name?: string;
  model_id?: string | null;
  model_name?: string | null;
  run_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface CouncilParticipant {
  participant_id?: string;
  role: string;
  display_name?: string;
  perspective: string;
  model_id: string;
  temperature?: number | null;
  max_tokens?: number | null;
  order?: number;
  enabled?: boolean;
}

export interface CouncilMessage {
  participant_id: string;
  role: string;
  display_name?: string;
  model_id: string;
  model_name?: string;
  provider_id?: string | null;
  provider_name?: string | null;
  provider_type?: string | null;
  model_identifier?: string | null;
  family?: string | null;
  content: string;
  status: string;
  error?: string | null;
  run_id?: string;
  telemetry?: Telemetry;
}
export interface CouncilRound {
  round: number;
  name: string;
  messages: CouncilMessage[];
}
export interface CouncilSynthesis {
  consensus?: string[]; disagreements?: string[]; key_risks?: string[];
  critical_unknowns?: string[]; strongest_arguments?: string[];
  recommended_decision?: string; confidence?: number; conditions?: string[];
  suggested_next_actions?: string[];
}
export interface Council {
  id: string;
  project_id: string;
  title: string;
  question: string;
  participants: CouncilParticipant[];
  moderator_model_id: string;
  moderator_model_name?: string;
  rounds: CouncilRound[];
  synthesis?: CouncilSynthesis;
  synthesis_raw?: string | null;
  synthesis_error?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ResearchSource { title: string; url: string; note: string }
export interface ResearchSession {
  id: string;
  project_id: string;
  title: string;
  research_question: string;
  notes: string;
  status: string;
  tags: string[];
  sources: ResearchSource[];
  related_experiment_ids: string[];
  related_artifact_ids: string[];
  related_conversation_ids: string[];
  related_council_ids: string[];
  related_finding_ids: string[];
  related_dataset_ids: string[];
  related_run_ids: string[];
  linked_experiments?: Experiment[];
  linked_artifacts?: Artifact[];
  linked_conversations?: Conversation[];
  linked_councils?: Council[];
  linked_findings?: Finding[];
  linked_datasets?: Dataset[];
  created_at: string;
  updated_at: string;
}

export type FindingEvidenceType =
  | "experiment" | "research" | "artifact" | "run"
  | "evaluation" | "council" | "dataset" | "conversation" | string;
export interface FindingEvidence { type: FindingEvidenceType; ref_id: string; label: string; exists?: boolean; route?: string | null; title?: string }
export interface Finding {
  id: string;
  project_id: string;
  title: string;
  statement: string;
  description: string;
  confidence: string;
  status: string;
  notes: string;
  tags: string[];
  evidence: FindingEvidence[];
  resolved_evidence?: FindingEvidence[];
  created_at: string;
  updated_at: string;
}

export interface Dataset {
  id: string;
  project_id: string;
  name: string;
  description: string;
  tags: string[];
  created_at: string;
  updated_at: string;
  case_count?: number;
  cases?: DatasetCase[];
}

export interface DatasetCase {
  id: string;
  dataset_id: string;
  project_id: string;
  input_text: string;
  expected_output: string;
  metadata: Record<string, any>;
  tags: string[];
  ordering: number;
  created_at: string;
  updated_at: string;
}

export interface DatasetVersion {
  id: string;
  dataset_id: string;
  project_id: string;
  version: number;
  status: "draft" | "frozen";
  description?: string;
  case_count: number;
  content_hash?: string | null;
  created_at: string;
  updated_at?: string;
  frozen_at?: string | null;
  source_version_id?: string | null;
}

export interface DatasetVersionCase {
  id: string;
  dataset_id: string;
  dataset_version_id: string;
  project_id: string;
  input_text: string;
  expected_output: string;
  metadata: Record<string, any>;
  tags: string[];
  ordering: number;
  evaluation_hints?: Record<string, any>;
  reference_answer?: string | null;
  assertions?: any[];
  tool_policy?: Record<string, any>;
  knowledge_policy?: Record<string, any>;
  grounding_expectations?: Record<string, any>;
  created_at: string;
  updated_at?: string;
}

export interface ExperimentProvenance {
  legacy?: boolean;
  dataset_version?: any;
  configurations?: any[];
  protocol_hash?: string;
  configuration_hash?: string;
  dataset_version_hash?: string;
  runner_version?: string;
  schema_version?: number;
  rubric_snapshot?: any;
  judge_model_snapshot?: any;
  created_at?: string;
  ready_at?: string | null;
  [k: string]: any;
}

export interface ObsPoint {
  created_at: string; model_id: string; model_name: string;
  model_identifier?: string | null;
  provider_id?: string | null;
  provider_name?: string | null;
  project_id?: string | null;
  latency_ms: number | null; ttft_ms?: number | null;
  input_tokens: number | null; output_tokens: number | null;
  estimated_cost: number | null; cost_available?: boolean;
  pricing_snapshot?: PricingSnapshot | null; status: string; context: string;
  run_id?: string | null;
  experiment_id?: string | null;
  cached_tokens?: number | null;
  reasoning_tokens?: number | null;
  cache_read_tokens?: number | null;
  cache_write_tokens?: number | null;
  cache_creation_tokens?: number | null;
  uncached_input_tokens?: number | null;
  total_tokens?: number | null;
  reasoning_content?: string | null;
  reasoning_summary?: string | null;
  token_usage?: TokenUsage | null;
  cache_telemetry?: CacheTelemetry | null;
  reasoning_telemetry?: ReasoningTelemetry | null;
  cost_breakdown?: CostBreakdown | null;
  execution_kind?: string | null;
  streaming?: boolean;
}
export interface ObsProviderQuotaRow {
  provider: string;
  resource: string;
  window: string;
  window_seconds: number;
  units_consumed: number;
  window_limit: number | null;
  window_remaining: number | null;
  window_reset_seconds: number;
}

export interface Observability {
  series: ObsPoint[];
  models: { model_id: string; model_name: string }[];
  providers: { provider_id: string; provider_name: string }[];
  totals: {
    requests: number; failures: number; avg_latency_ms: number | null;
    total_tokens: number | null; total_cost: number | null; cost_available: boolean;
    success_rate?: number | null; avg_ttft_ms?: number | null;
    input_tokens?: number | null; output_tokens?: number | null;
    known_cost_total?: number | null;
    unknown_cost_run_count?: number;
    known_usage_run_count?: number;
    unknown_usage_run_count?: number;
    input_tokens_known?: number;
    output_tokens_known?: number;
    scope?: string;
    project_id?: string | null;
  };
  provider_quota?: ObsProviderQuotaRow[] | null;
}

export interface Overview {
  counts: Record<string, number>;
  recent_experiments: Experiment[];
  recent_runs: Run[];
  failures: Run[];
  model_usage: {
    model_name: string; requests: number; input_tokens: number; output_tokens: number;
    avg_latency_ms: number; estimated_cost: number | null; cost_available: boolean;
    success: number; failures: number;
  }[];
  cost_summary: { total_estimated_cost: number | null; cost_available: boolean; total_requests: number };
  scope?: "project" | "global";
  project_id?: string | null;
  known_cost_total?: number | null;
  unknown_cost_run_count?: number;
}
export interface ProviderModelCatalogItem {
  model_identifier: string;
  display_name: string;
  context_window: number | null;
  input_cost: number | null;
  output_cost: number | null;
  input_price_per_1m: number | null;
  output_price_per_1m: number | null;
  input_price_per_1m_tokens: number | null;
  output_price_per_1m_tokens: number | null;
  cache_read_price_per_1m?: number | null;
  cache_write_price_per_1m?: number | null;
  cache_creation_price_per_1m?: number | null;
  reasoning_price_per_1m?: number | null;
  raw_pricing?: Record<string, any> | null;
  capabilities: Record<string, any>;
  modalities: Record<string, string[]>;
  provider_metadata: Record<string, any>;
  pricing_source: string | null;
  pricing_updated_at: string | null;
}

export interface ProviderModelCatalog {
  provider_id: string;
  provider_type: string;
  supports_model_discovery: boolean;
  capabilities?: Record<string, any>;
  models: ProviderModelCatalogItem[];
}

export interface PricingSnapshot {
  input_price_per_1m: number | null;
  output_price_per_1m: number | null;
  input_price_per_1m_tokens?: number | null;
  output_price_per_1m_tokens?: number | null;
  cache_read_price_per_1m?: number | null;
  cache_write_price_per_1m?: number | null;
  cache_creation_price_per_1m?: number | null;
  reasoning_price_per_1m?: number | null;
  pricing_source?: string | null;
  pricing_updated_at?: string | null;
  captured_at: string;
  currency: string;
  provider?: string | null;
  source?: string | null;
  fetched_at?: string | null;
  raw_pricing?: Record<string, any> | null;
}

// ---------------------------------------------------------------------------
// Phase 3: attachments, tools, traces, context snapshots, run comparison
// ---------------------------------------------------------------------------

export interface Attachment {
  id: string;
  owner_id: string;
  project_id: string;
  conversation_id?: string | null;
  filename: string;
  original_filename: string;
  mime_type: string;
  kind: "text" | "image" | "pdf" | "unsupported";
  extension?: string;
  size_bytes: number;
  sha256: string;
  processing_status: string;
  extraction_status: string;
  char_count?: number | null;
  text_preview?: string | null;
  has_extracted_text?: boolean;
  image_metadata?: Record<string, any> | null;
  metadata?: Record<string, any>;
  detached?: boolean;
  referenced_by_run?: boolean;
  created_at: string;
  updated_at?: string;
}

export interface ToolRecord {
  id: string;
  name: string;
  display_name: string;
  description: string;
  version: string;
  tool_type: string;
  input_schema: Record<string, any>;
  output_schema?: Record<string, any>;
  enabled: boolean;
  risk_level: string;
  permissions: string[];
  implementation?: string;
  scope?: string;
  project_id?: string | null;
  builtin?: boolean;
  available?: boolean;
  availability_reason?: string | null;
  tags?: string[];
  created_at?: string;
  updated_at?: string;
}

export interface TraceSummary {
  id: string;
  trace_type: string;
  status: string;
  project_id?: string | null;
  experiment_id?: string | null;
  conversation_id?: string | null;
  council_id?: string | null;
  root_run_id?: string | null;
  started_at: string;
  completed_at?: string | null;
  total_duration_ms?: number | null;
  span_count?: number;
  run_status?: string | null;
  model_name?: string | null;
  [k: string]: any;
}

export interface TraceSpan {
  id: string;
  trace_id: string;
  parent_span_id?: string | null;
  span_type: string;
  name: string;
  status: string;
  started_at: string;
  completed_at?: string | null;
  duration_ms?: number | null;
  run_id?: string | null;
  metadata?: Record<string, any>;
  structured_error?: StructuredError | null;
  ordering?: number;
  [k: string]: any;
}

export interface ToolCallRecord {
  id: string;
  trace_id?: string;
  run_id?: string;
  tool_id?: string;
  tool_name?: string;
  tool_version?: string;
  input?: any;
  validation_status?: string;
  permission_status?: string;
  execution_status?: string;
  output?: any;
  error?: any;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number | null;
  [k: string]: any;
}

export interface TraceDetail {
  available?: boolean;
  trace?: TraceSummary | Record<string, any>;
  spans?: TraceSpan[];
  runs?: Run[];
  tool_calls?: ToolCallRecord[];
  blind?: boolean;
  message?: string;
  reason?: string;
  run_id?: string;
  trace_id?: string | null;
}

export interface ContextComponent {
  component_type: string;
  ordering: number;
  source_ref?: string | null;
  content: string;
  content_hash?: string;
  token_count?: number | null;
  estimated_tokens?: number;
  estimated_method?: string;
  included: boolean;
  excluded_reason?: string | null;
  truncated?: boolean;
  truncation_reason?: string | null;
  metadata?: Record<string, any>;
}

export interface ContextSnapshot {
  id: string;
  run_id: string;
  trace_id?: string | null;
  project_id?: string | null;
  components: ContextComponent[];
  excluded_components?: ContextComponent[];
  total_context_tokens?: number | null;
  known_token_count?: number | null;
  estimated_total_tokens?: number | null;
  estimated_method?: string;
  unknown_token_component_count?: number;
  context_hash?: string;
  strategy?: string;
  over_budget?: boolean;
  warnings?: string[];
  created_at: string;
}

export interface ContextResponse {
  available: boolean;
  run_id?: string;
  trace_id?: string | null;
  snapshot?: ContextSnapshot;
  provider_input_tokens?: number | null;
  message?: string;
  reason?: string;
}

export interface RunCompareToolCall {
  name: string;
  status: string;
  duration_ms?: number | null;
}

export interface RunCompareToolSummary {
  enabled: string[];
  calls: RunCompareToolCall[];
}

export interface RunCompare {
  run_a: Run;
  run_b: Run;
  identity: {
    model_a?: string | null;
    model_b?: string | null;
    provider_a?: string | null;
    provider_b?: string | null;
  };
  parameters: {
    a: Record<string, any>;
    b: Record<string, any>;
    diff: Record<string, { a: any; b: any }>;
  };
  context: { a: any; b: any };
  tools: { a: RunCompareToolSummary; b: RunCompareToolSummary };
  result: { a: any; b: any };
  failures: { a: any[]; b: any[] };
  context_diff?: any | null;
}

// ---------------------------------------------------------------------------
// Failure Lab: execution + quality failure records, aggregates and detail
// ---------------------------------------------------------------------------

export interface FailureHistoryEvent {
  at: string;
  actor?: string;
  action: string;
  previous?: Record<string, any>;
  category?: string | null;
  subcategory?: string | null;
  source?: string | null;
  severity?: string | null;
  status?: string | null;
  note?: string;
}

export interface Failure {
  id: string;
  owner_id?: string | null;
  project_id?: string | null;
  run_id?: string | null;
  trace_id?: string | null;
  evaluation_id?: string | null;
  category: string;
  subcategory?: string;
  source: string;
  severity: string;
  title: string;
  description?: string;
  evidence?: Record<string, any>;
  confidence?: number | null;
  tags?: string[];
  failure_class?: "execution" | "quality" | string;
  status: string;
  resolved_at?: string | null;
  tool_id?: string | null;
  run_model_id?: string | null;
  run_model_name?: string | null;
  model_id?: string | null;
  experiment_id?: string | null;
  history?: FailureHistoryEvent[];
  created_at: string;
  updated_at?: string;
}

export interface FailureAggregate {
  total: number;
  execution_count: number;
  quality_count: number;
  by_category: { category: string; count: number }[];
  by_source: { source: string; count: number }[];
  by_status: { status: string; count: number }[];
  by_severity: { severity: string; count: number }[];
  by_model: { model: string; count: number }[];
  by_experiment: { experiment_id: string; count: number }[];
  by_tool: { tool_id: string; count: number }[];
  recent: Failure[];
}

export interface FailureDetail {
  failure: Failure;
  run?: Run | null;
  trace?: (TraceSummary & Record<string, any>) | Record<string, any> | null;
  context_available: boolean;
  evaluation?: Evaluation | null;
  tool_calls?: ToolCallRecord[];
}

// ---------------------------------------------------------------------------
// OmniLab V4: project workspace platform (additive)
// ---------------------------------------------------------------------------

export interface ProjectFileReferences {
  attachment_id?: string | null;
  knowledge_documents?: { id: string; collection_id?: string }[];
  run_count?: number;
  [k: string]: any;
}

export interface ProjectFile {
  id: string;
  owner_id: string;
  project_id: string;
  attachment_id?: string | null;
  display_name: string;
  description?: string;
  tags: string[];
  mime_type?: string | null;
  kind?: string | null;
  size_bytes?: number | null;
  sha256?: string | null;
  processing_status?: string;
  status: string;
  metadata?: Record<string, any>;
  created_at: string;
  updated_at?: string;
  references?: ProjectFileReferences;
}

export interface KnowledgeCollection {
  id: string;
  owner_id: string;
  project_id: string;
  name: string;
  description?: string;
  status?: string;
  embedding_config?: Record<string, any>;
  chunking_config?: Record<string, any>;
  doc_count?: number;
  chunk_count?: number;
  document_count?: number;
  created_at: string;
  updated_at?: string;
}

export interface KnowledgeDocument {
  id: string;
  owner_id: string;
  project_id: string;
  collection_id: string;
  project_file_id?: string | null;
  display_name?: string;
  status?: string;
  ingestion_status?: string;
  processing_status?: string;
  parser?: Record<string, any>;
  chunker?: Record<string, any>;
  index?: Record<string, any>;
  error?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  content_hash?: string | null;
  chunk_count?: number | null;
  token_estimate?: number | null;
  created_at: string;
  updated_at?: string;
}

export interface KnowledgeChunk {
  id: string;
  collection_id: string;
  document_id?: string | null;
  content?: string;
  content_preview?: string;
  chunk_index?: number | null;
  token_estimate?: number | null;
  metadata?: Record<string, any>;
  [k: string]: any;
}

export interface ToolVersionRecord {
  id: string;
  owner_id?: string;
  tool_id: string;
  version?: number | null;
  version_label?: string | null;
  tool_type: string;
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  permissions?: string[];
  risk_level?: string;
  implementation_config?: Record<string, any>;
  source?: string;
  created_at?: string;
}

export interface CustomToolRecord extends ToolRecord {
  tool_type: string;
  current_version_id?: string | null;
}

export interface ToolCredential {
  id: string;
  owner_id: string;
  project_id: string;
  name: string;
  credential_type: string;
  has_value: boolean;
  metadata?: Record<string, any>;
  created_at?: string;
  updated_at?: string;
}

export interface CodeInterpreterStatus {
  available: boolean;
  reason?: string | null;
  image?: string | null;
  runtime?: string | null;
  limits?: Record<string, any>;
  policy?: {
    network?: string;
    packages?: string[];
    tool?: string;
    permissions?: string[];
    risk?: string;
  };
}

export interface CodeExecution {
  id: string;
  execution_id?: string;
  owner_id?: string;
  project_id?: string;
  status: string;
  code?: string;
  stdout?: string;
  stderr?: string;
  stdout_truncated?: boolean;
  stderr_truncated?: boolean;
  output_limited?: boolean;
  exit_code?: number | null;
  duration_ms?: number | null;
  files?: { filename?: string; name?: string; path?: string }[] | string[];
  error?: string | null;
  created_at?: string;
  [k: string]: any;
}

export interface ProjectOverview {
  project: Project;
  counts: Record<string, number>;
  known_cost: number | null;
  recent_activity: Record<string, any>[];
  recent_conversations: Conversation[];
  recent_experiments: Experiment[];
}

export interface ProjectActivityItem {
  id?: string;
  activity_type?: string;
  message?: string;
  ref_type?: string | null;
  ref_id?: string | null;
  created_at?: string;
  [k: string]: any;
}

export interface RetrievalResult {
  chunk?: KnowledgeChunk & Record<string, any>;
  score?: number | null;
  document?: Record<string, any>;
  collection_id?: string;
  collection_name?: string;
  [k: string]: any;
}

export interface KnowledgeSearchResponse {
  results?: RetrievalResult[];
  items?: RetrievalResult[];
  query?: string;
  models_used?: string[];
  [k: string]: any;
}

export interface OpenAPIOperationPreview {
  operation_id: string;
  method: string;
  path: string;
  summary?: string;
  description?: string;
  url_template?: string;
  [k: string]: any;
}

export interface OpenAPIImportPreview {
  title?: string;
  version?: string;
  servers?: any[];
  base_url?: string | null;
  operation_count?: number;
  operations: OpenAPIOperationPreview[];
}

// Extended core records (interface merging adds optional additive fields).
export interface Project {
  default_model_id?: string | null;
  default_system_instruction?: string;
  default_temperature?: number | null;
  default_max_tokens?: number | null;
  default_top_p?: number | null;
  default_streaming?: boolean | null;
  default_context_strategy?: string;
  default_context_options?: Record<string, any>;
  default_enabled_tool_ids?: string[];
  default_knowledge_enabled?: boolean | null;
  default_knowledge_collection_ids?: string[];
  default_retrieval_options?: Record<string, any>;
  default_search_enabled?: boolean;
  default_search_provider_id?: string | null;
  default_fetch_enabled?: boolean;
  default_fetch_provider_id?: string | null;
}

export interface Conversation {
  knowledge_enabled?: boolean | null;
  knowledge_collection_ids?: string[];
  retrieval_options?: Record<string, any>;
  search_enabled?: boolean;
  search_provider_id?: string | null;
  fetch_enabled?: boolean;
  fetch_provider_id?: string | null;
  workspace_mode?: string | null;
}

export interface SearchFetchProviderBase {
  id: string;
  name: string;
  provider_type: string;
  enabled: boolean;
  capabilities: Record<string, any>;
  credential_status: string;
  configuration: Record<string, any>;
  health_status: string;
  credential_id?: string | null;
  has_vault_credential?: boolean;
  key_source?: "Vault" | "Direct" | "Env" | "None" | string | null;
  is_default?: boolean;
  last_checked_at?: string | null;
  last_test?: { ok?: boolean; status?: string; message?: string } | null;
  created_at?: string;
  updated_at?: string;
}

export interface SearchProvider extends SearchFetchProviderBase {
  credential_status: "NOT_CONFIGURED" | "CONFIGURED" | "INVALID" | string;
}

export interface FetchProvider extends SearchFetchProviderBase {
  credential_status: "NOT_REQUIRED" | "NOT_CONFIGURED" | "CONFIGURED" | "INVALID" | string;
}

export interface ToolRecord {
  tool_type: string;
  current_version_id?: string | null;
}

// ---------------------------------------------------------------------------
// OmniLab V5: evaluation + regression frontend contracts (additive)
// ---------------------------------------------------------------------------

export interface EvaluationFindingV5 { id?: string; category: string; severity: string; message: string; evidence?: any }
export interface EvaluationResultV5 { outcome: "PASS"|"FAIL"|"ERROR"|"NOT_EVALUATED"; status: string; passed: boolean|null; score: number|null; threshold: number|null; confidence: number|null; reason: string|null }
export interface EvaluationV5 {
  id: string; schema_version?: number; evaluation_type?: string; evaluator_type?: string; eval_type?: string;
  evaluator?: { type?: string; key?: string; implementation?: string; version?: string };
  run_id?: string; experiment_id?: string; project_id?: string; dataset_case_id?: string; dataset_version_id?: string;
  rubric?: { id?: string; version?: number; threshold?: number|null; snapshot?: any } | null;
  judge?: any | null; result?: EvaluationResultV5; scores?: Record<string, number>;
  findings?: EvaluationFindingV5[]; metrics?: Record<string, number|string|null>; evidence?: any; provenance?: any;
  parsed_score?: number|null; raw_result?: any; status?: string; created_at?: string;
}
export interface RegressionMetricRow { metric: string; baseline: number|null; candidate: number|null; delta: number|null; direction: string; classification: "improved"|"unchanged"|"degraded"|"unknown"; baseline_missing?: boolean; candidate_missing?: boolean }
export interface RegressionResultV5 { id?: string; name?: string; baseline?: any; candidate?: any; metrics?: RegressionMetricRow[]; summary?: {improved:number;unchanged:number;degraded:number;unknown:number}; case_counts?: Record<string,number>; cases?: any[]; created_at?: string }

// ---------------------------------------------------------------------------
// OmniLab V5 Marathon B: red team, scorecards, certification + finding
// promotions (additive). Every field is optional/nullable because the backend
// may omit it; absent values must render "n/a" and never a fabricated zero.
// ---------------------------------------------------------------------------

export interface RedTeamCaseRun {
  run_id?: string | null;
  configuration_id?: string | null;
  status?: string | null;
}

export interface RedTeamCaseEvaluation {
  evaluation_id?: string | null;
  outcome?: "PASS" | "FAIL" | "ERROR" | "NOT_EVALUATED" | string | null;
  result?: Record<string, any> | null;
}

export interface RedTeamCaseFinding {
  id?: string | null;
  failure_domain?: string | null;
  taxonomy_code?: string | null;
  severity?: string | null;
  status?: string | null;
  [k: string]: any;
}

export interface RedTeamCase {
  case_id?: string | null;
  input_snippet?: string | null;
  category?: string | null;
  expected_behavior?: string | null;
  expected_boundary?: string | null;
  severity?: string | null;
  tags?: string[] | null;
  runs?: RedTeamCaseRun[] | null;
  evaluations?: RedTeamCaseEvaluation[] | null;
  boundary_held?: boolean | null;
  failures?: RedTeamCaseFinding[] | null;
  [k: string]: any;
}

export interface RedTeamSummary {
  cases_total?: number | null;
  boundaries_held?: number | null;
  boundaries_violated?: number | null;
  not_evaluated?: number | null;
  case_evaluations?: number | null;
  [k: string]: any;
}

export interface RedTeamExperimentSummary {
  id?: string | null;
  title?: string | null;
  status?: string | null;
  experiment_type?: string | null;
  project_id?: string | null;
  dataset_version_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  [k: string]: any;
}

export interface RedTeamCatalogEntry {
  category?: string | null;
  description?: string | null;
  expected_behavior?: string | null;
  expected_boundary?: string | null;
  severity?: string | null;
  tags?: string[] | null;
  forbidden_tools?: string[] | null;
  deterministic_assertions?: Record<string, any>[] | null;
  knowledge_policy?: Record<string, any> | null;
  tool_policy?: Record<string, any> | null;
  input_text?: string | null;
  untrusted_source?: string | boolean | null;
  [k: string]: any;
}

export type ScorecardDimensionSource =
  | "deterministic"
  | "judge_derived"
  | "telemetry"
  | "policy_derived"
  | "invariant_derived"
  | string;

export interface ScorecardDimension {
  key?: string | null;
  label?: string | null;
  value?: number | null;
  unit?: string | null;
  state?: string | null;
  source?: ScorecardDimensionSource | null;
  explanation?: string | null;
  evidence?: any[] | null;
  evaluator_keys?: string[] | null;
  sample_size?: number | null;
  judge_derived?: boolean | null;
  deterministic?: boolean | null;
  not_evaluated_count?: number | null;
  direction?: string | null;
  runs?: any[] | null;
  missing_count?: number | null;
  [k: string]: any;
}

export interface ScorecardComparisonRow {
  key?: string | null;
  label?: string | null;
  unit?: string | null;
  direction?: string | null;
  baseline?: number | null;
  candidate?: number | null;
  delta?: number | null;
  classification?: "improved" | "degraded" | "unchanged" | "unknown" | string | null;
  baseline_state?: string | null;
  candidate_state?: string | null;
  significance?: number | null;
  significance_note?: string | null;
  [k: string]: any;
}

export interface Scorecard {
  id?: string | null;
  scope?: string | null;
  project_id?: string | null;
  run_id?: string | null;
  experiment_id?: string | null;
  configuration_id?: string | null;
  baseline_configuration_id?: string | null;
  candidate_configuration_id?: string | null;
  dimensions?: ScorecardDimension[] | null;
  dimension_map?: Record<string, ScorecardDimension> | null;
  configurations?: any[] | null;
  overall?: Record<string, any> | null;
  composite?: Record<string, any> | null;
  notes?: string | null;
  builder_version?: string | null;
  schema_version?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  [k: string]: any;
}

export interface ScorecardComparison {
  scope?: string | null;
  experiment_id?: string | null;
  baseline_configuration_id?: string | null;
  candidate_configuration_id?: string | null;
  baseline?: Record<string, any> | null;
  candidate?: Record<string, any> | null;
  dimensions?: ScorecardComparisonRow[] | null;
  dimension_map?: Record<string, ScorecardComparisonRow> | null;
  summary?: { improved?: number; degraded?: number; unchanged?: number; unknown?: number } | null;
  significance?: number | null;
  significance_note?: string | null;
  composite?: Record<string, any> | null;
  created_at?: string | null;
  [k: string]: any;
}

export interface CertificationReadinessCriterion {
  key?: string | null;
  label?: string | null;
  description?: string | null;
  detail?: string | null;
  required?: boolean | null;
  passed?: boolean | null;
  reason?: string | null;
  [k: string]: any;
}

// Additive V6 evidence kinds. Any list may be absent; absence must render "n/a"
// and never a fabricated value.
export interface CertificationEvidence {
  experiment_ids?: string[] | null;
  run_ids?: string[] | null;
  evaluation_ids?: string[] | null;
  regression_ids?: string[] | null;
  red_team_experiment_ids?: string[] | null;
  finding_ids?: string[] | null;
  scorecard_ids?: string[] | null;
  dataset_version_ids?: string[] | null;
  rubric_ids?: string[] | null;
  benchmark_plan_ids?: string[] | null;
  agent_version_ids?: string[] | null;
  prompt_version_ids?: string[] | null;
  context_pack_version_ids?: string[] | null;
  drift_analysis_ids?: string[] | null;
  [k: string]: any;
}

// One resolved (or missing) evidence reference in a readiness bundle snapshot.
export interface CertificationEvidenceSnapshotEntry {
  id?: string | null;
  version?: number | string | null;
  status?: string | null;
  exists?: boolean | null;
  [k: string]: any;
}

// A drift failure surfaced by readiness. Carries taxonomy/severity/status like a
// regular unresolved failure but is kept in its own list.
export interface CertificationDriftFailure {
  id?: string | null;
  title?: string | null;
  drift_type?: string | null;
  failure_domain?: string | null;
  taxonomy_code?: string | null;
  severity?: string | null;
  status?: string | null;
  [k: string]: any;
}

export interface CertificationReadiness {
  candidate_id?: string | null;
  state?: string | null;
  ready?: boolean | null;
  policy?: { id?: string | null; version?: string | null } | null;
  criteria?: CertificationReadinessCriterion[] | Record<string, CertificationReadinessCriterion> | null;
  unresolved_failures?: any[] | null;
  unresolved_drift_failures?: CertificationDriftFailure[] | null;
  bundle?: Record<string, any> | null;
  bundle_snapshot?: Record<string, any> | null;
  agent_versions?: CertificationEvidenceSnapshotEntry[] | null;
  prompt_versions?: CertificationEvidenceSnapshotEntry[] | null;
  context_pack_versions?: CertificationEvidenceSnapshotEntry[] | null;
  drift_analyses?: CertificationEvidenceSnapshotEntry[] | null;
  bundle_summary?: Record<string, any> | null;
  evaluated_at?: string | null;
  automatic_certification?: boolean | null;
  certification_performed?: boolean | null;
  [k: string]: any;
}

export interface CertificationCandidate {
  id?: string | null;
  owner_id?: string | null;
  project_id?: string | null;
  label?: string | null;
  subject?: Record<string, any> | null;
  evidence?: CertificationEvidence | Record<string, any> | null;
  environment?: Record<string, any> | null;
  notes?: string | null;
  state?: string | null;
  automatic_certification?: boolean | null;
  readiness?: CertificationReadiness | null;
  readiness_history?: CertificationReadiness[] | null;
  schema_version?: number | null;
  policy_version?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  [k: string]: any;
}

export interface CertificationPolicyCriterion {
  key?: string | null;
  description?: string | null;
  required?: boolean | null;
  [k: string]: any;
}

export interface CertificationPolicy {
  id?: string | null;
  version?: string | null;
  description?: string | null;
  criteria?: CertificationPolicyCriterion[] | null;
  [k: string]: any;
}

export interface FindingPromotion {
  finding_id?: string | null;
  promotion_id?: string | null;
  kind?: string | null;
  dataset_id?: string | null;
  dataset_version_id?: string | null;
  case_id?: string | null;
  red_team_category?: string | null;
  expected_behavior?: string | null;
  created_at?: string | null;
  [k: string]: any;
}

// ---------------------------------------------------------------------------
// OmniLab V6.0: Prompt Lab (prompt specs, blocks, project policies and
// invariants). Every field is optional/nullable because the backend may omit
// it; absent values must render "n/a" and never a fabricated zero.
// ---------------------------------------------------------------------------

export interface PromptSpec {
  id?: string | null;
  project_id?: string | null;
  name?: string | null;
  description?: string | null;
  purpose?: string | null;
  status?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  versions_count?: number | null;
}

export interface PromptBlock {
  id?: string | null;
  project_id?: string | null;
  name?: string | null;
  description?: string | null;
  category?: string | null;
  content?: string | null;
  variables?: Record<string, any> | null;
  status?: string | null;
  version?: number | null;
  archived?: boolean | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export type CatalogOption = string | { key?: string | null; label?: string | null } | null;

export interface PromptBlockCatalog {
  categories?: { key?: string | null; label?: string | null; description?: string | null }[] | null;
  statuses?: CatalogOption[] | null;
}

export interface PromptBlockStarterResult {
  created?: PromptBlock[] | null;
  existing?: PromptBlock[] | null;
}

export interface ProjectPolicy {
  id?: string | null;
  project_id?: string | null;
  name?: string | null;
  domain?: string | null;
  description?: string | null;
  content?: string | null;
  status?: string | null;
  version?: number | null;
  family_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectPolicyCatalog {
  domains?: { key?: string | null; label?: string | null }[] | null;
  statuses?: CatalogOption[] | null;
}

export interface ProjectInvariant {
  id?: string | null;
  project_id?: string | null;
  code?: string | null;
  domain?: string | null;
  title?: string | null;
  description?: string | null;
  severity?: string | null;
  enforcement_mode?: string | null;
  status?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectInvariantCatalog {
  domains?: { key?: string | null; label?: string | null }[] | null;
  severities?: CatalogOption[] | null;
  enforcement_modes?: CatalogOption[] | null;
  statuses?: CatalogOption[] | null;
}

// ---------------------------------------------------------------------------
// OmniLab V6.1: Prompt Registry (prompt versions, diffs and usage). Every
// field is optional/nullable because the backend may omit it; absent values
// must render "n/a" and never a fabricated zero.
// ---------------------------------------------------------------------------

export interface PromptVersion {
  id?: string | null;
  prompt_id?: string | null;
  project_id?: string | null;
  version?: number | null;
  status?: string | null;
  content?: string | null;
  system_prompt?: string | null;
  variables?: Record<string, any> | null;
  notes?: string | null;
  structured_spec?: Record<string, any> | null;
  blocks?: (string | Record<string, any>)[] | null;
  content_hash?: string | null;
  created_by?: string | null;
  source_version_id?: string | null;
  frozen_at?: string | null;
  deprecated_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PromptVersionDiffLine {
  op?: "equal" | "delete" | "insert" | string | null;
  from_line?: number | null;
  to_line?: number | null;
  text?: string | null;
}

export interface PromptVersionFieldDiff {
  field?: string | null;
  changed?: boolean | null;
  from?: any;
  to?: any;
}

export interface PromptVersionDiff {
  from?: Record<string, any> | null;
  to?: Record<string, any> | null;
  content_diff?: PromptVersionDiffLine[] | null;
  fields?: PromptVersionFieldDiff[] | null;
  variables?: {
    added?: string[] | null;
    removed?: string[] | null;
    changed?: string[] | null;
  } | null;
  blocks?: {
    added?: (string | Record<string, any>)[] | null;
    removed?: (string | Record<string, any>)[] | null;
  } | null;
  summary?: {
    lines_added?: number | null;
    lines_removed?: number | null;
    fields_changed?: number | null;
  } | null;
}

export interface PromptUsageEvaluation {
  id?: string | null;
  outcome?: string | null;
  evaluator_type?: string | null;
}

export interface PromptUsageRun {
  run_id?: string | null;
  experiment_id?: string | null;
  status?: string | null;
  evaluations?: PromptUsageEvaluation[] | null;
}

export interface PromptUsageVersion {
  id?: string | null;
  version?: number | null;
  status?: string | null;
  used_by?: {
    experiments?: { experiment_id?: string | null; configuration_id?: string | null; label?: string | null }[] | null;
    runs?: PromptUsageRun[] | null;
  } | null;
}

export interface PromptUsage {
  prompt_id?: string | null;
  versions?: PromptUsageVersion[] | null;
  totals?: {
    experiments?: number | null;
    runs?: number | null;
    evaluations?: number | null;
  } | null;
}

// ---------------------------------------------------------------------------
// OmniLab V6.2: Context Lab — context packs with immutable versions, budget
// policies, run-context assembly inspection, run diffs and the real preview
// path. Every field is optional/nullable because the backend may omit it;
// absent token values must render "n/a" and never a fabricated zero.
// ---------------------------------------------------------------------------

export interface ContextPackItem {
  id?: string | null;
  key?: string | null;
  title?: string | null;
  source_type?: string | null;
  content?: string | null;
  order?: number | null;
  token_estimate?: number | null;
  metadata?: Record<string, any> | null;
}

export interface ContextPackVersion {
  id?: string | null;
  pack_id?: string | null;
  version?: number | null;
  status?: string | null;
  description?: string | null;
  items?: ContextPackItem[] | null;
  content_hash?: string | null;
  created_at?: string | null;
  frozen_at?: string | null;
  deprecated_at?: string | null;
}

export interface ContextPack {
  id?: string | null;
  project_id?: string | null;
  name?: string | null;
  description?: string | null;
  status?: string | null;
  latest_version?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ContextPackVersionCompareField {
  field?: string | null;
  from?: any;
  to?: any;
}

export interface ContextPackVersionCompareChangedItem {
  key?: string | null;
  fields?: ContextPackVersionCompareField[] | null;
}

export interface ContextPackVersionCompare {
  from?: Record<string, any> | null;
  to?: Record<string, any> | null;
  items?: {
    added?: string[] | null;
    removed?: string[] | null;
    changed?: (string | ContextPackVersionCompareChangedItem)[] | null;
  } | null;
  summary?: {
    items_added?: number | null;
    items_removed?: number | null;
    items_changed?: number | null;
    content_changed?: boolean | null;
    token_delta?: number | null;
  } | null;
}

export interface ContextCatalogStarterPack {
  key?: string | null;
  name?: string | null;
  description?: string | null;
  item_count?: number | null;
}

export interface ContextCatalog {
  source_types?: CatalogOption[] | null;
  starter_packs?: ContextCatalogStarterPack[] | null;
  budget_fields?: CatalogOption[] | null;
  pack_statuses?: CatalogOption[] | null;
}

export interface ContextBudgetPolicy {
  id?: string | null;
  project_id?: string | null;
  name?: string | null;
  max_context_tokens?: number | null;
  reserve_output_tokens?: number | null;
  knowledge_budget?: number | null;
  history_budget?: number | null;
  file_budget?: number | null;
  policy_budget?: number | null;
  status?: string | null;
  archived?: boolean | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ContextAssemblyComponent {
  component_type?: string | null;
  source_class?: string | null;
  ordering?: number | null;
  included?: boolean | null;
  excluded_reason?: string | null;
  token_count?: number | null;
  estimated_tokens?: number | null;
  estimated_method?: string | null;
  truncated?: boolean | null;
  truncation_reason?: string | null;
  content_hash?: string | null;
  content_excerpt?: string | null;
  source_ref?: string | null;
  metadata?: Record<string, any> | null;
}

export interface ContextAssemblySourceSummary {
  source_class?: string | null;
  count?: number | null;
  included_count?: number | null;
  estimated_tokens?: number | null;
}

export interface ContextAssemblySnapshotReference {
  id?: string | null;
  available?: boolean | null;
}

export interface ContextAssemblyView {
  run_id?: string | null;
  available?: boolean | null;
  reason?: string | null;
  run?: Record<string, any> | null;
  snapshot?: ContextSnapshot | null;
  components?: ContextAssemblyComponent[] | null;
  excluded_components?: ContextAssemblyComponent[] | null;
  sources_summary?: ContextAssemblySourceSummary[] | null;
  snapshot_reference?: ContextAssemblySnapshotReference | null;
}

export interface ContextPreviewAssembly {
  components?: ContextAssemblyComponent[] | null;
  excluded_components?: ContextAssemblyComponent[] | null;
  estimated_total_tokens?: number | null;
  estimated_method?: string | null;
  known_token_count?: number | null;
  unknown_token_component_count?: number | null;
  context_hash?: string | null;
  strategy?: string | null;
  over_budget?: boolean | null;
  warnings?: string[] | null;
  budget_policy_applied?: boolean | null;
  budget_excluded_count?: number | null;
}

export interface ContextPreviewResult {
  assembly?: ContextPreviewAssembly | null;
  budget_policy?: ContextBudgetPolicy | null;
  applied_max_context_tokens?: number | null;
  applied_reserved_output_tokens?: number | null;
}

export interface ContextRunDiffComponent {
  component_type?: string | null;
  status?: string | null;
  source_class?: string | null;
  token_delta?: number | null;
  fields?: ContextPackVersionCompareField[] | null;
}

export interface ContextRunDiff {
  available?: boolean | null;
  reason?: string | null;
  run_a?: string | null;
  run_b?: string | null;
  context_hash_a?: string | null;
  context_hash_b?: string | null;
  estimated_tokens_a?: number | null;
  estimated_tokens_b?: number | null;
  token_delta?: number | null;
  components?: ContextRunDiffComponent[] | null;
  component_diffs?: Record<string, any> | null;
  [k: string]: any;
}

// ---------------------------------------------------------------------------
// OmniLab V6.3: Prompt Compiler — target/mode catalog, preview + compile
// requests and the compiled prompt document (preview results are not
// persisted; compiled results carry an id). Every field is optional/nullable
// because the backend may omit it; absent values must render "n/a" and never
// a fabricated value.
// ---------------------------------------------------------------------------

export interface PromptCompileContextItem {
  title?: string | null;
  content?: string | null;
  source?: string | null;
}

export interface PromptCompileRequest {
  project_id: string;
  prompt_version_id: string;
  agent_version_id?: string | null;
  target: string;
  mode: string;
  variables?: Record<string, any> | null;
  block_ids?: string[] | null;
  policy_ids?: string[] | null;
  include_policies?: boolean;
  include_invariants?: boolean;
  invariant_ids?: string[] | null;
  context_pack_version_ids?: string[] | null;
  untrusted_context?: PromptCompileContextItem[] | null;
  tool_outputs?: PromptCompileContextItem[] | null;
  tool_definitions?: Record<string, any>[] | null;
}

export interface CompiledPromptSection {
  kind?: string | null;
  trust?: string | null;
  title?: string | null;
  content?: string | null;
  order?: number | null;
  token_estimate?: number | null;
  source_ref?: string | null;
}

export interface CompiledPromptMessage {
  role?: string | null;
  content?: string | null;
}

export interface CompiledPromptBlockRef {
  id?: string | null;
  name?: string | null;
  version?: number | null;
}

export interface CompiledPromptPolicyRef {
  id?: string | null;
  family_id?: string | null;
  name?: string | null;
  domain?: string | null;
  version?: number | null;
}

export interface CompiledPromptInvariantRef {
  id?: string | null;
  code?: string | null;
  domain?: string | null;
  severity?: string | null;
  enforcement_mode?: string | null;
}

export interface CompiledPromptContextRef {
  kind?: string | null;
  ref_id?: string | null;
  name?: string | null;
}

export interface CompiledPrompt {
  id?: string | null;
  project_id?: string | null;
  compiler_version?: string | null;
  target?: string | null;
  mode?: string | null;
  prompt_spec_id?: string | null;
  prompt_version_id?: string | null;
  agent_version_id?: string | null;
  rendered_text?: string | null;
  messages?: CompiledPromptMessage[] | null;
  sections?: CompiledPromptSection[] | null;
  blocks_used?: CompiledPromptBlockRef[] | null;
  policies_used?: CompiledPromptPolicyRef[] | null;
  invariants_used?: CompiledPromptInvariantRef[] | null;
  variables_used?: Record<string, any> | null;
  context_refs?: CompiledPromptContextRef[] | null;
  checksum?: string | null;
  token_estimate?: number | null;
  token_method?: string | null;
  created_at?: string | null;
  persisted?: boolean | null;
}

export interface CompiledPromptCatalog {
  targets?: CatalogOption[] | null;
  modes?: CatalogOption[] | null;
  trust_classes?: CatalogOption[] | null;
  compiler_versions?: CatalogOption[] | null;
}

export interface CompiledPromptList {
  items?: CompiledPrompt[] | null;
  total?: number | null;
  limit?: number | null;
  offset?: number | null;
}

// ---------------------------------------------------------------------------
// OmniLab V6.4: Agent Lab — agent identities, append-only configuration
// versions, version diffs and reverse usage. Every field is optional/nullable
// because the backend may omit it; absent values must render "n/a" and never a
// fabricated value.
// ---------------------------------------------------------------------------

export interface AgentCatalog {
  roles?: CatalogOption[] | null;
  statuses?: CatalogOption[] | null;
  version_states?: CatalogOption[] | null;
  output_formats?: CatalogOption[] | null;
  routing_modes?: CatalogOption[] | null;
}

export interface Agent {
  id?: string | null;
  project_id?: string | null;
  name?: string | null;
  description?: string | null;
  role?: string | null;
  purpose?: string | null;
  responsibilities?: string[] | null;
  non_responsibilities?: string[] | null;
  status?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  latest_version?: number | null;
}

export interface AgentContextPolicy {
  context_pack_version_ids?: string[] | null;
  budget_policy_id?: string | null;
  strategy?: string | null;
  context_options?: Record<string, any> | null;
}

export interface AgentToolPolicy {
  default_deny?: boolean | null;
  allowed_tool_ids?: string[] | null;
  denied_tool_ids?: string[] | null;
  mode?: string | null;
}

export interface AgentKnowledgePolicy {
  knowledge_enabled?: boolean | null;
  allowed_collection_ids?: string[] | null;
  retrieval_top_k?: number | null;
  citation_required?: boolean | null;
  knowledge_only_mode?: boolean | null;
}

export interface AgentRuntimePolicy {
  max_tool_rounds?: number | null;
  max_tool_calls?: number | null;
  max_runtime_seconds?: number | null;
  max_input_tokens?: number | null;
  max_output_tokens?: number | null;
  max_cost?: number | null;
  temperature?: number | null;
  reasoning_effort?: string | null;
}

export interface AgentOutputContract {
  format?: string | null;
  schema?: Record<string, any> | null;
  required_sections?: string[] | null;
}

export interface AgentEvaluationProfile {
  evaluator_keys?: string[] | null;
  rubric_id?: string | null;
  judge_model_id?: string | null;
  threshold?: number | null;
}

export interface AgentRouting {
  mode?: string | null;
}

export interface AgentVersion {
  id?: string | null;
  agent_id?: string | null;
  project_id?: string | null;
  version?: number | null;
  status?: string | null;
  model_id?: string | null;
  prompt_version_id?: string | null;
  context_policy?: AgentContextPolicy | null;
  tool_policy?: AgentToolPolicy | null;
  knowledge_policy?: AgentKnowledgePolicy | null;
  runtime_policy?: AgentRuntimePolicy | null;
  output_contract?: AgentOutputContract | null;
  evaluation_profile?: AgentEvaluationProfile | null;
  routing?: AgentRouting | null;
  metadata?: Record<string, any> | null;
  content_hash?: string | null;
  created_by?: string | null;
  source_version_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  frozen_at?: string | null;
  deprecated_at?: string | null;
}

export interface AgentVersionDiffField {
  field?: string | null;
  changed?: boolean | null;
  from?: any;
  to?: any;
}

export interface AgentPolicyCollectionDiff {
  added?: any[] | null;
  removed?: any[] | null;
  changed?: AgentVersionDiffField[] | null;
}

export interface AgentToolPolicyDiff {
  allowed_added?: any[] | null;
  allowed_removed?: any[] | null;
  denied_added?: any[] | null;
  denied_removed?: any[] | null;
}

export interface AgentPolicyDiffBlock {
  changed_fields?: AgentVersionDiffField[] | null;
}

export interface AgentPolicyDiff {
  context?: AgentPolicyCollectionDiff | null;
  tools?: AgentToolPolicyDiff | null;
  knowledge?: AgentPolicyCollectionDiff | null;
  runtime?: AgentPolicyDiffBlock | null;
  output_contract?: AgentPolicyDiffBlock | null;
  evaluation?: AgentPolicyDiffBlock | null;
}

export interface AgentVersionCompareRef {
  id?: string | null;
  version?: number | null;
  status?: string | null;
}

export interface AgentVersionCompare {
  from?: AgentVersionCompareRef | Record<string, any> | null;
  to?: AgentVersionCompareRef | Record<string, any> | null;
  fields?: AgentVersionDiffField[] | null;
  policy_diff?: AgentPolicyDiff | null;
  summary?: { fields_changed?: number | null } | null;
}

export interface AgentUsageRun {
  run_id?: string | null;
  experiment_id?: string | null;
  status?: string | null;
  created_at?: string | null;
}

export interface AgentUsageEvaluation {
  id?: string | null;
  run_id?: string | null;
  outcome?: string | null;
  evaluator_type?: string | null;
}

export interface AgentUsageFinding {
  id?: string | null;
  failure_domain?: string | null;
  taxonomy_code?: string | null;
  severity?: string | null;
  status?: string | null;
}

export interface AgentUsageScorecard {
  id?: string | null;
  scope?: string | null;
  created_at?: string | null;
}

export interface AgentUsage {
  agent_id?: string | null;
  version_id?: string | null;
  runs?: AgentUsageRun[] | null;
  evaluations?: AgentUsageEvaluation[] | null;
  findings?: AgentUsageFinding[] | null;
  scorecards?: AgentUsageScorecard[] | null;
}

// ---------------------------------------------------------------------------
// OmniLab V6.5: Agent Runtime — execute a frozen/deprecated agent version via
// the backend runtime and inspect the real run. Every field is
// optional/nullable because the backend may omit it; absent values must render
// "n/a" and never a fabricated value. `runtime_policy_applied` is authoritative:
// fields listed under `unsupported` were NOT applied and must be surfaced.
// ---------------------------------------------------------------------------

export interface AgentRuntimeUnsupportedField {
  field?: string | null;
  reason?: string | null;
}

export interface AgentRuntimePolicyApplied {
  applied?: Record<string, any> | any[] | null;
  unsupported?: AgentRuntimeUnsupportedField[] | null;
  params?: Record<string, any> | null;
}

export interface PermissionDenial {
  kind?: string | null;
  resource_type?: string | null;
  resource_id?: string | null;
  name?: string | null;
  reason?: string | null;
}

export interface ToolPolicyAppliedModelCapability {
  supports_tools?: boolean | null;
  [k: string]: any;
}

export interface ToolPolicyApplied {
  default_deny?: boolean | null;
  allowed_tool_ids?: string[] | null;
  denied_tool_ids?: string[] | null;
  enabled_tool_ids?: string[] | null;
  model_capability?: ToolPolicyAppliedModelCapability | null;
}

export interface KnowledgePolicyApplied {
  knowledge_enabled?: boolean | null;
  allowed_collection_ids?: string[] | null;
  requested_collection_ids?: string[] | null;
  retrieval_top_k?: number | null;
  citation_required?: boolean | null;
  knowledge_only_mode?: boolean | null;
}

export interface AgentRunRequest {
  project_id: string;
  agent_id: string;
  agent_version_id: string;
  task: string;
  variables?: Record<string, any>;
  dataset_case_id?: string;
  dataset_version_id?: string;
  experiment_id?: string;
  configuration_id?: string;
  attachments?: string[];
  reserved_output_tokens?: number;
}

export interface AgentRuntimeRun {
  id?: string | null;
  status?: string | null;
  text?: string | null;
  normalized_response?: string | null;
  latency_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  estimated_cost?: number | null;
  cost_available?: boolean | null;
  tool_call_count?: number | null;
  trace_id?: string | null;
  created_at?: string | null;
  permission_denials?: PermissionDenial[] | null;
  tool_policy_applied?: ToolPolicyApplied | null;
  knowledge_policy_applied?: KnowledgePolicyApplied | null;
  policy_unsupported?: AgentRuntimeUnsupportedField[] | null;
  [k: string]: any;
}

export interface AgentRunResult {
  run?: AgentRuntimeRun | null;
  run_id?: string | null;
  trace_id?: string | null;
  compiled_prompt_id?: string | null;
  agent_id?: string | null;
  agent_version_id?: string | null;
  prompt_id?: string | null;
  compiled_prompt?: Record<string, any> | null;
  runtime_policy_applied?: AgentRuntimePolicyApplied | null;
  output_contract?: Record<string, any> | null;
  permission_denials?: PermissionDenial[] | null;
  tool_policy_applied?: ToolPolicyApplied | null;
  knowledge_policy_applied?: KnowledgePolicyApplied | null;
  policy_unsupported?: AgentRuntimeUnsupportedField[] | null;
  [k: string]: any;
}

export interface AgentRunSummary {
  id?: string | null;
  run_id?: string | null;
  status?: string | null;
  created_at?: string | null;
  latency_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  estimated_cost?: number | null;
  cost_available?: boolean | null;
  tool_call_count?: number | null;
  trace_id?: string | null;
  agent_id?: string | null;
  agent_version_id?: string | null;
  permission_denials?: PermissionDenial[] | null;
  tool_policy_applied?: ToolPolicyApplied | null;
  knowledge_policy_applied?: KnowledgePolicyApplied | null;
  [k: string]: any;
}

export interface AgentRunList {
  items?: AgentRunSummary[] | null;
  total?: number | null;
  limit?: number | null;
  offset?: number | null;
}

// ---------------------------------------------------------------------------
// OmniLab V6.6: Agent Evaluation — evaluate a real agent run and inspect the
// resulting evaluation documents. Every field is optional/nullable because the
// backend may omit it; absent values must render "n/a" and never a fabricated
// number. Rule keys and dimensions come from the catalog verbatim, never
// invented on the client.
// ---------------------------------------------------------------------------

export interface AgentEvaluationCatalog {
  agent_rule_keys?: CatalogOption[] | null;
  prompt_rule_keys?: CatalogOption[] | null;
  agent_dimensions?: CatalogOption[] | null;
  prompt_dimensions?: CatalogOption[] | null;
}

export interface AgentEvaluationRequest {
  evaluator_keys?: string[];
  use_agent_profile?: boolean;
  rubric_id?: string;
  judge_model_id?: string;
  threshold?: number;
  record_failures?: boolean;
  profile_type: "agent" | "prompt";
  dimensions?: string[];
  notes?: string;
}

export interface AgentEvaluationResult {
  run_id?: string | null;
  evaluations?: EvaluationV5[] | null;
  rule_evaluation?: EvaluationV5 | Record<string, any> | null;
  judge_evaluation?: EvaluationV5 | Record<string, any> | null;
  profile_type?: string | null;
  [k: string]: any;
}

export interface AgentRunEvaluationList {
  items?: EvaluationV5[] | null;
  total?: number | null;
  limit?: number | null;
  offset?: number | null;
}

// ---------------------------------------------------------------------------
// OmniLab V6.8: Arena V6 — agent and prompt arenas (additive). Every metric is
// optional/nullable because the backend may omit it; absent values must render
// "n/a" and never a fabricated zero.
// ---------------------------------------------------------------------------

export interface ArenaV6Catalog {
  arena_types?: string[] | null;
  evaluation_keys?: CatalogOption[] | null;
  routing_modes?: string[] | null;
}

export interface AgentArenaCreate {
  project_id: string;
  title: string;
  dataset_id: string;
  dataset_version_id: string;
  agent_version_ids: string[];
  model_ids?: string[];
  objective?: string;
  hypothesis?: string;
  repetitions?: number;
  evaluation_keys?: string[];
  rubric_id?: string | null;
  judge_model_id?: string | null;
}

export interface PromptArenaCreate {
  project_id: string;
  title: string;
  dataset_id: string;
  dataset_version_id: string;
  prompt_version_ids: string[];
  model_id: string;
  agent_version_id?: string | null;
  objective?: string;
  hypothesis?: string;
  repetitions?: number;
  evaluation_keys?: string[];
  rubric_id?: string | null;
  judge_model_id?: string | null;
}

export interface ArenaV6Experiment {
  id: string;
  project_id?: string | null;
  title?: string | null;
  experiment_type?: string | null;
  status?: string | null;
  objective?: string | null;
  hypothesis?: string | null;
  run_count?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  [k: string]: any;
}

export interface ArenaV6ConfigMetrics {
  configuration_id: string;
  label?: string | null;
  model_id?: string | null;
  agent_version_id?: string | null;
  prompt_version_id?: string | null;
  run_count?: number | null;
  completed?: number | null;
  failed?: number | null;
  completion_rate?: number | null;
  failure_rate?: number | null;
  avg_score?: number | null;
  tool_violations?: number | null;
  policy_violations?: number | null;
  latency_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
  estimated_cost?: number | null;
  evaluation_outcomes?: Record<string, any> | any[] | null;
}

export interface ArenaV6Summary {
  experiment?: ArenaV6Experiment | null;
  configurations?: ArenaV6ConfigMetrics[] | null;
  summary?: Record<string, any> | null;
}

export interface ArenaV6List {
  items?: ArenaV6Experiment[] | null;
  total?: number | null;
  limit?: number | null;
  offset?: number | null;
}

// ---------------------------------------------------------------------------
// OmniLab V6.9: Drift Analysis — detect drift between changed files and the
// project's canonical contracts/prompt context. Every field is optional/
// nullable because the backend may omit it; absent values must render "n/a"
// and never a fabricated count. Judge-derived findings must show provenance.
// ---------------------------------------------------------------------------

export type DriftSource = "deterministic" | "judge_derived" | string;

export interface DriftChangedFile {
  path: string;
  language?: string | null;
  added_lines?: number | null;
  removed_lines?: number | null;
  imports_added?: string[] | null;
  routes_added?: string[] | null;
  components_added?: string[] | null;
  services_added?: string[] | null;
  enums_added?: string[] | null;
  constants_added?: string[] | null;
  hardcoded_colors?: string[] | null;
  design_tokens_added?: string[] | null;
}

export interface DriftCanonicalService {
  name?: string | null;
  responsibility?: string | null;
  paths?: string[] | null;
}

export interface DriftForbiddenPattern {
  id?: string | null;
  pattern?: string | null;
  type?: string | null;
  reason?: string | null;
}

export interface DriftDuplicatedRule {
  id?: string | null;
  backend_pattern?: string | null;
  frontend_pattern?: string | null;
}

export interface DriftEnumContract {
  name?: string | null;
  backend_values?: string[] | null;
  frontend_values?: string[] | null;
}

export interface DriftContracts {
  canonical_services?: DriftCanonicalService[] | null;
  forbidden_patterns?: DriftForbiddenPattern[] | null;
  design_tokens?: string[] | null;
  allowed_layers?: string[] | null;
  enums?: DriftEnumContract[] | null;
  duplicated_rules?: DriftDuplicatedRule[] | null;
}

export interface DriftPromptContext {
  required_invariant_codes?: string[] | null;
  required_phases?: string[] | null;
  prohibited_scope?: string | null;
  compiled_invariants_used?: string[] | null;
  output_text?: string | null;
  prompt_version_id?: string | null;
}

export interface DriftEvidence {
  path?: string | null;
  pattern?: string | null;
  value?: any;
  [k: string]: any;
}

export interface DriftProvenance {
  model?: string | null;
  model_id?: string | null;
  rubric?: string | null;
  rubric_id?: string | null;
  [k: string]: any;
}

export interface DriftFinding {
  id?: string | null;
  drift_type: string;
  category?: string | null;
  severity?: string | null;
  message?: string | null;
  evidence?: DriftEvidence | Record<string, any> | null;
  source?: DriftSource | null;
  provenance?: DriftProvenance | Record<string, any> | null;
}

export interface DriftAnalysisSummary {
  total?: number | null;
  by_drift_type?: Record<string, number> | null;
  deterministic_count?: number | null;
  judge_derived_count?: number | null;
}

export interface DriftAnalysis {
  id?: string | null;
  project_id?: string | null;
  scope?: string | null;
  version?: number | null;
  findings?: DriftFinding[] | null;
  summary?: DriftAnalysisSummary | null;
  run_id?: string | null;
  evaluation_id?: string | null;
  created_at?: string | null;
  [k: string]: any;
}

export interface DriftCatalog {
  drift_types?: string[] | null;
  sources?: string[] | null;
  checks?: CatalogOption[] | null;
  scopes?: string[] | null;
}

export interface DriftAnalyzeRequest {
  project_id: string;
  scope: string;
  changed_files?: DriftChangedFile[];
  contracts?: DriftContracts;
  prompt_context?: DriftPromptContext;
  run_id?: string;
  evaluation_id?: string;
  experiment_id?: string;
  judge_model_id?: string;
  rubric_id?: string;
  notes?: string;
}

export interface DriftAnalysisList {
  items?: DriftAnalysis[] | null;
  total?: number | null;
  limit?: number | null;
  offset?: number | null;
}

export interface DriftMaterializeResult {
  created?: any[] | null;
  existing?: any[] | null;
  [k: string]: any;
}


// ---------------------------------------------------------------------------
// OmniLab V6.14: Workspace Prompt Capture / Composer. Capture a workspace
// conversation into a draft prompt (never auto-frozen on capture) reusing the
// V6 Prompt Lab block/policy/invariant registry; the compiled preview reuses
// the V6 Prompt Compiler `CompiledPrompt`. Every field is optional/nullable
// because the backend may omit it; absent values must render "n/a" and never a
// fabricated value.
// ---------------------------------------------------------------------------

export interface WorkspaceModeOption {
  key?: string | null;
  label?: string | null;
  description?: string | null;
}

export interface WorkspaceModesCatalog {
  modes?: WorkspaceModeOption[] | null;
  default?: string | null;
}

export interface CaptureLinkedBlock {
  id?: string | null;
  name?: string | null;
  category?: string | null;
  version?: number | null;
  matched_by?: string | null;
}

export interface CaptureSuggestedBlock {
  name?: string | null;
  category?: string | null;
  content?: string | null;
  reason?: string | null;
}

export interface CaptureLinkedPolicy {
  id?: string | null;
  name?: string | null;
  domain?: string | null;
  version?: number | null;
  matched_by?: string | null;
}

export interface CaptureLinkedInvariant {
  id?: string | null;
  code?: string | null;
  domain?: string | null;
  severity?: string | null;
  title?: string | null;
  matched_by?: string | null;
}

export interface CaptureCandidate {
  id?: string | null;
  name?: string | null;
  category?: string | null;
  domain?: string | null;
  version?: number | null;
  code?: string | null;
  severity?: string | null;
  title?: string | null;
}

export interface PromptCapturePreviewSource {
  conversation_id?: string | null;
  message_ids?: string[] | null;
  message_count?: number | null;
}

export interface PromptCapturePreviewStructure {
  role?: string | null;
  objective?: string | null;
  scope?: string[] | null;
  constraints?: string[] | null;
  architecture_rules?: string[] | null;
  design_rules?: string[] | null;
  security_rules?: string[] | null;
  phases?: string[] | null;
  validation?: string[] | null;
  expected_output?: string[] | null;
}

export interface PromptCapturePreviewBlocks {
  linked?: CaptureLinkedBlock[] | null;
  suggested_created?: CaptureSuggestedBlock[] | null;
  candidates?: CaptureCandidate[] | null;
}

export interface PromptCapturePreviewPolicies {
  linked?: CaptureLinkedPolicy[] | null;
  candidates?: CaptureCandidate[] | null;
}

export interface PromptCapturePreviewInvariants {
  linked?: CaptureLinkedInvariant[] | null;
  candidates?: CaptureCandidate[] | null;
}

export interface PromptCapturePreviewCandidates {
  blocks?: CaptureCandidate[] | null;
  policies?: CaptureCandidate[] | null;
  invariants?: CaptureCandidate[] | null;
}

export interface PromptCaptureRedaction {
  redacted?: boolean | null;
  matches?: number | null;
}

export interface PromptCapturePreview {
  project_id?: string | null;
  conversation_id?: string | null;
  message_ids?: string[] | null;
  mode?: string | null;
  title?: string | null;
  purpose?: string | null;
  source?: PromptCapturePreviewSource | null;
  structure?: PromptCapturePreviewStructure | null;
  content?: string | null;
  system_prompt?: string | null;
  variables?: Record<string, any> | null;
  structured_spec?: Record<string, any> | null;
  blocks?: PromptCapturePreviewBlocks | null;
  policies?: PromptCapturePreviewPolicies | null;
  invariants?: PromptCapturePreviewInvariants | null;
  candidates?: PromptCapturePreviewCandidates | null;
  redaction?: PromptCaptureRedaction | null;
  preview?: CompiledPrompt | null;
  decision_state?: PlanningState | null;
  capture_fields?: {
    confirmed_decisions?: string[] | null;
    constraints?: string[] | null;
    rejected_options?: string[] | null;
    assumptions?: string[] | null;
    open_questions?: string[] | null;
    verification_items?: string[] | null;
  } | null;
}

export interface PromptCaptureSaveRequest {
  project_id: string;
  conversation_id: string;
  message_ids?: string[];
  mode?: string;
  kind?: "prompt" | "planning_template";
  name?: string;
  title?: string;
  purpose?: string;
  notes?: string;
  prompt_id?: string | null;
  include_block_ids?: string[];
  include_policy_ids?: string[];
  include_invariant_ids?: string[];
  create_missing_blocks?: boolean;
  freeze_version?: boolean;
  target?: string;
  compiler_mode?: string;
  variables?: Record<string, any>;
}

export interface PromptCaptureRecord {
  id?: string | null;
  project_id?: string | null;
  conversation_id?: string | null;
  message_ids?: string[] | null;
  mode?: string | null;
  kind?: string | null;
  name?: string | null;
  title?: string | null;
  purpose?: string | null;
  notes?: string | null;
  prompt_id?: string | null;
  version_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  [k: string]: any;
}

export interface PromptCaptureSaveResult {
  capture?: PromptCaptureRecord | null;
  prompt?: PromptSpec | null;
  version?: PromptVersion | null;
  blocks?: PromptBlock[] | null;
  policies?: ProjectPolicy[] | null;
  invariants?: ProjectInvariant[] | null;
  preview?: CompiledPrompt | null;
  [k: string]: any;
}

// ---------------------------------------------------------------------------
// OmniLab V6.14: Workspace Planning Decision Memory — planning state,
// conflicts, catalog and consolidated plan shapes (additive).
// ---------------------------------------------------------------------------

export interface PlanningItem {
  id?: string | null;
  project_id?: string | null;
  conversation_id?: string | null;
  topic_key?: string | null;
  category?: string | null;
  kind?: string | null;
  value?: string | null;
  status?: string | null;
  source_type?: string | null;
  confidence?: number | null;
  verification_status?: string | null;
  source_message_id?: string | null;
  provenance?: Record<string, any> | null;
  supersedes_id?: string | null;
  superseded_by_id?: string | null;
  conflict_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PlanningConflict {
  id?: string | null;
  project_id?: string | null;
  conversation_id?: string | null;
  topic_key?: string | null;
  category?: string | null;
  previous_item_id?: string | null;
  new_item_id?: string | null;
  previous_value?: string | null;
  new_value?: string | null;
  status?: string | null;
  resolution?: string | null;
  resolved_value?: string | null;
  resolved_item_id?: string | null;
  source_message_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  resolved_at?: string | null;
}

export interface PlanningState {
  project_id?: string | null;
  conversation_id?: string | null;
  confirmed_decisions?: PlanningItem[] | null;
  constraints?: PlanningItem[] | null;
  rejected_options?: PlanningItem[] | null;
  assumptions?: PlanningItem[] | null;
  open_questions?: PlanningItem[] | null;
  verification_items?: PlanningItem[] | null;
  conflicts?: PlanningConflict[] | null;
  superseded?: PlanningItem[] | null;
  resolved_topics?: string[] | null;
  counts?: Record<string, number> | null;
}

export interface PlanningStateCatalog {
  statuses?: string[] | null;
  kinds?: string[] | null;
  source_types?: string[] | null;
  verification_statuses?: string[] | null;
  conflict_resolutions?: string[] | null;
  topics?: { key?: string | null; category?: string | null; label?: string | null }[] | null;
}

export interface ConsolidatedPlan {
  markdown?: string | null;
  sections?: Record<string, string[]> | null;
  policies?: any[] | null;
  invariants?: any[] | null;
  state?: PlanningState | null;
}

// ---------------------------------------------------------------------------
// OmniLab V6.15: Structured Planning Graph & Living PRD — deterministic graph
// nodes/edges/sections, node detail, impact analysis, PRD readiness,
// versioned PRD documents and section-level diffs (additive).
// ---------------------------------------------------------------------------

export interface PlanningGraphNode {
  id?: string | null;
  project_id?: string | null;
  node_key?: string | null;
  type?: string | null;
  title?: string | null;
  content?: string | null;
  status?: string | null;
  decision_id?: string | null;
  topic_key?: string | null;
  category?: string | null;
  section_key?: string | null;
  subdomain?: string | null;
  conversation_id?: string | null;
  source_message_id?: string | null;
  provenance?: Record<string, any> | null;
  order?: number | null;
  review_required?: boolean | null;
  review_reason?: string | null;
  review_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PlanningGraphEdge {
  id?: string | null;
  source_node_id?: string | null;
  target_node_id?: string | null;
  relation?: string | null;
  provenance?: Record<string, any> | null;
  created_at?: string | null;
}

export interface PlanningGraphSection {
  key?: string | null;
  title?: string | null;
  node_id?: string | null;
  counts?: Record<string, number> | null;
  review_required?: boolean | null;
}

export interface PlanningGraph {
  project_id?: string | null;
  conversation_id?: string | null;
  schema_version?: string | null;
  synced_at?: string | null;
  nodes?: PlanningGraphNode[] | null;
  edges?: PlanningGraphEdge[] | null;
  sections?: PlanningGraphSection[] | null;
  counts?: Record<string, number> | null;
}

export interface PlanningGraphNodeDetail {
  node?: PlanningGraphNode | null;
  decision?: PlanningItem | null;
  provenance?: Record<string, any> | null;
  dependencies?: PlanningGraphNode[] | null;
  dependents?: PlanningGraphNode[] | null;
  affected?: PlanningGraphNode[] | null;
  history?: {
    decision_id?: string | null;
    status?: string | null;
    value?: string | null;
    created_at?: string | null;
    superseded_by_id?: string | null;
  }[] | null;
}

export interface PlanningImpact {
  project_id?: string | null;
  decision?: PlanningItem | null;
  changed_section?: { key?: string | null; title?: string | null; node_id?: string | null } | null;
  affected_areas?: {
    node_id?: string | null;
    section_key?: string | null;
    title?: string | null;
    reason?: string | null;
    review_required?: boolean | null;
  }[] | null;
  count?: number | null;
  message?: string | null;
}

export interface PrdReadinessDimension {
  key?: string | null;
  label?: string | null;
  state?: string | null;
  detail?: string | null;
  section_key?: string | null;
  item_count?: number | null;
  blockers?: any[] | null;
}

export interface PrdReadiness {
  project_id?: string | null;
  conversation_id?: string | null;
  dimensions?: PrdReadinessDimension[] | null;
  blockers?: any[] | null;
  blocker_count?: number | null;
  ready?: boolean | null;
  summary?: string | null;
}

export interface PrdVersionSummary {
  id?: string | null;
  project_id?: string | null;
  version?: number | null;
  status?: string | null;
  title?: string | null;
  created_at?: string | null;
  approved_at?: string | null;
  content_hash?: string | null;
  source_revision?: Record<string, any> | null;
  section_count?: number | null;
  counts?: Record<string, number> | null;
}

export interface PrdSection {
  key?: string | null;
  title?: string | null;
  items?: {
    text?: string | null;
    status?: string | null;
    decision_id?: string | null;
    topic_key?: string | null;
    evidence_class?: string | null;
    provenance?: Record<string, any> | null;
  }[] | null;
}

export interface PrdVersion {
  id?: string | null;
  project_id?: string | null;
  conversation_id?: string | null;
  version?: number | null;
  status?: string | null;
  title?: string | null;
  markdown?: string | null;
  content?: { sections?: PrdSection[] | null; planning_state?: any } | null;
  source_revision?: Record<string, any> | null;
  section_count?: number | null;
  counts?: Record<string, number> | null;
  compiler_version?: string | null;
  content_hash?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  approved_at?: string | null;
  superseded_at?: string | null;
}

export interface PrdDiffSection {
  key?: string | null;
  title?: string | null;
  status?: "added" | "removed" | "changed" | "unchanged" | null;
  line_diff?: { op?: string | null; from_line?: number | null; to_line?: number | null; text?: string | null }[] | null;
  fields?: { field?: string | null; changed?: boolean | null; from?: any; to?: any }[] | null;
}

export interface PrdDiff {
  from?: { id?: string | null; version?: number | null; status?: string | null } | null;
  to?: { id?: string | null; version?: number | null; status?: string | null } | null;
  sections?: PrdDiffSection[] | null;
  summary?: Record<string, number> | null;
  affected_sections?: { key?: string | null; title?: string | null }[] | null;
}
