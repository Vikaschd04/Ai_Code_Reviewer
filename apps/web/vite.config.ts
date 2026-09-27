import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The browser talks only to this origin; /v1 is proxied to the loopback API so the session
// cookie stays same-origin and no CORS policy is needed. The API origin comes from crp-dev.
const apiOrigin = process.env.CRP_API_ORIGIN ?? "http://127.0.0.1:8710";
const proxy = { "/v1": { target: apiOrigin, changeOrigin: true } };

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy },
  build: { sourcemap: true },
});
