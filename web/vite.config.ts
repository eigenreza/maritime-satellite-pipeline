import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  optimizeDeps: { exclude: ['maplibre-gl'] },
  build: {
    chunkSizeWarningLimit: 1500,
    sourcemap: false,
  },
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
})
