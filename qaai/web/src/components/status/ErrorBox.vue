<script setup lang="ts">
import { useJobStore } from "../../stores/job";

const job = useJobStore();
</script>

<template>
  <div class="error-box" role="alert">
    <span class="error-mark" aria-hidden="true">✕</span>
    <pre>{{ job.error }}</pre>
  </div>
</template>

<style scoped>
.error-box {
  background: rgba(192, 57, 43, 0.06);
  border: 1px solid rgba(192, 57, 43, 0.35);
  padding: 16px 20px;
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.error-mark {
  color: var(--red);
  font-size: 18px;
  flex-shrink: 0;
}
.error-box pre {
  font-size: 12px;
  color: #e07060;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--mono);
}
</style>
