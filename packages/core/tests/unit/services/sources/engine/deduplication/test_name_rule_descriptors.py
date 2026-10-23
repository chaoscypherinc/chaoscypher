# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A name for someone's relation is a descriptor, never that someone's name (audit wave 3).

"Countess Rostóva" given "Natásha's mother" was folded into Natásha;
"Pierre's father" into Pierre; "Count Rostóv's valet" absorbed the count;
"Bezúkhov's son" on Pierre made him a holder of the Bezúkhov name. The rule
now reads a possessive (or "the X of") joined to one of the domain's
relation, role or generation words as a descriptor: no naming word, no given
name, no holder, and evidence that the bearer is not the person it names.
The deduplication moves such aliases to ``descriptors`` before the rule
reads aliases. The groups themselves are pinned through both real callers
in ``test_name_rule_holders``; this file pins the classification and the move.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
    descriptor_possessor,
    is_descriptor,
)
from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.repo_files import repo_root
from tests.fakes.domain_tables import domain_conventions


_V3_PACK = repo_root() / "internal/benchmarks/reference/war_and_peace_book1-v3/app.db"

V3_POSSESSIVE_ALIASES = {
    "Bezúkhov\u2019s son": True,  # on Pierre: whose son, not who
    # a possessive names its bearer through someone else's name, whatever word follows (wave 4)
    "Kurágins' set": True,
}
"""Every alias of the v3 pack written with a possessive, and whether the literary domain reads it as a descriptor."""


@pytest.mark.parametrize(
    ("name", "possessor"),
    [
        ("Natásha's mother", {"natasha"}),
        ("Count Rostóv's valet", {"rostov"}),
        ("Bezúkhov\u2019s son", {"bezukhov"}),
        ("Pierre's father", {"pierre"}),
        ("the old count's valet", set()),
        ("the mother of Natásha", {"natasha"}),
        ("Prince Vasíli's younger son", {"vasili"}),
        # any word after a possessive (W4-6: "envoy", "architect" name no relation)
        ("Kutúzov's envoy", {"kutuzov"}),
        ("Prince Bolkónski's architect", {"bolkonsk"}),
        ("Count Rostóv's huntsman", {"rostov"}),
        ("Kurágins' set", {"kuragin"}),
        ("General Kutúzov's aide-de-camp", {"kutuzov"}),
    ],
)
def test_a_possessive_relation_names_its_possessor(name: str, possessor: set[str]) -> None:
    """The literary domain's relation, role and generation words make these descriptors."""
    assert descriptor_possessor(name, domain_conventions("literary")) == frozenset(possessor)


@pytest.mark.parametrize(
    "name",
    [
        "the Duc d\u2019Enghien",
        "prodigal son Anatole",
        "Natásha",
        "King of Prussia",
    ],
)
def test_a_name_is_not_a_descriptor(name: str) -> None:
    """No possessive joined to a relation word: a name (or no person), as before."""
    assert not is_descriptor(name, domain_conventions("literary"))


def test_the_universal_conventions_read_the_common_relations() -> None:
    """A domain with no relation vocabulary still reads ``X's mother`` as a descriptor."""
    assert is_descriptor("Natásha's mother", DEFAULT_NAME_CONVENTIONS)
    assert not is_descriptor("Natásha Rostóva", DEFAULT_NAME_CONVENTIONS)


def test_the_v3_aliases_of_that_shape_are_classified() -> None:
    """The pinned v3 possessive aliases, as the literary domain classifies them."""
    literary = domain_conventions("literary")
    assert {a: is_descriptor(a, literary) for a in V3_POSSESSIVE_ALIASES} == V3_POSSESSIVE_ALIASES


@pytest.mark.skipif(not _V3_PACK.exists(), reason="the v3 reference pack is not in this tree")
def test_the_v3_pack_holds_exactly_the_pinned_possessive_aliases() -> None:
    """Read-only: every possessive alias of the v3 pack is one the test above classifies."""
    with sqlite3.connect(f"file:{_V3_PACK}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT properties FROM graph_nodes").fetchall()
    found = {
        str(a)
        for (props,) in rows
        for a in (json.loads(props or "{}").get("aliases") or [])
        if any(p in str(a) for p in ("'s ", "\u2019s ", "s' ", "s\u2019 "))
    }
    assert found == set(V3_POSSESSIVE_ALIASES)


@pytest.mark.asyncio
async def test_the_deduplication_keeps_a_descriptor_as_a_descriptor() -> None:
    """Moved out of ``aliases`` before the rule reads them, kept in ``descriptors``, input untouched."""
    entities = [
        {"name": "Countess Rostóva", "type": "Character", "aliases": ["Natásha's mother"]},
        {"name": "Natásha Rostóva", "type": "Character", "aliases": ["Natásha"]},
    ]
    out, _rels, _emb, log = await run_deduplication(
        entities=entities,
        relationships=[],
        detected_domain="literary",
        settings=EngineSettings(),
        embedding_service=None,
    )
    countess = next(e for e in out if e["name"] == "Countess Rostóva")
    assert countess["aliases"] == []
    assert countess["descriptors"] == ["Natásha's mother"]
    assert len(out) == 2
    assert entities[0]["aliases"] == ["Natásha's mother"]  # the caller's entity is not rewritten
    assert any(stage["stage"] == "relation_descriptor_aliases" for stage in log["stages"])
