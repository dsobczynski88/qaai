<script setup lang="ts">
import { ref } from "vue";
import TextField from "../controls/TextField.vue";
import FileDropzone from "../controls/FileDropzone.vue";
import CacheModeRadio from "../controls/CacheModeRadio.vue";
import LabeledCheckbox from "../controls/LabeledCheckbox.vue";
import SubmitButton from "../controls/SubmitButton.vue";
import { useJobStore } from "../../stores/job";
import { submitHazardReview } from "../../api/reviews";
import { TOOLTIPS } from "../../constants";
import type { CacheMode } from "../../types";

const job = useJobStore();

const project = ref("");
const file = ref<File | null>(null);
const sheet = ref("");
const identifierPattern = ref("");
const cacheMode = ref<CacheMode>("on");
const testMode = ref(true);
const edgeCase = ref(false);
const designSummaries = ref(false);

function run() {
  const name = project.value.trim();
  if (!name) {
    alert("Please enter a project name.");
    return;
  }
  if (!file.value) {
    alert("Please upload a SHA Excel file.");
    return;
  }
  const selected = file.value;

  const form = new FormData();
  form.append("project_name", name);
  form.append("file", selected);
  form.append("sheet_name", sheet.value.trim() || "SHA Table");
  form.append("identifier_pattern", identifierPattern.value.trim() || "GID-\\d+");
  form.append("cache_mode", cacheMode.value);
  form.append("test_mode", String(testMode.value));
  form.append("include_edge_case_analysis", String(edgeCase.value));
  form.append("include_design_summaries", String(designSummaries.value));

  job.start({
    submit: (signal) => submitHazardReview(form, signal),
    filename: "qaai_hazard_review.html",
    label: "Hazard Risk Review",
    baseSub: `Processing ${selected.name} · ${name}. This may take several minutes per hazard row.`,
  });
}
</script>

<template>
  <div>
    <TextField
      id="hz-project"
      v-model="project"
      label="Project Name"
      placeholder="e.g. Infusion Pump SW v3.0"
    />
    <div class="field">
      <label for="hz-file">SHA Excel Table</label>
      <FileDropzone v-model="file" accept=".xlsx,.xls">
        <strong>Click to browse</strong> or drag &amp; drop your SHA Excel file<br />
        Expects a sheet named <code>SHA Table</code> · .xlsx / .xls
      </FileDropzone>
    </div>
    <TextField
      id="hz-sheet"
      v-model="sheet"
      label="Sheet Name"
      placeholder="SHA Table"
      optional
    />
    <TextField
      id="hz-identifier-pattern"
      v-model="identifierPattern"
      label="Requirements Prefix"
      placeholder="GID-\d+"
      optional
    />
    <CacheModeRadio
      v-model="cacheMode"
      name="hz-cache"
      :tooltip="TOOLTIPS.cacheModeHazard"
    />
    <LabeledCheckbox
      id="hz-test-mode"
      v-model="testMode"
      label="Test mode (cached JAMA only)"
      :tooltip="TOOLTIPS.testModeHazard"
    />
    <LabeledCheckbox
      id="hz-edge-case"
      v-model="edgeCase"
      label="Include Edge Case Analysis"
      :tooltip="TOOLTIPS.edgeCaseHazard"
    />
    <LabeledCheckbox
      id="hz-design-summaries"
      v-model="designSummaries"
      label="Include Design Summaries"
      :tooltip="TOOLTIPS.designSummariesHazard"
    />
    <SubmitButton label="Run Hazard Risk Review" :busy="job.isRunning" @click="run" />
  </div>
</template>
