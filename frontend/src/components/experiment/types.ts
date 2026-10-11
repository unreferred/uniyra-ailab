// Local response shapes for the Experiment protocol / Benchmark UI.
//
// These mirror the *runtime* payloads returned by the backend (see
// backend/app/services/{experiments,evaluation,benchmark}.py). The shared
// `@/lib/types` contracts are intentionally broader; these types keep the
// experiment components precisely typed without touching lib/types.ts.
import {
  BenchmarkPlan,
  BenchmarkProgress,
  ConfigAggregate,
  Experiment,
  ExperimentConfiguration,
  ExperimentProtocol,
} from "@/lib/types";

export interface ConfigurationRecord extends ExperimentConfiguration {
  id: string;
  order?: number;
  is_control?: boolean;
  configuration_hash?: string;
  pricing_snapshot?: Record<string, any> | null;
  prompt_version_snapshot?: Record<string, any> | null;
}

export interface ExperimentListItem extends Experiment {
  configurations_count?: number;
  protocol?: ExperimentProtocol;
  dataset_id?: string | null;
  benchmark_plan?: BenchmarkPlan | null;
}

export interface ExperimentDetailResponse extends Experiment {
  configurations_count?: number;
  dataset_id?: string | null;
  hashes?: {
    configuration_hash?: string | null;
    protocol_hash?: string | null;
    dataset_version_hash?: string | null;
  };
  rubric_snapshot?: any;
  judge_model_snapshot?: any;
  judge_rubric_snapshot?: any;
  ready_at?: string | null;
}

export interface BenchmarkConfigProgress {
  configuration_id: string;
  label?: string | null;
  total: number;
  completed: number;
  failed: number;
  attempted_cases: number;
  pending: number;
}

export interface BenchmarkResponse {
  plan: BenchmarkPlan | null;
  progress?: BenchmarkProgress | null;
  configurations?: BenchmarkConfigProgress[];
}

export interface AggregateRow extends ConfigAggregate {
  model_id?: string | null;
  model_snapshot?: Record<string, any> | null;
  attempted_runs?: number;
}

export interface EvaluateTarget {
  run_id: string;
  label?: string | null;
  model_name?: string | null;
  status?: string | null;
}

export interface CaseRunCell {
  run_id?: string | null;
  configuration_id?: string | null;
  label?: string | null;
  status?: string | null;
  latency_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cost_known?: boolean;
  cost?: number | null;
  score?: number | null;
  evaluation_id?: string | null;
}

export interface CaseRow {
  dataset_case_id: string;
  input_text?: string | null;
  expected_output?: string | null;
  configurations: Record<string, CaseRunCell | null>;
}

export interface CasesPage {
  total: number;
  limit: number;
  offset: number;
  items: CaseRow[];
}

export interface ResultsResponse {
  experiment_id?: string;
  legacy?: boolean;
  configurations: AggregateRow[];
  cases?: CasesPage;
  total_planned_calls?: number | null;
  plan_status?: string | null;
  run_count?: number;
  note?: string;
}

export interface CompareDeltas {
  avg_score: number | null;
  success_rate: number | null;
  avg_latency_ms: number | null;
  known_cost_total: number | null;
}

export interface CompareCaseRow {
  case_id: string;
  input_snippet?: string;
  classification: string;
  baseline_score: number | null;
  candidate_score: number | null;
  delta?: number | null;
}

export interface CompareResponse {
  experiment_id?: string;
  baseline?: AggregateRow;
  candidate?: AggregateRow;
  comparable: boolean;
  deltas: CompareDeltas;
  case_counts: {
    improved: number;
    regressed: number;
    unchanged: number;
    incomparable_error: number;
  };
  changed_cases?: CompareCaseRow[];
  cases?: CompareCaseRow[];
}

export interface ProvenanceResponse {
  experiment_id?: string;
  title?: string;
  status?: string;
  legacy?: boolean;
  experiment_type?: string;
  protocol?: ExperimentProtocol | null;
  dataset_version?: Record<string, any> | null;
  dataset_version_hash?: string | null;
  protocol_hash?: string | null;
  configuration_hash?: string | null;
  configurations?: any[];
  repetitions?: number;
  runner_version?: string | null;
  schema_version?: number | string | null;
  rubric_snapshot?: any;
  judge_model_snapshot?: any;
  judge_rubric_snapshot?: any;
  hashes?: {
    configuration_hash?: string | null;
    protocol_hash?: string | null;
    dataset_version_hash?: string | null;
  };
  created_at?: string;
  updated_at?: string;
  ready_at?: string | null;
}

export interface RunDetail {
  id: string;
  run_id?: string;
  experiment_id?: string | null;
  context?: string | null;
  execution_kind?: string | null;
  provider_name?: string | null;
  model_id?: string | null;
  model_name?: string | null;
  model_identifier?: string | null;
  status?: string;
  latency_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cached_tokens?: number | null;
  reasoning_tokens?: number | null;
  estimated_cost?: number | null;
  cost_available?: boolean;
  error_message?: string | null;
  created_at?: string | null;
}
