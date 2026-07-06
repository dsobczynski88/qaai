<script setup lang="ts">
import { ref } from "vue";
import ReviewerCard from "./ReviewerCard.vue";
import RtmForm from "./forms/RtmForm.vue";
import TcForm from "./forms/TcForm.vue";
import HazardForm from "./forms/HazardForm.vue";
import { useJobStore } from "../stores/job";
import type { ReviewKind } from "../types";

const job = useJobStore();
const active = ref<ReviewKind | null>(null);

function select(id: ReviewKind) {
  if (active.value === id) return;
  active.value = id;
  // Switching reviewers supersedes any in-flight poll loop and hides the status.
  job.cancelSilently();
}
</script>

<template>
  <section class="selector">
    <p class="selector-label">What would you like to review today?</p>

    <ReviewerCard
      id="rtm"
      title="Requirement Coverage"
      badge="Test Suite Review"
      :active="active === 'rtm'"
      @select="select('rtm')"
    >
      <RtmForm />
    </ReviewerCard>

    <ReviewerCard
      id="tc"
      title="Test Case Adequacy"
      badge="Test Case Review"
      :active="active === 'tc'"
      @select="select('tc')"
    >
      <TcForm />
    </ReviewerCard>

    <ReviewerCard
      id="hz"
      title="Software Hazard Analysis"
      badge="Hazard Risk Review"
      :active="active === 'hz'"
      @select="select('hz')"
    >
      <HazardForm />
    </ReviewerCard>
  </section>
</template>

<style scoped>
.selector {
  width: 100%;
  max-width: 640px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  animation: fadeUp 0.6s ease 0.15s both;
}

.selector-label {
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 6px;
}
</style>
