import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";
import { useAuthStore } from "../stores/auth";
import type { Role } from "../types";
import ReviewHome from "../views/ReviewHome.vue";
import Unauthorized from "../views/Unauthorized.vue";

// Hash-mode routing keeps every route in the URL fragment, so it never hits the
// server or the JupyterHub/ALB proxy — no SPA fallback config is needed and deep
// links work under any proxy prefix.
const routes: RouteRecordRaw[] = [
  {
    path: "/",
    name: "home",
    component: ReviewHome,
    meta: { requiresAuth: true },
  },
  {
    path: "/unauthorized",
    name: "unauthorized",
    component: Unauthorized,
  },
  { path: "/:pathMatch(.*)*", redirect: { name: "home" } },
];

const router = createRouter({
  history: createWebHashHistory(),
  routes,
});

// Ensure identity is loaded, then enforce auth + optional per-route role gating.
// This is UX gating; the backend must enforce the same on the API (follow-up phase).
router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (!auth.loaded) await auth.loadIdentity();

  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: "unauthorized" };
  }
  const roles = to.meta.roles as Role[] | undefined;
  if (roles && !roles.some((r) => auth.hasRole(r))) {
    return { name: "unauthorized" };
  }
  return true;
});

export default router;
