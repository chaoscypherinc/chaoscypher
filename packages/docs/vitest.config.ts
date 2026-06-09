import { defineConfig } from 'vitest/config';

export default defineConfig({
  esbuild: {
    // Avoid the Docusaurus tsconfig.json extends resolution issue in vitest/esbuild
    tsconfigRaw: '{ "compilerOptions": { "target": "ES2020", "module": "ESNext", "moduleResolution": "bundler", "strict": true } }',
  },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
