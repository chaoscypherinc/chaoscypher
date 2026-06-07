# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Top-cited-entities limit resolution for ``get_source_stats`` (Task C6).

``get_source_stats`` gains an explicit ``top_cited_entities_limit`` parameter.
``None`` resolves to ``QualitySettings().top_cited_entities_limit`` (the class
default), not the app singleton — the SQLite adapter holds no settings object.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.settings import QualitySettings


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_dir = tmp_path / "cc-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def _seed_with_distinct_entities(adapter: SqliteAdapter, count: int) -> None:
    adapter.create_source(
        {
            "id": "src-1",
            "database_name": "test",
            "filename": "src-1.pdf",
            "filepath": "/tmp/src-1.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": "hash-src-1",
            "status": "committed",
            "total_content_length": 100,
        }
    )
    citations = [
        {
            "id": f"cit-{i}",
            "database_name": "test",
            "entity_uri": f"uri-{i}",
            "entity_label": f"Entity {i}",
            "entity_type": "Person",
            "source_id": "src-1",
            "chunk_id": None,
            "confidence": 0.9,
            "extraction_method": "llm",
        }
        for i in range(count)
    ]
    adapter.create_citations_batch(citations)


def test_explicit_limit_caps_top_entities(adapter: SqliteAdapter) -> None:
    """An explicit limit caps the ``top_entities`` list length."""
    _seed_with_distinct_entities(adapter, count=15)
    stats = adapter.get_source_stats("src-1", top_cited_entities_limit=3)
    assert len(stats["top_entities"]) == 3


def test_none_uses_class_default_not_singleton(adapter: SqliteAdapter) -> None:
    """``None`` uses the class default limit, ignoring a poisoned singleton."""
    _seed_with_distinct_entities(adapter, count=15)

    poisoned = MagicMock()
    poisoned.quality.top_cited_entities_limit = 2

    with patch("chaoscypher_core.app_config.get_settings", return_value=poisoned):
        stats = adapter.get_source_stats("src-1")

    default_limit = QualitySettings().top_cited_entities_limit
    assert len(stats["top_entities"]) == default_limit
    # Sanity: class default differs from the poisoned singleton value.
    assert default_limit == 10
    assert default_limit != 2
