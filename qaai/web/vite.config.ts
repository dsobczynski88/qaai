import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// base: "./" makes every built asset URL RELATIVE to index.html, so the SPA works
// unchanged under any proxy prefix (local "/", JupyterHub "/user/x/proxy/8000/",
// or an AWS ALB path) without hardcoding the base. Runtime /api calls are prefixed
// separately via detectRootPath() in src/api/client.ts.
export default defineConfig({
  base: "./",
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // In `npm run dev`, proxy backend paths to the FastAPI server so the Vue HMR
    // dev server and the API run side by side. Production serves the built dist/
    // from FastAPI itself, so this proxy is dev-only.
    proxy: {
      "/api": "http://localhost:8000",
      "/guide": "http://localhost:8000",
      "/docs": "http://localhost:8000",
      "/redoc": "http://localhost:8000",
    },
  },
});
