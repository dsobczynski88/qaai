<script setup lang="ts">
import { ref } from "vue";
import { useJobStore } from "../../stores/job";

const job = useJobStore();
const stopping = ref(false);

async function stop() {
  stopping.value = true;
  try {
    await job.stop();
  } finally {
    stopping.value = false;
  }
}
</script>

<template>
  <button type="button" class="btn-stop" :disabled="stopping" @click="stop">
    ⏹ Stop Run
  </button>
</template>

<style scoped>
.btn-stop {
  margin-left: auto;
  flex-shrink: 0;
  align-self: center;
  padding: 8px 18px;
  background: transparent;
  border: 1px solid var(--red);
  color: var(--red);
  font-family: var(--display);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  cursor: pointer;
  transition:
    background 0.2s,
    color 0.2s,
    opacity 0.2s;
}
.btn-stop:hover:not(:disabled) {
  background: var(--red);
  color: #0a0600;
}
.btn-stop:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
