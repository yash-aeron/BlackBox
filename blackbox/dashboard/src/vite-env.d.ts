/// <reference types="vite/client" />

/**
 * Environment surface this app reads. `VITE_API_BASE` lets the dashboard talk
 * to an API on another origin; when it is unset the app is served through the
 * Vite dev proxy and uses same-origin `/api`.
 */
interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
