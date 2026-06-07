# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Smoke tests for SourceStatus.VISION_PENDING."""

from __future__ import annotations


def test_vision_pending_state_value() -> None:
    from chaoscypher_core.models import SourceStatus

    assert SourceStatus.VISION_PENDING.value == "vision_pending"


def test_vision_pending_exported_from_package_barrel() -> None:
    from chaoscypher_core import SourceStatus as Re_exported
    from chaoscypher_core.models import SourceStatus

    assert Re_exported is SourceStatus
    assert Re_exported.VISION_PENDING.value == "vision_pending"


def test_vision_pending_lifecycle_position() -> None:
    """VISION_PENDING sits between INDEXING and INDEXED in declaration order."""
    from chaoscypher_core.models import SourceStatus

    members = list(SourceStatus)
    indexing_pos = members.index(SourceStatus.INDEXING)
    vision_pending_pos = members.index(SourceStatus.VISION_PENDING)
    indexed_pos = members.index(SourceStatus.INDEXED)

    assert indexing_pos < vision_pending_pos < indexed_pos
