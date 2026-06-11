# ChaosCypher Cortex

**Full-featured knowledge graph backend API - Processing center**

Cortex is the main backend API for ChaosCypher, providing comprehensive CRUD operations, workflow execution, document source processing, AI-powered chat, and knowledge graph management using Vertical Slice Architecture (VSA).

## Features

- 📊 **Knowledge Graph Management**: Full CRUD for nodes, edges, and templates
- 💬 **AI Chat**: Conversational interface with RAG and tool use
- 📁 **Document Source Processing**: Process PDFs, text files, CSVs into knowledge graphs
- 🔄 **Workflow Engine**: Execute multi-step AI research workflows with triggers
- 🔍 **Search**: FTS5 full-text + sqlite-vec vector search; multi-hop GraphRAG retrieval
- 🔌 **MCP Server**: Expose graph operations as Model Context Protocol tools
- ⚙️ **Settings Management**: Configure LLM providers, databases, and system settings
- 🔐 **Single-User Auth**: nginx `auth_request` gates every API call (no admin/user split)
- 🗄️ **Multi-Database**: Isolated workspaces with independent graphs

## Architecture

Cortex is part of the ChaosCypher neural architecture:

- **Core** - Brain (business logic)
- **Cortex** - Processing center (full backend) 👈 You are here
- **Neuron** - Worker cells (background processing)
- **Interface** - Interaction layer (UI)

### Vertical Slice Architecture (VSA)

Cortex uses VSA with self-contained feature slices. Each slice contains its
own routes, service logic, and Pydantic models. The authoritative list is the
set of directories under
`packages/cortex/src/chaoscypher_cortex/features/`:

```
packages/cortex/src/chaoscypher_cortex/
├── api/                      # API composition
│   └── v1/router.py          # Router registration (mounts all feature routers)
├── features/                 # VSA slices (chats, sources, nodes, edges,
│                             # templates, search, llm, queue, settings,
│                             # settings_public, workflows, triggers, tools,
│                             # dashboard, graph, graph_snapshot, mcp, lexicon,
│                             # backup, export, quality, counts, health,
│                             # diagnostics, logs, pause, upgrade, edition,
│                             # databases, local_auth, admin_plugins)
├── shared/                   # Shared infrastructure
│   ├── api/                 # Auth dependencies, error handling, pagination
│   ├── database/            # Database session
│   ├── llm/                 # LLM factory
│   └── queue/               # Queue utilities
├── app_factory.py            # create_app() — FastAPI app assembly
├── boot.py                   # Startup orchestration
├── lifespan.py               # Lifespan (startup/shutdown) wiring
├── middleware.py             # Middleware stack
├── shutdown.py               # Graceful shutdown
└── main.py                   # Thin CLI entrypoint
```

Each feature slice contains:
- `models.py` - Pydantic DTOs (Request/Response)
- `repository.py` - Data access layer
- `service.py` - Business logic
- `api.py` - REST endpoints + DI factory
- `__init__.py` - Barrel exports

## Installation

```bash
# From source (workspace sync — installs core + cortex + all dev tools)
uv sync --all-packages --extra dev

# Single-package mode (cortex + its core dep only)
uv sync --package chaoscypher-cortex
```

The repo uses uv workspaces (see `pyproject.toml` `[tool.uv.workspace]`); `pip
install -e` is no longer the supported install path. Install uv via
[the official installer](https://docs.astral.sh/uv/getting-started/installation/)
before running these commands.

## Usage

### Standalone

```bash
# Start Cortex server
cc-cortex start

# Custom host/port
cc-cortex start --host 0.0.0.0 --port 8080

# With environment variables
QUEUE_HOST=localhost QUEUE_PORT=6379 cc-cortex start
```

### Docker

```bash
# Development
docker compose -f packages/docker/multi-container/docker-compose.dev.yml up cortex

# Production
docker run -p 8080:8080 -e QUEUE_HOST=valkey -e QUEUE_PORT=6379 chaoscypher-cortex
```

### Programmatic

```python
from chaoscypher_cortex.main import create_app

app = create_app()

# Run with uvicorn
import uvicorn
uvicorn.run(app, host="0.0.0.0", port=8080)
```

## Configuration

Configure via environment variables or `settings.yaml` in the data directory
(`/data/settings.yaml` inside the `app-data` volume under Docker; the platform
data dir, e.g. `~/.local/share/chaoscypher`, when running bare):

```bash
# Queue (Valkey)
QUEUE_HOST=localhost
QUEUE_PORT=6379

# Database
CHAOSCYPHER_DATA_DIR=~/.local/share/chaoscypher
CHAOSCYPHER_CONFIG_DIR=~/.config/chaoscypher

# Logging
LOG_LEVEL=INFO
USE_JSON_LOGGING=false

# LLM Provider (provider only — models are configured in the Settings UI
# or via settings.yaml llm.* keys)
CHAOSCYPHER_LLM_PROVIDER=ollama   # ollama | openai | anthropic | gemini

# API Keys (if using cloud providers)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
```

## API Endpoints

Routes live under `/api/v1/` and are gated by nginx `auth_request` (no
register/login flow inside Cortex). The full surface includes:

- **Knowledge graph** — `/nodes`, `/edges`, `/templates`, `/graph`
- **Sources** — `/sources` (upload, list, extract, commit, citations, chunks). `SourceResponse` exposes user upload-time choices via the nested `upload_options` object (`auto_analyze`, `enable_normalization`, `enable_vision`, `content_filtering`, `filtering_mode`, `extraction_depth`, `forced_domain`), per-stage drop / merge counters via `quality_metrics` (40+ typed counters + companion fields like `loader_encoding_used`), and search-index health via `quality_metrics.vector_indexing_status` (`pending` / `indexed` / `degraded` / `failed`). New persisted upload settings must round-trip through `upload_options`, never as siblings on `SourceResponse` — see `packages/docs/docs/reference/api/sources.md`.
- **Search** — `/search` (FTS5, vector, hybrid, GraphRAG)
- **Chat** — `/chats`, `/chats/{id}/messages`, `/chats/{id}/send` + `/chats/{id}/events` (SSE), plus `/cancel`, `/retry`, `/regenerate`, `/export`
- **Workflows** — `/workflows`, `/workflows/{id}/executions`, `/triggers`, `/tools`
- **Settings** — `/settings`, `/settings/reset` (plus scoped `/settings/reset/{scope}` variants)
- **Queue** — `/queue/tasks`, `/queue/stats`
- **Operations** — `/llm/stats`, `/llm/tasks` (Ollama instance management lives under `/settings/ollama`), `/databases`, `/exports`, `/backup`
- **Diagnostics** — `/health`, `/diagnostics`, `/logs`, `/edition`, plus pause/resume under `/sources/{id}/...` and `/system/processing/...`
- **MCP** — `/mcp` (Streamable HTTP transport: POST for JSON-RPC, GET for the SSE stream, DELETE to end a session)

The complete reference (request/response shapes, query params, error envelopes)
is in `packages/docs/docs/reference/api/` and at the live OpenAPI page
http://localhost:8080/docs (disabled by default — start Cortex with
`ENABLE_API_DOCS=true` to enable `/docs`, `/redoc`, and `/openapi.json`).

## Development

### Project Structure

```
packages/cortex/
├── src/chaoscypher_cortex/
│   ├── api/                # Router registration (api/v1/router.py)
│   ├── features/           # VSA feature slices
│   ├── shared/             # Shared infrastructure
│   └── main.py             # Thin CLI entrypoint
├── tests/                  # Test suite
├── Dockerfile              # Production image
├── Dockerfile.dev          # Development image
└── pyproject.toml          # Package configuration
```

### Adding New Features

1. Create directory: `features/{feature}/`
2. Define DTOs: `{feature}/models.py`
3. Create repository: `{feature}/repository.py`
4. Create service: `{feature}/service.py`
5. Create API + factory: `{feature}/api.py`
6. Export: `{feature}/__init__.py`
7. Register router in `api/v1/router.py`

Example:

```python
# features/my_feature/models.py
from pydantic import BaseModel

class MyFeatureRequest(BaseModel):
    name: str

class MyFeatureResponse(BaseModel):
    id: str
    name: str

# features/my_feature/service.py
class MyFeatureService:
    def create(self, data: dict) -> dict:
        # Business logic
        return {"id": "123", "name": data["name"]}

# features/my_feature/api.py
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/api/v1/my-feature", tags=["My Feature"])

def get_service() -> MyFeatureService:
    return MyFeatureService()

@router.post("/", response_model=MyFeatureResponse)
def create_item(
    request: MyFeatureRequest,
    service: Annotated[MyFeatureService, Depends(get_service)]
):
    return service.create(request.model_dump())
```

### Testing

```bash
# Run all tests
pytest

# Unit tests only
pytest -m unit

# With coverage
pytest --cov=chaoscypher_cortex --cov-report=html
```

### Hot-Reload Development

```bash
# Using watchdog (not a workspace dependency — uv adds it for this run;
# it is preinstalled only in the dev Docker image)
uv run --with watchdog watchmedo auto-restart -d src -p "*.py" -- cc-cortex start

# Using Docker
docker compose -f packages/docker/multi-container/docker-compose.dev.yml up cortex
```

## Dependencies

- **Core**: `chaoscypher-core` - Business logic
- **FastAPI**: Web framework
- **SQLModel**: Database ORM
- **Valkey** (via `chaoscypher_core.queue`): Background task queue
- **Structlog**: Structured logging
- **Pydantic**: Data validation

## License

AGPL-3.0 License - See LICENSE file for details
