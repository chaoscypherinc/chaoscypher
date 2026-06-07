# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""awaiting_confirmation maps to a distinct 'awaiting input' phase.

A parked source must NOT read as a spinning 'extracting' bucket — the UI
needs to render an actionable "Confirm domain" chip, not a progress bar.
The phase is also explicitly NOT searchable-only-by-accident: a parked
source has finished indexing, so it IS searchable.
"""

from __future__ import annotations

from chaoscypher_cortex.features.sources.progress import map_status_to_progress


def test_awaiting_confirmation_maps_to_awaiting_input_phase() -> None:
    progress = map_status_to_progress("awaiting_confirmation")
    assert progress.phase == "awaiting_input"


def test_awaiting_confirmation_is_searchable() -> None:
    """Indexing is complete by the time a source parks, so RAG search works."""
    progress = map_status_to_progress("awaiting_confirmation")
    assert progress.is_searchable is True
