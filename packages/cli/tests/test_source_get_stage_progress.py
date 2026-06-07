# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CLI source get formatter — Stages: section snapshot tests."""

from __future__ import annotations

from chaoscypher_cli.commands.source.get import _format_stage_progress


def test_no_stages_returns_none() -> None:
    assert _format_stage_progress({}) is None


def test_completed_stage_hidden() -> None:
    progress = {
        "vision": {
            "total": 184,
            "processed": 184,
            "avg_ms": 8200,
            "started_at": "2026-05-10T18:00:00Z",
            "last_activity": "2026-05-10T19:00:00Z",
            "completed_at": "2026-05-10T19:00:00Z",
            "extras": None,
        }
    }
    assert _format_stage_progress(progress) is None


def test_active_stage_renders() -> None:
    progress = {
        "vision": {
            "total": 184,
            "processed": 47,
            "avg_ms": 8200,
            "started_at": "2026-05-10T18:00:00Z",
            "last_activity": "2026-05-10T18:30:00Z",
            "completed_at": None,
            "extras": None,
        }
    }
    out = _format_stage_progress(progress)
    assert out is not None
    assert "Vision processing" in out
    assert "47" in out and "184" in out
    assert "8.2s avg" in out


def test_unknown_stage_falls_back_to_name() -> None:
    progress = {
        "my_custom_stage": {
            "total": 5,
            "processed": 3,
            "avg_ms": None,
            "started_at": "2026-05-10T18:00:00Z",
            "last_activity": "2026-05-10T18:30:00Z",
            "completed_at": None,
            "extras": None,
        }
    }
    out = _format_stage_progress(progress)
    assert out is not None
    assert "my_custom_stage" in out


def test_avg_ms_none_omits_eta() -> None:
    progress = {
        "mcp_extraction": {
            "total": 45,
            "processed": 12,
            "avg_ms": None,
            "started_at": "2026-05-10T18:00:00Z",
            "last_activity": "2026-05-10T18:30:00Z",
            "completed_at": None,
            "extras": None,
        }
    }
    out = _format_stage_progress(progress)
    assert out is not None
    assert "avg" not in out and "remaining" not in out
