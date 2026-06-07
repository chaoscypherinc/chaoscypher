#!/bin/bash
# Chaos Cypher Docker Entrypoint
# Ensures /data directory structure exists and is writable before starting the app.
# Works in both all-in-one and multi-container deployments.
#
# The Dockerfiles create a non-root 'appuser' (uid 1000) and /data owned by that user.
# Docker named volumes initialize ownership from the container filesystem on first use,
# so /data will be correctly owned when the volume is created.

set -e

DATA_DIR="/data"

# Verify /data is writable (catches misconfigured volumes or stale permissions)
if ! touch "${DATA_DIR}/.write_test" 2>/dev/null; then
    echo ""
    echo "ERROR: ${DATA_DIR} is not writable by uid=$(id -u)."
    echo ""
    echo "This usually means the Docker volume has stale root ownership."
    echo "Fix by removing and recreating the volume:"
    echo ""
    echo "  docker compose down"
    echo "  docker volume rm chaoscypher_app-data"
    echo "  docker compose up --build"
    echo ""
    exit 1
fi
rm -f "${DATA_DIR}/.write_test"

# Create required subdirectories
mkdir -p "${DATA_DIR}/databases"
mkdir -p "${DATA_DIR}/logs"
mkdir -p "${DATA_DIR}/mcp"
mkdir -p "${DATA_DIR}/queue"
if [ "$(id -u)" = "0" ]; then
    chown -R appuser:appuser "${DATA_DIR}/logs" "${DATA_DIR}/mcp" "${DATA_DIR}/queue"
fi

# Ephemeral runtime state (nginx active config, Valkey ACL, edge-auth snippet).
# Lives under /run so it never persists across container recreation.
# appuser:appuser 0700: nginx-ready and valkey-startup both rewrite files in
# here as appuser; root (the entrypoint) bypasses DAC for its own writes.
RUNTIME_DIR="/run/chaoscypher"
if [ "$(id -u)" = "0" ]; then
    install -d -o appuser -g appuser -m 0700 "$RUNTIME_DIR"
else
    mkdir -p "$RUNTIME_DIR"
fi

# ============================================================================
# Auto-generate deployment secrets on first run
# ============================================================================
# Single secrets tree at /data/secrets/. Files are root-owned with appuser
# read access (0640); supervisor_password is the sole root-only exception
# (0600) because a compromised appuser process must not be able to read it.
SECRETS_DIR="${DATA_DIR}/secrets"
TLS_DIR="${SECRETS_DIR}/tls"
if [ "$(id -u)" = "0" ]; then
    install -d -o root -g appuser -m 0750 "$SECRETS_DIR"
    # tls/ is group-writable (0770) so cortex (appuser) can write
    # server.{crt,key} from the in-app "generate self-signed" flow.
    # nginx (also appuser) reads the same files. Operators dropping in
    # their own cert via `docker cp` need to chown root:appuser after.
    install -d -o root -g appuser -m 0770 "$TLS_DIR"
fi

QUEUE_PW_FILE="${SECRETS_DIR}/queue_password"
SESSION_SECRET_FILE="${SECRETS_DIR}/session_secret"
EDGE_AUTH_TOKEN_FILE="${CHAOSCYPHER_EDGE_AUTH_TOKEN_FILE:-${SECRETS_DIR}/edge_auth_token}"
SUPER_PW_FILE="${SECRETS_DIR}/supervisor_password"

# Queue password (appuser-readable)
if [ ! -f "$QUEUE_PW_FILE" ]; then
    head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24 > "$QUEUE_PW_FILE"
    [ "$(id -u)" = "0" ] && chown root:appuser "$QUEUE_PW_FILE"
    chmod 640 "$QUEUE_PW_FILE"
    echo "  Queue password generated at ${QUEUE_PW_FILE}"
fi

# Session HMAC secret (appuser-readable; never regenerated — would invalidate cookies)
if [ ! -f "$SESSION_SECRET_FILE" ]; then
    head -c 32 /dev/urandom > "$SESSION_SECRET_FILE"
    [ "$(id -u)" = "0" ] && chown root:appuser "$SESSION_SECRET_FILE"
    chmod 640 "$SESSION_SECRET_FILE"
    echo "  Session HMAC secret generated at ${SESSION_SECRET_FILE}"
fi

# Edge auth token (appuser-readable nginx-to-Cortex trust marker)
if [ -z "$CHAOSCYPHER_EDGE_AUTH_TOKEN" ]; then
    if [ ! -f "$EDGE_AUTH_TOKEN_FILE" ]; then
        od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$EDGE_AUTH_TOKEN_FILE"
        [ "$(id -u)" = "0" ] && chown root:appuser "$EDGE_AUTH_TOKEN_FILE"
        chmod 640 "$EDGE_AUTH_TOKEN_FILE"
        echo "  Edge auth token generated at ${EDGE_AUTH_TOKEN_FILE}"
    fi
    export CHAOSCYPHER_EDGE_AUTH_TOKEN="$(cat "$EDGE_AUTH_TOKEN_FILE")"
fi

# Supervisor password (root-only — appuser cannot read or rewrite)
if [ ! -f "$SUPER_PW_FILE" ]; then
    head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24 > "$SUPER_PW_FILE"
    chown root:root "$SUPER_PW_FILE"
    chmod 600 "$SUPER_PW_FILE"
    echo "  Supervisor password generated at ${SUPER_PW_FILE}"
fi

# Export password env vars from files (unchanged contract for supervisord/cortex/neuron)
[ -z "$QUEUE_PASSWORD" ] && [ -f "$QUEUE_PW_FILE" ] && export QUEUE_PASSWORD="$(cat "$QUEUE_PW_FILE")"
[ -z "$SUPERVISOR_PASSWORD" ] && [ -f "$SUPER_PW_FILE" ] && export SUPERVISOR_PASSWORD="$(cat "$SUPER_PW_FILE")"

if [ -f /etc/nginx/nginx.conf ]; then
    printf 'set $chaoscypher_edge_auth_token "%s";\n' "$CHAOSCYPHER_EDGE_AUTH_TOKEN" > "${RUNTIME_DIR}/edge-auth-token-var.conf"
fi

# ============================================================================
# Render orchestration configs from Pydantic settings (single source of truth)
# ============================================================================
# Replaces hand-edited static nginx/supervisord/valkey configs with output of
# chaoscypher_core.services.orchestration.render_all. Configs always reflect
# current Pydantic settings — no drift possible.
#
# We invoke the renderer via ``python -m chaoscypher_core.services.orchestration``
# so the container's boot path depends on the runtime substrate (``core``)
# rather than on the user-facing ``chaoscypher-cli`` package.  Skipped on
# hosts without core installed (e.g. minimal worker images).
#
# In the all-in-one container ``/etc/nginx/nginx.conf`` exists — when it does,
# missing core is a fatal misconfiguration: nginx will be left without
# nginx-http.conf, valkey without valkey-args.txt, and supervisord without
# its rendered service block. We fail loud rather than silently skipping.
if python -c "import chaoscypher_core.services.orchestration" >/dev/null 2>&1; then
    echo "[entrypoint] Rendering orchestration configs from Pydantic settings..."
    mkdir -p /etc/nginx/conf.d /etc/supervisor/conf.d

    CC_RENDER_DIR=$(mktemp -d)
    python -m chaoscypher_core.services.orchestration --output-dir "$CC_RENDER_DIR"

    # Move into place. The renderer wrote 6 files; route them to their consumers.
    # The all-in-one container's nginx loads the active config via the
    # /etc/nginx/conf.d/default.conf -> /run/chaoscypher/nginx-active.conf
    # symlink switched in the all-in-one section below; nginx-active.conf in turn
    # symlinks to one of /etc/nginx/nginx-http.conf or /etc/nginx/nginx-https.conf.
    # These two files MUST live at /etc/nginx/ (not /etc/nginx/conf.d/) — Debian
    # nginx auto-includes conf.d/*.conf inside http {}, which would cause
    # duplicate log_format/limit_req_zone declarations and refuse to start.
    # proxy-common.conf must also live at /etc/nginx/ to match the
    # `include /etc/nginx/proxy-common.conf;` statements inside the rendered
    # nginx-http.conf and nginx-https.conf.
    if [ -f /etc/nginx/nginx.conf ]; then
        mv "$CC_RENDER_DIR/nginx-http.conf"   /etc/nginx/nginx-http.conf
        mv "$CC_RENDER_DIR/nginx-https.conf"  /etc/nginx/nginx-https.conf
        mv "$CC_RENDER_DIR/proxy-common.conf" /etc/nginx/proxy-common.conf
    else
        rm -f "$CC_RENDER_DIR/nginx-http.conf" \
              "$CC_RENDER_DIR/nginx-https.conf" \
              "$CC_RENDER_DIR/proxy-common.conf"
    fi

    # supervisord.conf overwrites the build-time stub at the same path so the
    # Dockerfile CMD (supervisord -c /etc/supervisor/conf.d/supervisord.conf)
    # continues to work unchanged.
    mv "$CC_RENDER_DIR/supervisord.conf"  /etc/supervisor/conf.d/supervisord.conf
    mv "$CC_RENDER_DIR/valkey-args.txt"   "${RUNTIME_DIR}/valkey-args.txt"

    # multi-interface-nginx.conf is for the multi-container interface deployment;
    # the all-in-one container does not consume it. Drop it.
    rm -f "$CC_RENDER_DIR/multi-interface-nginx.conf"
    rmdir "$CC_RENDER_DIR"

    echo "[entrypoint] Orchestration configs rendered to /etc/nginx/, /run/chaoscypher/, /etc/supervisor/conf.d/"
elif [ -f /etc/nginx/nginx.conf ]; then
    echo "[entrypoint] FATAL: chaoscypher_core.services.orchestration is not importable" >&2
    echo "[entrypoint] but nginx is present (all-in-one mode). nginx-http.conf and" >&2
    echo "[entrypoint] valkey-args.txt cannot be rendered; container would boot into" >&2
    echo "[entrypoint] a broken state. Refusing to continue." >&2
    exit 1
fi

# ============================================================================
# All-in-one mode: service configuration
# ============================================================================
# Only runs when Nginx is present (all-in-one container).
# Multi-container deployments skip this section entirely.
#
# Log levels are decoupled:
#   LOG_LEVEL           → cortex + neuron (application, hot-reloadable via UI)
#   NGINX_LOGLEVEL      → nginx error log level (default: warn)
#   NGINX_ACCESS_LOG    → nginx access log on/off (default: off)
#   VALKEY_LOGLEVEL     → valkey log level (default: warning)
#   (Rate limiting moved to RateLimitSettings.enabled in settings.yaml)
#   SUPERVISOR_LOGLEVEL → supervisord log level (default: warn)
# ============================================================================

if [ -f /etc/nginx/nginx.conf ]; then
    # Generate Diffie-Hellman parameters only when TLS certs are present.
    # Persisted alongside the cert on the data volume so generation happens at most once.
    if [ -f "${TLS_DIR}/server.crt" ] && [ -f "${TLS_DIR}/server.key" ]; then
        DH_PERSIST="${TLS_DIR}/dhparam.pem"
        DH_LINK="/etc/nginx/dhparam.pem"
        if [ ! -f "$DH_PERSIST" ]; then
            echo "Generating DH parameters (2048-bit)... this may take a moment."
            openssl dhparam -out "$DH_PERSIST" 2048
            [ "$(id -u)" = "0" ] && chown root:appuser "$DH_PERSIST"
            chmod 644 "$DH_PERSIST"
        fi
        ln -sf "$DH_PERSIST" "$DH_LINK"
    fi

    # Application log level (cortex + neuron only)
    export LOG_LEVEL="${LOG_LEVEL:-INFO}"
    export USE_JSON_LOGGING="${USE_JSON_LOGGING:-false}"

    # Infrastructure log levels — independent of LOG_LEVEL, production-quiet defaults
    export NGINX_LOGLEVEL="${NGINX_LOGLEVEL:-warn}"
    export NGINX_ACCESS_LOG="${NGINX_ACCESS_LOG:-off}"
    export VALKEY_LOGLEVEL="${VALKEY_LOGLEVEL:-warning}"
    export SUPERVISOR_LOGLEVEL="${SUPERVISOR_LOGLEVEL:-warn}"

    # Validate NGINX_LOGLEVEL to prevent config corruption
    case "$NGINX_LOGLEVEL" in
        debug|info|notice|warn|error|crit|alert|emerg) ;;
        *) NGINX_LOGLEVEL="warn" ;;
    esac

    # Configure Nginx error log level
    sed -i "s|error_log.*|error_log /dev/stderr ${NGINX_LOGLEVEL};|" /etc/nginx/nginx.conf

    # Configure Nginx access log
    if [ "$NGINX_ACCESS_LOG" = "off" ]; then
        sed -i 's|^\([[:space:]]\+\)access_log.*|\1access_log off;|' /etc/nginx/nginx.conf
    fi

    # Start with the static startup page config (no proxy_pass, no upstream errors).
    # nginx-ready switches nginx-active.conf → full proxy config once Cortex is listening.
    # conf.d/default.conf is a static root-owned include; only the runtime symlink
    # /run/chaoscypher/nginx-active.conf is appuser-writable.
    ln -sf /etc/nginx/nginx-startup.conf "${RUNTIME_DIR}/nginx-active.conf"

    # Rate limiting is now controlled by RateLimitSettings.enabled in
    # settings.yaml — the orchestration renderer emits limit_req_zone /
    # limit_req directives only when enabled. The previous sed-based
    # NGINX_RATE_LIMITING env-var toggle has been removed.
fi

# Drop root and exec the main command (supervisord) as appuser.
# All privileged setup above has already completed; PID 1 has no need
# for root. gosu performs an exec-style replacement so supervisord
# inherits PID 1 directly. Multi-container variants drop privileges
# via their Dockerfile `USER appuser` and do not need this branch.
#
# When the entrypoint is invoked by something that is already running
# as appuser (e.g. a manual `docker exec`), skip the gosu drop —
# gosu refuses to run from a non-root context.
if [ "$(id -u)" = "0" ]; then
    # FIFO-based stdio bridge for the gosu privilege drop.
    #
    # Problem: after ``exec gosu appuser supervisord``, supervisord (now
    # appuser) cannot open ``/dev/stdout``. Docker connects PID 1's stdio
    # to a root-owned pipe; the pipe FD is inherited fine but the OPEN
    # syscall via the /dev/stdout symlink hits EACCES on the underlying
    # device. chmod on the symlink target is a no-op (Docker pipes don't
    # honor mode bits across UID boundaries). This affects both
    # supervisord's own logfile and every [program:*]'s stdout_logfile.
    #
    # Fix: create a world-writable FIFO that appuser can open, and start
    # a root-owned ``cat`` reader before the gosu drop. The reader
    # inherits root's connection to Docker's stdout, so anything written
    # to the FIFO from any UID flows through to ``docker logs``. The
    # template wires both supervisord.logfile and every program's
    # stdout_logfile to this FIFO path.
    STDOUT_FIFO=/run/chaoscypher/stdout.fifo
    rm -f "$STDOUT_FIFO" 2>/dev/null || true
    mkfifo -m 0666 "$STDOUT_FIFO"
    # cat reads from the FIFO forever; the trailing ``&`` keeps it
    # running in the background. Without ``</dev/null`` it would inherit
    # the entrypoint's stdin and could exit early on EOF; with it cat's
    # stdin is closed so it only blocks on the FIFO.
    cat "$STDOUT_FIFO" </dev/null &
    exec gosu appuser "$@"
else
    exec "$@"
fi
