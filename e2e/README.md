# Chaos Cypher End-to-End Test Harness

System-level tests that exercise the full stack (Cortex API + Neuron worker
+ Valkey + nginx) via Docker Compose, browser automation, or the CLI. For
per-package unit + integration tests, see `CONTRIBUTING.md`.

## What's here

- `api/` — HTTP journey tests against a running Cortex stack. Pytest + httpx.
  Requires Docker.
- `browser/` — Playwright-based UI tests against a running Interface stack.
  Requires Docker + browser binaries.
- `cli/` — CLI end-to-end tests that drive the `chaoscypher` binary as a
  subprocess. Most run without Docker; some need a running stack.
- `fixtures/` — seed data and helpers used across the harness (sample
  documents, `seed.ccx`, generator scripts).
- `conftest.py` — fixtures shared across api/browser/cli.

## Running

| Target                                | Runs                                   | Needs                       |
| ------------------------------------- | -------------------------------------- | --------------------------- |
| `make e2e-cli`                        | CLI tier (no Docker)                   | uv environment              |
| `make e2e-fresh`                      | API tests against a freshly seeded DB  | Docker                      |
| `make e2e-resume`                     | Resume / warm-start subset             | Docker (after `e2e-fresh`)  |
| `make e2e-browser`                    | Playwright UI tests                    | Docker + browser binaries   |
| `make e2e`                            | CLI + Docker fresh + Docker resume     | Docker                      |
| `uv run pytest e2e/cli/ -v`           | CLI tests directly                     | no Docker                   |
| `make test-all`                       | Per-package tests + e2e                | Docker                      |
| `make test`                           | Per-package tests only (excludes e2e)  | no Docker                   |

## Markers

- `e2e` — applied to every test in this tree. Use `-m "not e2e"` to exclude.
- `api`, `browser`, `cli` — finer-grained selection (one per tier).
- `fresh`, `resume` — phase markers for the API subset.

All markers are registered in the root `pyproject.toml`
`[tool.pytest.ini_options]` section.

## CI vs local-only tiers

The `api` and `cli` tiers run in CI nightly and on manual dispatch via
`.github/workflows/e2e.yml` (they are not a per-PR gate).
The **`browser` tier is intentionally local-only**: it is *not* wired into any
GitHub Actions workflow, by design — the Playwright stack adds enough wall-clock
+ flake budget that it does not earn its place in the CI gate. The tier is
operator-run: invoke it locally with `make e2e-browser` (or
`uv run pytest e2e/browser/ -v` against an already-running stack) before cutting
a release, after a UI-heavy refactor, or whenever you want to validate end-to-end
UI behaviour. If you change a browser test, run the tier locally and report the
result in the PR description; reviewers will not see CI signal for it.

## Adding a test

Match the surrounding files. For `api/`: use the API client fixture from
`api/conftest.py`. For `browser/`: use the Playwright `page` fixture from
`browser/conftest.py`. For `cli/`: use the `invoke_cli` helper from
`cli/conftest.py` and assert on `result.exit_code` and `result.output`.

Use the seed fixtures under `fixtures/sample_data/` rather than inventing
new corpus files; if you need additional shapes, extend
`fixtures/generate_seed.py` so the seed bundle stays reproducible.

When in doubt, follow the surrounding tests and the public contributor guidance in `CONTRIBUTING.md`.
