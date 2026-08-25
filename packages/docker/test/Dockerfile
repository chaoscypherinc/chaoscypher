# Test runner container for Chaos Cypher
# Isolated environment for running tests without affecting local system
#
# Usage: docker compose -f docker-compose.yml run --rm test

FROM python:3.14.3-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    make \
    && rm -rf /var/lib/apt/lists/*

# Install gitleaks (version pinned to match pre-commit hook in .pre-commit-config.yaml)
ARG GITLEAKS_VERSION=8.21.2
RUN curl -sL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
    | tar -xz -C /usr/local/bin gitleaks \
    && chmod +x /usr/local/bin/gitleaks

# Install Node.js for frontend tests
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Set environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
# uv: skip self-update / telemetry / hard-link warnings inside the container.
ENV UV_NO_PROGRESS=1
ENV UV_LINK_MODE=copy
# uv.lock is bind-mounted read-only from the host at runtime; UV_FROZEN=1
# tells `uv run` to trust the existing .venv and never try to update the
# lockfile or re-resolve. The image was built with `uv sync --frozen`, so
# the venv is already pinned to this lockfile.
ENV UV_FROZEN=1

# Install uv (used for both lockfile-driven sync and `uv run` invocations from
# the Makefile inside the container).
RUN pip install uv

# Copy workspace manifests + lockfile. uv sync needs all four members'
# pyproject.toml + the root workspace config + uv.lock to do a frozen sync.
COPY pyproject.toml /app/pyproject.toml
COPY uv.lock /app/uv.lock
COPY packages/core/pyproject.toml /app/packages/core/pyproject.toml
COPY packages/core/src /app/packages/core/src
COPY packages/cortex/pyproject.toml /app/packages/cortex/pyproject.toml
COPY packages/cortex/src /app/packages/cortex/src
COPY packages/neuron/pyproject.toml /app/packages/neuron/pyproject.toml
COPY packages/neuron/src /app/packages/neuron/src
COPY packages/cli/pyproject.toml /app/packages/cli/pyproject.toml
COPY packages/cli/src /app/packages/cli/src

# Sync the workspace from the lockfile. --all-packages installs every member
# (core, cortex, neuron, cli); --extra dev pulls in the union of [dev] extras
# (pytest, pytest-cov, pytest-asyncio, ruff, mypy, vulture, pre-commit,
# pip-audit, interrogate, httpx, types-requests). --frozen forbids resolution
# drift — uv must use uv.lock exactly.
RUN uv sync --all-packages --extra dev --frozen

# Create directories for mounts (volumes will overlay these at runtime)
RUN mkdir -p /app/packages/neuron/src \
    /app/packages/cli/src \
    /app/packages/interface/src \
    /app/scripts \
    /app/tests

# Semgrep project root — load-bearing, not a convenience. semgrep locates
# its project root via `git rev-parse --show-toplevel` and anchors BOTH
# .semgrepignore (mounted in docker-compose.yml, see that file's comment)
# AND any root-anchored `paths.exclude`/`paths.include` pattern (e.g.
# CC005's "/packages/core/src/chaoscypher_core/adapters/**", CC019's
# ".../utils/id.py") to that root. This container receives the tree
# entirely via bind mounts and .dockerignore excludes .git/, so with no
# discoverable root those root-anchored patterns silently miss and
# `make lint-claude` both under-runs rules whose root-anchored `include`
# can't resolve and reports phantom CC005/CC019 findings on files the
# host-side run correctly excludes (measured on 2026-08-14: 14/26 rules
# effectively enforced over 850 targets, 6 phantom findings, vs. the
# host's 26/26 over 1995 targets, 0 findings). See commit 69093b455 and
# packages/core/tests/unit/scripts/test_semgrep_rule_selftests.py's
# _semgrep_project_root_is_discoverable for the same root cause hitting
# the CC040/CC041 self-tests.
#
# A bare `git init` is sufficient: semgrep only needs
# `git rev-parse --show-toplevel` to succeed, not real history, a remote,
# or tracked commits (69093b455's manual repro: Targets scanned 0 -> 2
# across that same before/after). A real bind-mounted worktree checkout
# points its `.git` at an external gitdir living outside the mounted
# tree (a *file*, not a directory) that would need mounting too and
# differs per host machine; a throwaway init sidesteps that fragility
# entirely and is safe because nothing else in this image or the mounted
# source trees expects /app to be a real, history-bearing git checkout.
RUN git init --quiet /app

# Run as non-root user
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m -s /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Default command: run linting (basic validation)
CMD ["make", "lint"]
