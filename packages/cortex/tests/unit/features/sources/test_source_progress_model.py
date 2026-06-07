# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Public 4-phase SourceProgress model that maps internal 9-state SourceStatus."""

from __future__ import annotations

import pytest

from chaoscypher_cortex.features.sources.progress import (
    SourceProgress,
    map_status_to_progress,
)


@pytest.mark.parametrize(
    ("internal_status", "expected_phase", "expected_searchable"),
    [
        ("pending", "waiting_to_index", False),
        ("indexing", "indexing", False),
        # vision_pending is the transient indexer-paused sub-state during
        # per-page vision captioning; treat it as still indexing.
        ("vision_pending", "indexing", False),
        ("indexed", "extracting", True),  # indexed == RAG-ready; enters extracting phase
        ("extracting", "extracting", True),
        ("mcp_extracting", "extracting", True),
        ("extracted", "extracting", True),  # extracted == ready to commit, still extracting phase
        ("committing", "extracting", True),
        ("committed", "ready", True),
        ("error", "waiting_to_index", False),  # error maps back to waiting (retry from start)
    ],
)
def test_map_status_to_progress(
    internal_status: str, expected_phase: str, expected_searchable: bool
) -> None:
    progress = map_status_to_progress(internal_status)
    assert progress.phase == expected_phase
    assert progress.is_searchable is expected_searchable


def test_source_progress_is_pydantic_model() -> None:
    """SourceProgress is a proper Pydantic BaseModel."""
    p = SourceProgress(phase="ready", is_searchable=True)
    assert p.phase == "ready"
    assert p.is_searchable is True


def test_source_progress_rejects_unknown_phase() -> None:
    """SourceProgress rejects phases not in the Literal type."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SourceProgress(phase="unknown_phase", is_searchable=False)  # type: ignore[arg-type]


def test_map_status_unknown_raises_value_error() -> None:
    """Unknown statuses raise ValueError — loud failure beats silent fallback.

    History: the previous contract silently mapped unknown values to
    ``waiting_to_index``. That fallback is exactly how ``vision_pending``
    shipped without a phase mapping (the recent vision pipeline added the
    enum member but nobody noticed the mapping was missing because the
    UI just showed the wrong phase instead of crashing). The current
    contract relies on the import-time exhaustiveness assertion in
    progress.py plus this loud runtime failure so any future drift
    surfaces at the call site.
    """
    with pytest.raises(ValueError, match="not a valid SourceStatus"):
        map_status_to_progress("some_future_status")
