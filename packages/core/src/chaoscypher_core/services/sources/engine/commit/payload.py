# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The commit payload: one definition of what a commit is handed.

The extraction finalizer, source recovery's re-commit and the CLI's commit
each built this dict by hand, and the copies drifted: recovery dropped
``symmetric_relationships``, so a recovered source committed its symmetric
facts unoriented (audit W2-13). Every builder goes through
:func:`build_commit_payload` now, and :data:`COMMIT_PAYLOAD_KEYS` is the key
set ``SourceCommitService.commit`` reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


COMMIT_PAYLOAD_KEYS: tuple[str, ...] = (
    "entities",
    "relationships",
    "suggested_templates",
    "suggested_edge_templates",
    "inverse_relationships",
    "symmetric_relationships",
    "create_templates",
    "auto_enable",
)
"""Every key of a commit payload, in the order the builders write them."""


def build_commit_payload(
    *,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    extraction_results: Mapping[str, Any] | None = None,
    auto_enable: bool = True,
) -> dict[str, Any]:
    """The commit payload for ``entities`` and ``relationships``.

    Args:
        entities: The final entity list.
        relationships: The final relationship list.
        extraction_results: Where the templates and the domain's relationship
            rules come from (an extraction result or a stored payload); a
            missing key, or ``None``, gives that key's empty value.
        auto_enable: Whether the committed source is enabled at once.

    Returns:
        A dict with exactly :data:`COMMIT_PAYLOAD_KEYS`.
    """
    src: Mapping[str, Any] = extraction_results or {}
    return {
        "entities": entities,
        "relationships": relationships,
        "suggested_templates": list(src.get("suggested_templates") or []),
        "suggested_edge_templates": list(src.get("suggested_edge_templates") or []),
        "inverse_relationships": dict(src.get("inverse_relationships") or {}),
        "symmetric_relationships": list(src.get("symmetric_relationships") or []),
        "create_templates": True,
        "auto_enable": auto_enable,
    }


__all__ = ["COMMIT_PAYLOAD_KEYS", "build_commit_payload"]
