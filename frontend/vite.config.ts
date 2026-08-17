import { defineConfig, loadEnv, type Connect, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// Standalone pages under public/, reachable at a clean path.
const STATIC_PAGES: Record<string, string> = {
  '/demo': '/demo/index.html',
}

/**
 * Serve the standalone pages at their clean paths.
 *
 * Vite's SPA fallback answers any extensionless request with the app's
 * index.html, so `/demo` would boot React instead of serving the page sitting in
 * public/demo/. Rewriting before the fallback runs is all it takes; a static host
 * resolves the same directory index on its own.
 */
function staticPages(): Plugin {
  const rewrite: Connect.NextHandleFunction = (req, _res, next) => {
    const path = (req.url || '').split('?')[0].replace(/\/$/, '')
    const target = STATIC_PAGES[path]
    if (target) req.url = target
    next()
  }

  return {
    name: 'static-pages',
    configureServer: (server) => {
      server.middlewares.use(rewrite)
    },
    configurePreviewServer: (server) => {
      server.middlewares.use(rewrite)
    },
  }
}

// The backend runs on 8010 by default (8000 is a common conflict).
// Override with VITE_API_TARGET in frontend/.env if you run it elsewhere.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_TARGET || 'http://localhost:8010'

  return {
    plugins: [react(), staticPages()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
