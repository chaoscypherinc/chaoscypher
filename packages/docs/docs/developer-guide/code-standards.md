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
| Files (TypeScript) | `camelCase.tsx` | `sourceList.tsx` |

**Be descriptive:** `create_workflow_from_template()` not `do_workflow()`.

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

The project includes custom architectural lint rules (`make lint-claude`):

| Rule | Description |
|------|-------------|
| `CC001` | Factory function naming must be `get_{feature}_service()` |
| `CC002` | Data type boundary violations (entity access on storage dicts) |
| `CC003` | SQLAlchemy list methods without `load_only()` |
| `CC004` | Barrel pattern missing `__all__` |
| `CC005` | Manual session creation |

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
