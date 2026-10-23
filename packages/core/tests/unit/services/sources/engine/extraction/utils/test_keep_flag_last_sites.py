# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Keep, flag, never delete (2026-10-08): the last three drop sites and the selection's rank.

The pattern of ``test_keep_flag_drop_sites`` (a record that carries evidence is
kept with a flag, ``True`` exactly, read by ``name_identity.pipeline_flag``)
applied to:

1. the entity half of the evidence filter: an entity its cited sentence does
   not name, with a description, is kept flagged ``unevidenced`` (the edges'
   key) with ``evidence_reason``; it ranks doubtful; one carrying nothing drops;
2. the strict edge-type constraints: a label no template answers to is kept as
   ``related_to`` flagged ``unmatched_label`` (``written_label`` beside it), and
   endpoints the template refuses are kept flagged ``type_mismatch``; both rank
   with a capped line;
3. the domain exclusions: an excluded name that carries evidence is kept
   flagged ``excluded_name`` (``excluded_by`` beside it); one carrying nothing
   drops;
4. the ``max_triples`` selection ranks by the full ``TripleStanding``
   (``test_graphrag_unevidenced`` pins it on the handler).

Each flag is in ``PIPELINE_FLAG_KEYS`` or ``PIPELINE_EDGE_FLAG_KEYS`` (the cortex
guard test pins them to the interface's tables), merges by the unanimous rule
(``test_unevidenced_edges``) and is counted on the source row (migration 0016).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.quality.counters import QualityCounter
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DOUBTED_EDGE_FLAG_KEYS,
    DOUBTFUL_NODE_FLAG_KEYS,
    EVIDENCE_REASON_KEY,
    EXCLUDED_BY_KEY,
    EXCLUDED_NAME_KEY,
    PIPELINE_EDGE_FLAG_KEYS,
    PIPELINE_FLAG_KEYS,
    PIPELINE_WRITTEN_KEYS,
    TYPE_MISMATCH_KEY,
    UNANIMOUS_FLAG_KEYS,
    UNEVIDENCED_KEY,
    UNMATCHED_LABEL_KEY,
    WRITTEN_LABEL_KEY,
    NodeStanding,
    node_standing,
    triple_standing,
)
from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    ExclusionRule,
)
from chaoscypher_core.services.sources.engine.extraction.utils import evidence_validator as ev
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    EXCLUDED_NAME_STAGE_COUNT,
    TYPE_CONSTRAINT_STAGE_COUNTS,
    filter_excluded_entities,
    kept_as_reference,
    type_constraint_flags,
    validate_relationship_type_constraints,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import FilteringLog


# -- the tables ---------------------------------------------------------------------------------


def test_the_new_flags_join_the_tables() -> None:
    """One key for the node and the edge ``unevidenced``; each new flag in its table and rule."""
    assert PIPELINE_FLAG_KEYS[-2:] == (UNEVIDENCED_KEY, EXCLUDED_NAME_KEY)
    assert UNEVIDENCED_KEY in PIPELINE_EDGE_FLAG_KEYS
    assert PIPELINE_EDGE_FLAG_KEYS[-2:] == (UNMATCHED_LABEL_KEY, TYPE_MISMATCH_KEY)
    assert {UNEVIDENCED_KEY, EXCLUDED_NAME_KEY} <= set(UNANIMOUS_FLAG_KEYS)
    assert {UNEVIDENCED_KEY, EXCLUDED_NAME_KEY} <= set(DOUBTFUL_NODE_FLAG_KEYS)
    assert {EVIDENCE_REASON_KEY, EXCLUDED_BY_KEY} <= PIPELINE_WRITTEN_KEYS
    assert set(DOUBTED_EDGE_FLAG_KEYS) == {"capped", UNMATCHED_LABEL_KEY, TYPE_MISMATCH_KEY}


def test_an_unevidenced_or_excluded_node_ranks_doubtful_and_a_text_value_is_no_flag() -> None:
    for key in (UNEVIDENCED_KEY, EXCLUDED_NAME_KEY):
        assert node_standing({key: True}) == NodeStanding(False, False, True)
        assert node_standing({key: "yes"}) == NodeStanding(False, False, False)
        assert node_standing({key: 1}) == NodeStanding(False, False, False)


def test_an_unmatched_or_mismatched_line_ranks_with_a_capped_one() -> None:
    """One tier, after an evidenced line and before an unevidenced one."""
    plain = node_standing({})
    lines = {
        "plain": triple_standing({}, plain, plain),
        "capped": triple_standing({"capped": True}, plain, plain),
        "unmatched": triple_standing({UNMATCHED_LABEL_KEY: True}, plain, plain),
        "mismatch": triple_standing({TYPE_MISMATCH_KEY: True}, plain, plain),
        "unevidenced": triple_standing({UNEVIDENCED_KEY: True}, plain, plain),
        "text": triple_standing({UNMATCHED_LABEL_KEY: "no", TYPE_MISMATCH_KEY: "1"}, plain, plain),
    }
    assert lines["text"] == lines["plain"]
    assert lines["capped"] == lines["unmatched"] == lines["mismatch"]
    assert lines["plain"] < lines["capped"] < lines["unevidenced"]


# -- 1. entities without a valid evidence sentence ------------------------------------------------

_SENTENCES = ["Pierre dined with the Rostóvs.", "The guests left at midnight."]


def test_an_entity_its_sentence_does_not_name_is_kept_flagged_for_its_description() -> None:
    entities = [
        {"name": "Pierre", "type": "Person", "sent_ref": "S1", "description": "A young man."},
        {
            "name": "Anna Pávlovna",
            "type": "Person",
            "sent_ref": "S2",
            "description": "The hostess.",
        },
        {"name": "Hippolyte", "type": "Person", "sent_ref": "S2", "description": ""},
        {"name": "Mavra", "type": "Person", "description": "The maid."},  # no sent_ref
    ]
    sorting = ev.sort_entities_by_evidence(entities, _SENTENCES, "standard")
    assert [e["name"] for e in sorting.entities] == ["Pierre", "Anna Pávlovna", "Mavra"]
    assert sorting.index_mapping == {0: 0, 1: 1, 2: None, 3: 2}
    assert sorting.entities[0] is entities[0]  # evidenced: as written
    assert sorting.entities[1]["properties"] == {
        UNEVIDENCED_KEY: True,
        EVIDENCE_REASON_KEY: '"Anna Pávlovna" is not named in S2',
    }
    assert sorting.entities[2]["properties"][EVIDENCE_REASON_KEY] == "it cites no sentence"
    assert [v.failure for _, v in sorting.flagged] == [
        ev.EvidenceFailure.NOT_NAMED,
        ev.EvidenceFailure.NO_SENT_REF,
    ]
    assert [e["name"] for e, _ in sorting.dropped] == ["Hippolyte"]
    assert "properties" not in entities[1]  # the input is untouched


def test_the_verdict_is_the_validators_and_the_rules_are_unchanged() -> None:
    entity = {"name": "Anna Pávlovna", "type": "Person", "sent_ref": "S2"}
    verdict = ev.entity_evidence(entity, _SENTENCES, "standard")
    assert not verdict.evidenced
    assert ev.validate_entity_evidence(entity, _SENTENCES, "standard") is False
    assert ev.entity_evidence({**entity, "sent_ref": "S9"}, _SENTENCES).failure is (
        ev.EvidenceFailure.SENT_REF_OUT_OF_RANGE
    )
    assert ev.entity_evidence({**entity, "sent_ref": "line 2"}, _SENTENCES).failure is (
        ev.EvidenceFailure.UNREADABLE_SENT_REF
    )
    assert ev.entity_evidence(entity, _SENTENCES, "relaxed").evidenced


def test_a_text_value_under_the_entity_flag_is_no_flag() -> None:
    """An evidenced entity carrying the text's ``unevidenced: "no"`` is no flagged one."""
    entity = {
        "name": "Pierre",
        "type": "Person",
        "sent_ref": "S1",
        "properties": {UNEVIDENCED_KEY: "no"},
    }
    sorting = ev.sort_entities_by_evidence([entity], _SENTENCES, "standard")
    assert sorting.entities == [entity]
    assert sorting.flagged == []
    assert not node_standing(sorting.entities[0]["properties"]).doubtful


@pytest.mark.asyncio
async def test_the_filter_counts_kept_and_dropped_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    """``entities_unevidenced_kept`` per kept entity, ``evidence_entities_dropped`` per drop."""
    bumps: list[QualityCounter] = []

    async def fake_increment(*, adapter, source_id, database_name, counter, n=1) -> None:  # type: ignore[no-untyped-def]
        bumps.extend([counter] * n)

    monkeypatch.setattr(ev, "increment_quality_counter", fake_increment)
    entities = [
        {"name": "Anna Pávlovna", "sent_ref": "S2", "description": "The hostess."},
        {"name": "Hippolyte", "sent_ref": "S2"},
        {"name": "Pierre", "sent_ref": "S1"},
    ]
    log = FilteringLog()
    kept, mapping, stats = await ev.filter_entities_by_evidence(
        entities, _SENTENCES, "standard", log, adapter=MagicMock(), source_id="s1"
    )
    assert [e["name"] for e in kept] == ["Anna Pávlovna", "Pierre"]
    assert mapping == {0: 0, 1: None, 2: 1}
    assert (stats["entities_dropped"], stats["entities_unevidenced"]) == (1, 1)
    assert bumps == [
        QualityCounter.EVIDENCE_ENTITIES_DROPPED,
        QualityCounter.ENTITIES_UNEVIDENCED_KEPT,
    ]
    (stage,) = log.to_dict()["stages"]
    assert stage["removed_count"] == 1
    assert stage["counts"] == {ev.ENTITY_UNEVIDENCED_STAGE_COUNT: 1}


# -- 2. strict edge-type constraints --------------------------------------------------------------

_CONSTRAINTS = {
    "parent_of": {"source_types": ["Person"], "target_types": ["Person"]},
    "located_in": {"source_types": ["Person"], "target_types": ["Location"]},
    "related_to": {"source_types": [], "target_types": []},
}
_ENTITIES = [
    {"name": "Natásha", "type": "Person"},
    {"name": "Count Rostóv", "type": "Person"},
    {"name": "Moscow", "type": "Location"},
]


def test_the_check_drops_nothing_and_its_flags_reach_the_source_row() -> None:
    """The finalizer reads the stage's counts (``type_constraint_flags``): no removed_count."""
    rels = [
        {"source": 0, "target": 1, "type": "owes_money_to", "justification": "She owed him."},
        {"source": 0, "target": 2, "type": "parent_of", "justification": "Moscow raised her."},
        {"source": 2, "target": 0, "type": "located_in"},  # reversed: corrected
        {"source": 1, "target": 0, "type": "parent_of", "properties": {TYPE_MISMATCH_KEY: "no"}},
    ]
    log = FilteringLog()
    kept, stats = validate_relationship_type_constraints(
        rels, _ENTITIES, _CONSTRAINTS, log, strict_edge_type_constraints=True
    )
    assert [(r["source"], r["type"], r["target"]) for r in kept] == [
        (0, "related_to", 1),
        (0, "parent_of", 2),
        (0, "located_in", 2),
        (1, "parent_of", 0),
    ]
    assert kept[0]["properties"] == {UNMATCHED_LABEL_KEY: True, WRITTEN_LABEL_KEY: "owes_money_to"}
    assert kept[0]["justification"] == "She owed him."
    assert kept[1]["properties"][TYPE_MISMATCH_KEY] is True
    assert kept[3]["properties"] == {TYPE_MISMATCH_KEY: "no"}  # the text's value: no flag
    assert (stats["unmatched_label"], stats["type_mismatch"]) == (1, 1)
    stages = log.to_dict()["stages"]
    (check,) = [s for s in stages if s["stage"] == "relationship_type_constraint"]
    assert check["removed_count"] == 0
    assert type_constraint_flags(stages) == dict(
        zip(TYPE_CONSTRAINT_STAGE_COUNTS, (1, 1), strict=True)
    )


def test_without_strict_constraints_nothing_is_flagged_but_a_reversed_edge() -> None:
    """Non-strict: a refused edge falls through unflagged; one that fits only reversed, with the
    correction off, is kept flagged (until 2026-10-08 it was dropped in either mode).
    """
    rels = [
        {"source": 0, "target": 1, "type": "owes_money_to"},
        {"source": 0, "target": 2, "type": "parent_of"},
        {"source": 2, "target": 0, "type": "located_in"},
    ]
    kept, stats = validate_relationship_type_constraints(
        rels, _ENTITIES, _CONSTRAINTS, enable_direction_correction=False
    )
    assert len(kept) == 3
    assert "properties" not in kept[0] and "properties" not in kept[1]
    assert kept[2]["properties"][TYPE_MISMATCH_KEY] is True
    assert (stats["fell_through"], stats["type_mismatch"]) == (2, 1)


# -- 3. domain exclusions -------------------------------------------------------------------------

_RULES = [
    ExclusionRule(description="Chapter headings", examples=["Chapter One", "Book Two"]),
    ExclusionRule(description="Titles alone", examples=["the king"]),
]


def test_an_excluded_name_that_carries_evidence_is_kept_flagged_with_its_rule() -> None:
    entities = [
        {"name": "Chapter One", "type": "Work", "description": "Where the soirée opens."},
        {"name": "Book Two", "type": "Work", "description": ""},
        {"name": "the king", "type": "Person", "description": "He sent the letter."},
        {"name": "Pierre", "type": "Person", "aliases": ["Chapter One", "Petya"]},
        {"name": "Anna", "type": "Person", "properties": {EXCLUDED_NAME_KEY: "no"}},
    ]
    log = FilteringLog()
    kept, mapping = filter_excluded_entities(entities, _RULES, log)
    assert [e["name"] for e in kept] == ["Chapter One", "the king", "Pierre", "Anna"]
    assert mapping == {0: 0, 1: None, 2: 1, 3: 2, 4: 3}
    assert kept[0]["properties"] == {EXCLUDED_NAME_KEY: True, EXCLUDED_BY_KEY: "Chapter headings"}
    assert kept[1]["properties"] == {EXCLUDED_NAME_KEY: True, EXCLUDED_BY_KEY: "Titles alone"}
    assert kept[2]["aliases"] == ["Petya"]  # an excluded alias goes, the name stays
    assert not node_standing(kept[3]["properties"]).doubtful  # the text's value: no flag
    (stage,) = log.to_dict()["stages"]
    assert stage["removed_count"] == 1
    assert stage["counts"] == {EXCLUDED_NAME_STAGE_COUNT: 2}
    # the flag the filter wrote says nothing of a reference (kept_as_reference reads the text's)
    bare = {"name": "the king", "type": "Person", "properties": dict(kept[1]["properties"])}
    assert not kept_as_reference(bare)


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
async def test_the_per_chunk_filters_count_the_new_flags_on_the_source_row() -> None:
    """``entities_unevidenced_kept`` and ``excluded_entities_kept`` (migration 0016)."""
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        _filter_entities,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        FilteringConfig,
    )

    adapter = _RecordingAdapter()
    cfg = FilteringConfig(
        evidence_validation_mode="standard",
        enable_entity_exclusions=True,
        enable_plausibility_filter=False,
    )
    entities = [
        {"name": "Pierre", "type": "Person", "sent_ref": "S1", "description": "A young man."},
        {"name": "Anna Pávlovna", "type": "Person", "sent_ref": "S2", "description": "Hostess."},
        {"name": "Chapter One", "type": "Work", "sent_ref": "S2", "description": "It opens."},
        {"name": "Hippolyte", "type": "Person", "sent_ref": "S2", "description": ""},
    ]
    filtered, stats = await _filter_entities(
        entities=entities,
        sentences=_SENTENCES,
        chunk_content=" ".join(_SENTENCES),
        filtering_config=cfg,
        entity_exclusions=_RULES,
        valid_entity_type_names=None,
        named_referent_types=None,
        normalization_rules=None,
        property_type_mapping=None,
        filtering_log=FilteringLog(),
        adapter=adapter,
        source_id="s1",
        database_name="db",
    )
    assert [e["name"] for e in filtered] == ["Pierre", "Anna Pávlovna", "Chapter One"]
    assert filtered[2]["properties"][UNEVIDENCED_KEY] is True
    assert filtered[2]["properties"][EXCLUDED_NAME_KEY] is True
    assert adapter.counts == {
        QualityCounter.ENTITIES_UNEVIDENCED_KEPT.value: 2,
        QualityCounter.EVIDENCE_ENTITIES_DROPPED.value: 1,
        QualityCounter.EXCLUDED_ENTITIES_KEPT.value: 1,
    }
    assert (stats["entities_unevidenced"], stats["entities_dropped"]) == (2, 1)
