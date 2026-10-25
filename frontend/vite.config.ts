import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const target = process.env.API_URL ?? "http://localhost:8000";
const basePath = (process.env.VITE_BASE_PATH ?? "").replace(/\/+$/, "");  // e.g. /assistant when served inside the main app
export default defineConfig({
  base: basePath ? `${basePath}/` : "/",
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": target, "/healthz": target } },
});
