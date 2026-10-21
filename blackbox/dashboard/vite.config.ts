import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * BlackBox API origin. The dashboard never talks to anything else:
 * every request goes to `<API_BASE>/api/...` where API_BASE is either the dev
 * proxy below (empty string in the client) or VITE_API_BASE for a direct hit.
 */
const API_TARGET = 'http://127.0.0.1:8099';
const DEV_PORT = 5273;

export default defineConfig({
  plugins: [react()],
  server: {
    port: DEV_PORT,
    strictPort: false,
    // Same-origin /api calls; the agent's FastAPI app runs on 8099.
    // SSE (/api/targets/{id}/stream) is proxied as a normal streaming response.
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: DEV_PORT,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    target: 'es2020',
  },
});
