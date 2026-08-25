# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Effective per-queue worker timeout — one source of truth for the reconciler.

A worker's real per-task deadline is the operator's ``workers.yaml`` override
(clamped to the safe range), NOT ``TimeoutSettings.*_worker_default``. The two
diverge the moment anyone raises a worker timeout, and that divergence is a
duplicate-dispatch bug rather than a cosmetic one:

- ``reconcile_queue``'s absolute-timeout branch is deliberately heartbeat-blind
  (an event-loop hang keeps the heartbeat firing while the work stalls), so a
  perfectly healthy long task is judged purely on ``started_at`` vs the cutoff.
- Its requeue path, ``requeue_atomic.lua``, refuses only ``completed`` and
  ``cancelled``. A ``running`` task is reset to ``queued`` and re-added to
  pending, where a second worker claims it and runs the same handler again.

So any cutoff shorter than the deadline the worker actually enforces silently
duplicates live work. This module resolves that effective deadline for every
reconciler call site (worker startup, worker periodic loop, Cortex safety net,
admin API) and adds the safety margin that keeps the reconciler from racing the
worker's own ``asyncio.wait_for`` at the exact instant it fires — ``started_at``
is stamped immediately before that wait, so an unmargined cutoff expires
simultaneously with it.

The invariant, exactly::

    cutoff = max(clamped workers.yaml value, settings default) + margin
    unreadable workers.yaml -> policy.WORKER_TIMEOUT_MAX_SECONDS + margin

Both asymmetries err LONG, deliberately, because this file is re-read on every
pass while a worker's deadline is pinned at process start — the two can
disagree, and only one direction of disagreement is dangerous:

- **Floored at the settings default.** An operator who *lowers* the value
  without restarting workers leaves the worker enforcing the old, higher number
  while the file reports the new, lower one. The floor bounds that skew to
  exactly the pre-fix cutoff, so this module is never *less* safe than the bare
  settings default it replaced. It does not eliminate the window: a value
  reverted to something still above the default (7200 -> 5400 while the worker
  enforces 7200) leaves a real gap that only a worker-reported deadline could
  close. The worker's own reconcile passes are immune — they use the timeout
  the worker actually booted with.
- **Unreadable file fails to the clamp ceiling, not the default.** If the file
  cannot be located, read, or parsed, we cannot know what the worker booted
  with; the ceiling is the largest deadline it could legally be enforcing.
  Failing to the default here would requeue live work precisely when we are
  least able to tell that we are doing it.

So the uncached read is a trade, not a free win: it picks up an operator's
*raise* without a Cortex restart, at the cost of the lowered-value window above.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

from chaoscypher_core import policy
from chaoscypher_core.constants import QUEUE_LLM, QUEUE_OPERATIONS


logger = structlog.get_logger(__name__)

# Grace period added on top of the worker's deadline before the reconciler will
# touch a task. Covers the worker's own timeout handling (cancel the handler,
# write the failed hash, SREM from running) plus clock skew between the Cortex
# and Neuron containers. Matches the margin QueueClient already applies to the
# cancel-flag TTL (llm_worker_default + 300).
RECONCILER_SAFETY_MARGIN_SECONDS = 300

# Queue name -> workers.yaml section. Mirrors the worker types Neuron loads.
WORKER_TYPE_BY_QUEUE: dict[str, str] = {
    QUEUE_LLM: "llm_worker",
    QUEUE_OPERATIONS: "operations_worker",
}


class WorkersConfigUnreadableError(RuntimeError):
    """``workers.yaml`` exists but its contents could not be determined.

    Distinct from "no override configured": an absent file, or a file with no
    usable ``timeout`` key, tells us the worker booted on its default. A file we
    cannot locate, open, or parse tells us nothing, so the caller must fail long
    rather than assume the default.
    """


def clamp_worker_timeout(value: float, *, default: int) -> int:
    """Clamp a raw worker timeout into the range the worker will accept.

    Shares ``policy.WORKER_TIMEOUT_{MIN,MAX}_SECONDS`` with
    ``chaoscypher_neuron.config.load_worker_config``, so an out-of-range value
    resolves to the same number on both sides.

    Args:
        value: Raw timeout in seconds, as read from ``workers.yaml``.
        default: Fallback used when ``value`` is not a usable number.

    Returns:
        The clamped timeout in whole seconds.
    """
    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError):  # fmt: skip
        return default
    return max(
        policy.WORKER_TIMEOUT_MIN_SECONDS,
        min(seconds, policy.WORKER_TIMEOUT_MAX_SECONDS),
    )


def read_workers_yaml_timeout(worker_type: str) -> float | None:
    """Read the operator's ``workers.yaml`` timeout override for a worker type.

    Deliberately uncached — see the module docstring for what that buys and what
    it costs. Shapes that tell us the worker booted on its default (absent file,
    non-mapping document, missing section, missing/boolean/non-numeric
    ``timeout``) return ``None``, matching Neuron's permissive handling. Shapes
    that tell us *nothing* raise, so the caller can fail long instead.

    Args:
        worker_type: ``workers.yaml`` section name, e.g. ``"llm_worker"``.

    Returns:
        The raw (unclamped) override in seconds, or ``None`` when no usable
        override is configured.

    Raises:
        WorkersConfigUnreadableError: The file could not be located, opened, or
            parsed, leaving the worker's deadline unknowable from here.
    """
    from chaoscypher_core.settings import PathSettings

    try:
        paths = PathSettings()
        config_path = Path(paths.data_dir) / paths.workers_config_filename
        if not config_path.exists():
            return None
        with config_path.open(encoding="utf-8") as handle:
            document: Any = yaml.safe_load(handle)
    except Exception as exc:
        logger.warning(
            "workers_yaml_unreadable",
            worker_type=worker_type,
            error=str(exc),
            fallback="clamp_ceiling",
        )
        msg = f"workers.yaml could not be read for {worker_type}"
        raise WorkersConfigUnreadableError(msg) from exc

    if not isinstance(document, dict):
        return None
    section = document.get(worker_type)
    if not isinstance(section, dict):
        return None
    value = section.get("timeout")
    # YAML booleans coerce silently (True == 1) — reject them like Neuron does.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def resolve_effective_worker_timeout(queue_name: str, *, default: int) -> int:
    """Return the longest per-task deadline the worker could be enforcing.

    Not simply "what the file says": the result is floored at ``default`` and
    fails to the clamp ceiling on an unreadable file, because the file is read
    live while the worker's deadline is pinned at its process start. See the
    module docstring for the full invariant and the residual window.

    Args:
        queue_name: Logical queue name (``"llm"`` or ``"operations"``).
        default: The configured default for this queue, normally
            ``settings.timeouts.<queue>_worker_default``. Doubles as the floor,
            so the derived cutoff is never shorter than the bare-settings cutoff
            this resolver replaced.

    Returns:
        The effective timeout in whole seconds.
    """
    worker_type = WORKER_TYPE_BY_QUEUE.get(queue_name)
    if worker_type is None:
        return default
    try:
        override = read_workers_yaml_timeout(worker_type)
    except WorkersConfigUnreadableError:
        # Fail LONG: the worker may legally be enforcing anything up to the
        # ceiling, and a cutoff short of that requeues live work.
        return policy.WORKER_TIMEOUT_MAX_SECONDS
    if override is None:
        return default
    # Floor at the default — a value lowered without a worker restart must never
    # pull the cutoff below where it sat before this resolver existed.
    return max(clamp_worker_timeout(override, default=default), default)


def reconciler_cutoff_seconds(effective_timeout_seconds: int | None) -> int | None:
    """Turn a worker's effective deadline into the reconciler's absolute cutoff.

    Args:
        effective_timeout_seconds: The deadline the worker enforces, or ``None``
            to leave the absolute-timeout branch disabled.

    Returns:
        The deadline plus ``RECONCILER_SAFETY_MARGIN_SECONDS``, or ``None`` when
        the caller passed ``None``.
    """
    if effective_timeout_seconds is None:
        return None
    return effective_timeout_seconds + RECONCILER_SAFETY_MARGIN_SECONDS
