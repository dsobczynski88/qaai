<script setup lang="ts">
defineProps<{ id: string; title: string; badge: string; active: boolean }>();
const emit = defineEmits<{ (e: "select"): void }>();

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    emit("select");
  }
}
</script>

<template>
  <div class="card" :class="{ active }" :id="`card-${id}`">
    <div
      class="card-header"
      role="button"
      tabindex="0"
      :aria-expanded="active"
      @click="emit('select')"
      @keydown="onKeydown"
    >
      <div class="radio-dot"></div>
      <span class="card-title">{{ title }}</span>
      <span class="card-badge">{{ badge }}</span>
    </div>
    <div v-show="active" class="form-panel">
      <div class="panel-rule"></div>
      <slot />
    </div>
  </div>
</template>

<style scoped>
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  cursor: pointer;
  transition:
    border-color 0.2s,
    background 0.2s;
  overflow: hidden;
}
.card:hover {
  border-color: var(--amber-dim);
}
.card.active {
  border-color: var(--amber);
  background: #0f1520;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  user-select: none;
}
.card-header:focus-visible {
  outline: 2px solid var(--amber);
  outline-offset: -2px;
}

.radio-dot {
  width: 16px;
  height: 16px;
  border: 1.5px solid var(--muted);
  border-radius: 50%;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: border-color 0.2s;
}
.card.active .radio-dot {
  border-color: var(--amber);
}
.radio-dot::after {
  content: "";
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--amber);
  opacity: 0;
  transform: scale(0);
  transition:
    opacity 0.2s,
    transform 0.2s;
}
.card.active .radio-dot::after {
  opacity: 1;
  transform: scale(1);
}

.card-title {
  font-family: var(--display);
  font-size: 15px;
  font-weight: 600;
  color: #c8d0dc;
  transition: color 0.2s;
}
.card.active .card-title {
  color: #eef1f5;
}

.card-badge {
  margin-left: auto;
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
  border: 1px solid var(--border);
  padding: 2px 7px;
  transition:
    color 0.2s,
    border-color 0.2s;
}
.card.active .card-badge {
  color: var(--amber);
  border-color: var(--amber-dim);
}

.form-panel {
  padding: 0 20px 20px;
  animation: slideDown 0.25s ease both;
}
.panel-rule {
  height: 1px;
  background: var(--border);
  margin-bottom: 20px;
}
</style>
