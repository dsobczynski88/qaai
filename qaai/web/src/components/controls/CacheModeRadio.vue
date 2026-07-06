<script setup lang="ts">
import InfoTooltip from "./InfoTooltip.vue";
import type { CacheMode } from "../../types";

defineProps<{ name: string; modelValue: CacheMode; tooltip: string }>();
const emit = defineEmits<{ (e: "update:modelValue", value: CacheMode): void }>();

const options: { value: CacheMode; label: string }[] = [
  { value: "on", label: "On (reuse cached, fresh final)" },
  { value: "test", label: "Test (recreate from cache, no LLM)" },
  { value: "off", label: "Off (re-run all, save timestamped)" },
];
</script>

<template>
  <div class="cache-toggle cache-radio">
    <div class="cache-radio-head">
      <span class="cache-radio-title">Cache mode</span>
      <InfoTooltip :text="tooltip" />
    </div>
    <label v-for="o in options" :key="o.value" class="cache-check">
      <input
        type="radio"
        :name="name"
        :value="o.value"
        :checked="modelValue === o.value"
        @change="emit('update:modelValue', o.value)"
      />
      <span>{{ o.label }}</span>
    </label>
  </div>
</template>
