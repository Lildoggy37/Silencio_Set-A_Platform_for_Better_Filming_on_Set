import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000' } },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    clearMocks: true,
    // These multi-step DOM journeys are state/permission checks, not latency
    // benchmarks. Keep each query's 5s wait, allow 10s for the whole journey,
    // and bound jsdom workers to avoid oversubscribing local development PCs.
    testTimeout: 10_000,
    maxWorkers: 2,
  },
})
