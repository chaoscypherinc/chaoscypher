# Chaos Cypher Cortex - Full-featured backend API
ARG PYTHON_BASE_IMAGE=python:3.14.3-slim@sha256:5e59aae31ff0e87511226be8e2b94d78c58f05216efda3b07dbbed938ec8583b

FROM ${PYTHON_BASE_IMAGE} AS wheels-builder

ENV PIP_ROOT_USER_ACTION=ignore \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY packages/core/pyproject.toml   /build/packages/core/pyproject.toml
COPY packages/cortex/pyproject.toml /build/packages/cortex/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip build

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install --ignore-installed \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.10.0

COPY packages/core/README.md   /build/packages/core/README.md
COPY packages/core/src         /build/packages/core/src
COPY packages/cortex/README.md /build/packages/cortex/README.md
COPY packages/cortex/src       /build/packages/cortex/src

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m build --wheel --outdir /wheels /build/packages/core && \
    python -m build --wheel --outdir /wheels /build/packages/cortex

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install --ignore-installed \
        --find-links=/wheels \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        /wheels/chaoscypher_core-*.whl \
        /wheels/chaoscypher_cortex-*.whl

FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Copy prebuilt wheels and dependencies into the runtime image. No editable
# installs or workspace source are needed at runtime.
COPY --from=wheels-builder /install /usr/local/

# Copy shared entrypoint script (strip CRLF for Windows git checkout compatibility)
COPY packages/docker/config/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Create non-root user and data directory with correct ownership.
# Docker named volumes initialize from the container filesystem on first use,
# so /data will be owned by appuser when the volume is created.
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/bash appuser && \
    mkdir -p /data && \
    chown appuser:appuser /data

# Copy healthcheck script
COPY packages/docker/multi-container/cortex/healthcheck.py /healthcheck.py

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "/healthcheck.py"]

USER appuser

ENTRYPOINT ["/entrypoint.sh"]

# Start Cortex server
CMD ["cc-cortex", "start", "--host", "0.0.0.0", "--port", "8080"]
