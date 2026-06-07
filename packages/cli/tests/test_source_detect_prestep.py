# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CLISourceProcessingService.detect_domain_for_source is a side-effect-free pre-step."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from chaoscypher_cli.sources.service import CLISourceProcessingService


def _ctx() -> Any:
    ctx = MagicMock()
    ctx.database_name = "default"
    ctx.storage_adapter.get_file.return_value = {
        "id": "if_testsrc12345",
        "filename": "doc.pdf",
        "status": "indexed",
        "forced_domain": None,
        "metadata": {},
    }
    ctx.storage_adapter.get_chunks_for_extraction.return_value = [
        {"content": "some sample text about networks and protocols"}
    ]
    return ctx


def test_detect_returns_recommendation_payload() -> None:
    ctx = _ctx()
    service = CLISourceProcessingService(ctx)

    fake_domain_result = {
        "domain": object(),
        "detected_domain": "technical",
        "confidence": 4.2,
        "ranking": [
            {"domain": "technical", "score": 4.2},
            {"domain": "scientific", "score": 1.1},
        ],
        "low_confidence": False,
        "entity_guidance": "",
        "relationship_guidance": "",
    }

    with patch(
        "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
        return_value=fake_domain_result,
    ):
        rec = service.detect_domain_for_source("if_testsrc12345")

    assert rec == {
        "detected_domain": "technical",
        "confidence": 4.2,
        "ranking": [
            {"domain": "technical", "score": 4.2},
            {"domain": "scientific", "score": 1.1},
        ],
        "low_confidence": False,
    }


def test_detect_does_not_move_status() -> None:
    """The pre-step must NOT call start_extraction (no EXTRACTING flip)."""
    ctx = _ctx()
    service = CLISourceProcessingService(ctx)

    fake_domain_result = {
        "domain": None,
        "detected_domain": "generic",
        "confidence": 0.1,
        "ranking": [],
        "low_confidence": True,
        "entity_guidance": "",
        "relationship_guidance": "",
    }
    with patch(
        "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
        return_value=fake_domain_result,
    ):
        rec = service.detect_domain_for_source("if_testsrc12345")

    ctx.storage_adapter.start_extraction.assert_not_called()
    assert rec["low_confidence"] is True
    assert rec["ranking"] == []


def test_detect_returns_none_when_no_chunks() -> None:
    ctx = _ctx()
    ctx.storage_adapter.get_chunks_for_extraction.return_value = []
    service = CLISourceProcessingService(ctx)

    assert service.detect_domain_for_source("if_testsrc12345") is None
