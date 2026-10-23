# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""make_carrier_variants.py: a probe whose exclusion check names someone the carrier chunk
contains gets no variant on that chunk (the chunk would make a legitimate extraction a fail).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


SCRIPT = Path(__file__).resolve().parents[5] / "scripts" / "benchmark" / "make_carrier_variants.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_carrier_variants", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


D6_EASY = {
    "id": "D6-easy",
    "probe": "D6",
    "instruction": "No invented relationships",
    "passage": "Kutuzov slept. Napoleon dined. Alexander wrote letters.\n",
    "checks": [
        {"type": "relationship_count", "max": 0},
        {
            "type": "relationship_count_matching",
            "names": ["Kutuzov", "Napoleon", "Alexander"],
            "max": 0,
        },
        {"type": "finish_stop"},
    ],
}
D6_HARD = {
    "id": "D6-hard",
    "probe": "D6",
    "instruction": "No invented relationships",
    "passage": "Prince Nikolai Bolkonsky read. Lise Bolkonskaya embroidered.\n",
    "checks": [
        {"type": "relationship_count", "max": 0},
        {
            "type": "relationship_count_matching",
            "names": ["Nikolai Bolkonsky", "Lise Bolkonskaya"],
            "max": 0,
        },
        {"type": "finish_stop"},
    ],
}
SOIREE_CAST = ["Emperor Alexander|Alexander", "Buonaparte", "Anna Pávlovna|Anna Pavlovna|Anna"]


def test_exclusion_collisions_names_the_cast_member_the_check_forbids() -> None:
    """Alexander is in the soirée (Anna calls Buonaparte a villain the sovereign must stop):
    a model that links them extracted the chunk, and D6's max-0 count would call it invention.
    """
    mcv = _load()
    assert mcv.exclusion_collisions(D6_EASY, SOIREE_CAST) == ["Alexander"]
    assert mcv.exclusion_collisions(D6_HARD, SOIREE_CAST) == []


def test_exclusion_collisions_folds_accents_and_matches_either_way() -> None:
    mcv = _load()
    probe = {"checks": [{"type": "no_entity_matching", "names": ["Kutuzov"]}]}
    assert mcv.exclusion_collisions(probe, ["Prince Kutúzov|Kutúzov"]) == ["Kutuzov"]
    # an inclusion check may overlap the cast: the person is simply present twice over
    present = {"checks": [{"type": "entities_present", "names": ["Kutuzov"]}]}
    assert mcv.exclusion_collisions(present, ["Kutúzov"]) == []


def test_build_section_skips_the_colliding_variant_and_keeps_the_twin() -> None:
    mcv = _load()
    spec = {"carrier": "carriers/x.txt", "cast": SOIREE_CAST, "probes": ["D6-easy", "D6-hard"]}
    out, skipped = mcv.build_section([spec], {"D.yaml": {"probes": [D6_EASY, D6_HARD]}})
    assert [p["id"] for p in out["probes"]] == ["D6-hard-carrier"]
    assert skipped == [("D6-easy", "carriers/x.txt", ["Alexander"])]
    kept = out["probes"][0]
    assert {c["type"] for c in kept["checks"]} >= {
        "relationship_count_matching",
        "carrier_coverage",
        "no_invention",
    }
    assert "relationship_count" not in {c["type"] for c in kept["checks"]}


def test_build_section_still_refuses_a_vacuous_variant() -> None:
    mcv = _load()
    vacuous = {
        "id": "X1-easy",
        "probe": "X1",
        "instruction": "i",
        "passage": "p",
        "checks": [{"type": "entity_count", "max": 0}],
    }
    spec = {"carrier": "carriers/x.txt", "cast": [], "probes": ["X1-easy"]}
    with pytest.raises(SystemExit, match="vacuous"):
        mcv.build_section([spec], {"X.yaml": {"probes": [vacuous]}})
