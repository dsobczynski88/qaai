<script setup lang="ts">
import type { JobMessage } from "../../types";

defineProps<{ messages: JobMessage[] }>();
</script>

<template>
  <ul v-if="messages.length" class="progress-log">
    <li
      v-for="(m, i) in messages"
      :key="i"
      :class="'log-' + (m.level || 'warning')"
    >
      <span class="log-item">{{ m.item_id ?? "—" }}</span>{{ m.text ?? "" }}
    </li>
  </ul>
</template>

<style scoped>
.progress-log {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  max-height: 150px;
  overflow-y: auto;
}
.progress-log li {
  font-size: 11px;
  line-height: 1.5;
  padding: 4px 9px;
  margin-bottom: 4px;
  border-left: 2px solid var(--muted);
  background: rgba(255, 255, 255, 0.02);
  color: var(--muted);
}
.progress-log li.log-error {
  border-left-color: var(--red);
  color: #e07060;
}
.progress-log li.log-warning {
  border-left-color: var(--amber);
  color: #d8b07a;
}
.progress-log li .log-item {
  font-weight: 600;
  margin-right: 6px;
}
</style>
