# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Conformance tests for the 6-way source-storage Protocol split.

Phase 1 Task 12 replaced the bloated SourceStorageProtocol + SourcesProtocol
with 6 narrow Protocols. These tests verify:
- Each new Protocol is @runtime_checkable
- SqliteAdapter structurally satisfies every new Protocol
- Old protocol names are no longer importable (clean break)
- ports/storage.py no longer contains the old SourceStorageProtocol class
- The new slim SourceStorageProtocol is defined in ports/storage_sources.py
"""

from __future__ import annotations

import ast
import inspect

import pytest

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.ports.storage_chunks import ChunkStorageProtocol
from chaoscypher_core.ports.storage_citations import CitationStorageProtocol
from chaoscypher_core.ports.storage_embeddings import EntityEmbeddingStorageProtocol
from chaoscypher_core.ports.storage_extraction_queue import ExtractionQueueStorageProtocol
from chaoscypher_core.ports.storage_source_tags import SourceTagStorageProtocol
from chaoscypher_core.ports.storage_sources import SourceStorageProtocol


@pytest.fixture
def adapter(tmp_path):
    """Create a live SqliteAdapter backed by a temporary database."""
    db_path = tmp_path / "test.db"
    sqlite_adapter = SqliteAdapter(db_path=str(db_path))
    try:
        yield sqlite_adapter
    finally:
        sqlite_adapter.disconnect()


# ---------------------------------------------------------------------------
# Conformance: SqliteAdapter satisfies each split Protocol
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "protocol",
    [
        SourceStorageProtocol,
        CitationStorageProtocol,
        ChunkStorageProtocol,
        EntityEmbeddingStorageProtocol,
        ExtractionQueueStorageProtocol,
        SourceTagStorageProtocol,
    ],
    ids=[
        "SourceStorageProtocol",
        "CitationStorageProtocol",
        "ChunkStorageProtocol",
        "EntityEmbeddingStorageProtocol",
        "ExtractionQueueStorageProtocol",
        "SourceTagStorageProtocol",
    ],
)
def test_sqlite_adapter_satisfies_protocol(adapter, protocol):
    """SqliteAdapter must structurally satisfy each split Protocol."""
    assert isinstance(adapter, protocol), (
        f"SqliteAdapter does not satisfy {protocol.__name__}. "
        "Check that the Protocol method signatures match the adapter's actual implementation."
    )


# ---------------------------------------------------------------------------
# Safety: each Protocol is @runtime_checkable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "protocol",
    [
        SourceStorageProtocol,
        CitationStorageProtocol,
        ChunkStorageProtocol,
        EntityEmbeddingStorageProtocol,
        ExtractionQueueStorageProtocol,
        SourceTagStorageProtocol,
    ],
    ids=[
        "SourceStorageProtocol",
        "CitationStorageProtocol",
        "ChunkStorageProtocol",
        "EntityEmbeddingStorageProtocol",
        "ExtractionQueueStorageProtocol",
        "SourceTagStorageProtocol",
    ],
)
def test_protocol_is_runtime_checkable(protocol):
    """All split Protocols must be @runtime_checkable to support isinstance.

    A Protocol without @runtime_checkable raises TypeError on isinstance.
    If isinstance works with an empty class (returning False), the decorator
    is present.
    """

    class Empty:
        """Empty class for negative isinstance test."""

    # isinstance should return False, not raise TypeError
    assert not isinstance(Empty(), protocol), (
        f"{protocol.__name__} must be @runtime_checkable. "
        "isinstance(Empty(), protocol) should return False, not raise TypeError."
    )


# ---------------------------------------------------------------------------
# Clean break: old protocol names are no longer importable
# ---------------------------------------------------------------------------


def test_old_sources_protocol_not_importable():
    """Clean break: ports/source.py is deleted; SourcesProtocol is gone."""
    with pytest.raises(ImportError):
        from chaoscypher_core.ports.source import SourcesProtocol  # noqa: F401


def test_old_sources_protocol_not_in_ports_init():
    """SourcesProtocol must not be re-exported from ports/__init__.py."""
    import chaoscypher_core.ports as ports_module

    assert not hasattr(ports_module, "SourcesProtocol"), (
        "SourcesProtocol must not be re-exported from chaoscypher_core.ports after Task 12."
    )


# ---------------------------------------------------------------------------
# AST checks: storage.py no longer defines the old SourceStorageProtocol
# ---------------------------------------------------------------------------


def test_ports_storage_no_longer_exists():
    """ports/storage.py god file was deleted in Task 10 (Workstream A).

    All seven Protocols it once housed now live in their own
    storage_<domain>.py files. The module must not be importable at all —
    a missing-module error is a stronger assertion than 'class absent'.
    """
    with pytest.raises(ImportError):
        import chaoscypher_core.ports.storage  # noqa: F401


def test_new_source_storage_protocol_lives_in_storage_sources():
    """The slim SourceStorageProtocol is defined in ports/storage_sources.py."""
    import chaoscypher_core.ports.storage_sources as source_crud_mod

    tree = ast.parse(inspect.getsource(source_crud_mod))
    class_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    assert "SourceStorageProtocol" in class_names, (
        "SourceStorageProtocol must be defined in ports/storage_sources.py. "
        f"Found class definitions: {class_names}"
    )


# ---------------------------------------------------------------------------
# Re-export check: the 6 new protocols are accessible from ports/__init__
# ---------------------------------------------------------------------------


def test_all_six_protocols_exported_from_ports():
    """All 6 split Protocols must be accessible from chaoscypher_core.ports."""
    import chaoscypher_core.ports as ports_module

    expected = [
        "SourceStorageProtocol",
        "CitationStorageProtocol",
        "ChunkStorageProtocol",
        "EntityEmbeddingStorageProtocol",
        "ExtractionQueueStorageProtocol",
        "SourceTagStorageProtocol",
    ]
    for name in expected:
        assert hasattr(ports_module, name), (
            f"{name} must be exported from chaoscypher_core.ports. Check ports/__init__.py."
        )
