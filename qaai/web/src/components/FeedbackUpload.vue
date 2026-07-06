<script setup lang="ts">
import { ref, computed } from "vue";
import { useAuthStore } from "../stores/auth";
import { uploadFeedback } from "../api/reviews";

// Send an exported reviewer feedback JSON back to the server (saved under
// ./shared/feedback/). Role-gated: hidden from viewers.
const auth = useAuthStore();
const canUpload = computed(() => auth.can("upload_feedback"));

const fileInput = ref<HTMLInputElement | null>(null);
const chosenName = ref("");
const status = ref("");

function choose() {
  fileInput.value?.click();
}

function onFileChange() {
  const f = fileInput.value?.files?.[0];
  chosenName.value = f ? "✓ " + f.name : "";
}

async function upload() {
  const file = fileInput.value?.files?.[0];
  if (!file) {
    alert("Please choose a feedback JSON file first.");
    return;
  }
  status.value = "Uploading…";
  try {
    const saved = await uploadFeedback(file);
    status.value = "Uploaded ✓ " + saved;
  } catch (e) {
    status.value = "Upload failed — " + (e instanceof Error ? e.message : String(e));
  }
}
</script>

<template>
  <div v-if="canUpload" class="footer-feedback">
    <input
      ref="fileInput"
      type="file"
      accept=".json"
      hidden
      @change="onFileChange"
    />
    <button type="button" class="btn-link" @click="choose">
      Choose feedback file…
    </button>
    <span class="feedback-chosen">{{ chosenName }}</span>
    <button type="button" class="btn-link" @click="upload">
      ⬆ Upload feedback
    </button>
    <span class="feedback-status" aria-live="polite">{{ status }}</span>
  </div>
</template>

<style scoped>
.footer-feedback {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin-bottom: 16px;
  text-transform: none;
  letter-spacing: normal;
}

.btn-link {
  background: none;
  border: none;
  padding: 0;
  font-family: inherit;
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--amber);
  cursor: pointer;
}
.btn-link:hover {
  text-decoration: underline;
}

.feedback-chosen {
  color: var(--green);
}
.feedback-status {
  color: var(--muted);
}
</style>
