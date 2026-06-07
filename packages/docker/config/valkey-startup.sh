#!/bin/bash
# Valkey startup script with safe AOF handling.
#
# Uses --appendfsync always to prevent AOF corruption from incomplete
# writes during container shutdown. Combined with --aof-load-truncated
# yes, Valkey itself handles any edge-case trailing bytes gracefully.
#
# The repair step only runs if the AOF manifest exists and only nukes
# files as a last resort if Valkey cannot load them at all.

set -euo pipefail

AOF_DIR="/data/queue/appendonlydir"
MANIFEST="${AOF_DIR}/appendonly.aof.manifest"

repair_aof() {
    if [ ! -f "$MANIFEST" ]; then
        return 0
    fi

    echo "[valkey-startup] AOF manifest found, validating..."

    # --fix only truncates genuinely corrupted entries; with appendfsync
    # always there should be nothing to fix under normal operation.
    # Pipe 'yes' to auto-confirm prompts. Ignore SIGPIPE from 'yes' when
    # valkey-check-aof exits before reading all input (pipefail would
    # otherwise treat the broken pipe as a failure).
    if (yes 2>/dev/null || true) | valkey-check-aof --fix "$MANIFEST" 2>&1; then
        echo "[valkey-startup] AOF check passed"
        return 0
    fi

    echo "[valkey-startup] AOF repair failed, removing corrupted AOF directory"
    rm -rf "$AOF_DIR"
    touch /run/chaoscypher/valkey_was_wiped
    echo "[valkey-startup] Clean slate — Valkey will create fresh AOF files"
}

repair_aof

# Read CLI args rendered by chaoscypher_core.services.orchestration.
# (The entrypoint moves the renderer's output to /run/chaoscypher/valkey-args.txt.)
VALKEY_ARGS_FILE="/run/chaoscypher/valkey-args.txt"
if [ ! -f "$VALKEY_ARGS_FILE" ]; then
    echo "[valkey-startup] FATAL: $VALKEY_ARGS_FILE not found — entrypoint did not render configs"
    exit 1
fi

# Generate an ACL file that combines authentication (replacing
# --requirepass) with command restrictions on the default user. This
# avoids a startup race we observed where ACL SETUSER issued via
# valkey-cli immediately after server start would not persist through
# AOF base-file creation (the OK return was observed but subsequent
# reads showed +@all again). Embedding the restrictions directly in
# the ACL file applies them atomically at startup with no race window.
#
# Long-term: this should be rendered from ValkeySettings in
# chaoscypher_core.services.orchestration (same pattern as rate
# limiting moved out of env vars and into RateLimitSettings).
#
# Writes into /run/chaoscypher/ — the ephemeral runtime dir
# (root:appuser, mode 755) shared with nginx and recreated on each boot.
ACL_FILE="/run/chaoscypher/valkey-users.acl"
PASSWORD_HASH=$(printf '%s' "$QUEUE_PASSWORD" | sha256sum | cut -d' ' -f1)
(
    umask 077
    cat > "$ACL_FILE" <<EOF
user default on #${PASSWORD_HASH} ~* &* +@all -flushall -flushdb -config -shutdown -slaveof -replicaof -debug
EOF
)

# Append env-driven args that the renderer cannot inject (Jinja doesn't
# process shell $VAR / ${VAR} — those are shell-only). The rendered
# args file expects this wrapper to add the auth/ACL config and
# --loglevel.
# shellcheck disable=SC2046
exec valkey-server $(cat "$VALKEY_ARGS_FILE") \
    --aclfile "$ACL_FILE" \
    --loglevel "${VALKEY_LOGLEVEL:-notice}" \
    2>&1 | log-prefix valkey
