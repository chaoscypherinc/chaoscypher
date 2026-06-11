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

### End-to-End Tests

The system-level E2E harness at `e2e/` runs in tiers:

```bash
make e2e-cli       # CLI tier — subprocess tests, no Docker required
make e2e-browser   # Playwright browser tier only (Docker)
make e2e           # Full suite: CLI + Docker API + browser
```

`make e2e` runs the Docker-backed tests in two phases: a **fresh** phase that wipes data and starts a clean stack, then a **resume** phase that restarts the app and verifies state survives the restart. HTML, JUnit, and JSON reports for every tier land in `test-reports/`.

## Coverage Requirements

Two complementary gates protect coverage:

| Gate | Scope | Threshold | Command |
|------|-------|-----------|---------|
| Repo-wide | All source under `packages/{core,cortex,neuron,cli}/src/` | ≥80% | `make docker-test` (blocking) |
| Diff-scoped | Lines changed in the current branch vs `origin/main` | ≥90% | `make coverage-diff` (blocking pre-merge; advisory in `make ci`) |

The diff-scoped gate keeps **new** code well-tested without punishing legacy code that hasn't been backfilled yet. Check both locally:

```bash
make docker-test        # full suite + coverage reports (produces coverage.xml)
make coverage-diff      # checks ≥90% on changed lines vs origin/main
```

After `make docker-test`, coverage reports land on the host at `packages/docker/test-output/` (HTML at `index.html`, Cobertura XML at `coverage.xml`). A local `make test-cov-internal` run writes them to `coverage_html/` at the repo root instead.

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

The full CI pipeline (`make ci`) runs, in order:

1. **Lint + format** — Ruff (Python) and ESLint (frontend)
2. **Type generation** — regenerate the TypeScript API types from the backend schema
3. **Type checking** — mypy (Python) + tsc (TypeScript)
4. **Custom architectural rules** — `lint-claude` (import-linter + semgrep + AST checker, with self-tests)
5. **Secret scan** — gitleaks
6. **Public-export hygiene** — internal-reference check + SPDX license headers
7. **Docstring coverage** — 100% required for public APIs
8. **Dead code detection** — vulture scanning
9. **Bundle-size budget** — frontend build + size-limit check
10. **License scan** — Python + frontend dependency license policy
11. **Frontend tests + coverage** — Vitest with coverage
12. **Docker tests** — full Python test suite with the 80% coverage gate
13. **Diff coverage (advisory)** — ≥90% on changed lines vs `origin/main`
14. **Security audit** — pip-audit + npm audit

The authoritative step list lives in `scripts/run_ci.py` (`uv run python scripts/run_ci.py --list`). All blocking checks must pass before merging.
