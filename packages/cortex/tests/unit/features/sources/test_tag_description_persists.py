# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: tag description survives create + update round trip.

Service-level tests that mock the engine and assert the description kwarg
is forwarded through both create_tag and update_tag.
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
# Description round-trip tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTagDescriptionPersists:
    """Regression tests: description kwarg must be forwarded through both layers."""

    def test_create_tag_forwards_description_to_engine(self) -> None:
        """create_tag must forward description= to engine_service.create_tag."""
        engine = MagicMock()
        engine.create_tag.return_value = {
            "id": "tag-1",
            "name": "literature",
            "color": "#cc0000",
            "description": "Books, papers, articles.",
        }
        service = _make_service(engine, database_name="my_db")

        result = service.create_tag(
            name="literature",
            color="#cc0000",
            description="Books, papers, articles.",
        )

        engine.create_tag.assert_called_once_with(
            name="literature",
            color="#cc0000",
            description="Books, papers, articles.",
            database_name="my_db",
        )
        assert result["description"] == "Books, papers, articles."

    def test_update_tag_forwards_description_to_engine(self) -> None:
        """update_tag must forward description= to engine_service.update_tag."""
        engine = MagicMock()
        engine.update_tag.return_value = {
            "id": "tag-1",
            "name": "literature",
            "description": "revised description",
        }
        service = _make_service(engine)

        result = service.update_tag(tag_id="tag-1", description="revised description")

        engine.update_tag.assert_called_once_with(
            tag_id="tag-1",
            name=None,
            color=None,
            description="revised description",
        )
        assert result is not None
        assert result["description"] == "revised description"
