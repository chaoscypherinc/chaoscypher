# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI's VRAM table must show the models the core presets actually set."""

import pytest

from chaoscypher_cli.commands.setup import VRAM_PRESETS, _get_vram_preset_settings


EXPECTED_EXTRACTION = {
    "vram_16gb": "qwen2.5:14b-instruct",
    "vram_20gb": "qwen2.5:14b-instruct",
    "vram_24gb": "gemma4:26b",
    "vram_32gb": "gemma4:31b",
    "vram_48gb": "gemma4:31b",
    "vram_96gb": "gemma4:31b",
    "vram_128gb": "gemma4:31b",
}


@pytest.mark.parametrize("row", VRAM_PRESETS, ids=lambda r: r["preset"])
def test_cli_vram_table_matches_core_preset(row: dict) -> None:
    """One place decides the default models: the preset plugin, not the table.

    Loaded through the same registry the wizard uses. The chat model is the
    table's hand-written column; the extraction default is the one the
    leaderboard set (2026-09-25) and the value this pins.
    """
    settings = _get_vram_preset_settings(str(row["preset"]))
    assert settings["ollama_chat_model"] == row["model"]
    assert settings["ollama_extraction_model"] == EXPECTED_EXTRACTION[row["preset"]]
