# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Keep, flag, never delete (2026-10-07): the extraction's remaining drop sites.

A record that carries evidence (``entity_cleaner.carries_evidence``) is never
dropped; it is kept with a flag (``True`` exactly, read by
``name_identity.pipeline_flag``) and the consumers decide. The orphan filter
at commit was the first site (``test_orphan_filter``); these are the
extraction's:

1. the plausibility score keeps a name below the bar that carries evidence,
   flagged ``low_plausibility`` with its score;
2. a written type no core type answers to is kept as a ``Concept`` flagged
   ``unplaced_type``, the written type beside it;
3. the relationship caps keep every edge, flagging the ones past a cap
   ``capped`` (walked at a tenth of an edge's weight);
6. a group, place or event written without a proper name is a reference of
   its own type, kept and flagged ``descriptor_reference``.

(The alias cleaner, site 4, and the folded bare title, site 5, are pinned by
``deduplication/test_alias_cleaner_keeps_attested_names``; the content
exclusions, site 7, by ``test_content_filtering_thresholds``.)
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.quality.counters import QualityCounter
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    CAPPED_EDGE_WEIGHT,
    CAPPED_KEY,
    DEFAULT_EDGE_WEIGHT,
    DESCRIPTOR_REFERENCE_KEY,
    LOW_PLAUSIBILITY_KEY,
    PIPELINE_EDGE_FLAG_KEYS,
    PIPELINE_FLAG_KEYS,
    PLAUSIBILITY_SCORE_KEY,
    UNPLACED_TYPE_KEY,
    WRITTEN_TYPE_KEY,
    NodeStanding,
    edge_walk_weight,
    labelled_by_reference,
    node_standing,
    pipeline_flag,
)
from chaoscypher_core.services.sources.engine.deduplication.service import (
    merge_node_properties,
    publish_descriptor_references,
)
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    CAP_STAGE_COUNTS,
    capped_relationships,
    enforce_relationship_limits,
    filter_implausible_entities,
    kept_as_reference,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import FilteringLog
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
    UNKNOWN_TYPE_KEY,
    rescue_invalid_entity_types,
)
from tests.fakes.domain_tables import domain_conventions


SONYA = {
    "name": "Sónya",
    "type": "Person",
    "description": "Flushed and in tears",
    # The v5 packs' extracted shape: a property of the text's under a flag's key is no flag.
    "properties": {
        "status": "In love",
        "family_role": "Niece",
        "orphan": "yes",
        "low_plausibility": "no",
        "unplaced_type": "Niece",
        "capped": "true",
    },
}


# -- the one reading of a flag ------------------------------------------------------------------


def test_a_flag_is_true_exactly_and_an_extracted_value_is_the_texts() -> None:
    """Every reader requires ``True``: Sónya's extracted values under flag keys are no flags."""
    props = SONYA["properties"]
    for key in (*PIPELINE_FLAG_KEYS, *PIPELINE_EDGE_FLAG_KEYS):
        assert not pipeline_flag(props, key), key
        assert pipeline_flag({key: True}, key)
        assert not pipeline_flag({key: 1}, key)
    assert node_standing(props) == NodeStanding(False, False, False)
    assert edge_walk_weight(props) == DEFAULT_EDGE_WEIGHT
    assert edge_walk_weight({CAPPED_KEY: True}) == CAPPED_EDGE_WEIGHT == 0.1 * DEFAULT_EDGE_WEIGHT


def test_a_doubted_node_ranks_after_an_undoubted_one_of_its_standing() -> None:
    """``doubtful`` is the least significant field: a reference or an orphan still ranks first."""
    plain = node_standing({})
    doubted = node_standing({LOW_PLAUSIBILITY_KEY: True})
    unplaced = node_standing({UNPLACED_TYPE_KEY: True})
    orphan = node_standing({"orphan": True})
    assert doubted == unplaced == NodeStanding(False, False, True)
    assert plain < doubted < orphan


# -- 1. the plausibility score --------------------------------------------------------------------

_SENTENCES = [
    "Anna Pávlovna led them from the Dining Room into the drawing room.",
    "Natásha ran to the conservatory with her doll.",
]


def test_a_name_below_the_bar_that_carries_evidence_is_kept_flagged_with_its_score() -> None:
    """The v4 literary replay's drops (``conservatory``, ``doll``, described) are kept flagged."""
    entities = [
        {"name": "conservatory", "type": "Location", "description": "A glass room off the hall."},
        {"name": "doll", "type": "Object", "evidence": [{"text": "Natásha ran with her doll."}]},
        {"name": "doll", "type": "Object", "description": ""},  # carries nothing: dropped
        {"name": "Anna Pávlovna", "type": "Person", "description": "The hostess."},
    ]
    log = FilteringLog()
    kept, mapping = filter_implausible_entities(
        entities, _SENTENCES, named_referent_types={"Location", "Object"}, filtering_log=log
    )
    assert [e["name"] for e in kept] == ["conservatory", "doll", "Anna Pávlovna"]
    assert mapping == {0: 0, 1: 1, 2: None, 3: 2}
    for entity in kept[:2]:
        assert entity["properties"][LOW_PLAUSIBILITY_KEY] is True
        assert entity["properties"][PLAUSIBILITY_SCORE_KEY] < 0.40
    assert "properties" not in kept[2]
    (stage,) = log.to_dict()["stages"]
    assert stage["stage"] == "implausible_entity_filter"
    assert stage["removed_count"] == 1  # the drops only
    assert stage["counts"] == {"low_plausibility": 2}
    assert sum(1 for i in stage["items"] if (i.get("details") or {}).get("kept")) == 2
    assert "properties" not in entities[0]  # the input is not mutated


def test_the_flag_gives_way_to_nothing_the_text_wrote_beside_it() -> None:
    """Flagging keeps the entity's other properties; a text value under the key gives way."""
    entity = {
        "name": "doll",
        "type": "Object",
        "description": "Mimi.",
        "properties": {"colour": "red", LOW_PLAUSIBILITY_KEY: "no"},
    }
    (kept,), _ = filter_implausible_entities([entity], _SENTENCES, named_referent_types={"Object"})
    assert kept["properties"]["colour"] == "red"
    assert kept["properties"][LOW_PLAUSIBILITY_KEY] is True


# -- 2. a written type the rescue cannot place ------------------------------------------------------


def test_an_unplaced_type_is_a_concept_flagged_with_the_written_type() -> None:
    """Never dropped: typed Concept, the written type kept, its edges kept, counted ``unknown_type``."""
    entities = [
        {"name": "Pierre", "type": "Person"},
        {"name": "Glorp", "type": "Quux", "description": "Nothing said."},
        {"name": "Zyx", "type": "", "description": ""},
    ]
    rels = [{"source": 0, "target": 1, "type": "knows"}]
    log = FilteringLog()
    kept, kept_rels, mapping, stats = rescue_invalid_entity_types(entities, rels, filtering_log=log)
    assert [(e["name"], e["type"]) for e in kept] == [
        ("Pierre", "Person"),
        ("Glorp", "Concept"),
        ("Zyx", "Concept"),
    ]
    assert kept[1]["properties"] == {UNPLACED_TYPE_KEY: True, WRITTEN_TYPE_KEY: "Quux"}
    assert kept[1]["type_rescued_from"] == "Quux"
    assert mapping == {0: 0, 1: 1, 2: 2}
    assert kept_rels == rels
    assert stats[UNKNOWN_TYPE_KEY] == 2
    (stage,) = log.to_dict()["stages"]
    assert stage["removed_count"] == 0
    assert stage["counts"][UNKNOWN_TYPE_KEY] == 2


def test_a_flag_the_pipeline_wrote_says_nothing_of_a_reference() -> None:
    """``kept_as_reference`` reads the text's properties: an unplaced flag alone keeps nothing."""
    bare = {"name": "the count", "type": "Concept", "description": ""}
    flagged = {**bare, "properties": {UNPLACED_TYPE_KEY: True, WRITTEN_TYPE_KEY: "UNKNOWN"}}
    said = {**bare, "properties": {"title": "Count"}}
    assert not kept_as_reference(bare)
    assert not kept_as_reference(flagged)
    assert kept_as_reference(said)


class _RecordingAdapter:
    """The source-row counter surface, recording each increment."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def increment_source_counter(
        self, *, source_id: str, database_name: str, column: str, n: int
    ) -> None:
        self.counts[column] = self.counts.get(column, 0) + n

    def update_source_columns(
        self, *, source_id: str, database_name: str, updates: dict[str, Any]
    ) -> None:
        raise AssertionError(updates)


@pytest.mark.asyncio
async def test_the_per_chunk_filters_count_the_flagged_entities_on_the_source_row() -> None:
    """``plausibility_entities_flagged`` and ``unplaced_type_entities_flagged`` (migration 0014)."""
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        _filter_entities,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        FilteringConfig,
    )

    adapter = _RecordingAdapter()
    cfg = FilteringConfig(evidence_validation_mode="off", enable_entity_exclusions=False)
    entities = [
        {"name": "conservatory", "type": "Location", "description": "A glass room."},
        {"name": "Glorp", "type": "Quux", "description": "Glorp ran."},
        {"name": "Anna Pávlovna", "type": "Person", "description": "The hostess."},
        # a Core/subtype no pack offers: kept on its core type, flagged and counted the same
        {"name": "Natásha", "type": "Person/wizard", "description": "She ran."},
    ]
    filtered, _ = await _filter_entities(
        entities=entities,
        sentences=_SENTENCES,
        chunk_content=" ".join(_SENTENCES),
        filtering_config=cfg,
        entity_exclusions=None,
        valid_entity_type_names=None,
        named_referent_types={"Location", "Person"},
        normalization_rules=None,
        property_type_mapping=None,
        filtering_log=FilteringLog(),
        adapter=adapter,
        source_id="s1",
        database_name="db",
    )
    assert [e["name"] for e in filtered] == ["conservatory", "Glorp", "Anna Pávlovna", "Natásha"]
    assert filtered[3]["type"] == "Person"
    assert adapter.counts == {
        QualityCounter.UNPLACED_TYPE_ENTITIES_FLAGGED.value: 2,
        QualityCounter.PLAUSIBILITY_ENTITIES_FLAGGED.value: 1,
    }


# -- 3. the relationship caps ---------------------------------------------------------------------


def _hub(n: int) -> list[dict[str, Any]]:
    return [
        {"source": 0, "target": t, "type": "knows", "confidence": 1.0 - t / 100}
        for t in range(1, n + 1)
    ]


@pytest.mark.parametrize(
    ("caps", "cap_pass"),
    [
        ({"max_same_source_type": 3}, "source_type"),
        ({"max_entity_degree": 3, "protect_orphans": False}, "degree"),
        ({"max_relationship_ratio": 0.5}, "total"),
    ],
)
def test_an_edge_past_a_cap_is_kept_flagged_and_nothing_is_dropped(
    caps: dict[str, Any], cap_pass: str
) -> None:
    rels = _hub(6)
    log = FilteringLog()
    kwargs: dict[str, Any] = {
        "max_relationship_ratio": 8.0,
        "max_entity_degree": 25,
        "max_same_source_type": 12,
        **caps,
    }
    if cap_pass == "total":
        # the total cap counts only edges whose endpoints both hold two edges
        rels = [
            {"source": s, "target": t, "type": "knows", "confidence": 0.9 - 0.01 * (s + t)}
            for s in range(4)
            for t in range(4)
            if s < t
        ]
    kept, stats = enforce_relationship_limits(
        [dict(r) for r in rels], entity_count=6, filtering_log=log, **kwargs
    )
    assert len(kept) == len(rels)
    flagged = [r for r in kept if pipeline_flag(r.get("properties"), CAPPED_KEY)]
    assert flagged
    assert stats[f"capped_{cap_pass}"] == len(flagged)
    assert all(r["properties"]["cap_pass"] == cap_pass for r in flagged)
    assert kept[-len(flagged) :] == flagged  # within the caps first, the flagged after
    (stage,) = log.to_dict()["stages"]
    assert stage["removed_count"] == 0
    assert set(stage["counts"]) == set(CAP_STAGE_COUNTS)
    assert capped_relationships([stage]) == len(flagged)


def test_a_capped_edge_counts_toward_no_later_cap() -> None:
    """An edge flagged by the same-source cap does not make its endpoints' degree."""
    rels = _hub(4)
    kept, stats = enforce_relationship_limits(
        [dict(r) for r in rels],
        entity_count=5,
        max_same_source_type=2,
        max_entity_degree=2,
        protect_orphans=False,
    )
    assert stats["capped_source_type"] == 2
    assert stats["capped_degree"] == 0  # the two within the pair cap make degree 2, no more
    assert len(kept) == 4


def test_the_counter_reads_the_flagged_edges_not_a_removed_count() -> None:
    """The source row's ``relationships_dropped_capped`` counts the flagged edges since 2026-10-07."""
    assert QualityCounter.RELATIONSHIPS_DROPPED_CAPPED.value == "relationships_dropped_capped"
    stages = [
        {"stage": "relationship_limit_enforcement", "removed_count": 0, "counts": {
            "capped_source_type": 1, "capped_degree": 2, "capped_total": 3}},
        {"stage": "relationship_dedup", "removed_count": 7},
    ]  # fmt: skip
    assert capped_relationships(stages) == 6


def test_the_walk_weighs_a_capped_edge_at_a_tenth() -> None:
    """PageRank (``build_digraph``) gives a capped hub edge a tenth of an edge's mass."""
    from types import SimpleNamespace

    from chaoscypher_core.services.graph.engine.algorithms import calculate_pagerank

    nodes = [SimpleNamespace(id=i, label=i) for i in ("hub", "a", "b")]
    plain = SimpleNamespace(source_node_id="hub", target_node_id="a", properties={})
    capped = SimpleNamespace(
        source_node_id="hub", target_node_id="b", properties={CAPPED_KEY: True}
    )
    scores = calculate_pagerank(nodes, [plain, capped], personalization={"hub": 1.0})[
        "pagerank_scores"
    ]
    assert scores["a"] > scores["b"]
    unflagged = SimpleNamespace(source_node_id="hub", target_node_id="b", properties={})
    even = calculate_pagerank(nodes, [plain, unflagged], personalization={"hub": 1.0})[
        "pagerank_scores"
    ]
    assert even["a"] == pytest.approx(even["b"])


# -- 6. a reference of every type ------------------------------------------------------------------


@pytest.mark.parametrize("domain", ["literary", "generic"])
@pytest.mark.parametrize(
    ("label", "entity_type"),
    [("the army", "Organization"), ("the government", "Organization"),
     ("the village", "Location"), ("the war", "Event"), ("the regiment", "Group")],
)  # fmt: skip
def test_a_group_place_or_event_written_without_a_name_is_a_reference(
    domain: str, label: str, entity_type: str
) -> None:
    conv = domain_conventions(domain)
    assert labelled_by_reference(label, conv, entity_type)
    entity = {"name": label, "type": entity_type, "description": "Marched at dawn."}
    assert kept_as_reference(entity, conv)
    assert not kept_as_reference({**entity, "description": ""}, conv)
    entities = [dict(entity)]
    publish_descriptor_references(entities, conv)
    assert entities[0]["properties"][DESCRIPTOR_REFERENCE_KEY] is True
    assert entities[0]["type"] == entity_type  # typed by the rescue's core type


@pytest.mark.parametrize(
    ("label", "entity_type"),
    [("Moscow", "Location"), ("the Preobrazhénsky regiment", "Group"),
     ("Caesar's Commentaries", "Work"), ("army", "Organization")],
)  # fmt: skip
def test_a_name_of_any_type_is_no_reference(label: str, entity_type: str) -> None:
    assert not labelled_by_reference(label, domain_conventions("literary"), entity_type)


def test_the_named_referent_bar_no_longer_drops_a_described_group_reference() -> None:
    """``the army`` (described) was scored against the named-referent bar and dropped."""
    entities = [
        {"name": "the army", "type": "Organization", "description": "Marched at dawn."},
        {"name": "the army", "type": "Organization", "description": ""},
    ]
    # a bar every name is below: the described reference is never scored, the bare one is
    kept, _ = filter_implausible_entities(
        [dict(e) for e in entities],
        _SENTENCES,
        named_referent_types={"Organization"},
        threshold=0.99,
    )
    assert [e["description"] for e in kept] == ["Marched at dawn."]
    assert "properties" not in kept[0]  # a reference, not a doubted name


# -- merges -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", [LOW_PLAUSIBILITY_KEY, UNPLACED_TYPE_KEY])
def test_a_doubt_survives_a_merge_only_when_every_merged_record_carried_it(key: str) -> None:
    assert key not in merge_node_properties({key: True}, {"definition": "A room."})
    assert key not in merge_node_properties({}, {key: True})
    assert merge_node_properties({key: True}, {key: True})[key] is True
    assert merge_node_properties({key: "no"}, {key: True})[key] == "no"  # the text's value


# -- an unplaced mention joins its name's typed mentions ----------------------------------------


def _unplaced(name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "type": "Concept",
        "description": description,
        "properties": {UNPLACED_TYPE_KEY: True, WRITTEN_TYPE_KEY: "UNKNOWN"},
    }


def test_an_unplaced_mention_merges_into_its_names_one_typed_entity() -> None:
    """The v5 completions' shape: ``Natásha`` with an empty type column, beside typed mentions.

    The exact dedup reads its Concept as no type when the name's typed mentions are of one
    core type: one Person, unflagged, with both descriptions (until 2026-10-07 the mention
    was dropped at the rescue, description and all).
    """
    from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor

    processor = EntityProcessor(name_conventions=domain_conventions("literary"))
    entities = [
        _unplaced("Natásha", "She ran in laughing."),
        {"name": "Natásha", "type": "Person", "description": "The younger daughter."},
    ]
    out, mapping = processor.deduplicate_entities_with_mapping(
        entities, require_type_compatibility=True
    )
    assert len(out) == 1
    assert mapping == {0: 0, 1: 0}
    assert out[0]["type"] == "Person"
    assert not pipeline_flag(out[0].get("properties"), UNPLACED_TYPE_KEY)
    assert "laughing" in out[0]["description"] and "daughter" in out[0]["description"]


def test_an_unplaced_mention_stays_apart_when_its_name_has_two_types() -> None:
    """``Moscow`` the place and ``Moscow`` the person: which one it is no type says, so neither."""
    from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor

    processor = EntityProcessor(name_conventions=domain_conventions("literary"))
    entities = [
        {"name": "Moscow", "type": "Location", "description": "The old capital."},
        {"name": "Moscow", "type": "Person", "description": "A nickname."},
        _unplaced("Moscow", "Burned."),
    ]
    out, _ = processor.deduplicate_entities_with_mapping(entities, require_type_compatibility=True)
    assert sorted(e["type"] for e in out) == ["Concept", "Location", "Person"]
    (kept,) = [e for e in out if e["type"] == "Concept"]
    assert pipeline_flag(kept["properties"], UNPLACED_TYPE_KEY)
