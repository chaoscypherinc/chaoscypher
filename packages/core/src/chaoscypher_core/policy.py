# packages/core/src/chaoscypher_core/policy.py
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Hardcoded security/protocol policy constants.

These values are NOT operator-tunable. They live here (rather than in
``chaoscypher_core.settings``) because they encode invariants that the
operator cannot safely override:

- **Auth field constraints** — bounded by hash algorithm and HTTP-header limits.
- **Queue / operation name caps** — bounded by Valkey key-length conventions
  and log-readability concerns.
- **Database / role name caps** — bounded by SQLite identifier conventions.
- **Time conversion constants** — physical, not configurable.

Operator-tunable lengths (chat content, pause reason, source titles, etc.)
live in ``chaoscypher_core.settings`` and are enforced via
``chaoscypher_core.utils.settings_validators.max_length_from_settings``.

If you find yourself wanting to *change* one of these constants for a
deployment, you almost certainly want a settings field instead. Talk to
the team first.
"""

# ---- Auth (LocalAuthSettings consumers) ---------------------------------
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 64
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
API_KEY_NAME_MAX_LENGTH = 64

# ---- Queue / operation identifiers --------------------------------------
QUEUE_NAME_MAX_LENGTH = 50
OPERATION_NAME_MAX_LENGTH = 100

# ---- Database / role identifiers ----------------------------------------
DATABASE_NAME_MAX_LENGTH = 100
CHAT_ROLE_MAX_LENGTH = 50

# ---- Time / unit conversion ---------------------------------------------
SECONDS_PER_DAY = 86_400
SECONDS_PER_HOUR = 3_600
BYTES_PER_KIB = 1_024
BYTES_PER_MIB = 1_048_576
BYTES_PER_GIB = 1_073_741_824

# ---- Worker task-timeout clamp ------------------------------------------
# Bounds an operator's ``workers.yaml`` ``timeout`` override is clamped into.
# Not operator-tunable: they encode supervisor / Valkey tolerances. They live
# here (rather than beside either consumer) because BOTH the worker that
# enforces the deadline (``chaoscypher_neuron.config.load_worker_config``) and
# the reconciler cutoff resolver that must clear it
# (``chaoscypher_core.queue.worker_timeouts``) clamp with them — drift between
# the two puts the reconciler's heartbeat-blind absolute-timeout branch back
# inside the worker's live window, which requeues running tasks.
WORKER_TIMEOUT_MIN_SECONDS = 60
WORKER_TIMEOUT_MAX_SECONDS = SECONDS_PER_DAY

# ---- Worker retry-budget clamp ------------------------------------------
# Bounds an operator's ``workers.yaml`` ``max_tries`` override is clamped
# into. Shared for the same reason as the timeout clamp above: the worker
# forwards the resolved value into its reconcile passes while Cortex's safety
# net resolves it independently, and a disagreement makes one of them
# terminally fail a task the other would still retry.
WORKER_MAX_TRIES_MIN = 1
WORKER_MAX_TRIES_MAX = 20

# ---- Backup interval presets (cortex lifespan.py) -----------------------
BACKUP_INTERVAL_PRESETS: dict[str, int] = {
    "hourly": SECONDS_PER_HOUR,
    "daily": SECONDS_PER_DAY,
    "weekly": 7 * SECONDS_PER_DAY,
}
