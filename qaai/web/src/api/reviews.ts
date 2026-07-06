import { apiFetch, parseErr } from "./client";
import type { BaselineReviewRequest, JobStatus } from "../types";

/** POST a baseline (RTM or TC) review → 202 { job_id }. */
export async function submitBaselineReview(
  endpoint: string,
  body: BaselineReviewRequest,
  signal?: AbortSignal,
): Promise<string> {
  const resp = await apiFetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) throw new Error(await parseErr(resp));
  const { job_id } = await resp.json();
  if (!job_id) throw new Error("Server did not return a job_id.");
  return job_id;
}

/** POST the hazard review (multipart form incl. Excel file) → 202 { job_id }. */
export async function submitHazardReview(
  form: FormData,
  signal?: AbortSignal,
): Promise<string> {
  const resp = await apiFetch("/api/v1/hazard-risk-review", {
    method: "POST",
    body: form,
    signal,
  });
  if (!resp.ok) throw new Error(await parseErr(resp));
  const { job_id } = await resp.json();
  if (!job_id) throw new Error("Server did not return a job_id.");
  return job_id;
}

export async function getJobStatus(
  jobId: string,
  signal?: AbortSignal,
): Promise<JobStatus> {
  const resp = await apiFetch(`/api/v1/jobs/${jobId}`, { signal });
  if (!resp.ok) throw new Error(await parseErr(resp));
  return resp.json();
}

export async function getJobResult(
  jobId: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const resp = await apiFetch(`/api/v1/jobs/${jobId}/result`, { signal });
  if (!resp.ok) throw new Error(await parseErr(resp));
  return resp.blob();
}

export async function cancelJob(jobId: string): Promise<void> {
  const resp = await apiFetch(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" });
  if (!resp.ok) throw new Error(await parseErr(resp));
}

/** Upload an exported reviewer feedback JSON → { saved }. */
export async function uploadFeedback(file: File): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const resp = await apiFetch("/api/v1/feedback-upload", {
    method: "POST",
    body: form,
  });
  if (!resp.ok) throw new Error(await parseErr(resp));
  const { saved } = await resp.json();
  return saved || file.name;
}
