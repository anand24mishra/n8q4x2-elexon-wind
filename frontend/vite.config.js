import { defineConfig } from 'vite'

export default defineConfig({
  server: { proxy: { '/api': process.env.BACKEND_URL || 'http://localhost:8000' } },
})
