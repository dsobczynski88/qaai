<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue";
import { useJobStore } from "../../stores/job";
import ProgressLog from "./ProgressLog.vue";

const job = useJobStore();

// Tick once a second so the elapsed timer advances between 4s polls.
const now = ref(Date.now());
let timer: number | undefined;
onMounted(() => {
  timer = window.setInterval(() => (now.value = Date.now()), 1000);
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
});

function fmtElapsed(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s elapsed`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m ${r}s elapsed`;
}

function fmtEta(sec: number | null): string {
  if (sec == null) return "Estimating time remaining…";
  if (sec < 45) return "Estimated <1 min remaining";
  return `Estimated ${Math.round(sec / 60)} min remaining`;
}

const plural = (n: number) => (n === 1 ? "" : "s");
const elapsedText = computed(() => fmtElapsed(now.value - job.startTs));

const subLine = computed(() =>
  job.hasCounts
    ? job.sub
    : `${job.sub} · Detecting items… · ${elapsedText.value}`,
);

const countText = computed(() =>
  job.done === 0
    ? `${job.total} item${plural(job.total)} to review`
    : `[${job.done}/${job.total}] reviewed`,
);

const etaText = computed(() =>
  job.done >= job.total ? "Finalizing…" : fmtEta(job.etaSeconds),
);
const metaText = computed(
  () => `${job.pct}% · ${etaText.value} · ${elapsedText.value}`,
);
</script>

<template>
  <p class="progress-sub">{{ subLine }}</p>

  <div v-if="job.hasCounts" class="progress-wrap">
    <div class="progress-count">{{ countText }}</div>
    <div
      class="progress-bar"
      role="progressbar"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-valuenow="job.pct"
    >
      <div class="progress-fill" :style="{ width: job.pct + '%' }"></div>
    </div>
    <div class="progress-meta">{{ metaText }}</div>
    <ProgressLog :messages="job.messages" />
  </div>
</template>

<style scoped>
.progress-sub {
  font-size: 12px;
  color: var(--muted);
}
.progress-wrap {
  margin-top: 14px;
}
.progress-count {
  font-family: var(--display);
  font-size: 13px;
  font-weight: 600;
  color: #eef1f5;
  margin-bottom: 7px;
}
.progress-bar {
  width: 100%;
  height: 8px;
  background: var(--border);
  border-radius: 999px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  width: 0;
  background: var(--blue);
  border-radius: 999px;
  transition: width 0.4s ease;
}
.progress-meta {
  font-size: 12px;
  color: var(--muted);
  margin-top: 7px;
}
</style>
