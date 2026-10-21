import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development (`npm run dev`, http://localhost:5173) Vite forwards API and WebSocket
// requests to FastAPI on :8000. In production FastAPI serves the built `dist/` itself.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
