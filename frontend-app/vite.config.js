import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: '../frontend-dist',
    emptyOutDir: true,
    rollupOptions: { input: {
      app: fileURLToPath(new URL('index.html', import.meta.url)),
      render: fileURLToPath(new URL('render.html', import.meta.url)),
    } },
  },
})
