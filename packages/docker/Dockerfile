# Chaos Cypher All-in-One — Single container with Nginx, Valkey, Cortex, Neuron
# Usage: docker build -f packages/docker/Dockerfile -t chaoscypher/chaoscypher .

# Stage 1: Produce openapi.json from the FastAPI app so the frontend stage
# can generate TypeScript types without a host-side `make types` step.
FROM python:3.14.6-slim AS types-builder

# Silence pip's "running as root" warning. The container's filesystem is
# already the isolation boundary — a venv adds no real benefit here.
ENV PIP_ROOT_USER_ACTION=ignore

WORKDIR /build

COPY packages/core/pyproject.toml /build/packages/core/pyproject.toml
COPY packages/cortex/pyproject.toml /build/packages/cortex/pyproject.toml

RUN mkdir -p /build/packages/core/src/chaoscypher_core /build/packages/cortex/src/chaoscypher_cortex && \
    touch /build/packages/core/src/chaoscypher_core/__init__.py \
          /build/packages/cortex/src/chaoscypher_cortex/__init__.py

# Bump pip to latest so subsequent installs don't emit the "new release
# available" notice on every cold build.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip

# CPU-only torch matches the main Python stage — pulled in transitively via
# sentence-transformers. Using the same wheel keeps the pip cache warm for
# stage 3.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -e /build/packages/core && \
    pip install -e /build/packages/cortex

COPY packages/core/src /build/packages/core/src
COPY packages/cortex/src /build/packages/cortex/src

RUN CHAOSCYPHER_SCHEMA_ONLY=1 \
    CHAOSCYPHER_DATA_DIR=/tmp/oa \
    LOG_LEVEL=WARNING \
    PYTHONPATH=/build/packages/core/src:/build/packages/cortex/src \
    python -c "import json; from chaoscypher_cortex.main import app; open('/build/openapi.json', 'w').write(json.dumps(app.openapi()))"

# Stage 2: Build React frontend
FROM node:22.23.1-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS frontend-builder

WORKDIR /app
# Bump npm to latest so `npm ci` below doesn't emit a "new major version"
# update notice on every cold build.
RUN npm install -g npm@latest
COPY packages/interface/package*.json packages/interface/.npmrc ./
RUN npm ci
COPY packages/interface ./
COPY --from=types-builder /build/openapi.json /app/openapi.json
RUN npx openapi-typescript /app/openapi.json -o src/types/generated/api.ts
RUN npm run build

# Stage 3: Build Python wheels and install them (with all transitive deps,
# including torch CPU) into an isolated /install prefix that gets COPY'd into
# the final stage. This keeps pip / setuptools / build toolchain OUT of the
# runtime image and replaces the previous `pip install -e` source duplication.
FROM python:3.14.6-slim AS wheels-builder

ENV PIP_ROOT_USER_ACTION=ignore \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Bring in only the workspace pyprojects first — they rarely change relative
# to the source tree. Source COPYs are deferred until after the heavyweight
# torch install below, so source edits don't invalidate that ~200 MB layer.
# The standalone `chaoscypher` CLI (packages/cli/) is intentionally excluded
# — it's distributed via PyPI for host installs, not bundled into the
# container runtime.
COPY packages/core/pyproject.toml   /build/packages/core/pyproject.toml
COPY packages/cortex/pyproject.toml /build/packages/cortex/pyproject.toml
COPY packages/neuron/pyproject.toml /build/packages/neuron/pyproject.toml

# Bump pip + install the PEP 517 build frontend used below.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip build

# Pre-install torch from the CPU-only PyTorch index into /install. Placed
# BEFORE the source COPYs so source edits don't invalidate this ~200 MB
# layer — torch changes rarely.
#
# --ignore-installed is critical: pip otherwise treats packages already
# present in /usr/local/lib/python3.14/site-packages (notably `packaging`
# and `setuptools`, dragged in by `pip install build` above) as satisfied
# and skips writing them into /install. The runtime image then crashes on
# `from packaging.version import parse` when langchain_core boots.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install --ignore-installed \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.10.0

# Now bring in source + READMEs — needed by `python -m build` below. Anything
# under packages/{core,cortex,neuron}/src lands in site-packages via the
# wheel build; no source is needed in the final stage.
COPY packages/core/README.md   /build/packages/core/README.md
COPY packages/core/src         /build/packages/core/src
COPY packages/cortex/README.md /build/packages/cortex/README.md
COPY packages/cortex/src       /build/packages/cortex/src
COPY packages/neuron/README.md /build/packages/neuron/README.md
COPY packages/neuron/src       /build/packages/neuron/src

# Build wheels for the three workspace packages bundled into the image. We
# deliberately use the stdlib-friendly `python -m build` instead of
# `uv build` here so the final image doesn't need uv installed — `pip
# install` in the next layer resolves inter-package deps from the local
# wheel directory via --find-links.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m build --wheel --outdir /wheels /build/packages/core && \
    python -m build --wheel --outdir /wheels /build/packages/cortex && \
    python -m build --wheel --outdir /wheels /build/packages/neuron

# Install the three workspace wheels + their PyPI dependencies into
# /install. --find-links=/wheels lets pip pick up chaoscypher-core when
# resolving the cortex/neuron wheels' inter-package requirement on it. We
# pass the PyPI mirror as the default index and keep the PyTorch index
# available so any torch sub-deps (already installed above) match
# version-wise.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install --ignore-installed \
        --find-links=/wheels \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        /wheels/chaoscypher_core-*.whl \
        /wheels/chaoscypher_cortex-*.whl \
        /wheels/chaoscypher_neuron-*.whl

# Strip build-time artifacts that get pulled into /install as transitive deps
# of the workspace packages but serve no purpose at runtime:
#   * setuptools / wheel — needed by pip/build, not by the running app.
#   * _distutils_hack / distutils-precedence.pth — companions to setuptools.
#   * dist-info/RECORD/INSTALLER/REQUESTED — only used by pip uninstall.
# We keep .dist-info/METADATA + entry_points.txt because importlib.metadata
# queries them (used by chaoscypher_core's provider plugin discovery).
RUN find /install/lib/python3.14/site-packages \
        \( -name 'setuptools' -type d \
        -o -name 'setuptools-*.dist-info' -type d \
        -o -name 'wheel' -type d \
        -o -name 'wheel-*.dist-info' -type d \
        -o -name '_distutils_hack' -type d \
        \) -prune -exec rm -rf {} + && \
    rm -f /install/lib/python3.14/site-packages/distutils-precedence.pth

# Stage 4: Runtime image — pure Python + system services. No pip, no
# setuptools editable installs, no duplicated workspace source. All Python
# packages arrive as a prebuilt /install tree COPY'd from wheels-builder.
FROM python:3.14.6-slim

WORKDIR /app

# INCLUDE_MEDIA=0 (default) → lean image (~400 MB smaller). Set to 1 to add
# tesseract + ffmpeg + language packs for OCR / audio / video ingest paths.
# See packages/docker/README.md for operator instructions.
ARG INCLUDE_MEDIA=0

# Install system dependencies
# gosu is the canonical "drop-privileges-as-PID-1" tool — entrypoint.sh runs
# as root for the privileged setup (chown of secrets, bind-mount preparation,
# nginx config rendering) then re-execs supervisord under appuser so that
# PID 1 never holds root. See entrypoint.sh's final `exec gosu appuser`.
RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        nginx \
        valkey-server \
        supervisor \
        logrotate \
        libcap2-bin \
        gosu && \
    if [ "$INCLUDE_MEDIA" = "1" ]; then \
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
            ffmpeg \
            tesseract-ocr \
            tesseract-ocr-eng; \
    fi && \
    rm -rf /var/lib/apt/lists/* && \
    setcap 'cap_net_bind_service=+ep' /usr/sbin/nginx

# Copy the prebuilt Python install tree from wheels-builder. /install was
# populated by `pip install --prefix=/install ...` in the builder stage, so
# overlaying it onto /usr/local/ here lands site-packages at
# /usr/local/lib/python3.14/site-packages and console scripts (cc-cortex,
# cc-neuron) at /usr/local/bin — exactly where the slim image's python
# expects them. No `pip install` runs in this stage.
COPY --from=wheels-builder /install /usr/local/

# Copy built frontend to static dir
COPY --from=frontend-builder /app/dist /app/static

# Copy configs
# supervisord.conf, nginx-http.conf, nginx-https.conf, and proxy-common.conf
# are now rendered at container start by chaoscypher_core.services.orchestration
# (see entrypoint.sh). The supervisord.conf build-time stub is still copied as
# a fallback target the entrypoint overwrites; the nginx confs are written
# directly into /etc/nginx/conf.d/ at startup.
COPY packages/docker/config/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY packages/docker/config/nginx-startup.conf /etc/nginx/nginx-startup.conf
COPY packages/docker/config/edge-auth-proxy.conf /etc/nginx/edge-auth-proxy.conf
COPY packages/docker/config/edge-unauthorized.conf /etc/nginx/edge-unauthorized.conf
COPY packages/docker/config/security-headers.conf /etc/nginx/security-headers.conf
COPY packages/docker/config/security-headers-no-csp.conf /etc/nginx/security-headers-no-csp.conf
COPY packages/docker/config/proxy-public.conf /etc/nginx/proxy-public.conf
COPY packages/docker/config/startup.html /usr/share/nginx/html/startup.html
COPY packages/docker/config/error.html /usr/share/nginx/html/error.html
COPY packages/interface/public/logo.png /usr/share/nginx/html/logo.png
COPY packages/docker/config/entrypoint.sh /entrypoint.sh
COPY packages/docker/config/log-prefix.sh /usr/local/bin/log-prefix
COPY packages/docker/config/valkey-startup.sh /usr/local/bin/valkey-startup
COPY packages/docker/config/logrotate.conf /etc/logrotate.d/chaoscypher
RUN chmod +x /entrypoint.sh /usr/local/bin/log-prefix /usr/local/bin/valkey-startup

# Set up Nginx config layout.
# /etc/nginx/conf.d/default.conf is a static root-owned file that just
# delegates to /run/chaoscypher/nginx-active.conf, a symlink in the
# tmpfs-style runtime dir. nginx-ready (running as appuser) replaces
# nginx-active.conf to switch from startup → http/https without ever touching conf.d.
# The edge-auth token snippet + valkey ACL + rendered valkey-args also live in /run/chaoscypher.
RUN rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf && \
    printf 'include /run/chaoscypher/nginx-active.conf;\n' > /etc/nginx/conf.d/default.conf

# Create non-root user and directories.
# /etc/nginx/conf.d stays fully root-owned. /run/chaoscypher is owned
# appuser:appuser mode 0700: nginx-ready (appuser) needs to rewrite the
# nginx-active.conf symlink, valkey-startup (appuser) needs to write the
# ACL file, and entrypoint (root) writes here too (root bypasses DAC).
# The dir is recreated fresh on every container boot — never persisted.
# /data/secrets is created by the entrypoint at runtime (not here) because
# it needs root:appuser ownership the volume mount would clobber.
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/bash -m appuser && \
    mkdir -p /data /data/databases && \
    chown -R appuser:appuser /data && \
    chown -R appuser:appuser /app/static && \
    install -d -o appuser -g appuser -m 0700 /run/chaoscypher && \
    printf 'set $chaoscypher_edge_auth_token "";\n' > /run/chaoscypher/edge-auth-token-var.conf && \
    ln -s /etc/nginx/nginx-startup.conf /run/chaoscypher/nginx-active.conf && \
    chown appuser:appuser /run/chaoscypher/edge-auth-token-var.conf && \
    chown -h appuser:appuser /run/chaoscypher/nginx-active.conf && \
    chmod 644 /run/chaoscypher/edge-auth-token-var.conf && \
    chown -R appuser:appuser /var/lib/nginx && \
    chown -R appuser:appuser /var/log/nginx && \
    touch /run/nginx.pid && chown appuser:appuser /run/nginx.pid && \
    sed -i 's/^user www-data;/# user www-data;  # Disabled: running as appuser via supervisord/' /etc/nginx/nginx.conf

# Environment
# PYTHONPATH=/app is retained for parity with the rendered supervisord.conf
# (see packages/core/src/chaoscypher_core/services/orchestration/templates/
# supervisord.conf.j2), even though all Python packages now resolve via
# site-packages. /app contains only the built static frontend.
# PYTHONWARNINGS: suppress langchain_core Pydantic V1 warning (their internal compat
# code imports pydantic.v1 which doesn't exist in Python 3.14 — harmless, no fix upstream yet)
# CHAOSCYPHER_DATA_DIR: the canonical data root for the all-in-one image, and
# the mount target documented everywhere (`-v chaoscypher-data:/data`). It MUST
# be set in the image itself, not only in the compose file: without it, a bare
# `docker run` (the README's recommended quickstart) leaves it unset, and the
# storage layer splits — the search adapter writes under /data while the
# relational DB + Alembic fall back to the platformdirs default
# (~/.local/share/chaoscypher). The worker then can't find graph_templates and
# document processing stalls forever. Setting it here keeps every component on
# one data root regardless of how the container is launched.
# CHAOSCYPHER_OLLAMA_URL: the library default is http://localhost:11434
# (bare-metal installs talk to an Ollama on the same host), but inside a
# container "localhost" is the container itself — the host's Ollama lives
# at host.docker.internal. First-boot only: an existing settings.yaml
# keeps whatever URL it persisted.
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_NAME=web_ui \
    STATIC_DIR=/app/static \
    CHAOSCYPHER_DATA_DIR=/data \
    CHAOSCYPHER_OLLAMA_URL=http://host.docker.internal:11434 \
    PYTHONWARNINGS="ignore::UserWarning:langchain_core._api.deprecation"

# Copy healthcheck script (all-in-one: checks Nginx + Cortex + Valkey + workers)
COPY packages/docker/config/healthcheck.py /healthcheck.py

# Expose ports
EXPOSE 80 443

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "/healthcheck.py"]

# PID 1 (supervisord) runs as appuser — the entrypoint runs as root for
# privileged setup steps (chown of secrets, /data ownership, rendered
# nginx configs into /etc/nginx) and then re-execs supervisord under
# appuser via gosu. Per-program `user=appuser` directives in the rendered
# supervisord.conf become redundant but stay for documentation parity
# with the multi-container variants.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
