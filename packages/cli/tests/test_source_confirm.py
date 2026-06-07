# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""cc source confirm <id> reads the proposal, persists choices, runs extraction."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.source.confirm import confirm_cmd


def _awaiting_source() -> dict[str, Any]:
    return {
        "id": "if_awaitsrc1234"[:15],
        "filename": "doc.pdf",
        "status": "awaiting_confirmation",
        "forced_domain": None,
        "extraction_depth": "full",
        "detection_proposal": {
            "ranking": [{"domain": "technical", "score": 4.2}, {"domain": "news", "score": 1.0}],
            "confidence": 4.2,
            "detected_domain": "technical",
            "low_confidence": False,
        },
    }


def _make_service() -> MagicMock:
    svc = MagicMock()
    svc.__enter__ = lambda s: s
    svc.__exit__ = MagicMock(return_value=False)
    svc.has_llm = True
    svc.get_file_status.return_value = _awaiting_source()
    svc.ctx.database_name = "default"
    svc.ctx.storage_adapter.get_file.return_value = _awaiting_source()
    svc.extract_entities.return_value = (
        {"stats": {"entities_count": 4, "relationships_count": 2, "detected_domain": "technical"}},
        {"total_calls": 1, "estimated_cost_usd": 0.0},
    )
    return svc


def test_confirm_accepts_recommended_domain_and_extracts() -> None:
    runner = CliRunner()
    svc = _make_service()

    with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
        with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
            # --yes accepts ranking[0] non-interactively.
            result = runner.invoke(confirm_cmd, ["if_awaitsrc1234"[:15], "--yes", "--quiet"])

    assert result.exit_code == 0, result.output
    # Persisted the recommended domain + flipped status back to indexed.
    svc.ctx.storage_adapter.update_file.assert_any_call(
        "if_awaitsrc1234"[:15],
        database_name="default",
        updates={"forced_domain": "technical", "status": "indexed"},
    )
    svc.extract_entities.assert_called_once()


def test_confirm_override_domain() -> None:
    runner = CliRunner()
    svc = _make_service()

    with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
        with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
            result = runner.invoke(
                confirm_cmd, ["if_awaitsrc1234"[:15], "--domain", "legal", "--yes", "--quiet"]
            )

    assert result.exit_code == 0, result.output
    svc.ctx.storage_adapter.update_file.assert_any_call(
        "if_awaitsrc1234"[:15],
        database_name="default",
        updates={"forced_domain": "legal", "status": "indexed"},
    )


def test_confirm_rejects_non_awaiting_source() -> None:
    runner = CliRunner()
    svc = _make_service()
    src = _awaiting_source()
    src["status"] = "committed"
    svc.get_file_status.return_value = src

    with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
        with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
            result = runner.invoke(confirm_cmd, ["if_awaitsrc1234"[:15], "--yes"])

    assert result.exit_code == 1
    assert "awaiting_confirmation" in result.output
    svc.extract_entities.assert_not_called()


def test_confirm_all_processes_every_awaiting_source() -> None:
    runner = CliRunner()
    svc = _make_service()
    svc.ctx.storage_adapter.list_files.return_value = [
        _awaiting_source(),
        {**_awaiting_source(), "id": "if_awaitsrc5678"[:15]},
    ]

    with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
        with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
            result = runner.invoke(confirm_cmd, ["--all", "--yes", "--quiet"])

    assert result.exit_code == 0, result.output
    assert svc.extract_entities.call_count == 2


def test_confirm_without_llm_keeps_source_parked() -> None:
    """No LLM configured → abort BEFORE mutating state, so the source stays parked.

    Regression: the has_llm guard ran AFTER flipping status to indexed +
    writing extraction_confirmed_at, stranding the source out of the
    confirmation queue with no extraction performed.
    """
    runner = CliRunner()
    svc = _make_service()
    svc.has_llm = False

    with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
        with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
            result = runner.invoke(confirm_cmd, ["if_awaitsrc1234"[:15], "--yes", "--quiet"])

    assert result.exit_code == 1
    # The source must NOT have been mutated out of awaiting_confirmation.
    svc.ctx.storage_adapter.update_file.assert_not_called()
    svc.extract_entities.assert_not_called()


def test_confirm_cmd_registered() -> None:
    from chaoscypher_cli.commands.source import LAZY_SUBCOMMANDS

    assert "confirm" in LAZY_SUBCOMMANDS
    assert LAZY_SUBCOMMANDS["confirm"][0] == ("chaoscypher_cli.commands.source.confirm:confirm_cmd")
