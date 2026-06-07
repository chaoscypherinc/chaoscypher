# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source-local file path helpers.

Canonical path formulas for per-source artefacts stored on disk under
``<data_dir>/sources/<source_id>/``.  Placing the logic here gives a single
source of truth shared by the indexing handler (writer) and any future reader
(e.g. a re-extraction path that needs to reload original.txt).

Phase 5a (2026-05-08): adds ``get_original_text_path`` for the raw-upload
preservation feature (``preserve_original_text_for_citations``).
"""

from __future__ import annotations

from pathlib import Path


def get_original_text_path(source_id: str, data_dir: Path) -> Path:
    """Return the canonical path for a source's raw-upload text file.

    The file is written *before* normalization so it records exactly what the
    loader extracted from the user's upload.  Chunk char offsets are later
    recomputed against this text to produce accurate citation anchors.

    Layout::

        <data_dir>/sources/<source_id>/original.txt

    Args:
        source_id: The source UUID (used as the sub-directory name).
        data_dir: Application data root (``settings.data_dir``).

    Returns:
        ``Path`` object for the original-text file.  The parent directory may
        or may not exist yet; callers are responsible for ``mkdir`` before
        writing.

    """
    return Path(data_dir) / "sources" / source_id / "original.txt"
