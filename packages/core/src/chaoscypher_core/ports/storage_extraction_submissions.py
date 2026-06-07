# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ExtractionSubmissionStorageProtocol — storage contract for MCP chunk extraction submissions.

Split from the legacy ``ports/storage.py`` god file on 2026-04-23.
Implemented by ``chaoscypher_core.adapters.sqlite.mixins.extraction_submissions.ExtractionSubmissionsMixin``.
Rows are transient — created during extraction, cleared on finalization.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ExtractionSubmissionStorageProtocol(Protocol):
    """Storage protocol for MCP extraction partial results.

    Manages the lifecycle of raw extraction output submitted per chunk
    during MCP-driven extraction. Rows are transient -- created during
    extraction, consumed during finalization, then deleted.
    """

    def create_extraction_submission(
        self, data: dict[str, Any], database_name: str
    ) -> dict[str, Any]:
        """Create or replace a submission for a chunk group."""
        ...

    def get_extraction_submission(
        self, source_id: str, chunk_group_index: int, database_name: str
    ) -> dict[str, Any] | None:
        """Get a single submission by source and chunk index."""
        ...

    def list_extraction_submissions(
        self, source_id: str, database_name: str
    ) -> list[dict[str, Any]]:
        """List all submissions for a source, ordered by chunk_group_index."""
        ...

    def count_extraction_submissions(self, source_id: str, database_name: str) -> int:
        """Count submitted chunks for a source."""
        ...

    def delete_extraction_submissions(self, source_id: str, database_name: str) -> int:
        """Delete all submissions for a source. Returns count deleted."""
        ...
