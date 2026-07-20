# Interface — Frontend Guidance

Developer guidance for the React/TypeScript frontend (`packages/interface/`). Root rules in `../../CLAUDE.md` still apply; this doc adds frontend-specific rules.

## Stack

- React 19.2, TypeScript 6 (`strict: true`), Vite 8
- MUI 9 + Emotion (MUI's default styling engine)
- React Router 7
- `@tanstack/react-query` — **server state**
- Context API — **UI state only**
- Vitest 4 + React Testing Library — tests colocated at `src/**/__tests__/*.test.tsx`

## Directory roles

- `components/` — shared, reusable UI
- `pages/` — route-level containers (one default-exported component per file)
- `hooks/` — custom React hooks (camelCase filename, `useX` export)
- `contexts/` — React Context providers (UI state)
- `services/` — API client + TanStack Query hooks
- `services/api/` — raw fetch wrappers per feature
- `types/` — shared TypeScript types (`types/generated/api.ts` from OpenAPI)
- `utils/` — pure helpers
- `theme/` — MUI theme config
- `test/` — test setup (`setup.ts`)

## State management

- **Server state** (anything fetched from `/api/*`) → **TanStack Query** (`useQuery` / `useMutation`). Query client configured in `src/services/queryClient.ts`.
- **Streaming / realtime state** (SSE token streams, polled background jobs) → **also the Query cache**, written via `queryClient.setQueryData(...)`. A query holds the accumulating value; the stream appends partials into that same cache entry (clone, never mutate). The chat stack is the reference implementation: `src/services/api/useChats.ts` owns the `['chats']` / `['chat', id]` cache + a `ChatCacheWriter`, and `src/pages/chat/hooks/useChatStream.ts` writes streamed events into it. Do **not** force a stream into a single `useQuery`, and do **not** hand-share `useState` setters across hooks to thread streamed data around.
- **UI state** (auth user, theme, notifications, dialog open/close) and genuine **stream-control / editor state** (input text, `loading`, `isStreamingActive`, a pending-approval prompt) → Context API or local `useState`.
- **Never** use raw `fetch + useState + useEffect` for server state in new code.

## API client

- Base client: `src/services/api/client.ts`. Wraps native `fetch`; normalizes errors.
- Base URL from `VITE_API_BASE` env var (default `/api/v1`).
- Per-feature service modules: `src/services/api/<feature>.ts` — typed Promise-returning functions.
- **Never `fetch()` from a component.** Always go through a service module.
- **Query hooks** (TanStack Query wrappers) live alongside service modules. New code uses the hook.

## Routing

- React Router v7. Routes defined in `App.tsx`.
- Auth gate via `<AuthGuard>` component. Layout via `<LayoutWrapper>`.
- Pages lazy-loaded via `React.lazy`.

## Styling

- **MUI + `sx` prop** for per-component styles. No CSS files for components.
- Theme at `src/theme/palette.ts`, component overrides at `src/theme/componentOverrides.ts`.
- **Dark-first** — `dark_mode ?? true` default in `App.tsx`.
- Accent components at `src/components/` (e.g., `AccentSection`, `accentSelectSx`).

## Components

- **Default-exported pages** (`export default function DashboardPage()`).
- **Named-exported shared components** (`export function NodeFormDialog()`).
- **Props typed via `interface`** (e.g., `interface NodeFormDialogProps { ... }`). Not `type`.
- **Plain function components with typed props preferred** over `React.FC<Props>`. Existing `React.FC` usage is acceptable; don't force migrations.

## Forms

- Hand-rolled `useState` is current practice. No form library.
- If form complexity grows, propose `react-hook-form` via a plan before adopting.

## Error handling

- Global `ErrorBoundary` at `src/components/ErrorBoundary.tsx`, wired in `App.tsx` via `<LayoutWrapper>`.
- API errors normalized to `{ message, code, details }` shape by `client.ts`.
- Surface errors via `NotificationContext` (toasts) or inline per component.

## Testing

- **Framework:** Vitest + React Testing Library + jsdom.
- **Run:** `npm run test` / `npm run test:ui` / `npm run test:coverage`.
- **Mocking:** mock network via stubbed service modules or `msw` (when added). Do not mock React components you own.

## Build + dev

- **Dev:** `npm run dev` (port 3000; proxies `/api` to `VITE_API_URL` default `http://localhost:8080`).
- **Build:** `npm run build` — outputs to `dist/`.
- **Type check:** `tsc && vite build` (bundled into `build` script).
- **Lint:** `npm run lint`.
- **Deadcode scan:** `npm run deadcode` (knip).
- **OpenAPI → types:** `npm run generate-types`.

## TypeScript rules

- `strict: true` with `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch`.
- **Never `@ts-ignore`**; use `// @ts-expect-error: <reason>` if truly unavoidable.
- **Never `any`** in new code (ESLint `@typescript-eslint/no-explicit-any: warn`). If forced, annotate with a `// reason:` comment.
- Props `interface` over `type` for consistency.

## Accessibility (baseline)

- Keyboard-navigable dialogs and menus (MUI provides by default — don't break it).
- `aria-label` on icon-only buttons.
- Color contrast ≥ 4.5:1 for body text (MUI palette mostly compliant; check accent components).

## Anti-patterns

❌ **Raw `fetch + useState + useEffect` in a page** — use TanStack Query.
❌ **API URLs hardcoded** — use `VITE_API_BASE`.
❌ **Data fetching in a component** — use a service + hook.
❌ **Component-local color values** — use the MUI theme.
❌ **Importing from `chaoscypher_cortex` Python paths** — wrong language; use `types/generated/api.ts` for types.
❌ **Console logging in production paths** — use a notification or error boundary.

## Tech debt (from Plan A)

- TanStack Query migration COMPLETE (2026-05-25 campaign): all pages are on TanStack Query — no remaining raw `fetch + useState` server-state pages. The legacy `services/api.ts` re-export barrel was retired in favor of direct `./api/*` module imports.
- Two files in `eslint.config.js:17-18` have lint ignores for gradual strict migration — `SettingsPage.tsx`, `ImportDetailPage.tsx`.

## Source detail page anatomy

`pages/SourcePage/` renders the Source detail view. The 2026-05-07 import-pipeline remediation introduced three first-class areas you must update when changing the source contract:

| Area | Component | Backed by | What to update when |
|---|---|---|---|
| Upload-time choices | `pages/SourcePage/components/header/FileInfoTooltip.tsx` | `SourceResponse.upload_options` | Any new persisted upload setting. |
| Per-stage drop / merge counters | `pages/SourcePage/components/pipeline/stageStats.ts` (`buildStageStats`, rendered by `StageStatsBoard` aligned under the funnel inside the Overview tab's collapsible **Pipeline Flow** section, `OverviewTab/sections/PipelineFlowSection.tsx`) | `SourceResponse.quality_metrics` | Any new `QualityCounter` exposed by the backend. Add it to the matching stage's column in `buildStageStats` (non-zero counters only). |
| Search-index health | `components/sources/mergedChipState.ts` + `pages/SourcePage/components/pipeline/stageStats.ts` (commit column) | `SourceResponse.quality_metrics.vector_indexing_status` | New search-index lifecycle states. |
| Vision per-page status + retry | `pages/SourcePage/components/pipeline/details/VisionPagesGrid.tsx` (rendered inside the **Chunk Overview band**, `chunks/ChunkOverviewBand.tsx`, on the Chunks tab when a vision job exists) | `useVisionPages` + `useSourceImages` | New vision-page status, per-page retry semantics, image lightbox behaviour. |
| Per-chunk text inspection | `pages/SourcePage/components/ChunksTab.tsx` (Separate mode) + `chunks/Chunk{Input,Output}View.tsx` | `SourceChunk.content` (list) + `SourceChunk.raw_content` (detail) + extracted entities + `ExtractionTask.filtering_log` | Any new per-chunk stored text variant or any new filter stage. Loaders must populate `raw_content` — document the loader contract in public docs if behavior changes. |

**Rule:** any new persisted upload setting must show up in `FileInfoTooltip`'s "Upload settings" block — the row → API → UI round-trip is the API contract (keep request/response shape documented and tested). Adding it to the row + API model without surfacing it in the UI is incomplete. (Until 2026-05-11 this surface lived in a standalone `UploadSettingsSection` glass panel under the stat tiles; it was folded into the title tooltip to declutter the Overview tab.)

**Rule:** any new `QualityCounter` on the backend must surface in the matching stage's column in `buildStageStats` (`pipeline/stageStats.ts`, rendered by `StageStatsBoard` under the funnel). The funnel's purpose is to make every silent-drop site visible per source rather than buried in logs — leaving a counter unrendered defeats it. (As of the 2026-05-26 restructure the standalone Processing tab is gone: the funnel + stage board live in the Overview tab's collapsible **Pipeline Flow** section, and the heavy per-chunk extraction detail + vision page grid render in the **Chunk Overview band** at the top of the Chunks tab. The leaf components still live under `components/pipeline/` — the directory was renamed from `ProcessingTab/` in the same restructure.)
