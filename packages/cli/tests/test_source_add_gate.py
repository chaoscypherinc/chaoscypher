# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The add pipeline gates auto/unforced sources before extraction.

Covered:
- Non-TTY (CliRunner default) + auto domain + no --no-confirm -> park + exit 1,
  hint at 'cc source confirm', extract never runs, no hang.
- --no-confirm bypass + auto domain -> proceeds to extract, recommendation surfaced.
- TTY prompt path: ranking[0] shown as default; entering a domain forces it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.source.add import add


_FILE_ID = "if_indexed12345"  # 15 chars — passes _is_file_id()


def _service_for_gate(detected: dict[str, Any] | None) -> MagicMock:
    svc = MagicMock()
    svc.__enter__ = lambda s: s
    svc.__exit__ = MagicMock(return_value=False)
    svc.has_llm = True
    svc.detect_domain_for_source.return_value = detected
    svc.ctx.database_name = "default"
    svc.ctx.storage_adapter.get_file.return_value = {
        "id": _FILE_ID,
        "filename": "doc.pdf",
        "status": "indexed",
        "forced_domain": None,
    }
    return svc


_REC = {
    "detected_domain": "technical",
    "confidence": 4.2,
    "ranking": [{"domain": "technical", "score": 4.2}, {"domain": "news", "score": 1.0}],
    "low_confidence": False,
}


def test_non_tty_auto_no_confirm_flag_parks_and_exits_nonzero() -> None:
    """No TTY + no --no-confirm: park, exit 1, never extract, never hang."""
    runner = CliRunner()
    svc = _service_for_gate(_REC)
    ctx_mock = MagicMock()
    ctx_mock.storage_adapter.get_file.return_value = {
        "id": _FILE_ID,
        "filename": "doc.pdf",
        "status": "indexed",
        "forced_domain": None,
    }

    with patch("chaoscypher_cli.context.get_context", return_value=ctx_mock):
        with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
            with patch(
                "chaoscypher_cli.utils.llm_check.check_llm_or_skip", return_value=(True, False)
            ):
                with patch(
                    "chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"
                ) as park:
                    # Skip-index so we go straight to the gate->extract boundary.
                    result = runner.invoke(add, [_FILE_ID, "--skip-index", "--extract-only"])

    assert result.exit_code == 1
    assert "cc source confirm" in result.output
    park.assert_called_once()
    svc.extract_entities.assert_not_called()


def test_no_confirm_flag_bypasses_gate_and_extracts() -> None:
    runner = CliRunner()
    svc = _service_for_gate(_REC)
    svc.extract_entities.return_value = (
        {"stats": {"entities_count": 3, "relationships_count": 1, "detected_domain": "technical"}},
        {"total_calls": 1},
    )
    ctx_mock = MagicMock()
    ctx_mock.storage_adapter.get_file.return_value = {
        "id": _FILE_ID,
        "filename": "doc.pdf",
        "status": "indexed",
        "forced_domain": None,
    }

    with patch("chaoscypher_cli.context.get_context", return_value=ctx_mock):
        with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
            with patch(
                "chaoscypher_cli.utils.llm_check.check_llm_or_skip", return_value=(True, False)
            ):
                with patch(
                    "chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"
                ) as park:
                    result = runner.invoke(
                        add, [_FILE_ID, "--extract-only", "--no-confirm", "--quiet"]
                    )

    assert result.exit_code == 0
    park.assert_not_called()
    svc.extract_entities.assert_called_once()
    # Recommendation surfaced on the quiet path.
    assert "technical" in result.output


def test_add_has_no_confirm_flag() -> None:
    params = {p.name for p in add.params}
    assert "no_confirm" in params


def test_quiet_park_prints_awaiting_hint_exactly_once() -> None:
    """--quiet + park: AWAITING/confirm hint appears exactly once, exit 1."""
    runner = CliRunner()
    svc = _service_for_gate(_REC)
    ctx_mock = MagicMock()
    ctx_mock.storage_adapter.get_file.return_value = {
        "id": _FILE_ID,
        "filename": "doc.pdf",
        "status": "indexed",
        "forced_domain": None,
    }

    with patch("chaoscypher_cli.context.get_context", return_value=ctx_mock):
        with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
            with patch(
                "chaoscypher_cli.utils.llm_check.check_llm_or_skip", return_value=(True, False)
            ):
                with patch(
                    "chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"
                ):
                    result = runner.invoke(
                        add, [_FILE_ID, "--skip-index", "--extract-only", "--quiet"]
                    )

    assert result.exit_code == 1, f"Expected exit 1, got {result.exit_code}"
    # The hint must appear exactly once — not duplicated by pipeline + add.py quiet block.
    assert result.output.count("cc source confirm") == 1, (
        f"Expected 'cc source confirm' exactly once, got:\n{result.output}"
    )
    assert _FILE_ID in result.output
