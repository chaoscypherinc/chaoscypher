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

# Run as non-root user
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m -s /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Default command: run linting (basic validation)
CMD ["make", "lint"]
