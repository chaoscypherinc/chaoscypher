#!/bin/bash
# Prefix each line of stdin with a label for uniform log identification.
# Tees to a per-service log file so logs persist on disk while supervisor
# forwards stdout to docker logs via stdout_logfile=/dev/stdout.
#
# Usage: some-command 2>&1 | log-prefix [label]
# Example: valkey-server ... 2>&1 | log-prefix valkey

LABEL="${1:-unknown}"
LOG_DIR="/data/logs"

exec sed -u "s/^/[${LABEL}] /" | tee -a "${LOG_DIR}/${LABEL}.log"
