import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "../src/agent_factory/dashboard_dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: { "/api": process.env.API_PROXY ?? "http://127.0.0.1:5099" },
  },
});
