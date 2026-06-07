# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: _ABORT_TRANSITIONS collapse preserves error_message for each status."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("status_value", "expected_message"),
    [
        ("pending", "Processing aborted by user"),
        ("indexing", "Indexing aborted by user"),
        ("extracting", "Extraction aborted by user"),
        ("committing", "Commit aborted by user"),
    ],
)
def test_abort_transitions_message_per_status(status_value: str, expected_message: str) -> None:
    from chaoscypher_cortex.features.sources.service import _ABORT_TRANSITIONS

    msg = _ABORT_TRANSITIONS[status_value]
    assert msg == expected_message


def test_abort_transitions_type_is_str_or_none() -> None:
    """Type signature: dict[str, str | None] — no tuple wrapper."""
    from chaoscypher_cortex.features.sources.service import _ABORT_TRANSITIONS

    for key, value in _ABORT_TRANSITIONS.items():
        assert isinstance(key, str)
        assert value is None or isinstance(value, str)
