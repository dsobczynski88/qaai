import type { Permission, Role } from "./types";

// ── Polling engine ──
// Reviews run as background jobs: POST returns 202 + job_id, then we poll a fast
// status endpoint. Because every request is sub-second, the upstream proxy never
// idles out (no 504s). Kept identical to the original vanilla app.
export const POLL_INTERVAL_MS = 4000;
export const MAX_POLL_MS = 4 * 60 * 60 * 1000; // 4 hours

// ── RBAC ──
// Which roles grant which UI action. This UX gating mirrors the backend permission
// map (qaai/api/authz.py PERMISSIONS_BY_ROLE), which is the real gate — a determined
// client can still call the API directly and gets a 401/403 there. Keep the two in
// sync. admin: everything; user: run reviews + upload feedback.
export const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  admin: ["run_review", "upload_feedback", "manage"],
  user: ["run_review", "upload_feedback"],
};

export const ALL_ROLES: Role[] = ["admin", "user"];

// ── Tooltip copy (centralized; ported verbatim from the original index.html) ──
export const TOOLTIPS = {
  cacheMode:
    "'On' (default): reuse the newest cached interim analysis and only re-run the final assessment fresh, saving a new timestamped result. 'Test': recreate the report entirely from cached results with no LLM calls (and JAMA read from cache) — fails if any node result is missing. 'Off': re-run every node and save a new timestamped result, reusing nothing. Files are kept as immutable, timestamped history under ./shared/runs; a run that errors or comes out incomplete purges only the files it just wrote.",
  cacheModeHazard:
    "'On' (default): reuse the newest cached interim analysis (H1–H6, R7, summarizers, embedded per-requirement test-suite review) and only re-run the final hazard assessment fresh, saving a new timestamped result. 'Test': recreate the report entirely from cached results with no LLM calls (and JAMA read from cache) — fails if any node result is missing. 'Off': re-run every node and save a new timestamped result, reusing nothing. Files are kept as immutable, timestamped history under ./shared/runs; a hazard row that errors or comes out incomplete purges only the files it just wrote.",
  testMode:
    "When test mode is on, QAAI runs strictly from previously cached JAMA results — no live JAMA API calls are made, so invalid or mock credentials are tolerated. Turn off to fetch live from JAMA.",
  testModeHazard:
    "When test mode is on, the bidirectional-trace JAMA lookup runs strictly from previously cached results — no live JAMA API calls are made, so invalid or mock credentials are tolerated. Turn off to fetch live from JAMA.",
  edgeCase:
    "When on, QAAI uses the edge-case prompt set (test_suite_reviewer_v4) whose decomposer surfaces boundary, concurrency, state/mode and degenerate-input specs. Off uses the baseline set (v3). Cached results are namespaced per prompt set.",
  edgeCaseHazard:
    "When on, the embedded test-suite review per requirement uses the edge-case prompt set (test_suite_reviewer_v4, edge-case decomposer). Off uses the baseline set (v3). Cached results are namespaced per prompt set.",
  decomposition:
    "When on (default), QAAI decomposes each requirement into atomic specs and evaluates coverage per spec (test_case_reviewer_v2). Turn off to skip decomposition and review each test case directly against the original requirement text (test_case_reviewer_v3) — faster, coarser-grained.",
  designSummaries:
    "When on, QAAI runs the design_summarizer so summarized design documents feed per-spec coverage and the R6 Design Alignment criterion. Off (default) skips that step in the graph. Cached results for design-sensitive nodes are keyed by this toggle so switching it never reuses a result computed under the other mode.",
  designSummariesHazard:
    "When on, the embedded per-requirement test-suite review runs its design_summarizer so design documents inform coverage and R6 Design Alignment. Off (default) skips it. Does not affect the hazard rubric's own H2/H3 design analysis. Cached results are keyed by this toggle.",
  baselineReviewType:
    "What the JAMA baseline contains. 'Tests' (default): the baseline holds test cases, and QAAI traces each upstream to its requirement (the original behavior). 'Requirements': the baseline holds requirements directly, and QAAI reviews each requirement's downstream test coverage. Both produce the same per-requirement report — only the JAMA fetch differs.",
} as const;
