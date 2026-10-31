import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { execSync } from "child_process";

function resolveGitSha(): string {
  if (process.env.VITE_GIT_SHA) return process.env.VITE_GIT_SHA.trim();
  try {
    return execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
  } catch {
    return "";
  }
}

export default defineConfig({
  plugins: [react()],
  define: {
    // Bake the short SHA into the status bar so a deploy can be verified in-browser.
    "import.meta.env.VITE_GIT_SHA": JSON.stringify(resolveGitSha()),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        // Docker: BACKEND_URL=http://backend:8000  |  Local dev: http://127.0.0.1:8000
        target: process.env.BACKEND_URL ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/health": {
        target: process.env.BACKEND_URL ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
