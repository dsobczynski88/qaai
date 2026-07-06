<script setup lang="ts">
import { useJobStore } from "../../stores/job";
import ProgressPanel from "./ProgressPanel.vue";
import ResultBox from "./ResultBox.vue";
import ErrorBox from "./ErrorBox.vue";
import StopRunButton from "./StopRunButton.vue";

const job = useJobStore();
</script>

<template>
  <section v-if="job.phase !== 'idle'" class="status-area">
    <div
      v-if="job.phase === 'loading'"
      class="status-box"
      role="status"
      aria-live="polite"
    >
      <div class="spinner"></div>
      <div class="status-text">
        <strong>{{ job.title }}</strong>
        <ProgressPanel />
      </div>
      <StopRunButton v-if="job.canStop" />
    </div>

    <ResultBox v-else-if="job.phase === 'done'" />
    <ErrorBox v-else-if="job.phase === 'error'" />
  </section>
</template>

<style scoped>
.status-area {
  width: 100%;
  max-width: 640px;
  margin-top: 28px;
  animation: fadeUp 0.4s ease both;
}

.status-box {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 20px 24px;
  display: flex;
  align-items: center;
  gap: 16px;
}

.spinner {
  width: 28px;
  height: 28px;
  border: 2.5px solid var(--border);
  border-top-color: var(--amber);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
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
</style>
