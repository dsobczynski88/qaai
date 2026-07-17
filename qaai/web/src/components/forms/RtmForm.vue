<script setup lang="ts">
import { ref } from "vue";
import TextField from "../controls/TextField.vue";
import BaselineReviewTypeRadio from "../controls/BaselineReviewTypeRadio.vue";
import CacheModeRadio from "../controls/CacheModeRadio.vue";
import LabeledCheckbox from "../controls/LabeledCheckbox.vue";
import SubmitButton from "../controls/SubmitButton.vue";
import { useJobStore } from "../../stores/job";
import { submitBaselineReview } from "../../api/reviews";
import { TOOLTIPS } from "../../constants";
import type { BaselineReviewType, CacheMode } from "../../types";

const job = useJobStore();

const baseline = ref("");
const reviewType = ref<BaselineReviewType>("tests");
const cacheMode = ref<CacheMode>("on");
const testMode = ref(true);
const edgeCase = ref(false);
const designSummaries = ref(false);

function run() {
  const id = baseline.value.trim();
  if (!id) {
    alert("Please enter a JAMA Baseline ID.");
    return;
  }
  job.start({
    submit: (signal) =>
      submitBaselineReview(
        "/api/v1/test-suite-review",
        {
          baseline_id: id,
          baseline_review_type: reviewType.value,
          cache_mode: cacheMode.value,
          test_mode: testMode.value,
          include_edge_case_analysis: edgeCase.value,
          include_decomposition_analysis: true,
          include_design_summaries: designSummaries.value,
        },
        signal,
      ),
    filename: "qaai_rtm_review.html",
    label: "Requirement Coverage Review",
    baseSub: `Fetching baseline ${id} from JAMA and processing requirements. This may take several minutes.`,
  });
}
</script>

<template>
  <div>
    <TextField
      id="rtm-baseline"
      v-model="baseline"
      label="JAMA Baseline ID"
      placeholder="e.g. BASE-84429"
    />
    <BaselineReviewTypeRadio
      v-model="reviewType"
      name="rtm-review-type"
      :tooltip="TOOLTIPS.baselineReviewType"
    />
    <CacheModeRadio v-model="cacheMode" name="rtm-cache" :tooltip="TOOLTIPS.cacheMode" />
    <LabeledCheckbox
      id="rtm-test-mode"
      v-model="testMode"
      label="Test mode (cached JAMA only)"
      :tooltip="TOOLTIPS.testMode"
    />
    <LabeledCheckbox
      id="rtm-edge-case"
      v-model="edgeCase"
      label="Include Edge Case Analysis"
      :tooltip="TOOLTIPS.edgeCase"
    />
    <LabeledCheckbox
      id="rtm-design-summaries"
      v-model="designSummaries"
      label="Include Design Summaries"
      :tooltip="TOOLTIPS.designSummaries"
    />
    <SubmitButton
      label="Run Requirement Coverage Review"
      :busy="job.isRunning"
      @click="run"
    />
  </div>
</template>
