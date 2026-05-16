import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// SPA served at root by nginx; backend (REST + Socket.IO) is proxied by nginx in
// the container. In `npm run dev` we proxy to a locally running backend so the
// future runtime wiring phase has a working seam.
export default defineConfig({
  base: "/",
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // NOT "assets" — that path is a backend API prefix (/assets/<id>/state).
    // Keeping SPA bundles under /spa-assets/ lets nginx cleanly split static
    // files from proxied API routes.
    assetsDir: "spa-assets",
  },
  server: {
    port: 5173,
    proxy: {
      "/socket.io": { target: "http://localhost:5000", ws: true },
      "/config": "http://localhost:5000",
      "/health": "http://localhost:5000",
    },
  },
});
