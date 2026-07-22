// ── Shared domain types ──

export type Role = "admin" | "user";

/** UI-gated actions. The client gates for UX; the backend enforces the same
 *  permission map server-side (qaai/api/authz.py). */
export type Permission = "run_review" | "upload_feedback" | "manage";

export interface User {
  id: string;
  name: string;
  email: string;
}

export interface Identity {
  user: User | null;
  roles: Role[];
}

export type ReviewKind = "rtm" | "tc" | "hz";
export type CacheMode = "on" | "test" | "off";
/** Which kind of JAMA baseline the RTM/test-suite reviewer is fed. */
export type BaselineReviewType = "requirements" | "tests";

/** A run-log line surfaced during polling (problem/advisory notes). */
export interface JobMessage {
  item_id?: string | null;
  text?: string;
  level?: "error" | "warning" | "info";
}

/** Shape returned by GET /api/v1/jobs/{id} (Job.to_status_dict on the backend). */
export interface JobStatus {
  job_id: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  filename?: string | null;
  error?: string | null;
  total?: number;
  done?: number;
  succeeded?: number;
  failed?: number;
  eta_seconds?: number | null;
  messages?: JobMessage[];
}

/** Reactive lifecycle phase the status UI binds to. */
export type Phase = "idle" | "loading" | "done" | "error";

/** JSON body for the RTM / TC (baseline) review endpoints. */
export interface BaselineReviewRequest {
  baseline_id: string;
  cache_mode: CacheMode;
  test_mode: boolean;
  include_edge_case_analysis: boolean;
  include_decomposition_analysis: boolean;
  include_design_summaries: boolean;
  baseline_review_type?: BaselineReviewType;
}
