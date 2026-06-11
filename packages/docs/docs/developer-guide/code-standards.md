---
id: code-standards
title: Code Standards
description: Chaos Cypher coding conventions — naming, data type boundaries, async rules, commit format, and the architectural lint rules enforced by semgrep and import-linter.
---

# Code Standards

## Naming Conventions

| Context | Convention | Example |
|---------|-----------|---------|
| Python functions | `snake_case` | `create_workflow_from_template()` |
| Python classes | `PascalCase` | `SourceService` |
| TypeScript functions | `camelCase` | `fetchSources()` |
| TypeScript types | `PascalCase` | `SourceResponse` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_RETRIES` |
| Files (Python) | `snake_case.py` | `source_service.py` |
| Files (React components) | `PascalCase.tsx` | `SourceList.tsx` |
| Files (TS hooks/services/utils) | `camelCase.ts` | `useSources.ts`, `graphSnapshot.ts` |

**Be descriptive:** `create_workflow_from_template()` not `do_workflow()`.

## License Headers (SPDX)

Every source file (`.py`, `.ts`, `.tsx`) must start with an SPDX license header:

```python
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
```

(TypeScript files use `//` comments.) Pre-commit enforces the `SPDX-License-Identifier` line on shipped source (`packages/{core,cortex,neuron,cli}/src`, `packages/interface/src`, plus `scripts/`, `tools/`, and `e2e/`) — new files without the header fail the hook. Run `uv run python scripts/check_spdx_headers.py --fix` to insert the lines automatically.

## Docstrings

Google-style docstrings are required on all public classes and functions. The CI enforces 100% docstring coverage.

```python
def process_source(source_id: str, depth: str = "full") -> dict:
    """Process a source through the extraction pipeline.

    Args:
        source_id: Unique identifier of the source.
        depth: Extraction depth - "full" or "quick".

    Returns:
        Dict with processing results including entity and relationship counts.

    Raises:
        SourceNotFoundError: If the source does not exist.
    """
```

### Rules

- Every public class and function needs a docstring
- Module docstrings are required
- Comments explain **why**, not what
- No `TODO` or `FIXME` in code. Track follow-up work in an issue or the project backlog.
- No commented-out code — delete it

## Linting

Chaos Cypher uses **Ruff** with 76+ individual rules via 38 rule prefixes. Configuration is in `pyproject.toml`.

```bash
make lint          # Check
make lint-fix      # Auto-fix
```

### Custom Rules

`make lint-claude` enforces 39 custom architectural rules (numbered `CC001` through `CC051`, with reserved gaps) across three engines:

| Engine | Enforces | Rules |
|--------|----------|-------|
| **import-linter** (contracts in `pyproject.toml`) | Module boundaries — layering, framework isolation in core | `CC010`, `CC012`–`CC014`, `CC042`, `CC043` |
| **semgrep** (`tools/semgrep/rules/cc-NNN-shortname.yml`) | Code patterns — naming conventions, forbidden expressions, missing calls | 27 rules: `CC001`, `CC003`, `CC005`–`CC009`, `CC015`, `CC019`, `CC022`/`CC023`, `CC026`–`CC029`, `CC031`, `CC033`, `CC036`, `CC038`, `CC040`/`CC041`, `CC045`–`CC050` |
| **AST checker** (`scripts/lint_claude_rules.py`) | Rules needing data-flow or cross-file analysis | `CC002`, `CC004`, `CC011`, `CC018`, `CC044`, `CC051` |

Examples: `CC001` — factory functions must be named `get_{feature}_service()`; `CC002` — data type boundary violations (entity attribute access on storage dicts); `CC003` — SQLAlchemy list methods without `load_only()`; `CC011` — writes in repositories must use `adapter.transaction()`; `CC033` — API route handlers must be `async def`.

#### Adding a new rule

Pick the enforcer that matches the rule type (import-linter for module boundaries, semgrep for code patterns, the AST checker only as a last resort), plant a self-test "canary" fixture so a silently broken rule fails CI (`make lint-claude-selftest`), and pick the next unused CC number. The full catalog and authoring workflow live in root [`CONTRIBUTING.md`](https://github.com/chaoscypherinc/chaoscypher/blob/main/CONTRIBUTING.md) under "Adding a new architectural lint rule".

## Type Checking

```bash
make typecheck
```

Runs both:

- **mypy** — Python type checking
- **tsc** — TypeScript type checking

## Import Organization

Imports are organized by Ruff's isort rules:

1. Standard library
2. Third-party packages
3. Local imports

## Barrel Pattern

Every package's `__init__.py` should export its public API:

```python
"""Sources feature — document upload and processing."""

from .service import SourcesService

__all__ = ["SourcesService"]
```

Requirements:

- Module docstring
- Grouped imports with comment headers
- Explicit `__all__` list

## Logging

Use **structlog** with event-first pattern:

```python
import structlog

logger = structlog.get_logger(__name__)

# Correct
logger.info("source_indexed", source_id=source_id, chunk_count=42)

# Wrong - no f-strings or print()
logger.info(f"Indexed source {source_id}")
print("Indexed source")
```

| Level | When |
|-------|------|
| `ERROR` | Unrecoverable failure |
| `WARNING` | Handled fallbacks or retries |
| `INFO` | Business logic milestones |
| `DEBUG` | Raw data for troubleshooting |

## Configuration

All configuration must be in Pydantic settings — no hardcoded values for paths, timeouts, ports, batch sizes, etc. See [Configuration](../getting-started/configuration.md).
