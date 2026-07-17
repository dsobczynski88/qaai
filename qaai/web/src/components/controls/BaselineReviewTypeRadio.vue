<script setup lang="ts">
import InfoTooltip from "./InfoTooltip.vue";
import type { BaselineReviewType } from "../../types";

defineProps<{ name: string; modelValue: BaselineReviewType; tooltip: string }>();
const emit = defineEmits<{ (e: "update:modelValue", value: BaselineReviewType): void }>();

const options: { value: BaselineReviewType; label: string }[] = [
  { value: "requirements", label: "Requirements" },
  { value: "tests", label: "Tests" },
];
</script>

<template>
  <div class="cache-toggle cache-radio">
    <div class="cache-radio-head">
      <span class="cache-radio-title">Baseline Review Type</span>
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
