import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { apiFetch, parseErr } from "../api/client";
import { ROLE_PERMISSIONS } from "../constants";
import type { Identity, Permission, Role, User } from "../types";

/**
 * Identity + role state for RBAC. Hydrated once from GET /api/v1/me, which resolves
 * the caller's identity from the ALB/OIDC-injected header (or a dev fallback). The
 * `can()` / `hasRole()` getters drive route guards and role-gated UI.
 *
 * NOTE: this is UX gating only. The backend must enforce the same permissions on
 * the API routes (RBAC follow-up phase); never treat client checks as security.
 */
export const useAuthStore = defineStore("auth", () => {
  const user = ref<User | null>(null);
  const roles = ref<Role[]>([]);
  const loaded = ref(false);
  const loading = ref(false);
  const error = ref<string | null>(null);

  const isAuthenticated = computed(() => user.value !== null);

  function hasRole(role: Role): boolean {
    return roles.value.includes(role);
  }

  function can(permission: Permission): boolean {
    return roles.value.some((r) => ROLE_PERMISSIONS[r]?.includes(permission));
  }

  /** Load identity once. Safe to call repeatedly (no-op after first success). */
  async function loadIdentity(force = false): Promise<void> {
    if (loaded.value && !force) return;
    loading.value = true;
    error.value = null;
    try {
      const resp = await apiFetch("/api/v1/me");
      if (!resp.ok) throw new Error(await parseErr(resp));
      const data: Identity = await resp.json();
      user.value = data.user ?? null;
      roles.value = data.roles ?? [];
      loaded.value = true;
    } catch (e) {
      user.value = null;
      roles.value = [];
      loaded.value = true; // resolved (as unauthenticated) so guards can proceed
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      loading.value = false;
    }
  }

  return {
    user,
    roles,
    loaded,
    loading,
    error,
    isAuthenticated,
    hasRole,
    can,
    loadIdentity,
  };
});
