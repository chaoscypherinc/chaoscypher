#!/usr/bin/env bash
# Wait for the E2E app container to be healthy.
#
# Usage: ./wait-for-healthy.sh [base_url] [timeout_seconds]

set -euo pipefail

BASE_URL="${1:-http://localhost:8888}"
TIMEOUT="${2:-120}"
INTERVAL=5
ELAPSED=0

echo "Waiting for ${BASE_URL}/api/v1/health to be healthy..."

while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    if curl -sf "${BASE_URL}/api/v1/health" > /dev/null 2>&1; then
        HEALTHY=$(curl -sf "${BASE_URL}/api/v1/health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('healthy', False))" 2>/dev/null || echo "False")
        if [ "$HEALTHY" = "True" ]; then
            echo "App is healthy after ${ELAPSED}s"
            exit 0
        fi
    fi
    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))
    echo "  ...waiting (${ELAPSED}s / ${TIMEOUT}s)"
done

echo "ERROR: App did not become healthy within ${TIMEOUT}s"
exit 1
