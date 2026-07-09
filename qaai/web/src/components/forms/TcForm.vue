<script setup lang="ts">
import { ref } from "vue";
import TextField from "../controls/TextField.vue";
import CacheModeRadio from "../controls/CacheModeRadio.vue";
import LabeledCheckbox from "../controls/LabeledCheckbox.vue";
import SubmitButton from "../controls/SubmitButton.vue";
import { useJobStore } from "../../stores/job";
import { submitBaselineReview } from "../../api/reviews";
import { TOOLTIPS } from "../../constants";
import type { CacheMode } from "../../types";

const job = useJobStore();

const baseline = ref("");
const cacheMode = ref<CacheMode>("on");
const testMode = ref(true);
const includeDecomp = ref(true);

function run() {
  const id = baseline.value.trim();
  if (!id) {
    alert("Please enter a JAMA Baseline ID.");
    return;
  }
  job.start({
    submit: (signal) =>
      submitBaselineReview(
        "/api/v1/test-case-review",
        {
          baseline_id: id,
          cache_mode: cacheMode.value,
          test_mode: testMode.value,
          include_edge_case_analysis: false,
          include_decomposition_analysis: includeDecomp.value,
          include_design_summaries: false,
        },
        signal,
      ),
    filename: "qaai_tc_review.html",
    label: "Test Case Adequacy Review",
    baseSub: `Fetching baseline ${id} from JAMA and processing requirements. This may take several minutes.`,
  });
}
</script>

<template>
  <div>
    <TextField
      id="tc-baseline"
      v-model="baseline"
      label="JAMA Baseline ID"
      placeholder="e.g. BASE-84429"
    />
    <CacheModeRadio v-model="cacheMode" name="tc-cache" :tooltip="TOOLTIPS.cacheMode" />
    <LabeledCheckbox
      id="tc-test-mode"
      v-model="testMode"
      label="Test mode (cached JAMA only)"
      :tooltip="TOOLTIPS.testMode"
    />
    <LabeledCheckbox
      id="tc-require-decomp"
      v-model="includeDecomp"
      label="Include requirement decomposition analysis"
      :tooltip="TOOLTIPS.decomposition"
    />
    <SubmitButton
      label="Run Test Case Adequacy Review"
      :busy="job.isRunning"
      @click="run"
    />
  </div>
</template>
