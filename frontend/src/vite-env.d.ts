/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute origin of the API when it is not same-origin. Empty in dev. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
