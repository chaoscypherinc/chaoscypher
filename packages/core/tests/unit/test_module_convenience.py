# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for module-level search() and add_document() convenience functions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.models import EngineSearchResult, ProcessingResult


@pytest.mark.unit
@pytest.mark.core
class TestModuleLevelSearch:
    """Tests for module-level search()."""

    @pytest.mark.asyncio
    async def test_search_creates_engine_and_delegates(self, tmp_path):
        """search() creates an Engine and delegates to engine.search()."""
        from chaoscypher_core import search

        mock_results = [EngineSearchResult(label="Test", score=0.9, result_type="node", id="n1")]

        with patch("chaoscypher_core.Engine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.search = AsyncMock(return_value=mock_results)
            mock_engine.__enter__ = MagicMock(return_value=mock_engine)
            mock_engine.__exit__ = MagicMock(return_value=False)
            mock_engine_cls.return_value = mock_engine

            results = await search("quantum", data_dir=str(tmp_path))

            mock_engine.search.assert_called_once_with("quantum", limit=10, mode="hybrid")
            assert results == mock_results

    @pytest.mark.asyncio
    async def test_search_forwards_parameters(self, tmp_path):
        """search() forwards limit and mode to engine.search()."""
        from chaoscypher_core import search

        with patch("chaoscypher_core.Engine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.search = AsyncMock(return_value=[])
            mock_engine.__enter__ = MagicMock(return_value=mock_engine)
            mock_engine.__exit__ = MagicMock(return_value=False)
            mock_engine_cls.return_value = mock_engine

            await search("test", data_dir=str(tmp_path), limit=5, mode="keyword")

            mock_engine.search.assert_called_once_with("test", limit=5, mode="keyword")


@pytest.mark.unit
@pytest.mark.core
class TestModuleLevelAddDocument:
    """Tests for module-level add_document()."""

    @pytest.mark.asyncio
    async def test_add_document_creates_engine_and_delegates(self, tmp_path):
        """add_document() creates an Engine and delegates to engine.add_document()."""
        from chaoscypher_core import add_document

        mock_result = ProcessingResult(source_id="s1", nodes=["n1"], edges=[], templates=[])

        with patch("chaoscypher_core.Engine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.add_document = AsyncMock(return_value=mock_result)
            mock_engine.__enter__ = MagicMock(return_value=mock_engine)
            mock_engine.__exit__ = MagicMock(return_value=False)
            mock_engine_cls.return_value = mock_engine

            result = await add_document("paper.pdf", data_dir=str(tmp_path))

            mock_engine.add_document.assert_called_once()
            assert result.source_id == "s1"

    @pytest.mark.asyncio
    async def test_add_document_forwards_analysis_depth(self, tmp_path):
        """add_document() forwards analysis_depth parameter."""
        from chaoscypher_core import add_document

        mock_result = ProcessingResult(source_id="s1")

        with patch("chaoscypher_core.Engine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.add_document = AsyncMock(return_value=mock_result)
            mock_engine.__enter__ = MagicMock(return_value=mock_engine)
            mock_engine.__exit__ = MagicMock(return_value=False)
            mock_engine_cls.return_value = mock_engine

            await add_document("doc.pdf", data_dir=str(tmp_path), analysis_depth="quick")

            _, kwargs = mock_engine.add_document.call_args
            assert kwargs["analysis_depth"] == "quick"

    @pytest.mark.asyncio
    async def test_add_document_forwards_on_progress(self, tmp_path):
        """add_document() forwards on_progress to engine.add_document()."""
        from chaoscypher_core import add_document

        callback = MagicMock()
        mock_result = ProcessingResult(source_id="s1")

        with patch("chaoscypher_core.Engine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.add_document = AsyncMock(return_value=mock_result)
            mock_engine.__enter__ = MagicMock(return_value=mock_engine)
            mock_engine.__exit__ = MagicMock(return_value=False)
            mock_engine_cls.return_value = mock_engine

            await add_document("doc.pdf", data_dir=str(tmp_path), on_progress=callback)

            _, kwargs = mock_engine.add_document.call_args
            assert kwargs["on_progress"] is callback


@pytest.mark.unit
@pytest.mark.core
class TestModuleLevelSearchSync:
    """Tests for module-level search_sync()."""

    def test_search_sync_delegates(self, tmp_path):
        """search_sync() calls search() synchronously."""
        from chaoscypher_core import search_sync

        mock_results = [EngineSearchResult(label="Test", score=0.9, result_type="node", id="n1")]

        with patch("chaoscypher_core.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = mock_results
            results = search_sync("quantum", data_dir=str(tmp_path))
            assert results == mock_results


@pytest.mark.unit
@pytest.mark.core
class TestModuleLevelAddDocumentSync:
    """Tests for module-level add_document_sync()."""

    def test_add_document_sync_delegates(self, tmp_path):
        """add_document_sync() calls add_document() synchronously."""
        from chaoscypher_core import add_document_sync

        mock_result = ProcessingResult(source_id="s1")

        with patch("chaoscypher_core.add_document", new_callable=AsyncMock) as mock_ad:
            mock_ad.return_value = mock_result
            result = add_document_sync("paper.pdf", data_dir=str(tmp_path))
            assert result.source_id == "s1"
