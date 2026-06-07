import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',  // Listen on all interfaces (required for Docker)
    port: 3000,
    watch: {
      usePolling: true,  // Required for Docker volume mounts on some systems
      interval: 10000,   // Check for file changes every 10 seconds (reduced from default ~100ms)
      binaryInterval: 10000,  // Check binary files every 10 seconds
      ignored: [
        '**/node_modules/**',
        '**/.git/**',
        '**/dist/**',
        '**/build/**',
        '**/.vite/**',
        '**/.cache/**',
        '**/coverage/**',
        '**/*.log',
        '**/.DS_Store',
        '**/tmp/**',
        '**/temp/**'
      ]
    },
    proxy: {
      '/api': {
        target: process.env.VITE_API_URL || 'http://localhost:8080',  // Use localhost for dev, backend for Docker
        changeOrigin: true,
      }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // SourcePage legitimately ships ~540 KB of markdown/syntax-highlighter
    // /MUI code; further splitting is not worth the complexity. Raise the
    // warning, don't ignore — anything above 800 KB is a smell.
    chunkSizeWarningLimit: 800,
    // `debugger` statements are stripped by default (oxc `dropDebugger`
    // defaults to true). `manualPureFunctions` lets tree-shaking eliminate
    // calls to the listed console methods as pure (dead code).
    // `console.error` and `console.info` are intentionally preserved.
    // `manualChunks` collapses the 100+ tiny per-icon MUI imports into one
    // chunk so the browser makes one request instead of one per icon.
    rolldownOptions: {
      treeshake: process.env.NODE_ENV === 'production' ? {
        manualPureFunctions: ['console.log', 'console.debug', 'console.warn'],
      } : undefined,
      output: {
        manualChunks: (id: string) => {
          if (id.includes('@mui/icons-material/')) {
            return 'mui-icons';
          }
          return undefined;
        },
      },
    },
  },
})
