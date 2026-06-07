# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-source full-mode fan-out ceiling guard.

Cost / resource-exhaustion fix (2026-05-25 review pass 2): full-mode
extraction enqueues one OP_EXTRACT_CHUNK task per chunk-group and full-mode
vision enqueues one OP_VISION_PAGE task per image page, with no per-document
ceiling. A single pathological upload can explode into millions of LLM/vision
tasks. ``enforce_source_fanout_ceiling`` is the shared hard-stop both stages
call before enqueueing any work: when the item count exceeds the per-source
ceiling it raises ``SourceFanoutLimitExceededError`` (a permanent failure —
the source is failed, zero LLM spend, no retry storm).
"""

from __future__ import annotations

import pytest

from chaoscypher_core.exceptions import SourceFanoutLimitExceededError
from chaoscypher_core.operations.importing.fanout_limits import (
    enforce_source_fanout_ceiling,
)


class TestEnforceSourceFanoutCeiling:
    def test_under_ceiling_is_noop(self) -> None:
        """Below the cap: no raise."""
        enforce_source_fanout_ceiling(
            item_count=9_999,
            max_items=10_000,
            item_noun="chunk-groups",
            stage="extraction",
            setting_path="chunking.max_groups_per_source",
        )

    def test_at_ceiling_is_noop(self) -> None:
        """Boundary: equal to the cap is allowed (only > trips)."""
        enforce_source_fanout_ceiling(
            item_count=10_000,
            max_items=10_000,
            item_noun="chunk-groups",
            stage="extraction",
            setting_path="chunking.max_groups_per_source",
        )

    def test_over_ceiling_raises(self) -> None:
        """One over the cap raises the dedicated exception."""
        with pytest.raises(SourceFanoutLimitExceededError):
            enforce_source_fanout_ceiling(
                item_count=10_001,
                max_items=10_000,
                item_noun="chunk-groups",
                stage="extraction",
                setting_path="chunking.max_groups_per_source",
            )

    def test_message_names_counts_and_setting(self) -> None:
        """The operator-facing message must carry the actual counts and the
        setting to raise — that is the whole point of a clear hard-stop.
        """
        with pytest.raises(SourceFanoutLimitExceededError) as exc_info:
            enforce_source_fanout_ceiling(
                item_count=2_001,
                max_items=2_000,
                item_noun="image pages",
                stage="vision",
                setting_path="loader.vision_max_pages",
            )
        msg = str(exc_info.value)
        assert "2001" in msg
        assert "2000" in msg
        assert "image pages" in msg
        assert "loader.vision_max_pages" in msg

    def test_message_has_no_transient_keywords(self) -> None:
        """The queue classifies unknown errors by message keywords; the
        message must not contain a transient keyword (``connection``,
        ``timeout``, ``reset``, ...) or the worker would retry a permanent
        condition. Defaults to permanent, but pin it so the message can't
        drift into a transient word.
        """
        transient_keywords = (
            "connection",
            "connect",
            "timeout",
            "timed out",
            "network",
            "unreachable",
            "refused",
            "reset",
            "temporarily unavailable",
        )
        with pytest.raises(SourceFanoutLimitExceededError) as exc_info:
            enforce_source_fanout_ceiling(
                item_count=10_001,
                max_items=10_000,
                item_noun="chunk-groups",
                stage="extraction",
                setting_path="chunking.max_groups_per_source",
            )
        msg = str(exc_info.value).lower()
        assert not any(kw in msg for kw in transient_keywords)
