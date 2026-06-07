# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Default value for ``enable_normalization`` based on a file's content type.

Normalization strips structural noise designed for prose. CSV / TSV / JSON /
JSONL / NDJSON / XML are structured formats where that stripping damages the
data, so the default for those files is ``False``; everything else defaults
to ``True``.

This helper lives in Core (rather than the Cortex sources feature where it
originated) because the indexing handler in Core needs it to resolve the
``enable_normalization=None`` tri-state at extraction time. Putting it here
keeps the resolution logic in one place regardless of which entry path
(file-upload, URL-import, CLI) created the source row.
"""

from __future__ import annotations

from pathlib import PurePath


__all__ = ["STRUCTURED_EXTENSIONS", "resolve_normalization_default"]


STRUCTURED_EXTENSIONS: frozenset[str] = frozenset(
    {".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".xml"}
)


def resolve_normalization_default(*, filename: str) -> bool:
    """Return the per-file-type default for ``enable_normalization``.

    True if the file's extension is prose-shaped, False if structured.
    Empty / extension-less filenames default to True (treat as prose).
    """
    suffix = PurePath(filename).suffix.lower()
    return suffix not in STRUCTURED_EXTENSIONS
