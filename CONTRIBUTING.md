# Contributing to ChaosCypher

Thank you for your interest in contributing to ChaosCypher! This document covers how to contribute, the review bar, and our conventions.

## Quick links

- **Architecture + AI contributor guide:** `CLAUDE.md`
- **Project overview:** `README.md`
- **Public docs:** `packages/docs/`
- **Security:** `SECURITY.md`

## Contributor License Agreement (CLA)

Before we can accept your contributions, you must sign our Contributor License Agreement. This is required because ChaosCypher uses a dual-licensing model:

- **Community Edition:** AGPL v3 (open source)
- **Enterprise Edition:** Proprietary (additional features)

The CLA grants the ChaosCypher Team the right to include your contributions in both editions. Without it, we cannot legally include community contributions in the enterprise product.

### How to sign

When you open your first pull request, a maintainer will confirm whether we already have your CLA on file. If not, we will ask you to complete it before the pull request can be merged. This is a one-time process -- once signed, all future contributions are covered.

The full CLA text is available in [CLA.md](CLA.md).

## SPDX headers

Every source file (`.py`, `.ts`, `.tsx`, `.js`) must start with:

```
# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: <year> <your name or org>
```

(For TypeScript/JavaScript, use `//` comments instead of `#`.)

New files added without the header will fail pre-commit.

## Development setup

Prerequisites: Docker, Make, Python 3.14+, Node.js 22+, **uv 0.11+**.

uv replaces pip for dependency management — it manages the workspace `.venv`
and resolves the committed `uv.lock`. Install it via the official standalone
installer (recommended) or pip:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Or via pip (any platform)
pip install --user uv
```

```bash
make install           # Installs all packages + pre-commit hooks + Docker test image
make docker-dev        # Starts the multi-container dev environment (hot-reload)
```

`make install` runs `uv sync --all-packages --extra dev`, which materializes
`.venv/` at the repo root with every workspace member installed editably plus
the union of every member's `[dev]` extras (ruff, mypy, pytest, pytest-cov,
pytest-asyncio, vulture, pre-commit, pip-audit, interrogate, types-requests).
The lockfile (`uv.lock`) is the authoritative resolution; treat it like
`package-lock.json` — commit changes to it.

Dev UI: http://localhost:3000 — Dev API: http://localhost:8080/api/v1

## Commit convention

We use **Conventional Commits** exclusively.

**Format:** `type(scope): subject`

**Allowed types:** `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `build`, `perf`, `style`, `ci`.

**Scopes:** `core`, `cortex`, `neuron`, `interface`, `cli`, `docker`, `claude` (for CLAUDE.md), `docs` (for published docs), `repo` (cross-cutting), or a sub-area like `migrations`, `auth`, `queue`, `quality`.

**Subject:** imperative mood, ≤72 chars, no trailing period.

**Body (optional):** wrap at 72; explain *why*, not *what*.

**Breaking changes:** append `!` after the scope, and include a `BREAKING CHANGE:` footer.

Examples:
- `feat(core): add SearchRetryQueueProtocol + SQLite mixin`
- `refactor(cortex): extract TriggerSyncService transaction boundary`
- `fix(interface): MaintenancePage Stack alignItems must be in sx prop`
- `feat(core)!: rename WorkflowService.create to create_workflow`

## Branching + PRs

- Branch from `main`. Keep branches short-lived.
- Rebase, don't merge, when updating your branch against `main`.
- PRs must:
  - Have a Conventional Commits title.
  - Link any related issue (`Closes #123`).
  - Pass CI.
  - Include tests for new behavior.
  - Update `CLAUDE.md`, `CONTRIBUTING.md`, package READMEs, or `packages/docs/` if rules, procedures, or user-facing behavior shifted.
  - Keep Alembic migrations in sync with SQLModel metadata (see `CLAUDE.md` and the relevant package README).

## Pre-merge checklist

Before requesting review:

- [ ] `make lint` passes
- [ ] `make typecheck` passes
- [ ] `make lint-claude` passes
- [ ] `make docker-test` passes and exercises the change
- [ ] `make coverage-diff` shows ≥90% coverage on changed lines (see "Coverage gates" below)
- [ ] Alembic migration added if SQLModel metadata changed
- [ ] Public docs (`CLAUDE.md`, `CONTRIBUTING.md`, package READMEs, or `packages/docs/`) updated if a rule shifted
- [ ] Docstrings on new public classes/functions use Google style
- [ ] Commit message uses `type(scope): subject`
- [ ] SPDX header on all new source files
- [ ] No TODO/FIXME added in code; open or update a GitHub issue instead
- [ ] No commented-out code
- [ ] If frontend: `npm run lint`, `npm run typecheck`, component tests pass

## Coverage gates

Two complementary gates protect test coverage:

| Gate | Scope | Threshold | Where it runs |
|------|-------|-----------|---------------|
| Repo-wide | All source under `packages/{core,cortex,neuron,cli}/src/` | ≥80% | `make test-cov-internal` (inside Docker, blocking) |
| Diff-scoped | Lines changed in the current branch vs `origin/main` | ≥90% | `make coverage-diff` (blocking) / `make ci` (advisory until promoted) |

The diff-scoped gate keeps **new** code well-tested without punishing legacy
code that hasn't been backfilled to 90% yet. Run it locally before pushing:

```bash
make docker-test        # produces coverage.xml
make coverage-diff      # checks ≥90% on changed lines vs origin/main
```

`make ci` currently runs diff-cover in **advisory mode** (prints the
result, doesn't fail the build) so we can collect a baseline of typical
diff-coverage levels. The threshold will be promoted to blocking after
two weeks of advisory data.

Shortcut: `make ci` runs the full pipeline (lint, typecheck, docstrings, tests, security).

## Secret scanning

Three layers guard against committed secrets:

1. **Pre-commit hook** — runs `gitleaks` (v8.21.2) on staged files. Bypassed by `git commit --no-verify`, web-UI commits, or any tool ignoring `core.hooksPath`.
2. **Local `make lint-secrets`** — runs `gitleaks detect` against the entire working tree. Chained into `make ci` and `make ci-local`, so the full local CI run catches anything pre-commit missed. Run ad-hoc with `make lint-secrets`.
3. **CI workflow** — `.github/workflows/gitleaks.yml` is active: it runs `gitleaks` (v8.21.2, mirroring the pre-commit hook and `make lint-secrets`) on every `push` to every branch (PR branches included) as the CI defeat-path guard for `git commit --no-verify` and web-UI commits, and is also available via `workflow_dispatch` for ad-hoc audits. It intentionally does **not** trigger on `pull_request` (the `push` trigger already covers PR branches, and gitleaks-action's pull_request path 403s under this workflow's read-only `contents` token). For organization-owned repos you must add a free `GITLEAKS_LICENSE` repo secret (obtain at gitleaks.io) before the first push, or org-repo runs will fail; it is not required while the repo lives under a personal account. Tune false positives by adding entries to `.gitleaks.toml`'s `allowlist`.

If `make lint-secrets` flags something:

- **False positive** (test fixture, redacted sample): add an `allowlist` entry to `.gitleaks.toml` matching the file path or regex.
- **Real secret**: rotate the credential first, then remove it from history (`git filter-repo` or a fresh branch), then commit the allowlist update.

## Adding a new architectural lint rule

Pick the right enforcer for the rule type, then add it.

| Rule type | Enforcer | Where to add it |
|-----------|----------|-----------------|
| **Module boundary** (X may not import Y; layer A → layer B; web framework forbidden in core) | **import-linter** | New contract in `pyproject.toml` `[[tool.importlinter.contracts]]` |
| **Pattern in code** (forbidden expression, missing call, naming convention, file-level grep) | **semgrep** | New `tools/semgrep/rules/cc-NNN-shortname.yml` |
| **Frontend boundary** (TypeScript layering: components/hooks/services/utils) | **eslint-plugin-boundaries** | Edit `packages/interface/eslint.config.js` (boundaries block) |
| **AST-shape required** (data-flow tracking, threshold-based file exclusion, class-base introspection with annotation walking, `# noqa:` line-level opt-out, cross-file value resolution) | **AST checker** (`scripts/lint_claude_rules.py`) | Add a new `check_*` function and wire it into `check_file()` |

**Use the AST checker only as a last resort.** The 6 rules that survived
the 2026 migration there (CC002, CC004, CC011, CC018, CC044, CC051) all share
one identity — they need analysis no other tool can express. New rules
should look like one of those before reaching for the AST checker.

When adding a rule:

1. **Plant a canary** — write a tiny file with a deliberate violation and
   confirm your rule fires on it. Then write a tiny file with a valid
   case and confirm your rule does NOT fire. This catches false negatives
   and false positives that won't appear in the existing clean codebase.
2. **Run against the full codebase** — `make lint-claude` should pass on
   the unchanged tree (no new violations from your rule means parity).
3. **Document in CLAUDE.md** — add a `**CCNNN** — <description>. *(import-linter / semgrep / eslint / AST checker)*` line to the Architectural lint rules section.
4. **Reference numbers** — pick the next unused CC number from
   the current CC rule range (the historical range; gaps like CC016/CC017 are
   reserved/unused). Don't reuse a number that was deleted.

Before adding a rule, inspect nearby existing rules and tests for path patterns, ignore syntax, and type-only handling.

## Code style

- Follow the patterns documented in `CLAUDE.md`.
- **Python:** `snake_case` functions, `PascalCase` classes. Google-style docstrings on all public APIs.
- **TypeScript:** `camelCase` functions, `PascalCase` types. Props typed via `interface`.
- **SPDX license headers** on all source files.

## Code review expectations

**Blocking:** correctness, security, architectural boundaries (CC010, CC012, CC013, CC014, CC042, CC043 enforced by `import-linter`; rules enforced by CI), test coverage of new behavior, license-compatible deps.

**Advisory:** style preferences, micro-optimizations, naming opinions not tied to a standard.

Reviewers and authors should cite the relevant public doc, package README, or `CLAUDE.md` section when a decision is contested — "because I prefer it" is not a standard.

## Reporting issues

Open an issue on GitHub with:
- Steps to reproduce
- Expected vs actual behavior
- ChaosCypher version and deployment method (Docker, local, etc.)

## Reporting security issues

See [SECURITY.md](SECURITY.md). Do not open public GitHub issues for vulnerabilities.

## Code of Conduct

Treat contributors with respect. Disagree about code, not people. Harassment is not tolerated.
