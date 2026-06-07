# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the wizard no-text short-circuit (§3.1 of the wizard design).

An image-only / scanned document has no extractable text at load time, so
domain detection cannot run until *after* vision extraction (minutes later).
The wizard must not wait on that. The instant we know a doc is image-bearing
with too little text (and routes into the vision pipeline), we write a
``no_text`` ``detection_proposal`` so the wizard immediately shows "not enough
text to detect — pick a domain (defaults to generic)". The doc still flows
through the *existing* vision pipeline (VISION_PENDING early-return) — never
down the empty-content ``ValidationError`` raise path.

Decisive cases (from the task brief):
- (a) gate-eligible image-only doc (<50 chars, image-bearing) → ``no_text``
      proposal written (``no_text=True``, ``detected_domain='generic'``,
      ``low_confidence=True``, well-formed ``ranking``) AND routes to vision.
- (b) truly-empty non-image doc (<50 chars, not image-bearing) → still raises
      (covered by test_indexing_empty_content.py / test_specific_empty_errors.py;
      one explicit non-image case asserted here for the short-circuit boundary).
- (c) normal text doc (>=50 chars) → unaffected (no no_text proposal).
- (d) non-gate-eligible image-only doc (forced_domain set) → routes to vision
      but NO no_text proposal written.

Patch convention mirrors test_indexing_eager_detection.py: symbols imported
function-locally inside the handler are patched at their SOURCE module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.exceptions import ValidationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings() -> MagicMock:
    s = MagicMock()
    s.priorities.background = 50
    s.data_dir = "/tmp"
    return s


def _image_only_pdf_doc() -> dict[str, Any]:
    """A scanned/image-only PDF doc as the loader emits it pre-vision.

    Loader signals: ``total_characters`` below the indexable floor,
    ``needs_vision`` set, ``image_page_count`` > 0, plus a ``pages`` list with
    ``has_images`` so ``_apply_vision_processing`` collects image pages and
    routes the source to VISION_PENDING.
    """
    return {
        "content": "",
        "metadata": {
            "total_characters": 0,
            "needs_vision": True,
            "image_page_count": 2,
            "extraction_method": "pypdf",
            "pages": [
                {"page_number": 1, "has_images": True, "image_count": 1},
                {"page_number": 2, "has_images": True, "image_count": 1},
            ],
        },
    }


def _patch_loader(monkeypatch, documents: list[dict[str, Any]]) -> None:
    fake_registry = MagicMock()
    fake_registry.load_document.return_value = documents
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )


# ---------------------------------------------------------------------------
# (a) Gate-eligible image-only doc → no_text proposal + routes to vision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_only_gate_eligible_writes_no_text_proposal_and_routes_to_vision(
    monkeypatch, tmp_path: Path
) -> None:
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()
    # Gate-eligible: confirmation_required True, no forced_domain.
    adapter.get_source.return_value = {
        "id": "src-img-1",
        "status": "indexing",
        "confirmation_required": True,
        "forced_domain": None,
    }

    _patch_loader(monkeypatch, [_image_only_pdf_doc()])

    # Real _apply_vision_processing returns a job id for an image-only doc with
    # vision on + a model configured; emulate that so the early-return to
    # VISION_PENDING fires.
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], "vjob-1")),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    chunking_service = MagicMock()

    with patch(
        "chaoscypher_core.operations.importing.confirmation_gate.write_detection_proposal"
    ) as write_spy:
        result = await indexing_handler._run_indexing(
            file_id="src-img-1",
            file_info={"filename": "scan.pdf"},
            filepath=str(tmp_path / "scan.pdf"),
            analysis_depth="full",
            enable_normalization=True,
            enable_vision=None,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=MagicMock(),
            settings=_settings(),
            database_name="default",
        )

    # Routed to vision (NOT raised).
    assert result.get("status") == "vision_pending"
    assert result.get("queued_for_vision") is True
    # Guard never reached → no fail_indexing.
    adapter.fail_indexing.assert_not_called()

    # no_text proposal written.
    write_spy.assert_called_once()
    proposal = write_spy.call_args[0][2]  # (adapter, file_id, proposal)
    assert proposal["no_text"] is True
    assert proposal["detected_domain"] == "generic"
    assert proposal["low_confidence"] is True
    # Well-formed ranking: at least one {domain, score} entry, generic first.
    assert isinstance(proposal["ranking"], list)
    assert proposal["ranking"]
    assert proposal["ranking"][0]["domain"] == "generic"
    assert "score" in proposal["ranking"][0]


# ---------------------------------------------------------------------------
# (d) Non-gate-eligible image-only doc (forced_domain) → vision, NO proposal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_only_forced_domain_routes_to_vision_without_proposal(
    monkeypatch, tmp_path: Path
) -> None:
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()
    # NOT gate-eligible: forced_domain set.
    adapter.get_source.return_value = {
        "id": "src-img-2",
        "status": "indexing",
        "confirmation_required": True,
        "forced_domain": "technical",
    }

    _patch_loader(monkeypatch, [_image_only_pdf_doc()])
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], "vjob-2")),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    with patch(
        "chaoscypher_core.operations.importing.confirmation_gate.write_detection_proposal"
    ) as write_spy:
        result = await indexing_handler._run_indexing(
            file_id="src-img-2",
            file_info={"filename": "scan.pdf"},
            filepath=str(tmp_path / "scan.pdf"),
            analysis_depth="full",
            enable_normalization=True,
            enable_vision=None,
            adapter=adapter,
            chunking_service=MagicMock(),
            engine_settings=MagicMock(),
            settings=_settings(),
            database_name="default",
        )

    assert result.get("status") == "vision_pending"
    write_spy.assert_not_called()


@pytest.mark.asyncio
async def test_image_only_confirmation_not_required_routes_to_vision_without_proposal(
    monkeypatch, tmp_path: Path
) -> None:
    """confirmation_required=False is also non-gate-eligible → no proposal."""
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()
    adapter.get_source.return_value = {
        "id": "src-img-3",
        "status": "indexing",
        "confirmation_required": False,
        "forced_domain": None,
    }

    _patch_loader(monkeypatch, [_image_only_pdf_doc()])
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], "vjob-3")),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    with patch(
        "chaoscypher_core.operations.importing.confirmation_gate.write_detection_proposal"
    ) as write_spy:
        result = await indexing_handler._run_indexing(
            file_id="src-img-3",
            file_info={"filename": "scan.pdf"},
            filepath=str(tmp_path / "scan.pdf"),
            analysis_depth="full",
            enable_normalization=True,
            enable_vision=None,
            adapter=adapter,
            chunking_service=MagicMock(),
            engine_settings=MagicMock(),
            settings=_settings(),
            database_name="default",
        )

    assert result.get("status") == "vision_pending"
    write_spy.assert_not_called()


# ---------------------------------------------------------------------------
# Image-bearing but text-rich (mixed PDF that still routes to vision) →
# NOT a no_text doc: no proposal written at the pre-vision seam.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_bearing_but_text_rich_does_not_write_no_text_proposal(
    monkeypatch, tmp_path: Path
) -> None:
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()
    adapter.get_source.return_value = {
        "id": "src-mixed-1",
        "status": "indexing",
        "confirmation_required": True,
        "forced_domain": None,
    }

    # Image-bearing (pages with images, needs_vision) but plenty of text.
    doc = {
        "content": "x" * 500,
        "metadata": {
            "total_characters": 500,
            "needs_vision": True,
            "image_page_count": 1,
            "extraction_method": "pypdf",
            "pages": [{"page_number": 1, "has_images": True, "image_count": 1}],
        },
    }
    _patch_loader(monkeypatch, [doc])
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], "vjob-mix")),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    with patch(
        "chaoscypher_core.operations.importing.confirmation_gate.write_detection_proposal"
    ) as write_spy:
        result = await indexing_handler._run_indexing(
            file_id="src-mixed-1",
            file_info={"filename": "mixed.pdf"},
            filepath=str(tmp_path / "mixed.pdf"),
            analysis_depth="full",
            enable_normalization=True,
            enable_vision=None,
            adapter=adapter,
            chunking_service=MagicMock(),
            engine_settings=MagicMock(),
            settings=_settings(),
            database_name="default",
        )

    assert result.get("status") == "vision_pending"
    # Text-rich doc must NOT get a no_text proposal even though it has images.
    write_spy.assert_not_called()


# ---------------------------------------------------------------------------
# (b) Truly-empty NON-image doc → still raises (short-circuit must not fire).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_non_image_doc_still_raises(monkeypatch, tmp_path: Path) -> None:
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()
    adapter.get_source.return_value = {
        "id": "src-empty-1",
        "status": "indexing",
        "confirmation_required": True,
        "forced_domain": None,
    }

    # Not image-bearing: no needs_vision, no image pages, plain text loader.
    _patch_loader(
        monkeypatch,
        [{"content": "", "metadata": {"extraction_method": "read_text"}}],
    )
    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **kw: (
            "",
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )
    # Vision is a no-op (no image pages) so we fall through to the guard.
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], None)),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    with (
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.write_detection_proposal"
        ) as write_spy,
        pytest.raises(ValidationError),
    ):
        await indexing_handler._run_indexing(
            file_id="src-empty-1",
            file_info={"filename": "blank.txt"},
            filepath=str(tmp_path / "blank.txt"),
            analysis_depth="full",
            enable_normalization=True,
            enable_vision=False,
            adapter=adapter,
            chunking_service=MagicMock(),
            engine_settings=MagicMock(),
            settings=_settings(),
            database_name="default",
        )

    # No no_text proposal for a truly-empty non-image doc.
    write_spy.assert_not_called()
    assert adapter.fail_indexing.called


# ---------------------------------------------------------------------------
# (c) Normal text doc (>=50 chars) → no no_text proposal, normal flow.
# (The eager-detection proposal is a separate step; here we assert the
#  no_text short-circuit specifically does not fire.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_text_doc_writes_no_no_text_proposal(monkeypatch, tmp_path: Path) -> None:
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()
    adapter.get_source.return_value = {
        "id": "src-text-1",
        "status": "indexing",
        "confirmation_required": True,
        "forced_domain": None,
    }

    _patch_loader(
        monkeypatch,
        [{"content": "x" * 300, "metadata": {"total_characters": 300}}],
    )
    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **kw: (
            "x" * 300,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], None)),
    )
    monkeypatch.setattr(
        indexing_handler,
        "queue_embed_chunks",
        AsyncMock(return_value="tsk_e1"),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    chunking_result = MagicMock()
    chunking_result.total_small_chunks = 1
    chunking_result.total_groups = 1
    chunking_result.chunks_filtered = 0
    chunking_result.normalize_drops = 0
    chunking_result.prestrip_lines_removed = 0
    chunking_result.chunks_skipped_by_depth = 0
    chunking_service = MagicMock()
    chunking_service.create_chunks = AsyncMock(return_value=chunking_result)
    chunking_service.store_chunks = MagicMock()

    # The eager-detection block (step 1) will try to run; stub its detection so
    # any proposal it writes is the *eager* (non-no_text) kind, then assert no
    # no_text key is present on whatever was written.
    detect_result = {
        "domain": MagicMock(),
        "detected_domain": "technical",
        "confidence": 0.9,
        "ranking": [{"domain": "technical", "score": 0.9}],
        "low_confidence": False,
        "entity_guidance": "",
        "relationship_guidance": "",
    }

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.create_domain_sample_text",
            return_value="sample",
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=detect_result,
        ),
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.write_detection_proposal"
        ) as write_spy,
    ):
        result = await indexing_handler._run_indexing(
            file_id="src-text-1",
            file_info={"filename": "doc.txt"},
            filepath=str(tmp_path / "doc.txt"),
            analysis_depth="full",
            enable_normalization=False,
            enable_vision=False,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=MagicMock(),
            settings=_settings(),
            database_name="default",
        )

    assert result.get("status") == "indexing"
    # Whatever proposal was written (the eager one) must NOT be a no_text blob.
    for call in write_spy.call_args_list:
        written = call[0][2]
        assert not written.get("no_text"), "no_text short-circuit must not fire for text docs"
