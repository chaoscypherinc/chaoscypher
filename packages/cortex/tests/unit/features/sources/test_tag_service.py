# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for TagService.

TagService is a thin wrapper over engine SourceService for tag CRUD and
source-tag assignment. Tests verify correct delegation, argument passing,
and return-value shape.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources.tag_service import TagService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(engine: MagicMock | None = None, database_name: str = "default") -> TagService:
    """Return a TagService wired to a MagicMock engine service."""
    return TagService(engine_service=engine or MagicMock(), database_name=database_name)


# ---------------------------------------------------------------------------
# Tag CRUD
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTagCrud:
    """Tests for TagService tag CRUD delegation."""

    def test_get_tag_delegates_to_engine(self) -> None:
        """get_tag returns whatever engine.get_tag returns."""
        engine = MagicMock()
        engine.get_tag.return_value = {"id": "tag-1", "name": "Research"}
        service = _make_service(engine)

        result = service.get_tag("tag-1")

        engine.get_tag.assert_called_once_with("tag-1")
        assert result == {"id": "tag-1", "name": "Research"}

    def test_get_tag_returns_none_when_missing(self) -> None:
        """get_tag passes through None when the engine returns nothing."""
        engine = MagicMock()
        engine.get_tag.return_value = None
        service = _make_service(engine)
        assert service.get_tag("missing") is None

    def test_list_tags_returns_engine_list(self) -> None:
        """list_tags returns the engine's full list."""
        engine = MagicMock()
        engine.list_tags.return_value = [
            {"id": "t1", "name": "Alpha"},
            {"id": "t2", "name": "Beta"},
        ]
        service = _make_service(engine)

        result = service.list_tags()

        assert len(result) == 2
        assert result[0]["name"] == "Alpha"

    def test_list_tags_returns_empty_list(self) -> None:
        """list_tags returns an empty list when no tags exist."""
        engine = MagicMock()
        engine.list_tags.return_value = []
        service = _make_service(engine)
        assert service.list_tags() == []

    def test_create_tag_forwards_database_name(self) -> None:
        """create_tag forwards name, color, description, and database_name to the engine."""
        engine = MagicMock()
        engine.create_tag.return_value = {"id": "new", "name": "New Tag"}
        service = _make_service(engine, database_name="my_db")

        result = service.create_tag(name="New Tag", color="#ff0000")

        engine.create_tag.assert_called_once_with(
            name="New Tag", color="#ff0000", description=None, database_name="my_db"
        )
        assert result["id"] == "new"

    def test_update_tag_forwards_fields(self) -> None:
        """update_tag forwards tag_id, name, color, and description to the engine."""
        engine = MagicMock()
        engine.update_tag.return_value = {"id": "t1", "name": "Renamed"}
        service = _make_service(engine)

        result = service.update_tag(tag_id="t1", name="Renamed", color="#00ff00")

        engine.update_tag.assert_called_once_with(
            tag_id="t1", name="Renamed", color="#00ff00", description=None
        )
        assert result is not None
        assert result["name"] == "Renamed"

    def test_delete_tag_returns_engine_bool(self) -> None:
        """delete_tag passes through the engine boolean return value."""
        engine = MagicMock()
        engine.delete_tag.return_value = True
        service = _make_service(engine)
        assert service.delete_tag("tag-1") is True
        engine.delete_tag.assert_called_once_with("tag-1")


# ---------------------------------------------------------------------------
# Tag assignment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTagAssignment:
    """Tests for TagService tag-assignment operations."""

    def test_assign_tag_returns_true_on_success(self) -> None:
        """assign_tag returns True after calling engine.assign_tag."""
        engine = MagicMock()
        service = _make_service(engine)

        result = service.assign_tag("src-1", "tag-1")

        engine.assign_tag.assert_called_once_with("src-1", "tag-1")
        assert result is True

    def test_assign_tag_propagates_value_error(self) -> None:
        """assign_tag re-raises ValueError from engine (source/tag missing)."""
        engine = MagicMock()
        engine.assign_tag.side_effect = ValueError("source not found")
        service = _make_service(engine)
        with pytest.raises(ValueError, match="source not found"):
            service.assign_tag("missing", "tag-1")

    def test_unassign_tag_delegates_and_returns_engine_bool(self) -> None:
        """unassign_tag returns engine boolean and passes both IDs."""
        engine = MagicMock()
        engine.unassign_tag.return_value = True
        service = _make_service(engine)

        result = service.unassign_tag("src-1", "tag-1")

        engine.unassign_tag.assert_called_once_with("src-1", "tag-1")
        assert result is True

    def test_get_source_tags_returns_engine_list(self) -> None:
        """get_source_tags returns the engine's tag list."""
        engine = MagicMock()
        engine.get_source_tags.return_value = [
            {"id": "t1", "name": "Alpha"},
            {"id": "t2", "name": "Beta"},
        ]
        service = _make_service(engine)

        result = service.get_source_tags("src-1")

        engine.get_source_tags.assert_called_once_with("src-1")
        assert len(result) == 2
