# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cortex hydration: SourceResponse.stage_progress from from-attributes dict."""

from __future__ import annotations

from datetime import UTC, datetime

from chaoscypher_cortex.features.sources.models import (
    SourceResponse,
    StageProgressRecord,
)


def _base_source_dict() -> dict:
    """Minimal source dict; padding for required SourceResponse fields."""
    now = datetime.now(UTC)
    return {
        "id": "src-1",
        "database_name": "default",
        "filename": "x.pdf",
        "status": "indexing",
        "extraction_mode": None,
        "created_at": now,
        "updated_at": now,
    }


def test_stage_progress_empty_dict() -> None:
    """A source with no progress rows hydrates with an empty dict."""
    data = _base_source_dict()
    data["stage_progress"] = {}
    resp = SourceResponse.model_validate(data)
    assert resp.stage_progress == {}


def test_stage_progress_with_vision_record() -> None:
    """A source mid-vision hydrates with a typed StageProgressRecord."""
    now = datetime.now(UTC)
    data = _base_source_dict()
    data["stage_progress"] = {
        "vision": {
            "total": 184,
            "processed": 47,
            "avg_ms": 8200,
            "started_at": now,
            "last_activity": now,
            "completed_at": None,
            "extras": None,
        }
    }
    resp = SourceResponse.model_validate(data)
    assert "vision" in resp.stage_progress
    record = resp.stage_progress["vision"]
    assert isinstance(record, StageProgressRecord)
    assert record.total == 184
    assert record.processed == 47
    assert record.avg_ms == 8200
    assert record.completed_at is None


def test_stage_progress_with_mcp_extras() -> None:
    """MCP extraction's extras (preview counts) hydrate as a dict."""
    now = datetime.now(UTC)
    data = _base_source_dict()
    data["stage_progress"] = {
        "mcp_extraction": {
            "total": 45,
            "processed": 12,
            "avg_ms": None,
            "started_at": now,
            "last_activity": now,
            "completed_at": None,
            "extras": {"entities_preview": 312, "relationships_preview": 198},
        }
    }
    resp = SourceResponse.model_validate(data)
    assert resp.stage_progress["mcp_extraction"].extras == {
        "entities_preview": 312,
        "relationships_preview": 198,
    }
