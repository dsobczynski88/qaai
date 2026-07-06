<script setup lang="ts">
import { ref, computed } from "vue";

const props = defineProps<{ modelValue: File | null; accept?: string }>();
const emit = defineEmits<{ (e: "update:modelValue", value: File | null): void }>();

const input = ref<HTMLInputElement | null>(null);
const dragOver = ref(false);
const filename = computed(() =>
  props.modelValue ? "✓ " + props.modelValue.name : "",
);

function onChange() {
  emit("update:modelValue", input.value?.files?.[0] ?? null);
}

function onDrop(e: DragEvent) {
  dragOver.value = false;
  const file = e.dataTransfer?.files?.[0];
  if (!file) return;
  // Reflect the dropped file into the hidden <input> too, so its .files stays in sync.
  const dt = new DataTransfer();
  dt.items.add(file);
  if (input.value) input.value.files = dt.files;
  emit("update:modelValue", file);
}
</script>

<template>
  <div
    class="dropzone"
    :class="{ 'drag-over': dragOver }"
    @dragover.prevent="dragOver = true"
    @dragleave="dragOver = false"
    @drop.prevent="onDrop"
  >
    <input
      ref="input"
      type="file"
      :accept="accept"
      @change="onChange"
      @click.stop
    />
    <div class="dropzone-icon">⬆</div>
    <p class="dropzone-text"><slot /></p>
    <p v-if="filename" class="dropzone-filename">{{ filename }}</p>
  </div>
</template>

<style scoped>
.dropzone {
  border: 1px dashed var(--border);
  background: var(--bg);
  padding: 28px 20px;
  text-align: center;
  cursor: pointer;
  transition:
    border-color 0.2s,
    background 0.2s;
  position: relative;
}
.dropzone:hover,
.dropzone.drag-over {
  border-color: var(--amber);
  background: rgba(232, 147, 10, 0.03);
}
.dropzone input[type="file"] {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
  width: 100%;
  height: 100%;
}
.dropzone-icon {
  font-size: 22px;
  margin-bottom: 8px;
  color: var(--amber-dim);
}
.dropzone-text {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.5;
}
.dropzone-text :deep(strong) {
  color: var(--amber);
  font-weight: 500;
}
.dropzone-text :deep(code) {
  color: var(--text);
}
.dropzone-filename {
  margin-top: 8px;
  font-size: 12px;
  color: var(--green);
  font-weight: 500;
}
</style>
