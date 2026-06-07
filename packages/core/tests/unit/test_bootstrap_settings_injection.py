# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Engine methods must read their own ``self.settings``, not the app singleton.

Tier 2 config-unification (Task C3): two Engine methods in ``bootstrap.py``
reached for the process-wide app settings via
``chaoscypher_core.app_config.get_settings()``:

* ``_maybe_park_for_confirmation`` read ``extraction.domain_detection_*`` to
  sample chunk content for the fast detection proposal, and
* ``search`` read ``search.result_preview_chars`` to clip chunk previews.

Post-union both field groups live on ``EngineSettings``, so the methods must
read ``self.settings`` (the engine's own settings) instead. These tests inject
a custom value through the engine settings and assert the read honors it; they
fail before the refactor because the code consults the app singleton's default.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core import Engine


@pytest.fixture
def engine(tmp_path):
    """Create a real Engine against a temporary database directory."""
    db_dir = tmp_path / "databases" / "test"
    db_dir.mkdir(parents=True)
    eng = Engine(str(db_dir), initialize_db=True)
    yield eng
    eng.close()


@pytest.mark.asyncio
async def test_maybe_park_samples_from_engine_settings(engine) -> None:
    """``_maybe_park_for_confirmation`` must sample chunk content using the
    engine's ``extraction.domain_detection_sample_count`` / ``_sample_chars``.
    """
    engine.settings.extraction.domain_detection_sample_count = 6
    engine.settings.extraction.domain_detection_sample_chars = 30

    engine.storage_adapter = MagicMock()
    engine.storage_adapter.get_source.return_value = {
        "id": "src_1",
        "filename": "doc.pdf",
    }
    long_content = "token " * 100  # 600 chars, exceeds the 30-char window
    engine.storage_adapter.list_chunks.return_value = [
        {"id": "c1", "chunk_index": 0, "content": long_content},
    ]

    detection = {
        "detected_domain": "generic",
        "confidence": 0.5,
        "low_confidence": True,
    }

    with (
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.gate_decision",
            return_value="park",
        ),
        patch("chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"),
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.proposal_from_detection",
            return_value={
                "detected_domain": "generic",
                "confidence": 0.5,
                "ranking": [],
                "low_confidence": True,
            },
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.factory.get_domain_registry"
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=detection,
        ) as mock_detect,
    ):
        await engine._maybe_park_for_confirmation(source_id="src_1", filename="doc.pdf")

    list_kwargs = engine.storage_adapter.list_chunks.call_args.kwargs
    assert list_kwargs["limit"] == 6, (
        "_maybe_park_for_confirmation must read domain_detection_sample_count "
        "from self.settings.extraction, not the app singleton"
    )
    sample_text = mock_detect.call_args.kwargs["sample_text"]
    assert len(sample_text) == 30, (
        "sample_text must be sliced to self.settings.extraction."
        f"domain_detection_sample_chars (30), got {len(sample_text)}"
    )


@pytest.mark.asyncio
async def test_search_preview_uses_engine_settings(engine) -> None:
    """``Engine.search`` must clip chunk previews using the engine's
    ``search.result_preview_chars``, not the app singleton default.
    """
    engine.settings.search.result_preview_chars = 7

    long_content = "x" * 200
    engine.search_service = MagicMock()
    engine.search_service.keyword_search = MagicMock(
        return_value={
            "data": [
                {
                    "result_type": "chunk",
                    "score": 0.9,
                    "chunk": {
                        "id": "ch1",
                        "content": long_content,
                        "filename": "doc.pdf",
                    },
                }
            ]
        }
    )

    results = await engine.search("query", mode="keyword")

    assert len(results) == 1
    assert len(results[0].label) == 7, (
        "chunk preview label must be clipped to self.settings.search."
        f"result_preview_chars (7), got {len(results[0].label)}"
    )
    # Full content is preserved on the ``content`` field; only the label clips.
    assert results[0].content == long_content
