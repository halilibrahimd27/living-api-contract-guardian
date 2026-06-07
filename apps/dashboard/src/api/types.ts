// Services
export interface ServiceCreate {
  name: string
  owner: string
}

export interface ServiceRead {
  id: string
  name: string
  owner: string
  created_at: string
}

export interface ContractVersionRead {
  id: string
  contract_id: string
  service_id: string
  version_hash: string
  spec_metadata: Record<string, unknown>
  created_at: string
}

export interface ContractRead {
  id: string
  service_id: string
  name: string
  kind: "openapi" | "proto"
  version: ContractVersionRead
  created: boolean
}

// Diff
export type Verdict = "additive" | "behavioral" | "breaking"

export interface ChangeRecord {
  change_id: string
  kind: string
  location: string
  verdict: Verdict
  rule_id: string
  rationale: string
  affected_clients: string[]
  before: unknown
  after: unknown
  detail: Record<string, unknown>
}

export interface ChangeReportSummary {
  total: number
  breaking: number
  behavioral: number
  additive: number
}

export interface SpectralFinding {
  code: string
  message: string
  severity: number
  path: string[]
}

export interface ChangeReport {
  diff_id: string | null
  contract_kind: "openapi" | "proto"
  summary: ChangeReportSummary
  changes: ChangeRecord[]
  spectral_findings: SpectralFinding[]
  ruleset_id: string
}

// Campaigns
export type CampaignState =
  | "draft"
  | "active"
  | "decaying"
  | "ready_to_remove"
  | "completed"
  | "aborted"

export interface MetricPoint {
  sampled_at: string
  usage_count: number
  ewma_value: number
  remaining_client_count: number
}

export interface ReminderPRRead {
  id: string
  client_repo: string
  pr_number: number | null
  branch_name: string | null
  pr_state: string
}

export interface CampaignRead {
  id: string
  name: string
  description: string | null
  endpoint_id: string | null
  field_path: string | null
  state: CampaignState
  usage_threshold_pct: number
  decay_window_days: number
  peak_usage: number
  github_repo: string | null
  created_at: string
  updated_at: string
  decay_curve: MetricPoint[]
  remaining_clients: string[]
  reminder_prs: ReminderPRRead[]
}

// Health
export interface HealthResponse {
  version: string
  git_sha: string
  db_ok: boolean
  redis_ok: boolean
}
