# CLAUDE.md

Public contributor guidance for AI coding assistants working in this repository.

## Project at a glance

ChaosCypher is a full-stack GraphRAG / knowledge graph platform. The public repository is a monorepo:

- `packages/core/` — framework-agnostic domain logic and storage/LLM ports
- `packages/cortex/` — FastAPI backend
- `packages/neuron/` — background workers
- `packages/interface/` — React + TypeScript UI
- `packages/cli/` — standalone command-line interface
- `packages/docker/` — Docker orchestration
- `packages/docs/` — Docusaurus documentation site
- `e2e/` — public end-to-end tests

## Working rules

1. Search before adding new helpers or patterns. Prefer existing utilities and package-local conventions.
2. Keep package boundaries clean: Core should not depend on Cortex/Neuron/CLI, and app packages should depend on Core through explicit interfaces.
3. Add or update tests for behavior changes.
4. Keep public docs and examples runnable from the public tree.
5. Do not commit secrets, local data, benchmark outputs, or generated caches.
6. Use Conventional Commits: `type(scope): subject`.

## Common commands

```bash
make install          # install workspace deps + pre-commit hooks
make docker-up        # start the all-in-one container
make docker-dev       # start hot-reload development stack
make lint             # Python + frontend lint
make typecheck        # Python mypy + frontend typecheck
make test             # package tests
make docker-test      # isolated Docker test run
make ci               # full local CI sweep
make e2e-cli          # public CLI E2E tier
make e2e              # public E2E suite requiring Docker
```

## Documentation

- Start with `README.md` for install and architecture overview.
- Use `CONTRIBUTING.md` for contribution and PR expectations.
- Use `SECURITY.md` for vulnerability reporting.
- Use `packages/core/README.md` for Core architecture.
- Use `packages/interface/CLAUDE.md` for frontend-specific guidance.
- Use `packages/docs/` for the public documentation site.

Internal company strategy, private launch plans, and private agent runbooks are intentionally not part of the public repository.
