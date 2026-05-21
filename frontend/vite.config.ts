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
    // NOT "assets" - that path is a backend API prefix (/assets/<id>/state).
    // Keeping SPA bundles under /spa-assets/ lets nginx cleanly split static
    // files from proxied API routes.
    assetsDir: "spa-assets",
  },
  server: {
    port: 5173,
    // nginx (prod) proxies every non-SPA path to Flask. Dev must mirror
    // that or REST calls hit the vite server and 404 - the symptom was
    // "/deploy/apply unreachable" while the socket-only "live" chip stayed
    // green (only /config + /socket.io were proxied). Forward every backend
    // route prefix to the local backend.
    proxy: Object.fromEntries(
      [
        "/socket.io",
        "/config",
        "/health",
        "/deploy",
        "/arms",
        "/assets",
        "/runtime",
        "/state",
        "/objects",
        "/object_event",
        "/set_angle",
        "/restart_pipeline",
        "/reload_config",
        "/pipeline",
        "/topology",
        "/execute_task",
        "/scripts",
        "/actor_scripts",
        "/run_script",
        "/demo",
        "/log",
      ].map((p) => [
        p,
        p === "/socket.io"
          ? { target: "http://localhost:5000", ws: true }
          : { target: "http://localhost:5000", changeOrigin: true },
      ]),
    ),
  },
});
