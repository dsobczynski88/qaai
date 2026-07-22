<script setup lang="ts">
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
</script>

<template>
  <section class="unauthorized">
    <div class="lock" aria-hidden="true">⛔</div>
    <h2>Access denied</h2>
    <p v-if="auth.error">
      We could not verify your identity. Please sign in through your organization's
      portal and reload.
    </p>
    <p v-else>
      Your account is authenticated but does not have a role permitted to use this
      reviewer. Contact an administrator to be granted the User or Admin role.
    </p>
    <p class="detail">
      <span v-if="auth.user">Signed in as {{ auth.user.name }}.</span>
      Roles: {{ auth.roles.length ? auth.roles.join(", ") : "none" }}.
    </p>
  </section>
</template>

<style scoped>
.unauthorized {
  width: 100%;
  max-width: 640px;
  border: 1px solid var(--border);
  background: var(--surface);
  padding: 40px 32px;
  text-align: center;
  animation: fadeUp 0.5s ease both;
}
.lock {
  font-size: 34px;
  margin-bottom: 12px;
}
h2 {
  font-family: var(--display);
  font-size: 20px;
  font-weight: 700;
  color: #eef1f5;
  margin-bottom: 12px;
}
p {
  font-size: 13px;
  color: var(--muted);
  line-height: 1.6;
  max-width: 460px;
  margin: 0 auto;
}
.detail {
  margin-top: 16px;
  font-size: 11px;
  color: var(--amber-dim);
}
</style>
