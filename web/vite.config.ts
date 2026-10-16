import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

import { authenticatedProxy } from "./scripts/dev-proxy";

export default defineConfig(() => {
  const target = process.env.API_PROXY ?? "http://127.0.0.1:5099";
  return {
    plugins: [react(), tailwindcss(), authenticatedProxy(target)],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    build: {
      outDir: "../src/agent_factory/dashboard_dist",
      emptyOutDir: true,
    },
    server: {
      host: "127.0.0.1",
      cors: false,
      port: 5173,
      proxy: { "/api": { target, changeOrigin: true } },
    },
  };
});
