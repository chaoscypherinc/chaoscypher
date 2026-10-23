# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI extraction path resolves citation evidence at aggregation.

Before the 2026-10-06 fix the CLI's per-group results carried neither the
group's numbered sentences nor its small-chunk ids, so every entity citation's
``context_snippet`` was NULL and the commit looked the extraction-group index
up as a stored chunk index. ``_extract_and_finalize`` now hands both to
``aggregate_chunk_results``, which stamps ``evidence`` and ``cited_chunk_id``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cli.sources.service import CLISourceProcessingService
from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    split_into_sentences,
)


_BASE = "chaoscypher_core.services.sources.engine.extraction"


@pytest.mark.asyncio
async def test_group_sent_ref_becomes_chunk_evidence(
    mock_cli_context_with_llm: MagicMock, stub_extraction_plan: Any
) -> None:
    """S3 of a two-chunk group is the first sentence of the group's second chunk."""
    ctx = mock_cli_context_with_llm
    ctx.storage_adapter.update_step_progress = MagicMock()
    stored = {
        "c0": {"id": "c0", "content": "Anna met Pierre. Pierre bowed."},
        "c1": {"id": "c1", "content": "Anna left early."},
    }
    group_text = "Anna met Pierre. Pierre bowed.\n\nAnna left early."
    entities = [{"name": "Anna", "type": "Person", "sent_ref": "S3"}]
    relationships: list[dict[str, Any]] = []
    metrics = {"sentences": split_into_sentences(group_text), "finish_reason": "stop"}

    extractor = MagicMock()
    extractor.extract_single_chunk = AsyncMock(
        return_value=(entities, relationships, 10, 5, metrics)
    )
    extraction_service = MagicMock()
    extraction_service.finalize_distributed_extraction = AsyncMock(
        return_value={"entities": [], "relationships": []}
    )
    spend_tracker = MagicMock()
    collector = MagicMock()
    collector.attempts = []

    with (
        patch(f"{_BASE}.utils.ai_entities.AIEntityExtractor", return_value=extractor),
        patch(f"{_BASE}.service.ExtractionService", return_value=extraction_service),
        patch(
            "chaoscypher_core.services.llm.spend.get_llm_spend_tracker",
            return_value=spend_tracker,
        ),
    ):
        service = CLISourceProcessingService(ctx)
        await service._extract_and_finalize(
            groups_to_process=[{"combined_content": group_text, "small_chunk_ids": ["c0", "c1"]}],
            file_id="fid",
            plan=stub_extraction_plan,
            metrics_collector=collector,
            file_record={},
            detected_domain_name=None,
            forced_domain=None,
            total_groups=1,
            depth="full",
            stored_chunks_by_id=stored,
        )

    raw = extraction_service.finalize_distributed_extraction.call_args.kwargs["raw_entities"]
    assert raw[0]["cited_chunk_id"] == "c1"
    assert raw[0]["evidence"] == [{"text": "Anna left early.", "chunk_id": "c1", "local_index": 0}]
