import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: "frontend",
  envDir: "..",
  base: "/static/app/",
  plugins: [react()],
  build: {
    outDir: "../web/app",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/games": "http://127.0.0.1:8000",
      "/controller": "http://127.0.0.1:8000",
      "/brand-logo.png": "http://127.0.0.1:8000",
    },
  },
});
