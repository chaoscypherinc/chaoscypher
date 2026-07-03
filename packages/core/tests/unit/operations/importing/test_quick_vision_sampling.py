# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Wave 4-5 (2026-05-23): quick-mode vision-page work-queue sampling.

The bug: the upload dialog's Quick/Full toggle wrote
``extraction_depth='quick'`` onto the source row, but the work-queue
builder in ``_apply_vision_processing`` ignored it and enqueued every
image page. A 400-page Quick import therefore burned the same vision-
LLM cost as Full, defeating the toggle.

The fix: ``_select_quick_vision_pages`` narrows the queue to cover +
N evenly-spaced body pages + last page, capped at
``LoaderSettings.vision_quick_sample_max_pages``. The skipped count
increments ``QualityCounter.VISION_PAGES_SAMPLED_QUICK_MODE`` so the
Processing tab can surface a Quick run as "Quick mode: 12 of 400
pages" rather than reading as a partial vision failure.

These tests pin the policy + counter wiring so a regression cannot
silently revert to full-page-queue behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.operations.importing import indexing_handler
from chaoscypher_core.operations.importing.indexing_handler import (
    _select_quick_vision_pages,
)
from chaoscypher_core.services.quality.counters import QualityCounter
from chaoscypher_core.vision.states import VisionPageKind


def _make_pdf_pages(n: int) -> list[dict[str, Any]]:
    """Build ``n`` synthetic PDF image-page dicts (page_number 1..n)."""
    return [
        {
            "page_number": i + 1,
            "kind": VisionPageKind.PDF_PAGE,
            "image_path": "/tmp/book.pdf",
            "doc_index": 0,
        }
        for i in range(n)
    ]


class TestSelectQuickVisionPages:
    """Pure unit tests for ``_select_quick_vision_pages``."""

    def test_returns_input_unchanged_when_under_cap(self) -> None:
        """Sample == input when the page count fits inside the cap."""
        pages = _make_pdf_pages(10)
        result = _select_quick_vision_pages(pages, cap=20)
        assert result == pages

    def test_returns_input_unchanged_when_equal_to_cap(self) -> None:
        """Boundary: cap matches len exactly — no sampling needed."""
        pages = _make_pdf_pages(20)
        result = _select_quick_vision_pages(pages, cap=20)
        assert result == pages

    def test_samples_to_cap_on_large_pdf(self) -> None:
        """400-page book + cap=20 -> 20 pages selected."""
        pages = _make_pdf_pages(400)
        result = _select_quick_vision_pages(pages, cap=20)
        assert len(result) == 20

    def test_sampling_picks_cover_and_last_page(self) -> None:
        """Policy: cover (page 1) and last page are always included."""
        pages = _make_pdf_pages(400)
        result = _select_quick_vision_pages(pages, cap=20)
        page_numbers = [p["page_number"] for p in result]
        assert page_numbers[0] == 1, "cover (page 1) must be first"
        assert page_numbers[-1] == 400, "last page must be last"

    def test_sampling_is_evenly_spaced(self) -> None:
        """Interior picks should be roughly evenly spaced through the body.

        For 400 pages with cap=20, that's 18 interior picks distributed
        across 398 interior pages — gaps should be ~20-25 pages, no
        massive cluster at the front.
        """
        from itertools import pairwise

        pages = _make_pdf_pages(400)
        result = _select_quick_vision_pages(pages, cap=20)
        page_numbers = [p["page_number"] for p in result]
        gaps = [b - a for a, b in pairwise(page_numbers)]
        # No gap should exceed 2x the average gap (~21) — sanity bound.
        # Pinning approximate bounds rather than exact spacing so the
        # underlying float-step arithmetic is free to evolve without
        # breaking the policy intent.
        assert max(gaps) <= 50, f"interior spacing too irregular: {gaps}"
        assert min(gaps) >= 1

    def test_returns_in_ascending_page_order(self) -> None:
        """Returned pages must follow the input's ascending order."""
        pages = _make_pdf_pages(400)
        result = _select_quick_vision_pages(pages, cap=20)
        page_numbers = [p["page_number"] for p in result]
        assert page_numbers == sorted(page_numbers)

    def test_no_duplicates(self) -> None:
        """A pathological step size must not select the same page twice."""
        pages = _make_pdf_pages(400)
        result = _select_quick_vision_pages(pages, cap=20)
        page_numbers = [p["page_number"] for p in result]
        assert len(page_numbers) == len(set(page_numbers))

    def test_small_cap_three_picks_cover_middle_last(self) -> None:
        """cap=3 on a long PDF -> roughly [cover, middle, last]."""
        pages = _make_pdf_pages(100)
        result = _select_quick_vision_pages(pages, cap=3)
        page_numbers = [p["page_number"] for p in result]
        assert page_numbers[0] == 1
        assert page_numbers[-1] == 100
        assert len(result) == 3
        # The middle pick should be somewhere in the interior, not at
        # either end.
        assert 1 < page_numbers[1] < 100

    def test_cap_of_zero_returns_empty(self) -> None:
        """Defensive: invalid cap=0 returns empty rather than blowing up."""
        pages = _make_pdf_pages(10)
        result = _select_quick_vision_pages(pages, cap=0)
        assert result == []

    def test_empty_input_returns_empty(self) -> None:
        """No pages -> no selection."""
        result = _select_quick_vision_pages([], cap=20)
        assert result == []

    def test_standalone_images_always_included(self) -> None:
        """Standalone images (1-per-source) bypass sampling — sampling
        would erase the only image the user uploaded.
        """
        pages = [
            {
                "page_number": 1,
                "kind": VisionPageKind.STANDALONE_IMAGE,
                "image_path": "/tmp/img.png",
                "doc_index": 0,
            },
        ]
        result = _select_quick_vision_pages(pages, cap=20)
        assert result == pages

    def test_standalone_images_combined_with_pdf_pages(self) -> None:
        """Mixed standalone + PDF: standalone is preserved, PDF is
        sampled. Standalone goes to the end so the PDF page ordering
        stays clean.
        """
        pdf_pages = _make_pdf_pages(400)
        standalone = {
            "page_number": 1,
            "kind": VisionPageKind.STANDALONE_IMAGE,
            "image_path": "/tmp/img.png",
            "doc_index": 1,
        }
        result = _select_quick_vision_pages([*pdf_pages, standalone], cap=20)
        # 20 PDF picks + 1 standalone -> 21 total.
        assert len(result) == 21
        assert result[-1] == standalone


class TestApplyVisionProcessingHonorsExtractionDepth:
    """Integration of ``_select_quick_vision_pages`` with ``_apply_vision_processing``."""

    def _build_documents_with_pages(self, n: int) -> list[dict[str, Any]]:
        """Loader-output shape: one doc with N image pages."""
        return [
            {
                "content": "...",
                "metadata": {
                    "pages": [{"page_number": i + 1, "has_images": True} for i in range(n)],
                },
            }
        ]

    def _build_engine_settings(
        self,
        tmp_path: Path,
        *,
        vision_quick_sample_max_pages: int = 20,
        vision_max_pages: int = 100_000,
    ) -> Any:
        """EngineSettings mock with the loader vision caps set.

        ``vision_max_pages`` defaults high so the full-mode fan-out ceiling
        never trips in these sampling-focused tests; the dedicated ceiling
        tests live in ``test_vision_page_ceiling.py``. ``paths.data_dir`` is
        pinned to a real path — an unpinned MagicMock stringifies into a
        literal ``<MagicMock ...>`` directory at the repo root (issue #249).
        """
        engine_settings = MagicMock()
        engine_settings.paths.data_dir = str(tmp_path)
        engine_settings.loader.vision_quick_sample_max_pages = vision_quick_sample_max_pages
        engine_settings.loader.vision_max_pages = vision_max_pages
        return engine_settings

    def _build_adapter(self) -> Any:
        adapter = MagicMock()
        adapter.create_vision_job_with_pages = MagicMock(return_value="job_xyz")
        adapter.transition_source_status = MagicMock(return_value=True)
        adapter.list_vision_page_descriptions = MagicMock(return_value=[])
        # Counter increments path: this is the adapter the
        # ``increment_quality_counter`` helper writes through.
        adapter.increment_source_counter = MagicMock()
        return adapter

    @pytest.mark.asyncio
    async def test_quick_depth_narrows_work_queue(self, monkeypatch, tmp_path: Path) -> None:
        """``extraction_depth='quick'`` -> queue has cap pages, not all 400."""
        monkeypatch.setattr(indexing_handler, "_get_active_vision_model", lambda s: "fake-vision")

        documents = self._build_documents_with_pages(400)
        engine_settings = self._build_engine_settings(tmp_path, vision_quick_sample_max_pages=20)
        adapter = self._build_adapter()

        # Patch out the queue client — we only care about
        # ``create_vision_job_with_pages``'s page count.
        async def _noop_enqueue(*args, **kwargs) -> None:
            return None

        queue_client = MagicMock()
        queue_client.enqueue_task = AsyncMock(side_effect=_noop_enqueue)
        monkeypatch.setattr(indexing_handler, "queue_client", queue_client)

        await indexing_handler._apply_vision_processing(
            documents=documents,
            file_id="src_quick",
            filepath="/tmp/book.pdf",
            enable_vision=True,
            engine_settings=engine_settings,
            database_name="default",
            data_dir="/tmp",
            adapter=adapter,
            analysis_depth="quick",
        )

        # The adapter saw exactly 20 pages enqueued.
        adapter.create_vision_job_with_pages.assert_called_once()
        kwargs = adapter.create_vision_job_with_pages.call_args.kwargs
        assert len(kwargs["pages"]) == 20

    @pytest.mark.asyncio
    async def test_full_depth_processes_every_page(self, monkeypatch, tmp_path: Path) -> None:
        """``extraction_depth='full'`` -> every image page goes to the queue."""
        monkeypatch.setattr(indexing_handler, "_get_active_vision_model", lambda s: "fake-vision")

        documents = self._build_documents_with_pages(400)
        engine_settings = self._build_engine_settings(tmp_path, vision_quick_sample_max_pages=20)
        adapter = self._build_adapter()

        queue_client = MagicMock()
        queue_client.enqueue_task = AsyncMock()
        monkeypatch.setattr(indexing_handler, "queue_client", queue_client)

        await indexing_handler._apply_vision_processing(
            documents=documents,
            file_id="src_full",
            filepath="/tmp/book.pdf",
            enable_vision=True,
            engine_settings=engine_settings,
            database_name="default",
            data_dir="/tmp",
            adapter=adapter,
            analysis_depth="full",
        )

        adapter.create_vision_job_with_pages.assert_called_once()
        kwargs = adapter.create_vision_job_with_pages.call_args.kwargs
        assert len(kwargs["pages"]) == 400
        # The Quick-mode counter must NOT have been incremented on a
        # Full run.
        for call in adapter.increment_source_counter.call_args_list:
            assert call.kwargs.get("column") != QualityCounter.VISION_PAGES_SAMPLED_QUICK_MODE.value

    @pytest.mark.asyncio
    async def test_quick_depth_increments_skipped_counter(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """The skipped count (total - sampled) must reach the QualityCounter."""
        monkeypatch.setattr(indexing_handler, "_get_active_vision_model", lambda s: "fake-vision")

        documents = self._build_documents_with_pages(400)
        engine_settings = self._build_engine_settings(tmp_path, vision_quick_sample_max_pages=20)
        adapter = self._build_adapter()

        queue_client = MagicMock()
        queue_client.enqueue_task = AsyncMock()
        monkeypatch.setattr(indexing_handler, "queue_client", queue_client)

        await indexing_handler._apply_vision_processing(
            documents=documents,
            file_id="src_quick",
            filepath="/tmp/book.pdf",
            enable_vision=True,
            engine_settings=engine_settings,
            database_name="default",
            data_dir="/tmp",
            adapter=adapter,
            analysis_depth="quick",
        )

        # 400 - 20 = 380 pages skipped.
        sampled_quick_calls = [
            call
            for call in adapter.increment_source_counter.call_args_list
            if call.kwargs.get("column") == QualityCounter.VISION_PAGES_SAMPLED_QUICK_MODE.value
        ]
        assert len(sampled_quick_calls) == 1
        assert sampled_quick_calls[0].kwargs["n"] == 380

    @pytest.mark.asyncio
    async def test_quick_depth_under_cap_no_counter_increment(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """When the PDF has fewer images than the cap, no skip happens
        and the counter stays at zero — sampling has nothing to remove.
        """
        monkeypatch.setattr(indexing_handler, "_get_active_vision_model", lambda s: "fake-vision")

        documents = self._build_documents_with_pages(10)
        engine_settings = self._build_engine_settings(tmp_path, vision_quick_sample_max_pages=20)
        adapter = self._build_adapter()

        queue_client = MagicMock()
        queue_client.enqueue_task = AsyncMock()
        monkeypatch.setattr(indexing_handler, "queue_client", queue_client)

        await indexing_handler._apply_vision_processing(
            documents=documents,
            file_id="src_small",
            filepath="/tmp/short.pdf",
            enable_vision=True,
            engine_settings=engine_settings,
            database_name="default",
            data_dir="/tmp",
            adapter=adapter,
            analysis_depth="quick",
        )

        # Every page still goes to the queue and the counter never fires.
        kwargs = adapter.create_vision_job_with_pages.call_args.kwargs
        assert len(kwargs["pages"]) == 10
        for call in adapter.increment_source_counter.call_args_list:
            assert call.kwargs.get("column") != QualityCounter.VISION_PAGES_SAMPLED_QUICK_MODE.value
