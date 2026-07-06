<script setup lang="ts">
defineProps<{
  id: string;
  label: string;
  modelValue: string;
  placeholder?: string;
  optional?: boolean;
}>();
defineEmits<{ (e: "update:modelValue", value: string): void }>();
</script>

<template>
  <div class="field">
    <label :for="id">
      {{ label }}
      <span v-if="optional" class="field-optional">(optional)</span>
    </label>
    <input
      :id="id"
      type="text"
      :placeholder="placeholder"
      autocomplete="off"
      :value="modelValue"
      @input="$emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    />
  </div>
</template>
