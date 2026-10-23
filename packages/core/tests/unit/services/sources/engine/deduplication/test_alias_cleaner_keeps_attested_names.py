# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The alias cleaner removes no alias; a folded bare title is a merged name (2026-10-07).

Keep, flag, never delete. ``clean_descriptor_aliases`` deleted every alias
sharing no word root with the entity's label, attested or not: a given name
(``Lise`` for ``Princess Bolkónskaya``, ``Annette`` for ``Anna Pávlovna
Schérer``) or a spelling (``Moskva`` for ``Moscow``) the entity's own text
writes. Now an alias its text writes (``name_identity.record_attested_names``,
the stamp ``evidence_aliases`` reads) stays an alias whatever words it shares,
and only an unattested one sharing none is moved, to ``descriptors``, where the
name rule reads no name from it. And ``merge_entities`` recorded a folded
one-word bare title (``Emperor`` into ``Emperor Alexander``) nowhere but in
``merged_property_history.aliases_skipped``; it is a merged name now.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    ATTESTED_NAMES_KEY,
    MERGED_NAMES_KEY,
    name_record,
)
from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    clean_descriptor_aliases,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import FilteringLog
from tests.fakes.domain_tables import domain_conventions


def _entity(name: str, entity_type: str, aliases: list[str], attested: list[str]) -> dict[str, Any]:
    return {"name": name, "type": entity_type, "aliases": aliases, ATTESTED_NAMES_KEY: attested}


@pytest.mark.parametrize("domain", ["literary", "generic"])
def test_an_attested_alias_sharing_no_word_with_the_label_is_never_removed(domain: str) -> None:
    conv = domain_conventions(domain)
    entities = [
        _entity("Anna Pávlovna Schérer", "Person", ["Annette"], ["Annette"]),
        _entity("Princess Bolkónskaya", "Person", ["Lise", "the little princess"], ["Lise"]),
        _entity("Moscow", "Location", ["Moskva"], ["Moscow", "Moskva"]),
    ]
    log = FilteringLog()
    moved = clean_descriptor_aliases(entities, conv.titles, filtering_log=log, conventions=conv)
    assert entities[0]["aliases"] == ["Annette"]
    assert entities[1]["aliases"][0] == "Lise"
    assert entities[2]["aliases"] == ["Moskva"]
    assert all("Lise" not in (e.get("descriptors") or []) for e in entities)
    stage = next(s for s in log.to_dict()["stages"] if s["stage"] == "descriptor_alias_cleaning")
    assert stage["counts"]["kept_attested"] == 3
    assert moved == stage["counts"]["moved"]


def test_an_unattested_alias_sharing_no_word_is_moved_to_descriptors_not_deleted() -> None:
    """``Nieces`` on the Empress: the cleaner's descriptor, kept where no name is read from it."""
    conv = domain_conventions("literary")
    entity = _entity("Empress Márya Fëdorovna", "Person", ["Nieces", "Márya"], [])
    moved = clean_descriptor_aliases([entity], conv.titles, conventions=conv)
    assert moved == 1
    assert entity["aliases"] == ["Márya"]
    assert entity["descriptors"] == ["Nieces"]
    assert name_record(entity).descriptors == ("Nieces",)


def test_no_alias_is_lost_whatever_the_cleaner_decides() -> None:
    """Every alias ends up an alias or a descriptor (a merged-in name stays an alias)."""
    conv = domain_conventions("literary")
    entity = {
        **_entity("Pétya", "Person", ["Pierre", "Monsieur Pierre", "the", "Lise"], ["Lise"]),
        MERGED_NAMES_KEY: ["Pierre"],
    }
    before = set(entity["aliases"])
    clean_descriptor_aliases([entity], conv.titles, conventions=conv)
    assert before == set(entity["aliases"]) | set(entity.get("descriptors") or [])
    assert {"Pierre", "Lise"} <= set(entity["aliases"])


def test_a_folded_one_word_bare_title_is_a_merged_name_and_no_alias() -> None:
    """``Emperor`` folded into ``Emperor Alexander`` is recorded like any merged name."""
    processor = EntityProcessor(name_conventions=domain_conventions("literary"))
    merged = processor.merge_entities(
        {"name": "Emperor Alexander", "type": "Person", "aliases": []},
        {"name": "Emperor", "type": "Person", "aliases": []},
    )
    assert merged[MERGED_NAMES_KEY] == ["Emperor"]
    assert "Emperor" not in merged["aliases"]
    assert merged["merged_property_history"]["aliases_skipped"] == ["Emperor"]
    assert name_record(merged).merged == ("Emperor",)
