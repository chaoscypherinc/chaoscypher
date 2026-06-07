# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-source full-mode fan-out ceiling.

Full-mode extraction enqueues one ``OP_EXTRACT_CHUNK`` task per chunk-group
and full-mode vision enqueues one ``OP_VISION_PAGE`` task per image page.
Quick mode samples down to a small representative set, but full mode (the
default) has no per-document ceiling — a single pathological or malicious
upload can build into millions of LLM/vision tasks (cost runaway + task-queue
flood).

``enforce_source_fanout_ceiling`` is the shared hard-stop both import stages
call *before* enqueueing any work. When the fan-out would exceed the
settings-backed per-source ceiling it raises
:class:`~chaoscypher_core.exceptions.SourceFanoutLimitExceededError`, which the
queue classifies as a permanent failure: the source is marked failed with a
clear message, zero LLM spend is incurred, and the task is not retried.
"""

from __future__ import annotations

from chaoscypher_core.exceptions import SourceFanoutLimitExceededError


def enforce_source_fanout_ceiling(
    *,
    item_count: int,
    max_items: int,
    item_noun: str,
    stage: str,
    setting_path: str,
) -> None:
    """Raise when a full-mode source would fan out past the per-source ceiling.

    Args:
        item_count: Number of tasks the source would enqueue (chunk-groups
            for extraction, image pages for vision).
        max_items: The per-source ceiling from settings.
        item_noun: Plural noun for the items, used in the message
            (e.g. ``"chunk-groups"`` / ``"image pages"``).
        stage: The pipeline stage, used in the message
            (e.g. ``"extraction"`` / ``"vision"``).
        setting_path: Dotted settings path the operator can raise to lift the
            ceiling (e.g. ``"chunking.max_groups_per_source"``).

    Raises:
        SourceFanoutLimitExceededError: When ``item_count > max_items``. The
            message keeps clear of queue transient-classification keywords so
            the failure is treated as permanent (no retry).
    """
    if item_count <= max_items:
        return
    msg = (
        f"Document too large for full {stage}: {item_count} {item_noun} exceeds "
        f"the per-source ceiling of {max_items}. Split the document into smaller "
        f"files or raise {setting_path}."
    )
    raise SourceFanoutLimitExceededError(
        msg,
        details={
            "stage": stage,
            "item_count": item_count,
            "max_items": max_items,
            "setting_path": setting_path,
        },
    )
