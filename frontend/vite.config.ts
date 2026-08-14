import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The backend runs on 8010 by default (8000 is a common conflict).
// Override with VITE_API_TARGET in frontend/.env if you run it elsewhere.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_TARGET || 'http://localhost:8010'

  return {
    plugins: [react()],
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
