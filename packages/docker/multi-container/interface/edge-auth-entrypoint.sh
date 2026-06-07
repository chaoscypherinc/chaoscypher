#!/bin/sh
# Load the generated edge-auth token before the nginx image renders templates.
set -eu

TOKEN_FILE="${CHAOSCYPHER_EDGE_AUTH_TOKEN_FILE:-/run/chaoscypher-edge/.edge_auth_token}"

if [ -z "${CHAOSCYPHER_EDGE_AUTH_TOKEN:-}" ]; then
    if [ ! -r "$TOKEN_FILE" ]; then
        echo "ERROR: edge auth token file is not readable: $TOKEN_FILE" >&2
        exit 1
    fi
    CHAOSCYPHER_EDGE_AUTH_TOKEN="$(cat "$TOKEN_FILE")"
    export CHAOSCYPHER_EDGE_AUTH_TOKEN
fi

exec /docker-entrypoint.sh "$@"
