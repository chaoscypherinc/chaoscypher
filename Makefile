# Chaos Cypher Development Makefile
# Local CI enforcement - no external dependencies
# Tests run in Docker for isolation
#
# Run 'make' or 'make help' to see available commands

.PHONY: help install lint lint-fix format typecheck test test-unit test-cov test-cov-internal test-cov-interface coverage-diff coverage-diff-interface security docstrings deadcode deadcode-all ci ci-local ci-extended ci-docker docker-test docker-ci docs docker-dev docker-prod docker-down docker-up docker-rebuild clean clean-worktrees lint-claude lint-claude-selftest lint-secrets lint-internal-refs benchmark-list benchmark-quick benchmark-cards check-api-docs license-check license-check-python license-check-interface bundle-size mutate mutate-python mutate-interface

# Default target
help:
	@echo "Chaos Cypher Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install       Install all packages + pre-commit hooks"
	@echo ""
	@echo "Quality (local - fast):"
	@echo "  make lint          Run all linters (Python + Frontend)"
	@echo "  make lint-fix      Auto-fix linting issues"
	@echo "  make format        Format all code"
	@echo "  make typecheck     Run mypy type checking"
	@echo "  make lint-claude   Check CLAUDE.md architecture rules"
	@echo "  make lint-claude-selftest  Run self-tests for every CC0xx rule"
	@echo "  make lint-secrets  Scan for committed secrets (gitleaks)"
	@echo "  make deadcode      Check for dead/unused code (vulture)"
	@echo "  make bundle-size   Check frontend bundle size budgets (size-limit, brotli)"
	@echo ""
	@echo "Testing (Docker - isolated):"
	@echo "  make docker-test   Run tests in Docker (isolated)"
	@echo "  make docker-ci     Run full CI in Docker"
	@echo ""
	@echo "Testing (local):"
	@echo "  make test          Run all tests locally"
	@echo "  make test-unit     Run unit tests only"
	@echo "  make test-cov      Run tests with 80%% coverage gate"
	@echo "  make test-cov-interface  Run frontend tests with vitest ratchet thresholds"
	@echo "  make coverage-diff Check >=90%% coverage on changed lines vs origin/main"
	@echo "  make coverage-diff-interface  Check >=90%% frontend coverage on changed lines vs origin/main"
	@echo ""
	@echo "Security:"
	@echo "  make security      Scan for vulnerable dependencies"
	@echo "  make license-check          Scan runtime deps for forbidden licenses (Python + Frontend)"
	@echo "  make license-check-python   Python runtime closure only"
	@echo "  make license-check-interface Frontend production deps only"
	@echo ""
	@echo "CI Simulation:"
	@echo "  make ci            Run full CI pipeline (lint local, test Docker)"
	@echo "  make ci-local      Run full CI pipeline locally"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-up       Start all-in-one container (recommended)"
	@echo "  make docker-rebuild  Rebuild and restart all-in-one"
	@echo "  make docker-dev      Start multi-container dev environment"
	@echo "  make docker-prod     Start multi-container prod environment"
	@echo ""
	@echo "Benchmark:"
	@echo "  make benchmark-list    List benchmark configs and datasets"
	@echo "  make benchmark-quick   Run quick smoke (local-only, war_and_peace_tiny)"
	@echo ""
	@echo "Mutation testing (weekly, manual):"
	@echo "  make mutate            Run mutation testing for Python + TypeScript critical paths"
	@echo "  make mutate-python     Mutation-test Python critical paths (mutmut)"
	@echo "  make mutate-interface  Mutation-test TS API boundary (Stryker)"
	@echo "  make ci-extended       Full CI + mutation testing (run weekly, not per-PR)"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean             Remove all caches and generated artifacts (gitignored only)"
	@echo "  make clean-worktrees   List git worktrees and flag those on branches merged into main"

# ==========================================================================
# Development Setup
# ==========================================================================

install:
	@echo "Installing Python packages (uv workspace sync)..."
	uv sync --all-packages --extra dev
	@echo "Installing frontend dependencies..."
	cd packages/interface && npm install --include=dev --silent
	@echo "Installing pre-commit hooks..."
	uv run pre-commit install
	uv run pre-commit install --hook-type pre-push
	@echo "Building Docker test image..."
	cd packages/docker/test && docker compose -f docker-compose.yml build test
	@echo "Setup complete"

# ==========================================================================
# Linting (local - fast)
# ==========================================================================

lint:
	@echo "=== Python Linting ==="
	uv run ruff check packages/
	@echo ""
	@echo "=== Python Formatting Check ==="
	uv run ruff format --check packages/
	@echo ""
	@echo "=== Frontend Linting ==="
	cd packages/interface && npm run lint
	@echo ""
	@echo "All linting passed"

lint-fix:
	@echo "=== Auto-fixing Python ==="
	uv run ruff check packages/ --fix
	uv run ruff format packages/
	@echo "Python fixed"

format:
	uv run ruff format packages/

types:
	@echo "=== Generating TypeScript types from OpenAPI ==="
	./scripts/generate-types.sh
	@echo "Types generated at packages/interface/src/types/generated/api.ts"

typecheck:
	@echo "=== Python Type Checking ==="
	uv run mypy packages/core/src packages/cortex/src packages/neuron/src packages/cli/src --config-file=pyproject.toml
	@echo ""
	@echo "=== Frontend Type Checking ==="
	cd packages/interface && npx tsc --noEmit
	@echo ""
	@echo "Type checking passed"

# ==========================================================================
# Testing (Docker - isolated)
# ==========================================================================

docker-test:
	@echo "=== Running Tests in Docker (isolated) ==="
	cd packages/docker/test && docker compose -f docker-compose.yml run --rm test make test-cov-internal
	cd packages/docker/test && docker compose -f docker-compose.yml down
	@echo ""
	@echo "Docker tests complete"

docker-ci:
	@echo "=== Running Full CI in Docker ==="
	cd packages/docker/test && docker compose -f docker-compose.yml run --rm test make ci-local
	cd packages/docker/test && docker compose -f docker-compose.yml down
	@echo ""
	@echo "Docker CI complete"

# ==========================================================================
# E2E Testing
# ==========================================================================

E2E_COMPOSE = cd packages/docker/e2e && docker compose -f docker-compose.yml
E2E_WAIT = packages/docker/e2e/scripts/wait-for-healthy.sh
E2E_REPORTS_DIR = test-reports
E2E_PYTEST_REPORTS = \
  --html=$$REPORT_PREFIX.html --self-contained-html \
  --junit-xml=$$REPORT_PREFIX.xml \
  --json-report --json-report-file=$$REPORT_PREFIX.json

e2e: e2e-cli e2e-docker e2e-report-summary ## Run full E2E suite (CLI + Docker API + Browser)
	@echo ""
	@echo "========================================"
	@echo "ALL E2E TESTS PASSED"
	@echo "========================================"
	@echo "Reports: $(E2E_REPORTS_DIR)/"

e2e-cli: ## Run CLI E2E tests (no Docker required)
	@echo "=== CLI E2E Tests ==="
	@mkdir -p $(E2E_REPORTS_DIR)
	REPORT_PREFIX=$(E2E_REPORTS_DIR)/cli-report uv run python -m pytest e2e/cli/ --import-mode=importlib -v -m "cli" \
		--html=$(E2E_REPORTS_DIR)/cli-report.html --self-contained-html \
		--junit-xml=$(E2E_REPORTS_DIR)/cli-report.xml \
		--json-report --json-report-file=$(E2E_REPORTS_DIR)/cli-report.json
	@echo ""
	@echo "CLI E2E tests passed. Report: $(E2E_REPORTS_DIR)/cli-report.html"

e2e-docker: e2e-fresh e2e-resume e2e-cleanup ## Run Docker E2E (fresh + resume)
	@echo ""
	@echo "Docker E2E tests passed"

e2e-fresh: ## Run fresh phase E2E tests (wipes data, starts clean)
	@echo "=== E2E Fresh Phase ==="
	@mkdir -p $(E2E_REPORTS_DIR)
	$(E2E_COMPOSE) down -v 2>/dev/null || true
	$(E2E_COMPOSE) up -d app
	bash $(E2E_WAIT) http://localhost:8888 120
	$(E2E_COMPOSE) run --rm -e E2E_PHASE=fresh runner pytest e2e/ --import-mode=importlib -v --tb=short \
		-m "e2e and not resume and not cli" \
		--html=test-reports/api-fresh-report.html --self-contained-html \
		--junit-xml=test-reports/api-fresh-report.xml \
		--json-report --json-report-file=test-reports/api-fresh-report.json
	@echo ""
	@echo "Fresh phase passed. Report: $(E2E_REPORTS_DIR)/api-fresh-report.html"

e2e-resume: ## Run resume phase E2E tests (keeps data from fresh)
	@echo "=== E2E Resume Phase ==="
	@mkdir -p $(E2E_REPORTS_DIR)
	$(E2E_COMPOSE) restart app
	bash $(E2E_WAIT) http://localhost:8888 120
	$(E2E_COMPOSE) run --rm -e E2E_PHASE=resume runner pytest e2e/api/test_resume.py --import-mode=importlib -v --tb=short \
		-m "resume" \
		--html=test-reports/api-resume-report.html --self-contained-html \
		--junit-xml=test-reports/api-resume-report.xml \
		--json-report --json-report-file=test-reports/api-resume-report.json
	@echo ""
	@echo "Resume phase passed. Report: $(E2E_REPORTS_DIR)/api-resume-report.html"

e2e-report-summary: ## Print summary of all E2E test reports
	@echo ""
	@echo "=== E2E Test Report Summary ==="
	@uv run python scripts/e2e_report_summary.py $(E2E_REPORTS_DIR)/ 2>/dev/null || echo "(run individual e2e targets first to generate reports)"

e2e-browser: ## Run Playwright browser E2E tests only
	@echo "=== Browser E2E Tests ==="
	@mkdir -p $(E2E_REPORTS_DIR)
	$(E2E_COMPOSE) run --rm runner pytest e2e/browser/ --import-mode=importlib -v --tb=short -m "browser" \
		--html=test-reports/browser-report.html --self-contained-html \
		--junit-xml=test-reports/browser-report.xml \
		--json-report --json-report-file=test-reports/browser-report.json
	@echo ""
	@echo "Browser E2E tests passed. Report: $(E2E_REPORTS_DIR)/browser-report.html"

e2e-cleanup: ## Clean up E2E Docker resources
	$(E2E_COMPOSE) down -v 2>/dev/null || true
	@echo "E2E cleanup complete"

# ==========================================================================
# Pipeline smoke benchmark
# ==========================================================================
# Private profiling/smoke benchmarks are intentionally not wired into the
# public Makefile. Public correctness coverage lives in e2e/.

# ==========================================================================
# Testing (local - for internal use)
# ==========================================================================

test:
	uv run pytest packages/*/tests/ --import-mode=importlib -v -n auto -m "not e2e"

test-unit:
	uv run pytest packages/*/tests/ --import-mode=importlib -v -m unit -n auto

test-all: ## Run everything: per-package tests + e2e (e2e requires Docker)
	uv run pytest e2e/ packages/*/tests/ --import-mode=importlib -v -n auto

test-cov: docker-test

# Internal target - runs inside Docker.
# pytest-xdist note: -n auto distributes tests across worker subprocesses.
# When combined with --cov, pytest-cov requires deferring the report so the
# .coverage data files from each worker can be collected and merged before
# rendering. We let pytest-cov emit term-missing and html in the same call
# (it handles xdist coalescing transparently when --cov-report flags appear
# only on the final invocation, which is what we have here). The 80% gate
# (--cov-fail-under) fires AFTER coalescing, so the threshold is computed
# against the merged total — same behavior as the pre-xdist baseline.
test-cov-internal:
	@echo "=== Running Tests with Coverage (80%% gate, xdist parallel) ==="
	# Coverage outputs go to coverage_html/ (mounted host-writable in
	# docker-compose). coverage_html/coverage.xml lands on the host at
	# packages/docker/test-output/coverage.xml so `make coverage-diff`
	# can read it after the Docker run completes.
	uv run pytest packages/core/tests packages/cortex/tests packages/cli/tests packages/neuron/tests \
		--import-mode=importlib \
		-n auto \
		--cov=packages/core/src/chaoscypher_core \
		--cov=packages/cortex/src/chaoscypher_cortex \
		--cov=packages/cli/src/chaoscypher_cli \
		--cov=packages/neuron/src/chaoscypher_neuron \
		--cov-report=term-missing \
		--cov-report=html:coverage_html \
		--cov-report=xml:coverage_html/coverage.xml \
		--cov-fail-under=80
	@echo ""
	@echo "Coverage reports: coverage_html/index.html (HTML), coverage_html/coverage.xml (Cobertura, for diff-cover)"

# ==========================================================================
# Diff-cover (PR-scoped coverage gate)
# ==========================================================================
# Apply a 90%% coverage threshold to the lines changed in the current diff
# vs origin/main. The repo-wide 80%% gate (test-cov-internal) still applies;
# this is a complementary check that keeps NEW code well-tested without
# punishing legacy code that hasn't been backfilled to 90%% yet.
#
# Reads coverage.xml emitted by test-cov-internal. Run docker-test (or
# test-cov-internal directly inside Docker) before invoking this.
#
# Currently advisory in `make ci` (prints results, does not fail). After
# two weeks of advisory data we'll promote to blocking — see
# CONTRIBUTING.md and CONTRIBUTING.md.

COVERAGE_XML_PATH = packages/docker/test-output/coverage.xml

coverage-diff:
	@if [ ! -f $(COVERAGE_XML_PATH) ]; then \
		echo "ERROR: $(COVERAGE_XML_PATH) not found. Run 'make docker-test' first."; \
		exit 1; \
	fi
	@echo "=== Diff Coverage (>=90%% on changed lines vs origin/main) ==="
	uv run diff-cover $(COVERAGE_XML_PATH) --compare-branch=origin/main --fail-under=90
	@echo ""
	@echo "Diff coverage check passed"

coverage-diff-advisory:
	@if [ ! -f $(COVERAGE_XML_PATH) ]; then \
		echo "(skipping diff-cover — $(COVERAGE_XML_PATH) not found; run 'make docker-test' first)"; \
	else \
		echo "=== Diff Coverage (advisory, >=90%% on changed lines vs origin/main) ==="; \
		uv run diff-cover $(COVERAGE_XML_PATH) --compare-branch=origin/main --fail-under=90 || \
			echo "(advisory mode — diff-cover threshold not met, but not failing the build)"; \
	fi

# ==========================================================================
# Frontend coverage gate (vitest v8 provider)
# ==========================================================================
# Mirrors the Python `test-cov-internal` 80%% gate but with a ratchet model:
# thresholds in packages/interface/vitest.config.ts are pinned to TODAY's
# measured coverage minus a small (~2 pp) safety margin. Future PRs can
# maintain or improve; coverage cannot silently regress.
#
# NOT wired into pre-commit — coverage instrumentation roughly doubles test
# runtime and would make every commit painful. Run manually before claiming
# done if you touched packages/interface/.
#
# Baseline captured 2026-05-19 (278 tests passing, vitest v8 provider,
# all:true so orphan files count toward denominator):
#   Statements 22.43% | Branches 16.42% | Functions 17.65% | Lines 23.49%
# Threshold floors set: statements 20 | branches 14 | functions 15 | lines 21.
test-cov-interface:
	@echo "=== Frontend Coverage (vitest v8, ratchet thresholds) ==="
	cd packages/interface && npm run test:coverage -- --run
	@echo ""
	@echo "Coverage reports: packages/interface/coverage/index.html (HTML), packages/interface/coverage/lcov.info (LCOV, for diff-cover)"

# Frontend mirror of `make coverage-diff` (>=90%% on changed lines vs
# origin/main). diff-cover ingests vitest's lcov.info natively. Run
# `make test-cov-interface` first so packages/interface/coverage/lcov.info
# exists. Currently advisory — promote to blocking by removing the `|| true`
# once frontend coverage stabilises.
INTERFACE_LCOV_PATH = packages/interface/coverage/lcov.info

coverage-diff-interface:
	@if [ ! -f $(INTERFACE_LCOV_PATH) ]; then \
		echo "ERROR: $(INTERFACE_LCOV_PATH) not found. Run 'make test-cov-interface' first."; \
		exit 1; \
	fi
	@echo "=== Frontend Diff Coverage (>=90%% on changed lines vs origin/main) ==="
	uv run --with diff-cover diff-cover $(INTERFACE_LCOV_PATH) --compare-branch=origin/main --fail-under=90
	@echo ""
	@echo "Frontend diff coverage check passed"

# ==========================================================================
# Security
# ==========================================================================

security:
	@echo "=== Python Dependency Audit ==="
	# PYSEC-2022-42969: ReDoS in `py.path.svnwc`. `py` is a transitive dev
	# dep via `interrogate` (docstring coverage). Neither interrogate nor
	# this project use svnwc, so the affected code path is unreachable and
	# the package has no upstream fix. Re-evaluate when interrogate drops
	# its `py` dependency.
	uv run pip-audit --ignore-vuln PYSEC-2022-42969
	@echo ""
	@echo "=== Frontend Dependency Audit ==="
	cd packages/interface && npm audit --audit-level=high
	@echo ""
	@echo "Security scan complete"

# ==========================================================================
# License Scan (AGPL deny-list — protects dual-license model)
# ==========================================================================
# Why: ChaosCypher ships under AGPL-3.0-only (OSS) with a proprietary
# enterprise edition. A transitive GPL-family dep in the runtime / production
# closure would force the enterprise edition to inherit AGPL/GPL terms.
# These targets gate `uv lock` and `npm install` against a policy file
# (tools/license_check/policy.toml) with explicit allow / deny lists
# and per-package exceptions (each requires a written reason).
#
# Scope:
#   - Python: runtime closure only (uv export --no-dev). Dev tools (pytest,
#     mypy, interrogate, vulture, etc.) are deliberately unscanned — their
#     licenses cannot taint the shipped product.
#   - Frontend: --production deps only.
#
# Pre-commit hook (.pre-commit-config.yaml) fires these only when a dep
# manifest changes (uv.lock, pyproject.toml, package.json, package-lock.json).

license-check-python:
	@echo "=== License Scan (Python runtime closure) ==="
	uv run python scripts/license_check_python.py

license-check-interface:
	@echo "=== License Scan (Frontend production deps) ==="
	uv run python scripts/license_check_interface.py

license-check: license-check-python license-check-interface
	@echo ""
	@echo "License scan complete (no AGPL/GPL drift in runtime closures)"

# ==========================================================================
# Dead Code Detection
# ==========================================================================

deadcode:
	@echo "=== Dead Code Analysis (Vulture) ==="
	uv run vulture packages/core/src packages/cortex/src packages/neuron/src packages/cli/src .vulture_whitelist.py --min-confidence 80
	@echo ""
	@echo "=== Dead Code Analysis (knip - TypeScript) ==="
	cd packages/interface && npm run deadcode
	@echo ""

deadcode-all:
	@echo "=== Dead Code Detection (vulture - verbose, confidence 60) ==="
	uv run vulture packages/core/src packages/cortex/src packages/neuron/src packages/cli/src .vulture_whitelist.py --min-confidence 60
	@echo ""
	@echo "=== Dead Code Detection (knip - frontend) ==="
	cd packages/interface && npm run deadcode
	@echo ""
	@echo "Dead code check passed (verbose)"

# ==========================================================================
# Bundle Size Budget (frontend, brotli)
# ==========================================================================
# Enforces brotli'd byte budgets on the Vite-built JS chunks. Budgets live
# in packages/interface/.size-limit.json; baseline composition is documented
# at packages/interface/.size-limit.json.
#
# Always rebuilds first so the gate measures the current source — a stale
# dist/ would let bloat slip through. ~3 s to rebuild + ~1 s to measure.

bundle-size:
	@echo "=== Frontend Bundle Size Budget (size-limit, brotli) ==="
	@echo "Building production bundle..."
	cd packages/interface && npm run build >/dev/null
	@echo ""
	cd packages/interface && npx size-limit
	@echo ""
	@echo "Bundle-size budget check passed"

# ==========================================================================
# Docstring Coverage
# ==========================================================================

docstrings:
	@echo "=== Docstring Coverage (100%% required) ==="
	# `-c pyproject.toml` is mandatory: interrogate's auto-config-discovery
	# walks up from the source paths and finds the nearest `pyproject.toml`,
	# which in this monorepo is a per-package file with no `[tool.interrogate]`
	# section. Pinning the root config makes the exclude/ignore rules apply.
	uv run interrogate -c pyproject.toml packages/core/src packages/cortex/src packages/neuron/src packages/cli/src -f 100 -v
	@echo ""
	@echo "Docstring coverage passed"

# ==========================================================================
# CLAUDE.md Rules (custom architectural linting)
# ==========================================================================

lint-claude: lint-claude-selftest
	@echo "=== Module boundaries (import-linter: CC010, CC012, CC013, CC014, CC042, CC043) ==="
	uv run lint-imports
	@echo ""
	@echo "=== Pattern rules (semgrep: CC003, CC006-009, CC015, CC019, CC022, CC023, CC026-029, CC031, CC033, CC036, CC038, CC040, CC041, CC045) ==="
	# semgrep is intentionally NOT in [dev] extras (it pins click<8.2 which
	# conflicts with chaoscypher-cli's click>=8.3.0). `uv run --with semgrep
	# --no-project` runs it in an isolated environment.
	uv run --with semgrep --no-project semgrep --config tools/semgrep/rules/ packages/ --metrics off --error
	@echo ""
	@echo "=== CLAUDE.md Architecture Rules (AST checker: residual rules) ==="
	uv run python scripts/lint_claude_rules.py packages/
	@echo ""
	@echo "CLAUDE.md rules passed"

# Self-tests for every CC0xx rule (AST + semgrep). Catches the silent-break
# class that hit CC044 in May 2026: each rule must fire on a deliberately-bad
# fixture so a no-op'd rule fails CI immediately. See:
#   - packages/core/tests/unit/scripts/test_cc*.py        (AST self-tests)
#   - packages/core/tests/unit/scripts/test_semgrep_*.py  (semgrep sweep)
#   - tools/semgrep/tests/                       (semgrep fixtures)
lint-claude-selftest:
	@echo "=== CLAUDE.md rule self-tests (AST + semgrep) ==="
	uv run pytest packages/core/tests/unit/scripts/ -q
	@echo "CLAUDE.md rule self-tests passed"

lint-secrets:
	@echo "=== Secret Scan (gitleaks) ==="
	gitleaks detect --redact --verbose --no-banner
	@echo ""
	@echo "Secret scan passed"

lint-internal-refs:
	@echo "=== Public-export hygiene (private-docs refs + SPDX headers) ==="
	uv run python scripts/check_no_internal_refs.py
	uv run python scripts/check_spdx_headers.py

check-api-docs:
	cd packages/docs && uv run python scripts/check_api_docs.py

# ==========================================================================
# Local CI (Full Pipeline)
# ==========================================================================

# Default CI: lint locally (fast), test in Docker (isolated). diff-cover
# runs in advisory mode — failures print but don't fail the build. Promote
# to the blocking `coverage-diff` target after the advisory window per
# CONTRIBUTING.md.
#
# `ci` / `ci-local` delegate to scripts/run_ci.py so the identical chain runs
# with OR without make — Windows Git Bash has no make, so the pre-push hook
# calls the script directly. That script is the single source of truth for the
# CI step list; the individual targets below (lint, typecheck, …) stay for
# standalone use and run the same underlying commands.
ci:
	uv run python scripts/run_ci.py --mode docker

# Full local CI (no Docker) - used inside the Docker test container (which has make).
ci-local:
	uv run python scripts/run_ci.py --mode local

# ==========================================================================
# Mutation Testing (manual, weekly)
# ==========================================================================
#
# Mutation testing scores assertion strength: it mutates the source code
# (flips ==/!= , swaps constants, deletes branches) and measures how many
# of those "mutants" the test suite catches. A high line-coverage % with
# weak assertions can hide a low mutation score.
#
# Scope is intentionally tight to the highest-risk, hardest-to-replace
# logic (spend caps, foundational utils, API boundary code). Mutating
# the whole codebase would take hours; these targeted runs finish in
# under a minute (Python) / under a minute (TypeScript) each. The goal
# is "fast enough to run weekly," NOT "fast enough to gate every PR."
#
# Both targets enforce a mutation-score floor: the score must stay at
# or above (baseline - 2 pp). To re-baseline after tightening the
# suite, run the relevant target, note the new score, and bump:
#   * Python   -> MUTATION_SCORE_FLOOR in scripts/mutmut_score.py
#   * TypeScript -> thresholds.break in packages/interface/stryker.config.mjs
#
# NOT wired into pre-commit or `make ci` -- too slow. Use `make
# ci-extended` for a comprehensive weekly sweep, or invoke `make mutate`
# directly on demand.

mutate: mutate-python mutate-interface ## Run mutation testing for Python + TypeScript
	@echo ""
	@echo "========================================"
	@echo "ALL MUTATION SCORES MEET THEIR FLOORS"
	@echo "========================================"

mutate-python: ## Mutation-test Python critical paths (spend caps, foundational utils) via mutmut
	@echo "=== Mutation Testing (mutmut) ==="
	@echo "Scope: spend caps + foundational utils in packages/core/"
	@echo "Re-baseline floor in scripts/mutmut_score.py if you tighten the suite."
	cd packages/core && uv run mutmut run
	cd packages/core && uv run python ../../scripts/mutmut_score.py
	@echo ""

mutate-interface: ## Mutation-test the API client + TanStack Query hooks via Stryker
	@echo "=== Mutation Testing (Stryker) ==="
	@echo "Scope: src/services/api/ boundary code in packages/interface/"
	@echo "Re-baseline thresholds in packages/interface/stryker.config.mjs if you tighten the suite."
	cd packages/interface && npx stryker run
	@echo ""

# Extended CI = everything in `ci` PLUS mutation testing and the full
# e2e suite (CLI + Docker API + browser). Both add real minutes to the
# run, so this target is for weekly / pre-release rituals rather than
# per-PR feedback. e2e itself requires a Docker daemon and tears down
# its compose stack between phases.
ci-extended: ci mutate e2e
	@echo ""
	@echo "========================================"
	@echo "EXTENDED CI (incl. mutation + e2e) PASSED"
	@echo "========================================"

# ==========================================================================
# Documentation
# ==========================================================================

docs:
	cd packages/docs && npm run build:with-api
	@echo "Docs built: packages/docs/build/index.html"

docs-serve:
	cd packages/docs && npm start

# ==========================================================================
# Docker
# ==========================================================================

docker-up: ## Start all-in-one container (recommended)
	cd packages/docker && docker compose up -d
	@echo ""
	@echo "ChaosCypher is starting up. Open http://localhost when ready."
	@echo "Tail logs:    cd packages/docker && docker compose logs -f"
	@echo "Stop stack:   make docker-down"
	@echo ""
	@echo "Recent logs:"
	@cd packages/docker && docker compose logs --tail=20 2>/dev/null || true

docker-rebuild: ## Rebuild from source and restart all-in-one container
	cd packages/docker && docker compose build --no-cache && docker compose up --force-recreate -d

docker-dev: types ## Start multi-container dev environment (hot-reload)
	cd packages/docker/multi-container && docker compose -f docker-compose.dev.yml up

docker-prod: types ## Start multi-container prod environment
	cd packages/docker/multi-container && docker compose -f docker-compose.prod.yml up -d

docker-down:
	cd packages/docker/e2e && docker compose -f docker-compose.yml down -v 2>/dev/null || true
	cd packages/docker/test && docker compose -f docker-compose.yml down 2>/dev/null || true
	cd packages/docker/multi-container && docker compose -f docker-compose.dev.yml down 2>/dev/null || true
	cd packages/docker/multi-container && docker compose -f docker-compose.prod.yml down 2>/dev/null || true
	cd packages/docker && docker compose down 2>/dev/null || true

# ==========================================================================
# Cleanup
# ==========================================================================

clean:
	@echo "Cleaning build artifacts..."
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .grimp_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .import_linter_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -f .coverage .coverage.* coverage.xml diff-cover.md 2>/dev/null || true
	rm -rf coverage_html htmlcov 2>/dev/null || true
	rm -rf packages/docker/test-output packages/docker/test/test-output 2>/dev/null || true
	rm -f build/madge.json build/openapi.json build/semgrep.json 2>/dev/null || true
	rm -rf build/_openapi_tmp 2>/dev/null || true
	rm -f .playwright-mcp/console-*.log 2>/dev/null || true
	rm -f packages/docs/build.log 2>/dev/null || true
	rm -rf packages/docs/.docusaurus packages/docs/build 2>/dev/null || true
	rm -rf packages/interface/dist 2>/dev/null || true
	@echo "Clean complete"

# Diagnostic: list registered git worktrees and flag those whose branch is fully
# merged into main (safe to remove). Does not delete anything — prints the
# `git worktree remove` + `git branch -d` commands for review.
clean-worktrees:
	@echo "Scanning registered git worktrees..."
	@git worktree list | while read -r line; do \
		path=$$(printf '%s' "$$line" | awk '{print $$1}'); \
		branch=$$(printf '%s' "$$line" | sed -n 's/.*\[\(.*\)\].*/\1/p'); \
		if [ -z "$$branch" ]; then \
			echo "  detached  $$path  (skipping)"; \
			continue; \
		fi; \
		if [ "$$branch" = "main" ]; then \
			echo "  main      $$path  (skipping)"; \
			continue; \
		fi; \
		ahead=$$(git rev-list --count "main..$$branch" 2>/dev/null || echo "?"); \
		if [ "$$ahead" = "0" ]; then \
			echo "  MERGED    $$path  ($$branch) -- safe to remove:"; \
			echo "            git worktree remove '$$path' && git branch -d '$$branch'"; \
		else \
			echo "  active    $$path  ($$branch, $$ahead commits ahead of main)"; \
		fi; \
	done
	@echo ""
	@echo "Review the above and run the printed commands for any MERGED worktrees."

# ==========================================================================
# Benchmark
# ==========================================================================

benchmark-list:
	uv run chaoscypher benchmark list

benchmark-quick:
	uv run chaoscypher benchmark run quick --local-only

.PHONY: benchmark-full-smoke
benchmark-full-smoke: ## Three-stage smoke test (requires Ollama running locally + models pulled)
	CHAOSCYPHER_BENCHMARK_INTEGRATION=1 uv run pytest \
		packages/cli/tests/integration/benchmark/test_full_smoke.py -v

.PHONY: benchmark-cards
benchmark-cards: ## Regenerate the public model-cards docs page from the registry
	uv run python scripts/generate_model_cards.py
