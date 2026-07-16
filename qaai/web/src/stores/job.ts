import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { getJobResult, getJobStatus, cancelJob } from "../api/reviews";
import { POLL_INTERVAL_MS, MAX_POLL_MS } from "../constants";
import type { JobMessage, JobStatus, Phase } from "../types";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export interface StartOptions {
  /** Submits the job and resolves to a job_id (POST → 202). */
  submit: (signal: AbortSignal) => Promise<string>;
  /** Filename for the downloaded report. */
  filename: string;
  /** Human label for the status heading, e.g. "Requirement Coverage Review". */
  label: string;
  /** Sub-text shown under the spinner while running. */
  baseSub: string;
}

/**
 * The async review engine, ported from the original runJob(). A monotonically
 * increasing `generation` token plus an AbortController supersede any in-flight
 * poll loop when the user switches reviewers, starts a new run, or hits Stop —
 * the reactive replacement for the old global `pollToken`.
 */
export const useJobStore = defineStore("job", () => {
  let generation = 0;
  let controller: AbortController | null = null;

  const phase = ref<Phase>("idle");
  const title = ref("");
  const sub = ref("");

  // Progress (raw; components format for display).
  const total = ref(0);
  const done = ref(0);
  const succeeded = ref(0);
  const failed = ref(0);
  const etaSeconds = ref<number | null>(null);
  const messages = ref<JobMessage[]>([]);
  const startTs = ref(0);

  const currentJobId = ref<string | null>(null);

  // Result.
  const resultUrl = ref<string | null>(null);
  const resultFilename = ref("");
  const resultJob = ref<JobStatus | null>(null);

  const error = ref("");

  const isRunning = computed(() => phase.value === "loading");
  const canStop = computed(() => currentJobId.value !== null);
  const hasCounts = computed(() => total.value > 0);
  const pct = computed(() =>
    total.value > 0 ? Math.round((done.value / total.value) * 100) : 0,
  );
  const isPartial = computed(() => (resultJob.value?.failed ?? 0) > 0);

  function revokeResult() {
    if (resultUrl.value) {
      URL.revokeObjectURL(resultUrl.value);
      resultUrl.value = null;
    }
  }

  function resetProgress() {
    total.value = 0;
    done.value = 0;
    succeeded.value = 0;
    failed.value = 0;
    etaSeconds.value = null;
    messages.value = [];
  }

  function applyJob(job: JobStatus) {
    total.value = job.total ?? 0;
    done.value = job.done ?? 0;
    succeeded.value = job.succeeded ?? 0;
    failed.value = job.failed ?? 0;
    etaSeconds.value = job.eta_seconds ?? null;
    messages.value = job.messages ?? [];
  }

  /** Supersede any running loop and abort its in-flight fetch. */
  function bumpGeneration() {
    generation++;
    if (controller) {
      controller.abort();
      controller = null;
    }
  }

  /** Cancel polling quietly and hide the status area (used on reviewer switch). */
  function cancelSilently() {
    bumpGeneration();
    currentJobId.value = null;
    phase.value = "idle";
  }

  async function start(opts: StartOptions): Promise<void> {
    bumpGeneration();
    const myGen = generation;
    controller = new AbortController();
    const signal = controller.signal;

    revokeResult();
    resultJob.value = null;
    resultFilename.value = "";
    error.value = "";
    phase.value = "loading";
    title.value = `Running ${opts.label}…`;
    sub.value = opts.baseSub;
    resetProgress();
    startTs.value = Date.now();
    currentJobId.value = null;

    try {
      const jobId = await opts.submit(signal);
      if (myGen !== generation) return;
      currentJobId.value = jobId;

      while (true) {
        await sleep(POLL_INTERVAL_MS);
        if (myGen !== generation) return;
        if (Date.now() - startTs.value > MAX_POLL_MS) {
          throw new Error(
            "Timed out waiting for the review to finish (4 hr). " +
              "The job may still be running on the server — try again later.",
          );
        }
        const job = await getJobStatus(jobId, signal);
        if (myGen !== generation) return;
        applyJob(job);

        if (job.status === "completed") {
          const blob = await getJobResult(jobId, signal);
          if (myGen !== generation) return;
          revokeResult();
          resultUrl.value = URL.createObjectURL(blob);
          resultFilename.value = opts.filename;
          resultJob.value = job;
          phase.value = "done";
          currentJobId.value = null;
          return;
        }
        if (job.status === "failed") {
          throw new Error(job.error || "Review failed.");
        }
      }
    } catch (e) {
      // Ignore if this loop was already superseded (new run / stop / card switch
      // aborts the fetch, which surfaces here as an AbortError we don't display).
      if (myGen === generation) {
        error.value = e instanceof Error ? e.message : String(e);
        phase.value = "error";
      }
    } finally {
      if (myGen === generation) {
        currentJobId.value = null;
      }
    }
  }

  /** Cancel the in-flight job on the server and stop polling. */
  async function stop(): Promise<void> {
    const jobId = currentJobId.value;
    if (!jobId) return;
    bumpGeneration(); // supersede the poll loop so it exits quietly
    currentJobId.value = null;
    try {
      await cancelJob(jobId);
      phase.value = "error";
      error.value = "Run stopped.";
    } catch (e) {
      phase.value = "error";
      error.value = "Failed to stop run — " + (e instanceof Error ? e.message : String(e));
    }
  }

  return {
    phase,
    title,
    sub,
    total,
    done,
    succeeded,
    failed,
    etaSeconds,
    messages,
    startTs,
    currentJobId,
    resultUrl,
    resultFilename,
    resultJob,
    error,
    isRunning,
    canStop,
    hasCounts,
    pct,
    isPartial,
    start,
    stop,
    cancelSilently,
  };
});
