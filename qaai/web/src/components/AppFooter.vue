<script setup lang="ts">
import { ROOT_PATH } from "../api/client";
import FeedbackUpload from "./FeedbackUpload.vue";

// Footer links are root-path-aware (work locally and behind the JupyterHub proxy).
const guideHref = ROOT_PATH + "/guide/";
const swaggerHref = ROOT_PATH + "/docs";
</script>

<template>
  <footer class="footer">
    <FeedbackUpload />
    QAAI · Anthropic LangGraph · FastAPI &nbsp;·&nbsp;
    <a
      :href="guideHref"
      target="_blank"
      rel="noopener noreferrer"
      class="footer-link"
      >Documentation</a
    >
    &nbsp;·&nbsp;
    <a :href="swaggerHref" class="footer-link">Swagger</a>
  </footer>
</template>

<style scoped>
.footer {
  margin-top: auto;
  padding-top: 48px;
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.08em;
  text-align: center;
}

.footer-link {
  color: var(--amber-dim);
  text-decoration: none;
}
.footer-link:hover {
  text-decoration: underline;
}
</style>
