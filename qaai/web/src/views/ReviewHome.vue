<script setup lang="ts">
import ReviewerSelector from "../components/ReviewerSelector.vue";
import StatusPanel from "../components/status/StatusPanel.vue";
</script>

<template>
  <div class="review-home">
    <ReviewerSelector />
    <StatusPanel />
  </div>
</template>

<style scoped>
.review-home {
  width: 100%;
  max-width: 640px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
</style>
