# Chaos Cypher Neuron - Background task workers
ARG PYTHON_BASE_IMAGE=python:3.14.3-slim@sha256:5e59aae31ff0e87511226be8e2b94d78c58f05216efda3b07dbbed938ec8583b

FROM ${PYTHON_BASE_IMAGE} AS wheels-builder

ENV PIP_ROOT_USER_ACTION=ignore \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY packages/core/pyproject.toml   /build/packages/core/pyproject.toml
COPY packages/neuron/pyproject.toml /build/packages/neuron/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip build

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install --ignore-installed \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.10.0

COPY packages/core/README.md   /build/packages/core/README.md
COPY packages/core/src         /build/packages/core/src
COPY packages/neuron/README.md /build/packages/neuron/README.md
COPY packages/neuron/src       /build/packages/neuron/src

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m build --wheel --outdir /wheels /build/packages/core && \
    python -m build --wheel --outdir /wheels /build/packages/neuron

# Neuron depends on Core only (no Cortex, no Runtime; see CLAUDE.md).
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install --ignore-installed \
        --find-links=/wheels \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        /wheels/chaoscypher_core-*.whl \
        /wheels/chaoscypher_neuron-*.whl

FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

# Install system dependencies for media processing (OCR + audio extraction)
RUN apt-get update && \
    apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng ffmpeg && \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Copy prebuilt wheels and dependencies into the runtime image. No editable
# installs or workspace source are needed at runtime.
COPY --from=wheels-builder /install /usr/local/

# Copy healthcheck script
COPY packages/docker/multi-container/neuron/healthcheck.py /healthcheck.py

# Copy shared entrypoint script (strip CRLF for Windows git checkout compatibility)
COPY packages/docker/config/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Create non-root user and data directory with correct ownership.
# Docker named volumes initialize from the container filesystem on first use,
# so /data will be owned by appuser when the volume is created.
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/bash -m appuser && \
    mkdir -p /data && \
    chown appuser:appuser /data

# Health check matches docker-compose.prod.yml `neuron.healthcheck` so bare
# `docker run` deploys can detect a dead worker without compose-level health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "/healthcheck.py"]

USER appuser

ENTRYPOINT ["/entrypoint.sh"]

# Default: Start unified worker (can be overridden)
CMD ["cc-neuron"]
