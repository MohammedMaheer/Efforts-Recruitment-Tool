import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(path.dirname(fileURLToPath(import.meta.url)), './src'),
    },
  },
  build: {
    target: 'es2020',
    sourcemap: 'hidden',
    chunkSizeWarningLimit: 400,
    cssCodeSplit: true,
    rollupOptions: {
      output: {
        manualChunks: {
          // Core React — loaded on every page
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],
          // UI primitives
          'vendor-ui': ['@radix-ui/react-dialog', '@radix-ui/react-dropdown-menu', '@radix-ui/react-tabs', '@radix-ui/react-select', '@radix-ui/react-progress', '@radix-ui/react-slider'],
          // Charts — only Dashboard
          'vendor-charts': ['recharts'],
          // Utilities
          'vendor-utils': ['date-fns', 'clsx', 'tailwind-merge', 'zustand'],
          // Animation library (~130KB) — used across many pages
          'vendor-motion': ['framer-motion'],
          // Icons — used everywhere
          'vendor-icons': ['lucide-react'],
          // PDF generation — heavy (~330KB), only on-demand
          'vendor-pdf': ['jspdf', 'pdf-lib'],
          // Sanitization
          'vendor-sanitize': ['dompurify'],
        },
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/version': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
