<script setup lang="ts">
import { computed } from "vue";
import { useJobStore } from "../../stores/job";

const job = useJobStore();

const total = computed(() => job.resultJob?.total ?? 0);
const succeeded = computed(() => job.resultJob?.succeeded ?? 0);
const failed = computed(() => job.resultJob?.failed ?? 0);
const msgs = computed(() => job.resultJob?.messages ?? []);
const plural = (n: number) => (n === 1 ? "" : "s");

const partial = computed(() => failed.value > 0);
const icon = computed(() => (partial.value ? "⚠" : "✓"));
const heading = computed(() =>
  partial.value ? "Completed with issues" : "Review complete",
);

// Mirrors the original renderResultSummary(): all-clean, clean-with-advisories, or
// some-did-not-complete (amber).
const primaryLine = computed(() => {
  if (!total.value)
    return "Your report is ready. Click below to download the self-contained HTML viewer.";
  if (!partial.value)
    return (
      `All ${total.value} item${plural(total.value)} reviewed successfully.` +
      (msgs.value.length
        ? ` ${msgs.value.length} advisory note${plural(msgs.value.length)} recorded:`
        : " Your report is ready to download.")
    );
  return (
    `${succeeded.value} of ${total.value} item${plural(total.value)} completed cleanly — ` +
    `${failed.value} did not complete fully:`
  );
});
const showList = computed(() => total.value > 0 && msgs.value.length > 0);
const trailingNote = computed(() =>
  partial.value ? "These items are also flagged in the report's “View log”." : "",
);
</script>

<template>
  <div class="result-box" :class="{ partial }">
    <span class="result-icon">{{ icon }}</span>
    <div class="status-text">
      <strong>{{ heading }}</strong>
      <div class="result-summary">
        <div>{{ primaryLine }}</div>
        <ul v-if="showList" class="result-list">
          <li v-for="(m, i) in msgs" :key="i">
            {{ (m.item_id ?? "—") + " — " + (m.text ?? "") }}
          </li>
        </ul>
        <div v-if="trailingNote" class="trailing-note">{{ trailingNote }}</div>
      </div>
      <a
        class="btn-download"
        :href="job.resultUrl ?? '#'"
        :download="job.resultFilename"
        >⬇ Download Report</a
      >
    </div>
  </div>
</template>

<style scoped>
.result-box {
  background: rgba(44, 182, 125, 0.05);
  border: 1px solid rgba(44, 182, 125, 0.3);
  padding: 20px 24px;
  display: flex;
  align-items: center;
  gap: 16px;
}
.result-box.partial {
  background: rgba(232, 147, 10, 0.05);
  border-color: rgba(232, 147, 10, 0.35);
}
.result-icon {
  font-size: 22px;
  flex-shrink: 0;
  align-self: flex-start;
}
.status-text {
  flex: 1;
}
.status-text strong {
  display: block;
  font-family: var(--display);
  font-size: 14px;
  font-weight: 600;
  color: #eef1f5;
  margin-bottom: 3px;
}
.trailing-note {
  margin-top: 6px;
}
.result-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  font-size: 12px;
  color: var(--muted);
  max-height: 150px;
  overflow-y: auto;
}
.result-list li {
  margin-bottom: 3px;
  padding-left: 12px;
  position: relative;
}
.result-list li::before {
  content: "•";
  position: absolute;
  left: 0;
  color: var(--amber);
}
.btn-download {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  background: transparent;
  border: 1px solid var(--green);
  color: var(--green);
  font-family: var(--mono);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  cursor: pointer;
  text-decoration: none;
  transition:
    background 0.2s,
    color 0.2s;
  margin-top: 10px;
}
.btn-download:hover {
  background: var(--green);
  color: #060e0a;
}
</style>
