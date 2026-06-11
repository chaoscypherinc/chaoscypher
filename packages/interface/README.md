# ChaosCypher Interface

**Web-based user interface - Interaction layer**

Interface provides the React-based web UI for interacting with ChaosCypher knowledge graphs, workflows, and AI-powered research capabilities.

## Features

- 📊 **Knowledge Graph Visualization**: Interactive node and relationship exploration
- 💬 **AI Chat Interface**: Conversational knowledge discovery with context
- 📁 **Document Processing**: Upload and process documents into knowledge graphs
- 🔄 **Workflow Management**: Create and execute custom research workflows
- 🔍 **Search & Discovery**: Semantic search across knowledge bases
- ⚙️ **Settings Management**: Configure LLM providers, databases, and system settings

## Architecture

Interface is part of the ChaosCypher neural architecture:

- **Core** - Brain (business logic)
- **Cortex** - Processing center (full backend)
- **Neuron** - Worker cells (background processing)
- **Interface** - Interaction layer (UI) 👈 You are here

## Technology Stack

- **Framework**: React 19 + TypeScript 6 (strict)
- **Build Tool**: Vite 8
- **Routing**: React Router 7
- **State Management**: TanStack Query (server/streaming state) + React Context (UI state only)
- **UI Components / Styling**: MUI 9 + Emotion (`sx` prop); no CSS Modules
- **Testing**: Vitest 4 + React Testing Library
- **API Client**: typed fetch wrapper (`src/services/api/client.ts`)

## Installation

### Standalone (Development)

```bash
cd packages/interface
npm install
npm run dev
```

Access at: http://localhost:3000

### With Cortex Backend

```bash
# Start Cortex backend first
cd packages/cortex
cc-cortex start

# Then start Interface
cd packages/interface
VITE_API_URL=http://localhost:8080 npm run dev
```

### Docker

```bash
docker run -p 3000:80 chaoscypher-interface
```

The production image serves the compiled bundle behind nginx, which proxies
`/api` to the backend (see `packages/docker/config/multi-interface-nginx.conf`);
`VITE_*` variables are compile-time only and have no effect at container runtime.

## Configuration

Configure via environment variables:

```bash
VITE_API_URL=http://localhost:8080    # Dev-server proxy target (Vite proxies /api here)
VITE_API_BASE=/api/v1                 # Client request prefix (default /api/v1)
```

`VITE_API_URL` only sets the Vite dev-server proxy target. The API client itself
reads `VITE_API_BASE` (default `/api/v1`) for its request prefix.

## Development

### Project Structure

```
packages/interface/
├── src/
│   ├── components/      # Reusable UI components
│   ├── pages/          # Route-level page components
│   ├── hooks/          # Custom React hooks
│   ├── services/       # API client services
│   ├── types/          # TypeScript type definitions
│   └── App.tsx         # Main application component
├── public/             # Static assets
└── package.json        # Node.js dependencies
```

### Available Scripts

```bash
npm run dev           # Start development server with hot reload
npm run build         # Build for production
npm run preview       # Preview production build
npm run lint          # Run ESLint
npx tsc --noEmit      # TypeScript type checking (also bundled into `npm run build`)
npm run size          # Check bundle-size budgets (size-limit, brotli)
npm run size:why      # Explain which modules contribute to a chunk
```

### Bundle-size budget

`packages/interface/.size-limit.json` enforces brotli'd byte budgets on
the production build. Run `make bundle-size` from the repo root (or
`npm run size` here after building) to check. Budgets and baseline
composition are documented at `packages/interface/.size-limit.json`. The
gate also runs as part of `make ci`.

### Adding New Features

1. **Create Component**: `src/components/FeatureName/`
2. **Add Page**: `src/pages/FeatureName.tsx`
3. **Add Route**: Update `src/App.tsx`
4. **Add API module + query hook**: `src/services/api/<feature>.ts` and `src/services/api/use<Feature>.ts`
5. **Add Types**: `src/types/feature.ts`

## API Integration

The Interface connects to the Cortex backend through per-feature API modules
(typed functions over `src/services/api/client.ts`, which prefixes every path
with `VITE_API_BASE`, default `/api/v1`) plus TanStack Query hooks:

```typescript
// src/services/api/workflows.ts — typed functions over client.ts
import { apiClient } from './client';

export const workflowsApi = {
  list: () =>
    apiClient.get<{ data: Workflow[] }>('/workflows').then((r) => r.data.data),

  execute: (workflowId: string, inputs: Record<string, unknown>) =>
    apiClient
      .post<ExecuteWorkflowResponse>(`/workflows/${workflowId}/executions`, { inputs })
      .then((r) => r.data),
};
```

Components never call the API module directly — they consume the TanStack
Query hooks that wrap it (`useWorkflows`, `useExecuteWorkflow` in
`src/services/api/useWorkflows.ts`; `useWorkflowExecutions` in
`src/services/api/useWorkflowExecutions.ts`).

## Production Build

```bash
# Build optimized production bundle
npm run build

# Output: dist/ directory with static assets
# Serve with any static file server or integrate with Cortex
```

The production build can be:
1. Served by Cortex's static file serving
2. Deployed to CDN (Netlify, Vercel, etc.)
3. Served by Nginx/Apache
4. Containerized with Docker

## Environment Variables

Create `.env.local` for local development:

```bash
VITE_API_URL=http://localhost:8080    # Dev-server proxy target
VITE_API_BASE=/api/v1                 # Client request prefix (default /api/v1)
```

## Testing

```bash
# Run tests
npm test

# Interactive UI
npm run test:ui

# With coverage
npm run test:coverage
```

## Contributing

See main project `CONTRIBUTING.md` for development guidelines.

## License

AGPL-3.0 License - See LICENSE file for details
