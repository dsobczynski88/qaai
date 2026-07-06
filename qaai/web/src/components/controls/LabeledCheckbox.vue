<script setup lang="ts">
import InfoTooltip from "./InfoTooltip.vue";

defineProps<{
  id: string;
  label: string;
  modelValue: boolean;
  tooltip?: string;
}>();
const emit = defineEmits<{ (e: "update:modelValue", value: boolean): void }>();
</script>

<template>
  <div class="cache-toggle">
    <label class="cache-check">
      <input
        :id="id"
        type="checkbox"
        :checked="modelValue"
        @change="emit('update:modelValue', ($event.target as HTMLInputElement).checked)"
      />
      <span>{{ label }}</span>
    </label>
    <InfoTooltip v-if="tooltip" :text="tooltip" />
  </div>
</template>
