import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import boundaries from 'eslint-plugin-boundaries';
import importPlugin from 'eslint-plugin-import';
import jsxA11y from 'eslint-plugin-jsx-a11y';

export default tseslint.config(
  // Global ignores (replaces .eslintignore)
  {
    ignores: [
      'dist/',
      'build/',
      'node_modules/',
      // Files that need gradual migration to strict typing
      'src/pages/SettingsPage.tsx',
      'src/pages/ImportDetailPage.tsx',
      // Test files
      '**/*.test.ts',
      '**/*.test.tsx',
      '**/*.spec.ts',
      '**/*.spec.tsx',
    ],
  },

  // Base configs
  js.configs.recommended,
  ...tseslint.configs.recommended,

  // React hooks + refresh + jsx-a11y
  {
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      'jsx-a11y': jsxA11y,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-empty-object-type': 'warn',
      '@typescript-eslint/ban-ts-comment': [
        'error',
        {
          'ts-ignore': true,
          'ts-expect-error': 'allow-with-description',
          'ts-nocheck': true,
          'ts-check': false,
          minimumDescriptionLength: 8,
        },
      ],
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
      // Ban the @mui/icons-material barrel import — it pulls the entire 4 MB
      // icon catalog into the bundle. Components use per-icon subpath imports
      // (`import Foo from '@mui/icons-material/Foo'`); dynamic lookup by name
      // goes through `utils/iconRegistry.ts`. Type-only imports still resolve
      // to the barrel path because they have no runtime cost.
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: '@mui/icons-material',
              message:
                "Import specific icons via '@mui/icons-material/IconName' or add them to utils/iconRegistry.ts for dynamic lookup. The barrel import ships all 2000+ icons (~4 MB).",
              allowTypeImports: true,
            },
          ],
        },
      ],
      'react-hooks/exhaustive-deps': 'warn',
      // React Compiler rules (new in react-hooks v7) - warn for now, fix gradually
      'react-hooks/immutability': 'warn',
      'react-hooks/static-components': 'warn',
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/refs': 'warn',
      'react-hooks/preserve-manual-memoization': 'warn',
      'react-hooks/purity': 'warn',
      // Icon-only buttons must carry an accessible name. <IconButton> on its
      // own has no text content; a wrapping <Tooltip title="..."> renders
      // `title` (browser-native, not WCAG-conformant) and does not satisfy
      // assistive tech. control-has-associated-label catches the pattern.
      'jsx-a11y/control-has-associated-label': [
        'error',
        {
          controlComponents: ['IconButton'],
          ignoreElements: ['audio', 'canvas', 'embed', 'iframe', 'img', 'object', 'video'],
          ignoreRoles: [
            'grid', 'listbox', 'menu', 'menubar', 'radiogroup', 'row', 'tablist', 'toolbar', 'tree', 'treegrid',
          ],
          depth: 5,
        },
      ],
      'jsx-a11y/no-noninteractive-tabindex': 'warn',
    },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: {
          jsx: true,
        },
      },
    },
  },

  // ==========================================================================
  // Architectural boundaries for the public frontend tree
  // ==========================================================================
  // Four-layer hierarchy:
  //   L1 (top)     pages, App.tsx
  //   L2 (UI)      components, contexts, hooks    (no cross-L2 imports)
  //   L3 (data)    services
  //   L4 (leaves)  utils, types, theme, config, constants, test
  //
  // Rules:
  //   - Higher layers may import lower; lower may not import higher.
  //   - Within L2, components/contexts/hooks may not import each other
  //     (with one carve-out: contexts -> hooks for query-derived state).
  //   - Type-only imports cross any boundary freely (zero runtime cost).
  //   - Test files (`__tests__/`, `*.test.ts(x)`) bypass the rules.
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: [
      'src/**/__tests__/**',
      'src/**/*.test.{ts,tsx}',
      'src/test/**',
    ],
    plugins: {
      boundaries,
      import: importPlugin,
    },
    settings: {
      'boundaries/elements': [
        // Match files inside src/<dir>/, not the dir name itself.
        { type: 'page',      pattern: 'src/pages/*',      mode: 'folder' },
        { type: 'component', pattern: 'src/components/*', mode: 'folder' },
        { type: 'context',   pattern: 'src/contexts/*',   mode: 'folder' },
        { type: 'hook',      pattern: 'src/hooks/*',      mode: 'folder' },
        { type: 'service',   pattern: 'src/services/*',   mode: 'folder' },
        { type: 'util',      pattern: 'src/utils/*',      mode: 'folder' },
        { type: 'type',      pattern: 'src/types/*',      mode: 'folder' },
        { type: 'theme',     pattern: 'src/theme/*',      mode: 'folder' },
        { type: 'config',    pattern: 'src/config/*',     mode: 'folder' },
        { type: 'constant',  pattern: 'src/constants/*',  mode: 'folder' },
        { type: 'root',      pattern: 'src/*.{ts,tsx}',   mode: 'file'   },
      ],
      'boundaries/include': ['src/**/*.{ts,tsx}'],
      'boundaries/ignore': [
        'src/**/__tests__/**',
        'src/**/*.test.{ts,tsx}',
        'src/test/**',
      ],
      'boundaries/dependency-nodes': ['import', 'export'],
    },
    rules: {
      'boundaries/dependencies': [
        'error',
        {
          default: 'disallow',
          rules: [
            // L1 — pages and App.tsx may import from anything below.
            { from: ['page', 'root'], allow: ['page', 'component', 'context', 'hook', 'service', 'util', 'type', 'theme', 'config', 'constant'] },
            // L2 — components/contexts/hooks: only L3, L4. (See carve-out for contexts below.)
            { from: ['component'], allow: ['component', 'service', 'util', 'type', 'theme', 'config', 'constant'] },
            { from: ['hook'],      allow: ['hook',      'service', 'util', 'type', 'theme', 'config', 'constant'] },
            // contexts -> hooks: documented carve-out for query-derived
            // context state (e.g. DashboardContext composing useDashboardData).
            { from: ['context'],   allow: ['context', 'hook', 'service', 'util', 'type', 'theme', 'config', 'constant'] },
            // L3 — services may import only L4 (no UI).
            { from: ['service'],   allow: ['service', 'util', 'type', 'config', 'constant'] },
            // L4 — leaves import only other leaves.
            { from: ['util'],      allow: ['util', 'type', 'theme', 'config', 'constant'] },
            { from: ['type'],      allow: ['type', 'config', 'constant'] },
            { from: ['theme'],     allow: ['theme', 'type', 'config', 'constant'] },
            { from: ['config'],    allow: ['config', 'constant', 'type'] },
            { from: ['constant'],  allow: ['constant', 'type'] },
          ],
        },
      ],

      // Cycle detection — supplements `madge --circular` in CI.
      'import/no-cycle': ['error', { maxDepth: 10, ignoreExternal: true }],
      'import/no-self-import': 'error',
      'import/no-duplicates': 'error',
    },
  },

  // ==========================================================================
  // Targeted exceptions — documented patterns that bend a layering rule.
  // ==========================================================================
  {
    files: [
      // useBulkOperation returns a `<ProgressDialog />` JSX value (the
      // hook-renders-component pattern). Refactoring would touch every
      // caller in useCRUDPage; we accept the L2 cross-import here.
      'src/hooks/useBulkOperation.tsx',
    ],
    rules: {
      'boundaries/dependencies': 'off',
    },
  },
);
