export type JsonObject = Record<string, unknown>;

export interface RunRow {
  mode?: "live" | "demo";
  run_id: string;
  state: string;
  autonomy: string;
  format: string;
  summary: string;
  created_at: string;
  updated_at: string;
  is_paused: number;
  error?: string | null;
}

export interface TaskRow {
  task_id: string;
  task_type: string;
  target_state: string;
  tool_name?: string | null;
  status: string;
  attempt: number;
  max_attempts: number;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
}

export interface RunEvent {
  event_id: string;
  event_type: string;
  state: string;
  status?: string | null;
  summary: string;
  created_at: string;
  cost_usd: number;
  latency_ms: number;
  payload: JsonObject;
}

export interface EvidencePack extends JsonObject {
  evidence_pack_id: string;
  candidate_id: string;
  overall_confidence: number;
  context_completeness: number;
  risk_flags: string[];
  claims: Array<{ claim_id: string; normalized_claim: string; confidence: number }>;
  sources: Array<{ source_id: string; title: string; url: string; excerpt: string; trust_signals: string[] }>;
}

export interface Decision extends JsonObject {
  decision_id: string;
  candidate_id: string;
  selected: boolean;
  rank?: number | null;
  confidence: number;
  decision_summary: string;
  dimension_scores: Record<string, number>;
  risk_flags: string[];
  rejected_because: string[];
}

export interface Snapshot {
  run: RunRow & { budget: JsonObject; spent: JsonObject };
  goal: JsonObject & { query: string; target_duration_seconds: number; risk_tolerance: string };
  plan: JsonObject & { decision_summary: string };
  tasks: TaskRow[];
  events: RunEvent[];
  evidence: EvidencePack[];
  decisions: Decision[];
  script_issues: JsonObject[];
  quality_issues: JsonObject[];
  artifacts: JsonObject[];
  documents: Record<string, JsonObject>;
  media: { video?: string | null; cover?: string | null };
}

export interface MemoryDiagnostic {
  diagnostic_id: string;
  code: string;
  stage: "model_call" | "model_output" | "validation" | "storage" | "processing";
  http_status: number | null;
  validation_issues: Array<{ field: string; error_type: string }>;
}

export interface FeedbackResult {
  feedback_id: string;
  memory_id: string | null;
  memory_ids: string[];
  memory_processing: {
    status: "stored" | "skipped" | "disabled" | "unavailable" | "failed";
    message: string;
    memory_ids: string[];
    diagnostic?: MemoryDiagnostic | null;
  };
}

export interface MemoryCandidate {
  ai_processed?: boolean;
  processing_model?: string | null;
  processing_version?: string | null;
  source_quote?: string;
  source_ids?: string[];
  decision_summary?: string;
  memory_id: string;
  run_id: string;
  memory_type: string;
  content: string;
  confidence: number;
  status: string;
  created_at: string;
}


export interface UsageTotals {
  calls: number;
  failed_calls: number;
  canceled_calls: number;
  unknown_usage_calls: number;
  unpriced_calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cached_input_tokens: number | null;
  reported_cost_usd: number | null;
  estimated_cost_usd: number | null;
  average_latency_ms: number | null;
}

export interface UsageDashboard {
  days: number;
  timezone: string;
  generated_at: string;
  totals: UsageTotals;
  models: Array<UsageTotals & { model: string }>;
  purposes: Array<UsageTotals & { purpose: string }>;
  daily: Array<{ date: string; calls: number; total_tokens: number | null; unknown_usage_calls: number }>;
  recent: Array<{
    usage_id: string; created_at: string; model: string; purpose: string;
    status: string; run_id: string | null; total_tokens: number | null;
    latency_ms: number; error_type: string | null;
  }>;
  runs: Record<string, number>;
  memories: { approved_now: number; created_in_period: number };
  feedback_count: number;
}
