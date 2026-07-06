<script setup lang="ts">
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
</script>

<template>
  <header class="header">
    <div class="logo-row">
      <div class="logo-tag">v1.0 · Medical Device DHF</div>
      <div v-if="auth.isAuthenticated" class="identity" aria-label="Signed-in user and roles">
        <span class="identity-name">{{ auth.user?.name }}</span>
        <span v-for="r in auth.roles" :key="r" class="role-badge">{{ r }}</span>
      </div>
    </div>
    <h1>QAAI</h1>
    <p class="subtitle">
      AI-Assisted Reviewer for Quality Assurance &nbsp;·&nbsp; IEC 62304 / ISO 14971
    </p>
    <div class="divider"></div>
  </header>
</template>

<style scoped>
.header {
  width: 100%;
  max-width: 640px;
  margin-bottom: 56px;
  animation: fadeUp 0.6s ease both;
}

.logo-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 6px;
}

.logo-tag {
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 500;
  color: var(--amber);
  letter-spacing: 0.18em;
  text-transform: uppercase;
  border: 1px solid var(--amber-dim);
  padding: 3px 8px;
}

.identity {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
}

.identity-name {
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.05em;
}

.role-badge {
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--amber);
  border: 1px solid var(--amber-dim);
  padding: 2px 6px;
}

h1 {
  font-family: var(--display);
  font-size: clamp(28px, 5vw, 42px);
  font-weight: 800;
  color: #eef1f5;
  letter-spacing: -0.02em;
  line-height: 1.15;
  animation: shimmer 3s ease 0.4s both;
}

.subtitle {
  margin-top: 12px;
  font-family: var(--mono);
  font-size: 13px;
  color: var(--muted);
  letter-spacing: 0.02em;
}

.divider {
  margin-top: 24px;
  height: 1px;
  background: linear-gradient(90deg, var(--amber) 0%, var(--border) 60%);
}
</style>
