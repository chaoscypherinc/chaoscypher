# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""One survivor ranking for every merge step, and two family names withhold a merge (audit wave 6).

W6-2: the exact step broke ``written_name_rank`` ties by codepoint order (an
unaccented "a" sorts before "á"), and the hierarchical step and the resolver
ranked by naming-word count, so on the exploded v3 Anna Pávlovna became
"Anna Pavlovna Scherer", "prodigal son Anatole" outranked "Anatole", and
the two arms of the A/B labelled Pierre "Pierre Bezukhov" and "Monsieur
Pierre". Now the exact, semantic and hierarchical steps and the resolver
all call ``name_identity.pick_survivor``: own name, attested spelling,
accents, the fuller name, support; codepoint never.

W6-8: ``NameIndex.allows_merge`` let two countesses of different families
merge without name evidence; ``_family_names_differ`` withholds it.
"""

from __future__ import annotations

import itertools
import json
import re
import sqlite3
import unicodedata
from typing import Any

import pytest

from chaoscypher_core.services.graph.resolution.plan import plan_merges
from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni
from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor
from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
    plan_filtering_config,
)
from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
from chaoscypher_core.services.sources.engine.extraction.orchestration import (
    build_extraction_groups,
)
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.repo_files import repo_file_or_skip
from tests.fakes.domain_tables import domain_conventions


LITERARY = domain_conventions("literary")
GENERIC = domain_conventions("generic")
BOTH = ("literary", "generic")


def _pick(labels: list[str], conventions: ni.NameConventions = LITERARY, **kw: Any) -> str:
    """The survivor of a group of bare mentions."""
    return labels[ni.pick_survivor([ni.SurvivorCandidate(x, **kw) for x in labels], conventions)]


# -- the ranking --------------------------------------------------------------------------------


def test_a_name_beats_an_epithet_carrying_it() -> None:
    """The name "Anatole" over "prodigal son Anatole": lowercase words make no fuller name."""
    assert _pick(["prodigal son Anatole", "Anatole"]) == "Anatole"
    assert _pick(["Anatole", "prodigal son Anatole"]) == "Anatole"


def test_the_accented_spelling_beats_the_stripped_one_in_any_order() -> None:
    """Codepoint order never decides: "a" sorts before "á", the accents still win."""
    names = ["Anna Pavlovna Scherer", "Anna Pávlovna Schérer"]
    for order in itertools.permutations(names):
        assert _pick(list(order)) == "Anna Pávlovna Schérer"


def test_the_attested_spelling_beats_the_accented_one() -> None:
    """A spelling the corpus writes beats one it does not, accents or not."""
    labels = ["Anna Pávlovna Schérer", "Anna Pavlovna Scherer"]
    candidates = [
        ni.SurvivorCandidate(labels[0]),
        ni.SurvivorCandidate(labels[1], attested=frozenset({labels[1]})),
    ]
    assert labels[ni.pick_survivor(candidates, LITERARY)] == "Anna Pavlovna Scherer"


def test_the_own_name_beats_a_mention_of_a_given_alias() -> None:
    """A bare mention of what another mention gave as an alias is no name of its own."""
    candidates = [
        ni.SurvivorCandidate("Pierre", given=("Monsieur Pierre", "Pierre Bezúkhov")),
        ni.SurvivorCandidate("Pierre Bezúkhov"),
        ni.SurvivorCandidate("Monsieur Pierre"),
    ]
    for conv in (LITERARY, GENERIC):
        assert candidates[ni.pick_survivor(candidates, conv)].label == "Pierre"


def test_a_mention_giving_aliases_keeps_its_own_name_whoever_lists_it() -> None:
    """Mlle Schérer giving the hostess's full name does not make that full name an alias of hers."""
    candidates = [
        ni.SurvivorCandidate("Anna Pávlovna Schérer", given=("Annette",)),
        ni.SurvivorCandidate("Mlle Schérer", given=("Anna Pávlovna Schérer",)),
    ]
    assert candidates[ni.pick_survivor(candidates, LITERARY)].label == "Anna Pávlovna Schérer"


def test_attested_names_are_verbatim_and_whole() -> None:
    """Case and accents as written, whole words: an invented spelling is not attested."""
    text = "It was the well-known Anna Pávlovna Schérer, said Pierre."
    names = ["Anna Pávlovna Schérer", "Anna Pavlovna Scherer", "anna pávlovna schérer", "Pierr"]
    assert ni.attested_names(names, [text]) == ["Anna Pávlovna Schérer"]
    nfd = unicodedata.normalize("NFD", "Anna Pávlovna Schérer")
    assert ni.attested_names([nfd], [text]) == ["Anna Pávlovna Schérer"]


# -- every step reads the one ranking -----------------------------------------------------------


def _entities(*labels: str) -> list[dict[str, Any]]:
    """Bare Character mentions."""
    return [{"name": x, "type": "Character", "aliases": []} for x in labels]


def test_the_exact_step_keeps_the_accented_spelling() -> None:
    """Two spellings of one key: the accented one keeps the label, whichever came first."""
    processor = EntityProcessor(name_conventions=LITERARY)
    for order in itertools.permutations(["Anna Pavlovna Scherer", "Anna Pávlovna Schérer"]):
        unique, _ = processor.deduplicate_entities_with_mapping(_entities(*order))
        assert [e["name"] for e in unique] == ["Anna Pávlovna Schérer"]


def test_the_hierarchical_step_and_the_resolver_keep_the_name_over_the_epithet() -> None:
    """The epithet "prodigal son Anatole" (alias "Anatole") and "Anatole" fold into "Anatole" in both passes."""
    processor = EntityProcessor(name_conventions=LITERARY)
    entities = [
        {"name": "prodigal son Anatole", "type": "Character", "aliases": ["Anatole"]},
        {"name": "Anatole", "type": "Character", "aliases": ["Anatole Kurágin"]},
    ]
    merged, _rels, _map = processor.resolve_hierarchical_names([dict(e) for e in entities], [])
    assert [e["name"] for e in merged] == ["Anatole"]
    nodes = [
        {"id": "a", "label": "prodigal son Anatole", "type": "Character",
         "aliases": ["Anatole"], "properties": {}},
        {"id": "b", "label": "Anatole", "type": "Character",
         "aliases": ["Anatole Kurágin"], "properties": {}},
    ]  # fmt: skip
    (merge,) = plan_merges(nodes, [], conventions=LITERARY)
    assert merge.keep == "b"


def test_the_resolver_keeps_the_attested_spelling_a_commit_stored() -> None:
    """The commit stores ``attested_names`` on the node; the resolver's ranking reads it."""
    nodes = [
        {"id": "a", "label": "Anna Pávlovna Schérer", "type": "Character",
         "aliases": ["Anna Pavlovna Scherer"], "properties": {}},
        {"id": "b", "label": "Anna Pavlovna Scherer", "type": "Character", "aliases": [],
         "properties": {ni.ATTESTED_NAMES_KEY: ["Anna Pavlovna Scherer"]}},
    ]  # fmt: skip
    (merge,) = plan_merges(nodes, [], conventions=LITERARY)
    assert merge.keep == "a"  # the own name still comes first
    nodes[0]["aliases"] = []
    nodes[1]["aliases"] = ["Anna Pávlovna Schérer"]
    (merge,) = plan_merges(nodes, [], conventions=LITERARY)
    assert merge.keep == "b"  # own names both: the attested spelling


@pytest.mark.asyncio
@pytest.mark.parametrize("conventions", [LITERARY, GENERIC])
async def test_the_semantic_step_keeps_the_ranked_label_not_the_first_mention(
    conventions: ni.NameConventions,
) -> None:
    """A cluster at a cosine of 1.0 keeps its own name, not the first mention's (until wave 6, the first)."""
    entities = [
        {"name": "Natalie", "type": "Character", "aliases": [], "source_chunk_indices": [0]},
        {"name": "Natásha", "type": "Character", "aliases": ["Natalie"],
         "source_chunk_indices": [1]},
    ]  # fmt: skip
    unique, mapping, _emb = await EntityProcessor(
        name_conventions=conventions
    ).deduplicate_entities_semantic(
        entities,
        embedding_service=object(),
        similarity_threshold=0.9,
        precomputed_embeddings=[[1.0, 0.0], [1.0, 0.0]],
    )
    assert [e["name"] for e in unique] == ["Natásha"]
    assert "Natalie" in unique[0]["aliases"]
    assert set(mapping.values()) == {0}


@pytest.mark.asyncio
async def test_both_arms_label_pierre_the_same() -> None:
    """Per-chunk mentions of Pierre's aliases: one label, "Pierre", under literary and generic."""
    mentions = [
        {"name": "Pierre", "type": "Character",
         "aliases": ["Monsieur Pierre", "Pierre Bezúkhov", "Pierre Bezukhov"]},
        {"name": "Monsieur Pierre", "type": "Character", "aliases": []},
        {"name": "Pierre Bezukhov", "type": "Character", "aliases": []},
        {"name": "Pierre Bezúkhov", "type": "Character", "aliases": []},
    ]  # fmt: skip
    labels = set()
    for domain in BOTH:
        for order in itertools.permutations(range(len(mentions))):
            work = [
                {
                    **mentions[k],
                    "aliases": list(mentions[k]["aliases"]),
                    "source_chunk_indices": [k],
                }
                for k in order
            ]
            out, _r, _e, _l = await run_deduplication(
                entities=work,
                relationships=[],
                detected_domain=domain,
                settings=EngineSettings(),
                embedding_service=None,
            )
            labels |= {e["name"] for e in out if len(e["source_chunk_indices"]) == len(mentions)}
    assert labels == {"Pierre"}


# -- the v3 pack ---------------------------------------------------------------------------------


_V3 = "internal/benchmarks/reference/war_and_peace_book1-v3/app.db"


def _v3_mentions(path: Any) -> tuple[list[dict[str, Any]], str]:
    """v3's entities with every alias also a mention, attested names stamped from their groups' text."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        chunks = [
            {"id": str(i), "content": content or ""}
            for i, (content,) in enumerate(
                conn.execute("SELECT content FROM document_chunks ORDER BY chunk_index")
            )
        ]
        rows = conn.execute(
            "SELECT name, type, attributes FROM source_entities ORDER BY ordinal"
        ).fetchall()
    groups = [
        g["combined_content"] for g in build_extraction_groups([c for c in chunks if c["content"]])
    ]
    mentions: list[dict[str, Any]] = []
    for name, kind, attributes in rows:
        attrs = json.loads(attributes or "{}")
        aliases = list(attrs.get("aliases") or [])
        at = sorted(
            set(attrs.get("source_chunk_indices") or []) | {attrs.get("chunk_index")} - {None}
        )
        texts = [groups[g] for g in at if 0 <= g < len(groups)]
        for label, given in ((name, aliases), *((a, []) for a in aliases)):
            mentions.append(
                {
                    "name": label,
                    "type": kind,
                    "aliases": list(given),
                    "source_chunk_indices": [len(mentions)],
                    ni.ATTESTED_NAMES_KEY: ni.attested_names([label, *given], texts),
                }
            )
    return mentions, unicodedata.normalize("NFC", "\n".join(c["content"] for c in chunks))


@pytest.mark.asyncio
async def test_every_survivor_of_the_exploded_v3_is_written_in_the_corpus() -> None:
    """Both arms: each survivor's label occurs verbatim in the book (HEAD of wave 5 invented 3)."""
    mentions, corpus = _v3_mentions(repo_file_or_skip(_V3))
    pierre = set()
    for domain in BOTH:
        out, _r, _e, _l = await run_deduplication(
            entities=[dict(m, aliases=list(m["aliases"])) for m in mentions],
            relationships=[],
            detected_domain=domain,
            settings=EngineSettings(),
            embedding_service=None,
        )
        unwritten = [
            e["name"]
            for e in out
            if not re.search(
                rf"(?<![^\W_]){re.escape(unicodedata.normalize('NFC', e['name']))}(?![^\W_])",
                corpus,
            )
        ]
        assert unwritten == [], domain
        names = {e["name"] for e in out}
        assert "Anna Pávlovna Schérer" in names and "Anna Pavlovna Scherer" not in names
        assert "Anatole" in names and "prodigal son Anatole" not in names
        pierre |= {e["name"] for e in out if "Monsieur Pierre" in (e.get("aliases") or [])}
    assert pierre == {"Pierre"}


# -- W6-8: two family names withhold a merge without name evidence ----------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Countess Rostóva", "Countess Véra"),
        ("Countess Rostóva", "Countess Apráksina"),
        ("Countess Véra", "Countess Apráksina"),
        ("Count Rostóv", "Count Bezúkhov"),
        ("Dowager Empress Márya Fëdorovna", "Márya Lvóvna Karágina"),
        ("Dowager Empress Márya Fëdorovna", "Márya Ivánovna Dólokhova"),
    ],
)
def test_two_family_names_or_patronymics_are_two_people(a: str, b: str) -> None:
    """``allows_merge`` (the semantic step's question) says no under the literary tables."""
    index = ni.NameIndex([ni.NameRecord(a, "Character"), ni.NameRecord(b, "Character")],
                         conventions=LITERARY, typed=False)  # fmt: skip
    assert not index.allows_merge([0], [1])
    assert index.withheld(0, 1) == "different family names or patronymics"


@pytest.mark.parametrize(
    ("names", "a", "b"),
    [
        (["Countess Véra", "Véra Rostóva", "Countess Rostóva"], 0, 1),  # Véra is a given name here
        (["Prince Vasíli", "Vasíli Kurágin", "Prince Kurágin"], 0, 2),
        (["Napoleon Bonaparte", "Napoleon Buonaparte"], 0, 1),  # one family, two spellings
    ],
)
def test_a_given_name_or_a_shared_one_withholds_nothing(names: list[str], a: int, b: int) -> None:
    """A titled word the names show to be a given name is no family name; a shared given name lets the vectors decide."""
    index = ni.NameIndex([ni.NameRecord(n, "Character") for n in names], conventions=LITERARY,
                         typed=False)  # fmt: skip
    assert index.withheld(a, b) is None


def test_only_people_are_withheld_by_family_names() -> None:
    """Two places (not persons under the literary tables) are not read for family names."""
    index = ni.NameIndex([ni.NameRecord("Count Rostóv", "Location"),
                          ni.NameRecord("Count Bezúkhov", "Location")],
                         conventions=LITERARY, typed=False)  # fmt: skip
    assert index.withheld(0, 1) != "different family names or patronymics"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("domain", "a", "b"),
    [
        ("literary", "Countess Rostóva", "Countess Véra"),
        ("literary", "Countess Rostóva", "Countess Apráksina"),
        ("literary", "Count Rostóv", "Count Bezúkhov"),
        ("generic", "Mrs Holt", "Mrs Brand"),
    ],
)
async def test_two_countesses_stay_apart_at_a_cosine_of_one(domain: str, a: str, b: str) -> None:
    """The fixed-cosine replay of the audit's pairs: identical vectors, no merge."""
    out, _r, _e, _l = await run_deduplication(
        entities=[
            {"name": a, "type": "Character", "aliases": [], "source_chunk_indices": [0]},
            {"name": b, "type": "Character", "aliases": [], "source_chunk_indices": [1]},
        ],
        relationships=[],
        detected_domain=domain,
        settings=EngineSettings(),
        embedding_service=object(),
        filtering_config=plan_filtering_config("balanced", None),
        precomputed_embeddings=[[1.0, 0.0], [1.0, 0.0]],
    )
    assert len(out) == 2
