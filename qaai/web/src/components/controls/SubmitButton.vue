<script setup lang="ts">
import { computed } from "vue";
import { useAuthStore } from "../../stores/auth";
import type { Permission } from "../../types";

const props = withDefaults(
  defineProps<{ label: string; busy?: boolean; permission?: Permission }>(),
  { busy: false, permission: "run_review" },
);
const emit = defineEmits<{ (e: "click"): void }>();

const auth = useAuthStore();
const allowed = computed(() => auth.can(props.permission));
const disabled = computed(() => props.busy || !allowed.value);
const title = computed(() =>
  allowed.value
    ? undefined
    : "You need the Reviewer or Admin role to run reviews.",
);
</script>

<template>
  <button
    class="btn-submit"
    type="button"
    :disabled="disabled"
    :title="title"
    @click="emit('click')"
  >
    <span aria-hidden="true">▶</span> {{ label }}
  </button>
  <p v-if="!allowed" class="perm-note">
    Your role is view-only — running reviews requires the Reviewer or Admin role.
  </p>
</template>

<style scoped>
.btn-submit {
  width: 100%;
  margin-top: 6px;
  padding: 13px 24px;
  background: linear-gradient(135deg, #c07a08 0%, #e8930a 50%, #c07a08 100%);
  background-size: 200% 200%;
  background-position: 100% 0;
  border: none;
  color: #0a0600;
  font-family: var(--display);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  cursor: pointer;
  transition:
    background-position 0.4s ease,
    opacity 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
}
.btn-submit:hover:not(:disabled) {
  background-position: 0% 0;
}
.btn-submit:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.perm-note {
  margin-top: 8px;
  font-size: 11px;
  color: var(--muted);
  text-align: center;
}
</style>
