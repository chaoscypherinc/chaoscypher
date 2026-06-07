# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for vision state enums."""

from chaoscypher_core.vision.states import VisionPageKind, VisionPageStatus


def test_vision_page_kind_values() -> None:
    assert VisionPageKind.PDF_PAGE.value == "pdf_page"
    assert VisionPageKind.STANDALONE_IMAGE.value == "standalone_image"


def test_vision_page_status_values() -> None:
    assert VisionPageStatus.PENDING.value == "pending"
    assert VisionPageStatus.SUCCEEDED.value == "succeeded"
    assert VisionPageStatus.FAILED.value == "failed"
    assert VisionPageStatus.TRUNCATED.value == "truncated"


def test_vision_page_status_string_inheritance() -> None:
    """StrEnum members must compare equal to their string values."""
    assert VisionPageStatus.PENDING == "pending"  # type: ignore[comparison-overlap]
    assert "pending" == VisionPageStatus.PENDING  # type: ignore[comparison-overlap]  # noqa: SIM300


def test_vision_page_status_terminal_set() -> None:
    """All non-pending statuses count as terminal."""
    terminal = {
        VisionPageStatus.SUCCEEDED,
        VisionPageStatus.FAILED,
        VisionPageStatus.TRUNCATED,
    }
    assert VisionPageStatus.PENDING not in terminal
