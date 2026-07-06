<script setup lang="ts">
import { onMounted } from "vue";
import { useAuthStore } from "./stores/auth";
import AppHeader from "./components/AppHeader.vue";
import AppFooter from "./components/AppFooter.vue";

// Kick off identity load early (the router guard also awaits it).
const auth = useAuthStore();
onMounted(() => auth.loadIdentity());
</script>

<template>
  <AppHeader />
  <router-view />
  <AppFooter />
</template>
