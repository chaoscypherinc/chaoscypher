# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Schema tests for DocumentChunk.embedded_at field."""

import datetime

from chaoscypher_core.adapters.sqlite.models import DocumentChunk


def test_embedded_at_field_exists_and_defaults_none() -> None:
    chunk = DocumentChunk(
        id="c-1",
        database_name="default",
        source_id="src-1",
        chunk_index=0,
        content="test",
    )
    assert chunk.embedded_at is None


def test_embedded_at_accepts_datetime() -> None:
    now = datetime.datetime(2026, 4, 11, 12, 0, 0, tzinfo=datetime.UTC)
    chunk = DocumentChunk(
        id="c-1",
        database_name="default",
        source_id="src-1",
        chunk_index=0,
        content="test",
        embedded_at=now,
    )
    assert chunk.embedded_at == now
