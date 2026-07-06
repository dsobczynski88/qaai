<script setup lang="ts">
// The small "i" badge with a native title tooltip. Uses the global .cache-info
// style (styles/forms.css). tabindex=0 so it's keyboard-focusable.
defineProps<{ text: string }>();
</script>

<template>
  <span class="cache-info" tabindex="0" :title="text">i</span>
</template>
