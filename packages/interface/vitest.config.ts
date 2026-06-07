import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    coverage: {
      // v8 provider: faster than istanbul, no babel transform required.
      // Run via `npm run test:coverage` or `make test-cov-interface`.
      // NOT wired into pre-commit — coverage instrumentation roughly doubles
      // test runtime and would make every commit painful. CI / Makefile only.
      provider: 'v8',
      reporter: ['text', 'json', 'html', 'lcov'],
      reportsDirectory: './coverage',
      // `all: true` + `include` forces every source file under src/ into the
      // denominator, not just files imported by a test. Without this, an
      // orphan file (no test, no import path leading to it) shows up as 0/0
      // and is silently excluded — which would let untested new code slip
      // past the ratchet. Cost is ~10-15% slower coverage run.
      all: true,
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'node_modules/',
        'src/test/',
        '**/*.d.ts',
        '**/*.config.*',
        '**/mockData',
        'dist/',
        // Auto-generated from OpenAPI via `npm run generate-types`.
        // Excluded from both denominator and threshold.
        'src/types/generated/**'
      ],
      // Ratchet thresholds: set to today's measured coverage minus a small
      // safety margin (~2 pp) so noise doesn't break the build. Bump these
      // upward as coverage improves; never let them slip.
      // Baseline captured 2026-05-19 (278 tests passing, all:true enabled):
      //   Statements 22.43% | Branches 16.42% | Functions 17.65% | Lines 23.49%
      // Ratcheted 2026-05-19 after SettingsPage component-test batch
      // (ModelConfig + ProviderList + OllamaModelSelector + VRAMPresets):
      //   Statements 23.79% | Branches 18.96% | Functions 20.07% | Lines 24.86%
      // Ratcheted 2026-05-25 after settings component-test batch 2
      // (ModelOptionItem + InstanceManager + LogPane + EmbeddingProviderConfig
      //  + ProviderSelector + EmbeddingModelSelector; +188 tests):
      //   Statements 25.5% | Branches 21.74% | Functions 22.36% | Lines 26.58%
      // Ratcheted 2026-05-25 after pure-logic batch 3 (serialization +
      //  layoutUtils + transformers + useGraphReducers + useOllamaModels +
      //  useLogViewer; +216 tests):
      //   Statements 31.34% | Branches 25.67% | Functions 25.34% | Lines 32.48%
      // Ratcheted 2026-05-25 after GraphCanvas/Workflow hooks batch 4
      //  (useTestExecution + useSourceGroups + useNodeEdgeManager +
      //   useLayoutManager + useSigmaEvents + useContextUtilization; +193 tests):
      //   Statements 34.26% | Branches 27.26% | Functions 27.21% | Lines 35.45%
      // Ratcheted 2026-05-25 after hooks/utils batch 5 (useGraphData +
      //  handleStreamEvent + useWorkflowSerialization + useProviderSettings +
      //  parseLLMContent + progressCalculation; +318 tests):
      //   Statements 39.3% | Branches 31.43% | Functions 29.83% | Lines 40.37%
      // Ratcheted 2026-05-25 after services/hooks/formatters batch 6
      //  (formatters + useSourcesUpload + useCanvasInteractions +
      //   sourceProcessing + useToolSchemas + data; +274 tests):
      //   Statements 42.98% | Branches 34.5% | Functions 32.84% | Lines 43.88%
      // Ratcheted 2026-05-25 after components/pages batch 7 (TagManager +
      //  InlineTagEditor + NodePropertiesForm + ApiKeysSettings +
      //  AccountSettings + BackupTab; +153 tests):
      //   Statements 46.23% | Branches 37.03% | Functions 36.32% | Lines 47.3%
      // Ratcheted 2026-05-25 after Omnibar/chat/forms batch 8 (Omnibar +
      //  SearchMode + StateZero + ChatMarkdown + DynamicFormRenderer +
      //  TemplateSelectionModal; +194 tests) — CROSSED THE ≥50% LINES GOAL:
      //   Statements 49.6% | Branches 40.36% | Functions 39.75% | Lines 50.72%
      thresholds: {
        lines: 48,
        statements: 47,
        functions: 37,
        branches: 38
      }
    }
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  }
});
