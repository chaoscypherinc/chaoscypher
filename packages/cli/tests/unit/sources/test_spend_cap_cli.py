# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The LLM spend cap is enforced on the CLI standalone extraction path.

The CLI runs extraction directly (no queue), so before this it never consulted
the spend tracker — a daily/per-source cap set in settings.yaml was silently
ignored when extracting via the CLI. ``_extract_and_finalize`` now checks the
cap before each group's LLM call (and records spend after), using the per-
database ``app.db`` counter so the same cap that guards the worker also guards
the CLI and survives across invocations.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cli.sources.service import CLISourceProcessingService
from chaoscypher_core.exceptions import LLMSpendCapExceededError
from chaoscypher_core.services.llm.spend import _reset_tracker_for_tests


_EXTRACTOR_PATH = (
    "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor"
)


@pytest.fixture(autouse=True)
def _fresh_tracker():
    _reset_tracker_for_tests()
    yield
    _reset_tracker_for_tests()


def _ctx(*, per_day: int | None, daily_already_spent: int) -> SimpleNamespace:
    adapter = MagicMock()
    adapter.get_daily_token_spend.return_value = daily_already_spent
    adapter.update_step_progress = MagicMock()
    return SimpleNamespace(
        storage_adapter=adapter,
        database_name="default",
        settings=SimpleNamespace(
            llm=SimpleNamespace(max_tokens_per_source=None, max_tokens_per_day=per_day),
        ),
    )


async def _run_extract(service: CLISourceProcessingService) -> dict:
    return await service._extract_and_finalize(
        groups_to_process=[{"combined_content": "hello world"}],
        file_id="src1",
        node_templates="",
        edge_templates="",
        entity_guidance=None,
        relationship_guidance=None,
        entity_examples=None,
        relationship_examples=None,
        entity_exclusions=None,
        domain_extraction_limits=None,
        filtering_mode=None,
        metrics_collector=MagicMock(),
        file_record={},
        detected_domain_name=None,
        forced_domain=None,
        total_groups=1,
        depth="full",
    )


@pytest.mark.asyncio
async def test_cli_extraction_blocked_when_daily_cap_reached() -> None:
    """A daily cap already at the limit raises before the LLM extractor runs."""
    service = CLISourceProcessingService.__new__(CLISourceProcessingService)
    service.ctx = _ctx(per_day=1000, daily_already_spent=1000)

    extractor = MagicMock()
    extractor.extract_single_chunk = AsyncMock()

    with (
        patch(_EXTRACTOR_PATH, MagicMock(return_value=extractor)),
        pytest.raises(LLMSpendCapExceededError) as exc,
    ):
        await _run_extract(service)

    assert exc.value.scope == "day"
    # The cap fired BEFORE any LLM call — and is not swallowed by the
    # per-group failure handler (it propagates to fail the source).
    extractor.extract_single_chunk.assert_not_awaited()
