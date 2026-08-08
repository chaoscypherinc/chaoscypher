# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``Loaders.load_text`` must join ALL documents from multi-document loaders.

Regression guard: the facade used to return ``documents[0]["content"]``,
silently dropping every later document for multi-document loaders (CSV =
one doc per row, JSONL = one per line, archive = one per member). Both
``Engine.add_document`` and the MCP file tool reach this path, so a
multi-row CSV ingested through either entry point lost every row after
the first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import structlog


if TYPE_CHECKING:
    from pathlib import Path


class TestLoadTextMultiDocument:
    """facade.load_text joins every document's content with a blank line."""

    def test_multi_row_csv_contains_every_rows_content(self, tmp_path: Path) -> None:
        """A real multi-row CSV loads with every row's content present."""
        from chaoscypher_core.services.sources.loaders.facade import Loaders

        csv_file = tmp_path / "people.csv"
        csv_file.write_text(
            "name,role\nAda Lovelace,mathematician\nAlan Turing,computer scientist\n",
            encoding="utf-8",
        )

        text = Loaders.load_text(str(csv_file))

        assert "Ada Lovelace" in text
        assert "Alan Turing" in text, (
            "load_text dropped every document after the first — multi-row CSV "
            "content must all be present in the joined text"
        )

    def test_documents_joined_with_blank_line(self, tmp_path: Path) -> None:
        """Multiple documents are joined with a blank-line separator."""
        from chaoscypher_core.services.sources.loaders.facade import Loaders

        dummy = tmp_path / "many.csv"
        dummy.write_text("stub", encoding="utf-8")

        fake_registry = MagicMock()
        fake_registry.load_document.return_value = [
            {"content": "doc one", "metadata": {}},
            {"content": "doc two", "metadata": {}},
            {"content": "doc three", "metadata": {}},
        ]

        with patch(
            "chaoscypher_core.services.sources.loaders.facade.get_loader_registry",
            return_value=fake_registry,
        ):
            text = Loaders.load_text(str(dummy))

        assert text == "doc one\n\ndoc two\n\ndoc three"

    def test_single_document_unchanged(self, tmp_path: Path) -> None:
        """The common single-document case returns the content as-is."""
        from chaoscypher_core.services.sources.loaders.facade import Loaders

        dummy = tmp_path / "one.txt"
        dummy.write_text("stub", encoding="utf-8")

        fake_registry = MagicMock()
        fake_registry.load_document.return_value = [{"content": "only doc", "metadata": {}}]

        with patch(
            "chaoscypher_core.services.sources.loaders.facade.get_loader_registry",
            return_value=fake_registry,
        ):
            text = Loaders.load_text(str(dummy))

        assert text == "only doc"

    def test_merge_logs_structured_event_with_doc_count(self, tmp_path: Path) -> None:
        """Merging >1 documents emits a structured event naming the count.

        The facade has no source row to attach a QualityCounter to, so
        the log event is the decided visibility mechanism for the merge.
        """
        from chaoscypher_core.services.sources.loaders.facade import Loaders

        dummy = tmp_path / "two.csv"
        dummy.write_text("stub", encoding="utf-8")

        fake_registry = MagicMock()
        fake_registry.load_document.return_value = [
            {"content": "a", "metadata": {}},
            {"content": "b", "metadata": {}},
        ]

        with (
            patch(
                "chaoscypher_core.services.sources.loaders.facade.get_loader_registry",
                return_value=fake_registry,
            ),
            structlog.testing.capture_logs() as logs,
        ):
            Loaders.load_text(str(dummy))

        merge_events = [e for e in logs if e["event"] == "load_text_documents_merged"]
        assert len(merge_events) == 1
        assert merge_events[0]["document_count"] == 2

    def test_single_document_does_not_log_merge_event(self, tmp_path: Path) -> None:
        """No merge event for the common single-document case."""
        from chaoscypher_core.services.sources.loaders.facade import Loaders

        dummy = tmp_path / "one.txt"
        dummy.write_text("stub", encoding="utf-8")

        fake_registry = MagicMock()
        fake_registry.load_document.return_value = [{"content": "only doc", "metadata": {}}]

        with (
            patch(
                "chaoscypher_core.services.sources.loaders.facade.get_loader_registry",
                return_value=fake_registry,
            ),
            structlog.testing.capture_logs() as logs,
        ):
            Loaders.load_text(str(dummy))

        assert not [e for e in logs if e["event"] == "load_text_documents_merged"]
