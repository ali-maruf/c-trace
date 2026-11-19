// Response shapes of the C-Trace API (services/api). Request bodies come from the generated OpenAPI schema.
export type Role = "owner" | "manager" | "viewer" | "reviewer" | "buyer" | "lender";
export type Confidence = "High" | "Medium" | "Low";
export type Activity = "natural_gas" | "grid_electricity" | "diesel";
export type Target = "gas_m3" | "grid_kwh" | "diesel_l";

export interface Me {
  user_id: string;
  email: string | null;
  org_id: string;
  role: Role;
  memberships: { org_id: string; role: Role; org_name: string; is_demo: boolean }[];
}

export interface Asset { id: string; kind: string; capacity: number; unit: string; from_period: string }
export interface Intervention { id: string; measure: string; from_period: string; capex_bdt: number | null; notes: string | null; affects: Activity[] }

export interface Factory {
  id: string;
  org_id: string;
  name: string;
  location: string | null;
  lat: number | null;
  lon: number | null;
  factory_type: "knit_composite" | "woven" | "denim_wash" | "sweater";
  workers: number;
  has_wet_processing: boolean;
  captive_gas_gen: boolean;
  created_at: string;
  assets?: Asset[];
  interventions?: Intervention[];
}

export interface SummaryMonth {
  period: string;
  natural_gas?: number;
  grid_electricity?: number;
  diesel?: number;
  scope1_t: number;
  scope2_t: number;
  total_t: number;
  energy_gj: number;
  pieces?: number | null;
  kgco2e_per_piece?: number | null;
}

export interface Summary {
  factory_id: string;
  period_from: string | null;
  period_to: string | null;
  data_status: { records_by_status: Record<string, number>; unconfirmed_records: number; documents: number };
  months: SummaryMonth[];
  totals: null | {
    months: number;
    scope1_tco2e: number;
    scope2_location_tco2e: number;
    total_tco2e: number;
    energy_gj: number;
    pieces: number | null;
    kgco2e_per_piece: number | null;
    by_source_tco2e: Partial<Record<Activity, number>>;
    scope2_statutory: null | { scope2_statutory_tco2e: number; months_covered: number; factor_ids: string[]; note: string };
  };
}

export interface LedgerRow {
  trace_id: string;
  period: string;
  activity: Activity;
  scope: number;
  quantity: number;
  unit: string;
  energy_gj: number;
  kgco2e: number;
  tco2e: number;
  calculation: string;
  doc_sha256: string;
  activity_record_id: string;
  record_status: string;
  document_id: string;
  doc_name: string;
  factor_id: string;
  kgco2e_per_unit: number;
  factor_source: string;
  factor_source_url: string;
  factor_version: string;
}

export interface ActivityRecord {
  id: string;
  document_id: string;
  activity: Activity | null;
  period: string | null;
  quantity: number | null;
  unit: string | null;
  amount_bdt: number | null;
  bill_no: string | null;
  supplier: string | null;
  status: "extracted" | "confirmed" | "corrected" | "rejected";
  confirmed_by: string | null;
  confirmed_at: string | null;
  doc_name: string;
  doc_sha256: string;
  issues: string[];
  extraction_confidence: number | null;
  extraction_method: string | null;
  evidence: Record<string, string | number> | null;
}

export interface Suggestion {
  doc_name: string;
  period: string;
  activity: Activity;
  printed_quantity: number;
  suggested_quantity: number;
  median_tariff: number;
  implied_tariff: number;
  explanation: string;
  activity_record_id: string;
}

export interface Extraction {
  id: string;
  method: string;
  model: string | null;
  prompt_version: string | null;
  field_evidence: Record<string, { text: string; confidence: number | null } | string>;
  issues: string[];
  confidence: number | null;
  created_at: string;
}

export interface DocumentDetail {
  id: string;
  file_name: string;
  media_type: string;
  sha256: string;
  size_bytes: number;
  uploaded_at: string;
  factory_id: string;
  extractions: Extraction[];
}

export interface UploadResult {
  document: { id: string; file_name: string; uploaded_at: string; sha256: string };
  duplicate: boolean;
  job_id: number | null;
  message: string;
  job?: { status: string; last_error: string | null; result: { issues?: string[] } | null };
}

export interface Drivers { typical: number; production: number; weather: number; solar: number; season: number }
export interface CarrierCheck { z: number | null; expected: number | null; reported: number | null; expected_lo: number | null; expected_hi: number | null; drivers: Drivers | null }

export interface VerificationResult {
  period: string;
  z: Record<Target, CarrierCheck>;
  iso_score: number;
  confidence: Confidence;
  reasons: string[];
  resolved_by: string | null;
  resolved_at: string | null;
  resolution: string | null;
}

export interface Verification {
  run: { id: string; model_version: string; created_at: string; weather_source: Record<string, number> };
  stale: boolean;
  counts: Record<Confidence, number>;
  results: VerificationResult[];
}

export interface Check { value: number; limit: string; pass: boolean }

export interface Reduction {
  id: string;
  factory_id: string;
  intervention_id: string | null;
  measure: string | null;
  intervention_from: string | null;
  activity: Activity;
  unit: string;
  baseline_from: string;
  baseline_to: string;
  reporting_from: string;
  reporting_to: string;
  n_baseline: number;
  n_reporting: number;
  avoided: number;
  low: number;
  high: number;
  predicted: number;
  actual: number;
  tco2e: number;
  tco2e_low: number;
  tco2e_high: number;
  tariff_bdt: number | null;
  bdt_saved: number | null;
  r2: number;
  cv_rmse: number;
  nmbe: number;
  checks: Record<string, Check>;
  excluded: { baseline: string[]; reporting: string[] };
  curve: { period: string; baseline: number; actual: number | null; used: boolean; phase: "baseline" | "reporting" }[];
  verified: boolean;
  status: "draft" | "signed" | "superseded";
  status_reason: string;
  signed_by: string | null;
  signed_at: string | null;
  sign_comment: string | null;
}

export interface Benchmark {
  year: number;
  n_facilities: number;
  quantiles_tco2e: Record<"p10" | "p25" | "p50" | "p75" | "p90", number>;
  factory_scope1_tco2e_per_year: number | null;
  factory_percentile: number | null;
  source: string;
  source_url: string | null;
  licence: string | null;
  caveat: string;
  factory_months_used: number;
}

export interface WeatherMonth { period: string; t2m_c: number; cdd: number; source: string }

export interface ReportFacts {
  factory: string;
  location: string | null;
  period_from: string;
  period_to: string;
  months: number;
  scope1_tco2e: number;
  scope2_location_tco2e: number;
  total_tco2e: number;
  pieces: number | null;
  kgco2e_per_piece: number | null;
  energy_by_source: Record<string, { quantity: number; unit: string; tco2e: number }>;
  documents: number;
  factors: string[];
  confidence_months?: Partial<Record<Confidence, number>>;
  flagged_months?: { period: string; confidence: Confidence; reasons: string[] }[];
  verified_reductions?: { activity: Activity; avoided_tco2e: number; low: number; high: number; verified: boolean }[];
  data_quality?: { records_by_status: Record<string, number>; human_reviewed_share: number | null; verification_model: string | null };
  scope2_statutory_tco2e?: number;
}

export interface Report {
  id: string;
  factory_id: string;
  format: "buyer" | "ifrs_s2" | "bsec";
  title: string;
  period_from: string;
  period_to: string;
  narrative: string;
  method: string;
  facts: ReportFacts & { _guidance?: { title: string; source: string; score: number }[] };
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  include_documents: boolean;
  shared: boolean;
}

export interface CopilotAnswer {
  text: string;
  language: "en" | "bn";
  citations: { type: "trace" | "kb" | "reduction" | "verification" | "summary"; id: string; label: string }[];
  tool_calls: { tool: string; args: Record<string, unknown> }[];
  method: string;
  declined: boolean;
}
