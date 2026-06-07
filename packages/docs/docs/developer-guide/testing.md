---
id: testing
title: Testing
description: Multi-level testing approach for Chaos Cypher — unit tests with pytest, integration tests, and isolated Docker test runs, with fixtures, fakes, and async test patterns.
---

# Testing

## Test Strategy

Chaos Cypher uses a multi-level testing approach:

| Level | Location | Runner | Purpose |
|-------|----------|--------|---------|
| **Unit** | `packages/*/tests/unit/` | pytest | Individual functions and classes |
| **Integration** | `packages/*/tests/integration/` | pytest | Cross-component interactions within a package |
| **End-to-End** | `e2e/` (repo root) | pytest in Docker | Full-stack API + browser + CLI harness |
| **Docker** | `make docker-test` | pytest in Docker | Isolated, production-like environment |

## Running Tests

### Docker Tests (Recommended)

Run tests in an isolated Docker container that mirrors the production environment:

```bash
make docker-test
```

This builds a test image and runs all tests with coverage reporting. Use this before pushing to ensure tests pass in a clean environment.

### Local Tests

Run tests directly on your machine (faster, but may be affected by local state):

```bash
make test
```

### Individual Package Tests

```bash
# Core
cd packages/core && pytest

# Cortex
cd packages/cortex && pytest

# CLI
cd packages/cli && pytest
```

## Coverage Requirements

The CI pipeline enforces **80% code coverage**. Check coverage locally:

```bash
make docker-test
```

Coverage reports are generated in `coverage_html/` for detailed analysis.

## Writing Tests

### Test File Structure

Each package owns its own tests; the e2e harness lives at the repo root.

```
e2e/                          # System-level harness (Docker-driven)
├── api/                      # HTTP journey tests via httpx
├── browser/                  # Playwright UI tests
├── cli/                      # CLI subprocess tests
└── fixtures/                 # Seed data shared across e2e tiers

packages/core/tests/          # Core package — unit + integration
packages/cortex/tests/        # Cortex package — unit + integration
packages/cli/tests/           # CLI package — unit + integration
packages/neuron/tests/        # Neuron package — unit + integration
packages/interface/src/**/__tests__/   # Frontend (Vitest, colocated)
```

`make test` runs all per-package tests but excludes e2e (no Docker required).
`make test-all` runs everything including the Docker-dependent e2e harness.

### Testing Services

Service tests should mock the repository layer:

```python
def test_list_entities(mock_repository):
    """Test listing entities returns expected results."""
    mock_repository.list_entities.return_value = [
        MyEntity(id="1", name="Test", database_name="default")
    ]
    service = MyService(mock_repository)

    result = service.list_entities()

    assert len(result) == 1
    assert result[0]["name"] == "Test"
```

### Testing API Endpoints

Use FastAPI's `TestClient`:

```python
from fastapi.testclient import TestClient

def test_list_endpoint(client: TestClient):
    """Test list endpoint returns 200."""
    response = client.get("/api/v1/myentities")
    assert response.status_code == 200
```

### Testing Core Services

Core services use storage protocol mocks:

```python
def test_core_service(mock_storage):
    """Test core service with mocked storage."""
    mock_storage.get_source.return_value = {
        "id": "source-1",
        "name": "test.pdf",
        "status": "indexed",
    }
    service = SourceService(mock_storage)

    result = service.get_source("source-1")

    assert result["status"] == "indexed"
```

## CI Pipeline

The full CI pipeline (`make ci`) runs:

1. **Lint + format** — Ruff linting and formatting checks
2. **Type checking** — mypy (Python) + tsc (TypeScript)
3. **Custom architectural rules** — `lint-claude` checks (factory naming, data boundaries, etc.)
4. **Docstring coverage** — 100% required for public APIs
5. **Dead code detection** — deadcode/vulture scanning
6. **Tests + coverage** — 80% coverage gate in Docker
7. **Security scan** — Vulnerability scanning

All checks must pass before merging.
