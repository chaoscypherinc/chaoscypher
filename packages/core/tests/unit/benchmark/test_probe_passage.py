# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared probe passage preparation used by the local runner and the MCP bridge."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_core.benchmark.probes import (
    Probe,
    pad_with_filler,
    prepare_probe_passage,
    probe_node_templates,
    splice_into_carrier,
)


if TYPE_CHECKING:
    from pathlib import Path


def _probe(**kw: object) -> Probe:
    """Build a probe with sensible defaults."""
    base: dict = {
        "id": "X-easy",
        "probe": "X",
        "section": "A",
        "tier": "easy",
        "instruction": "i",
        "passage": "Kutuzov slept.\n",
        "checks": ({"type": "finish_stop"},),
    }
    base.update(kw)
    return Probe(**base)


def test_plain_probe_passage_is_unchanged(tmp_path: Path) -> None:
    """No carrier, no padding: the passage as written, offset 1."""
    assert prepare_probe_passage(_probe(), tmp_path) == ("Kutuzov slept.\n", 1)


def test_carrier_probe_is_spliced_then_padded(tmp_path: Path) -> None:
    """Same result as calling the two transforms by hand, in that order."""
    carrier = "Anna received her guests. Vasili arrived late. The abbe spoke."
    (tmp_path / "c.txt").write_text(carrier, encoding="utf-8")
    probe = _probe(carrier="c.txt")
    spliced, offset = splice_into_carrier(probe.passage, carrier, seed=7)
    assert prepare_probe_passage(probe, tmp_path, seed=7) == (spliced, offset)
    assert prepare_probe_passage(probe, tmp_path, seed=7, pad_to_chars=600) == (
        pad_with_filler(spliced, 600),
        offset,
    )


def test_probe_entity_types_replace_the_node_templates() -> None:
    """entity_types override the domain templates; otherwise they pass through."""
    assert probe_node_templates(_probe(), "- Character") == "- Character"
    typed = _probe(entity_types=("Person", "Place"))
    assert probe_node_templates(typed, "- Character") == "- Person\n- Place"
