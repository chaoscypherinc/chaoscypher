# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The running list pass 1 is handed: who the earlier groups named, ranked, bounded, rendered."""

from __future__ import annotations

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DOUBTFUL_NODE_FLAG_KEYS,
    NameConventions,
    NameTables,
    doubted_by_filters,
    fold,
    is_doubted_record,
    node_standing,
    with_pipeline_flag,
)
from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
    CitedSentences,
)
from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
    ALIAS_CROSSED_KNOWN_KEY,
    KNOWN_ENTITIES_HEADER,
    MIN_LINE_LIMIT,
    REFERENT_UNEVIDENCED_KEY,
    GuardStats,
    KnownEntities,
)


def _e(
    name: str,
    type_: str = "Person",
    aliases: list[str] | None = None,
    sent_ref: str = "S1",
    **props: str,
) -> dict:
    return {
        "name": name,
        "type": type_,
        "aliases": aliases or [],
        "description": "x",
        "sent_ref": sent_ref,
        "confidence": 1.0,
        "properties": dict(props),
    }


def test_references_are_left_out_and_names_kept() -> None:
    known = KnownEntities()
    known.add_group(
        [
            _e("Count Ilyá Rostóv", aliases=["the count"]),
            _e("the countess"),
            _e("Prince Andrew's father"),
            _e("the regiment", "Organization"),
            _e("Semënov regiment", "Organization", aliases=["the regiment"]),
        ],
        [],
    )
    assert [ln.split(" (")[0].lstrip("- ") for ln in known.lines()] == [
        "Count Ilyá Rostóv",
        "Semënov regiment",
    ]


def test_lines_print_the_core_type() -> None:
    known = KnownEntities()
    known.add_group([_e("Count Ilyá Rostóv", "Person/character")], [])
    assert known.lines() == ["- Count Ilyá Rostóv (Person)"]


def test_a_reference_alias_the_parser_files_as_a_descriptor_is_listed() -> None:
    from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
        parse_extraction_output,
    )

    records, _, _ = parse_extraction_output("E|Count Ilyá Rostóv|Person|Ilyá, the count|1.0|S1|x")
    assert records[0]["aliases"] == ["Ilyá"]
    assert records[0]["descriptors"] == ["the count"]
    known = KnownEntities()
    known.add_group(records, [])
    # The single-word "Ilyá" is not offered (spec D8); the entry keeps it.
    assert known.lines() == ["- Count Ilyá Rostóv (Person) — also: the count"]
    assert known.names()["count ilya rostov"].aliases == ("Ilyá", "the count")


def test_a_relation_of_someone_else_is_no_alias_in_the_line_nor_the_index() -> None:
    from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
        parse_extraction_output,
    )

    # Misattributed: Andrew's father written as an alias of Andrew himself.
    records, _, _ = parse_extraction_output(
        "E|Prince Andrew|Person|Andrew, Prince Andrew's father, the prince|1.0|S1|x"
    )
    assert records[0]["descriptors"] == ["Prince Andrew's father", "the prince"]
    known = KnownEntities()
    known.add_group(records, [])
    assert known.lines() == ["- Prince Andrew (Person) — also: the prince"]
    assert known.names()["prince andrew"].aliases == ("Andrew", "the prince")
    # Nor does it name Andrew when a later record writes it.
    assert known._resolve(fold("Prince Andrew's father")) is None  # the alias index


def test_a_works_possessive_title_stays_its_alias() -> None:
    known = KnownEntities(conventions=NameConventions.build(NameTables(person_types=["Person"])))
    known.add_group(
        [
            {
                "name": "Commentarii de Bello Gallico",
                "type": "Work",
                "aliases": [],
                "descriptors": ["Caesar's Commentaries"],
            }
        ],
        [],
    )
    assert known.lines() == ["- Commentarii de Bello Gallico (Work) — also: Caesar's Commentaries"]


def test_for_plan_reads_the_plans_conventions_and_the_settings_limit() -> None:
    from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
        DomainRegistry,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
    from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
        build_extraction_plan,
    )
    from chaoscypher_core.settings import EngineSettings

    settings = EngineSettings()
    settings.extraction.known_entities_limit = 1
    plan = build_extraction_plan(
        DomainStack.from_domain_name(DomainRegistry(), "literary"), settings
    )
    known = KnownEntities.for_plan(plan.chunk_kwargs, settings)
    # "Dowager" is a title only the literary pack declares: a bare title there, never listed.
    known.add_group([_e("Dowager"), _e("Pierre Bezúkhov"), _e("Natásha")], [])
    assert known.lines() == ["- Pierre Bezúkhov (Person)"]  # and the limit of 1 holds
    assert len(known) == 2


def test_aliases_and_relations_render_and_accumulate_across_groups() -> None:
    known = KnownEntities()
    known.add_group(
        [_e("Count Ilyá Rostóv", aliases=["the count"]), _e("Natásha")],
        [{"source": 0, "target": 1, "type": "parent_of", "justification": "x"}],
    )
    known.add_group(
        [
            _e("Count Ilyá Rostóv", aliases=["Papa"], family_role="father of Natásha"),
            _e("Countess Rostóva"),
        ],
        [{"source": 0, "target": 1, "type": "spouse_of", "justification": "x"}],
    )
    line = known.lines()[0]
    assert line.startswith("- Count Ilyá Rostóv (Person) — also: the count; ")  # no "Papa"
    assert known.names()["count ilya rostov"].aliases == ("the count", "Papa")
    assert "parent of Natásha" in line and "spouse of Countess Rostóva" in line
    assert "father of Natásha" in line
    assert known.groups == 2


def test_relations_are_capped_and_non_person_edges_render_as_words() -> None:
    known = KnownEntities()
    ents = [
        _e("Semënov regiment", "Organization"),
        _e("St. Petersburg", "Location"),
        _e("Kutúzov"),
        _e("Bagratión"),
        _e("Borís"),
    ]
    rels = [
        {"source": 0, "target": i, "type": t, "justification": "x"}
        for i, t in ((1, "located_in"), (2, "led_by"), (3, "led_by"), (4, "member_of"))
    ]
    known.add_group(ents, rels)
    line = known.lines()[0]
    assert "located in St. Petersburg" in line and "led by Kutúzov" in line
    assert "member of Borís" not in line  # the fourth relation is past the cap
    assert line.count(";") == 2  # three relations, no also-list


def test_the_block_is_bounded_by_count_and_line_length() -> None:
    known = KnownEntities(limit=2, line_limit=40)
    known.add_group([_e(f"Person Number {i}") for i in range(5)], [])
    known.add_group([_e("Person Number 3"), _e("Person Number 4")], [])  # cited twice: ranked first
    lines = known.lines()
    assert [ln[2:].split(" (")[0] for ln in lines] == ["Person Number 3", "Person Number 4"]
    assert all(len(ln) <= 40 for ln in lines)


def test_an_over_long_line_cuts_the_aliases_before_the_relations() -> None:
    known = KnownEntities(line_limit=56)
    known.add_group(
        [_e("Pierre", aliases=["Pierre Bezúkhov", "Count Pétya"], family_role="son of Kiríll")], []
    )
    full = "- Pierre (Person) — also: Pierre Bezúkhov, Count Pétya; son of Kiríll"
    assert len(full) > 56
    assert known.lines() == ["- Pierre (Person) — also: Pierre Bezúkhov; son of Kiríll"]
    tight = KnownEntities(line_limit=34)
    tight.add_group(
        [_e("Pierre", aliases=["Pierre Bezúkhov", "Count Pétya"], family_role="son of Kiríll")], []
    )
    assert tight.lines() == ["- Pierre (Person) — son of Kiríll"]
    assert all(len(ln) <= 34 for ln in tight.lines())


def test_the_list_offers_no_single_word_alias_and_the_entry_keeps_it() -> None:
    """Spec D8 (the list): a diminutive, a given name alone or a bare title is never offered.

    The entry's own name carries its words. The entry keeps the alias: it still
    resolves to the entry, and the alias guard still drops it from another
    record as a crossing (its owners are unchanged).
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
        parse_extraction_output,
    )

    records, _, _ = parse_extraction_output(
        "E|Vera Dárinovna Melzér|Person|Verochka; the hostess|1.0|S1|x"
    )
    known = KnownEntities()
    known.add_group(records, [])
    assert known.lines() == ["- Vera Dárinovna Melzér (Person) — also: the hostess"]
    assert "Verochka" not in known.block()
    key = fold("Vera Dárinovna Melzér")
    assert known.names()[key].aliases == ("Verochka", "the hostess")
    assert known._resolve(fold("Verochka")) == key
    rec = _e("Lady Ostrava", aliases=["Verochka", "the widow"])
    out, stats = known.guard([rec], ["[S1] Lady Ostrava smiled."])
    assert out[0]["aliases"] == ["the widow"]
    assert out[0]["properties"][ALIAS_CROSSED_KNOWN_KEY] == "Verochka"
    assert stats.aliases_crossed == 1


def test_a_speaker_relative_alias_is_not_offered_and_the_entry_keeps_it() -> None:
    """An alias opening with a possessive (my, his, her, their, our, your) is not offered."""
    known = KnownEntities()
    known.add_group([_e("Lady Mirela Ostrava", aliases=["My wife", "the little princess"])], [])
    assert known.lines() == ["- Lady Mirela Ostrava (Person) — also: the little princess"]
    assert known.names()[fold("Lady Mirela Ostrava")].aliases == ("My wife", "the little princess")


@pytest.mark.parametrize(
    ("alias", "offered"),
    [
        ("Verochka", False),  # a diminutive
        ("Mamma", False),  # a bare kinship title
        ("Prince", False),  # a bare title
        ("my dear Lela", False),  # reads "my Lela": one name word beside a possessive
        ("the Lela", False),
        ("the hostess", True),  # a reference of two words, no name reading
        ("the count", True),
        ("his father", False),  # speaker-relative: opens with a possessive
        ("their old nurse", False),
        ("Vera Darinovna", True),  # a name of two words
        ("Count Oswin", True),  # a title and a given name
    ],
)
def test_a_single_word_is_one_token_or_a_name_reading_of_one_word(
    alias: str, *, offered: bool
) -> None:
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        DEFAULT_NAME_CONVENTIONS,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
        offered_alias,
    )

    assert offered_alias(alias, DEFAULT_NAME_CONVENTIONS) is offered
    known = KnownEntities()
    known.add_group([_e("Lady Mirela Ostrava", aliases=[alias])], [])
    assert (alias in known.block()) is offered


def test_a_name_longer_than_the_line_limit_is_cut_hard() -> None:
    known = KnownEntities(line_limit=24)
    known.add_group([_e("Grand Duke Konstantin Pávlovich of Russia", aliases=["Konstantin"])], [])
    assert known.lines() == ["- Grand Duke Konstantin "[:24]]
    assert len(known.lines()[0]) == 24


def test_a_line_limit_below_the_floor_is_refused() -> None:
    with pytest.raises(ValidationError, match=str(MIN_LINE_LIMIT)):
        KnownEntities(line_limit=MIN_LINE_LIMIT - 1)
    KnownEntities(line_limit=MIN_LINE_LIMIT)


def test_the_block_is_empty_for_no_groups_and_for_limit_zero() -> None:
    assert KnownEntities().block() == ""
    off = KnownEntities(limit=0)
    off.add_group([_e("Pierre")], [])
    assert off.block() == "" and off.groups == 1
    on = KnownEntities()
    on.add_group([_e("Pierre")], [])
    assert on.block() == f"{KNOWN_ENTITIES_HEADER}\n- Pierre (Person)"


def test_names_index_folds_and_holds_given_names() -> None:
    known = KnownEntities()
    known.add_group([_e("Count Ilyá Rostóv", aliases=["Ilyá"])], [])
    assert "count ilya rostov" in known.names()
    entry = known.names()["count ilya rostov"]
    assert "Ilyá" in entry.aliases


def test_the_reference_read_is_typed() -> None:
    # Under conventions that declare person types, a possessive is a person's
    # descriptor only on a person: a work titled by one is a name and is listed.
    known = KnownEntities(conventions=NameConventions.build(NameTables(person_types=["Person"])))
    known.add_group([_e("Caesar's Commentaries", "Work"), _e("Prince Andrew's father")], [])
    assert known.lines() == ["- Caesar's Commentaries (Work)"]


def test_aliases_and_relations_are_deduplicated_by_fold_and_non_strings_skipped() -> None:
    known = KnownEntities()
    record = _e("Count Ilyá Rostóv", family_role="father of Natásha")
    record["aliases"] = ["Ilyá", "Ilya", "ILYÁ", None, 7, "  "]
    known.add_group([record], [])
    known.add_group([_e("Count Ilyá Rostóv", family_role="Father of Natasha")], [])
    entry = known.names()["count ilya rostov"]
    assert entry.aliases == ("Ilyá",)
    assert entry.relations == ("father of Natásha",)


def test_a_string_alias_is_one_alias_and_bad_properties_are_empty() -> None:
    known = KnownEntities()
    record = _e("Natásha")
    record["aliases"] = "Natalie"
    record["properties"] = "family_role=daughter"
    known.add_group([record], [])
    entry = known.names()["natasha"]
    assert entry.aliases == ("Natalie",)
    assert entry.relations == ()


def test_a_name_that_is_a_known_alias_joins_its_entry() -> None:
    known = KnownEntities()
    known.add_group([_e("Count Ilyá Rostóv", aliases=["Ilya"])], [])
    known.add_group(
        [_e("Ilyá", aliases=["Papa"]), _e("Natásha")],
        [
            {"source": 1, "target": 0, "type": "child_of", "justification": "x"},
        ],
    )
    assert len(known) == 2
    entry = known.names()["count ilya rostov"]
    assert entry.groups == 2
    assert entry.aliases == ("Ilya",)  # joined by an alias: nothing added
    assert known.names()["natasha"].relations == ()  # nor any edge, on either side


def test_an_alias_joined_record_hangs_no_edges_on_the_entry_it_joined() -> None:
    # A crossed alias: the entry that claimed "Ilyá" is not the Ilyá of group 2's edge.
    known = KnownEntities()
    known.add_group([_e("Count Cyril Bezúkhov", aliases=["Ilyá"]), _e("Natásha")], [])
    known.add_group(
        [_e("Ilyá"), _e("Natásha")],
        [
            {"source": 0, "target": 1, "type": "parent_of", "justification": "x"},
            {"source": 1, "target": 0, "type": "child_of", "justification": "x"},
        ],
    )
    assert known.lines() == ["- Count Cyril Bezúkhov (Person)", "- Natásha (Person)"]
    assert known.names()["count cyril bezukhov"].groups == 2


def test_an_alias_given_by_two_entries_joins_neither() -> None:
    known = KnownEntities()
    known.add_group(
        [
            _e("Anna Pávlovna Schérer", aliases=["Anna"]),
            _e("Anna Mikháylovna", aliases=["Anna"]),
            _e("Borís"),
        ],
        [],
    )
    known.add_group(
        [_e("Anna"), _e("Borís")],
        [{"source": 1, "target": 0, "type": "child_of", "justification": "x"}],
    )
    lines = known.lines()
    assert not any("child of Anna Pávlovna Schérer" in ln for ln in lines)
    assert not any("child of Anna Mikháylovna" in ln for ln in lines)
    assert lines == [
        "- Borís (Person) — child of Anna",
        "- Anna Pávlovna Schérer (Person)",
        "- Anna Mikháylovna (Person)",
        "- Anna (Person)",
    ]
    assert known.names()["anna pavlovna scherer"].groups == 1


def test_edge_names_are_stripped_and_self_and_bool_edges_skipped() -> None:
    known = KnownEntities()
    known.add_group(
        [_e("Count Ilyá Rostóv"), _e("  Natásha  ")],
        [
            {"source": 0, "target": 1, "type": "parent_of", "justification": "x"},
            {"source": 0, "target": 0, "type": "knows", "justification": "x"},
            {"source": True, "target": 1, "type": "spouse_of", "justification": "x"},
            {"source": 0, "target": False, "type": "met", "justification": "x"},
        ],
    )
    assert known.names()["count ilya rostov"].relations == ("parent of Natásha",)
    assert "natasha" in known.names()


# -- the guards (spec D3) -----------------------------------------------------------------------


def _known_rostovs() -> KnownEntities:
    known = KnownEntities()
    known.add_group(
        [
            _e("Count Ilyá Rostóv", aliases=["the count", "Ilyá"]),
            _e("Count Cyril Vladímirovich Bezúkhov", aliases=["Count Bezúkhov"]),
            _e("Semënov regiment", "Organization", aliases=["the regiment"]),
        ],
        [],
    )
    return known


def test_alias_guard_drops_an_alias_that_names_another_known_entry() -> None:
    known = _known_rostovs()
    rec = _e("Count Cyril Vladímirovich Bezúkhov", aliases=["the count", "Ilyá"])
    out, stats = known.guard([rec], ["[S1] he has come to see Count Cyril Vladímirovich"])
    assert out[0]["aliases"] == ["the count"]
    assert out[0]["properties"]["alias_crossed_known"] == "Ilyá"
    assert stats.aliases_crossed == 1


def test_alias_guard_folds_accents() -> None:
    known = _known_rostovs()
    rec = _e("Count Cyril Vladímirovich Bezúkhov", aliases=["Ilya"])
    out, _ = known.guard([rec], ["[S1] Count Cyril Vladímirovich"])
    assert out[0]["aliases"] == []


def test_name_guard_demotes_a_known_name_its_sentences_write_only_by_a_title() -> None:
    # spec D8: "the count", an alias earlier groups gave this entry alone, no longer carries
    # (until D8 rule 2 accepted it, the cascade that hung Count Rostóv's family on Bezúkhov)
    known = _known_rostovs()
    by_title = _e("Count Ilyá Rostóv", aliases=["the count"], sent_ref="S1")
    by_epithet = _e("Count Ilyá Rostóv", aliases=["the old count"], sent_ref="S2")
    out, stats = known.guard(
        [by_title, by_epithet],
        ["[S1] 'You spoil her,' said the count.", "[S2] The old count laughed."],
    )
    assert [r["name"] for r in out] == ["the count", "the old count"]
    assert [r["properties"]["referent_unevidenced"] for r in out] == ["Count Ilyá Rostóv"] * 2
    assert stats.names_demoted == 2


def test_name_guard_reads_no_known_alias_two_entries_gave() -> None:
    sentences = ["[S1] The Hermit of Vell laughed."]
    one = KnownEntities()
    one.add_group([_e("Oswin Kell", aliases=["the Hermit of Vell"]), _e("Pierre")], [])
    out, stats = one.guard([_e("Oswin Kell")], sentences)
    assert REFERENT_UNEVIDENCED_KEY not in out[0]["properties"] and stats == GuardStats()

    two = KnownEntities()
    two.add_group(
        [
            _e("Oswin Kell", aliases=["the Hermit of Vell"]),
            _e("Doran Mabe", aliases=["the Hermit of Vell"]),
        ],
        [],
    )
    # the alias's naming words are both entries' now: they name neither
    out, stats = two.guard([_e("Oswin Kell")], sentences)
    assert out[0]["name"] == "Oswin Kell"
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "Oswin Kell"
    assert stats.names_flagged == 1


def test_name_guard_flags_but_keeps_a_name_with_no_single_reference_phrase() -> None:
    known = _known_rostovs()
    rec = _e(
        "Semënov regiment", "Organization", aliases=["the regiment", "the guards"], sent_ref="S1"
    )
    out, stats = known.guard([rec], ["[S1] The regiment and the guards marched."])
    # two reference phrases are written: the record keeps its name, flagged
    assert out[0]["name"] == "Semënov regiment"
    assert out[0]["properties"]["referent_unevidenced"] == "the regiment; the guards"
    assert stats.names_flagged == 1


def test_guards_are_off_without_known_entries() -> None:
    rec = _e("Count Cyril Vladímirovich Bezúkhov", aliases=["Ilyá"])
    out, stats = KnownEntities().guard([rec], ["[S1] x"])
    assert out[0]["aliases"] == ["Ilyá"] and stats == GuardStats()


def test_guards_are_off_at_limit_zero_and_return_the_records_unchanged() -> None:
    off = KnownEntities(limit=0)
    off.add_group([_e("Count Ilyá Rostóv", aliases=["Ilyá"])], [])
    rec = _e("Count Cyril Vladímirovich Bezúkhov", aliases=["Ilyá"])
    out, stats = off.guard([rec], ["[S1] x"])
    assert out == [rec] and stats == GuardStats()


def test_the_name_rung_reads_naming_words_not_common_nouns_or_own_aliases() -> None:
    known = _known_rostovs()
    by_surname = _e("Count Ilyá Rostóv", aliases=["the old count"], sent_ref="S1")
    by_known_name = _e("Count Ilyá Rostóv", aliases=["the old count"], sent_ref="S2")
    out, stats = known.guard(
        [by_surname, by_known_name],
        ["[S1] Rostóv, the old count, laughed.", "[S2] 'Ilyá!' she called."],
    )
    assert [r["name"] for r in out] == ["Count Ilyá Rostóv", "Count Ilyá Rostóv"]
    assert stats == GuardStats()


def test_a_known_alias_written_alone_is_evidence_without_the_record_giving_it() -> None:
    known = KnownEntities()
    known.add_group([_e("Oswin Kell", aliases=["the Hermit of Vell", "the old man"])], [])
    rec = _e("Oswin Kell", sent_ref="S1")
    out, stats = known.guard([rec], ["[S1] 'You spoil her,' said the Hermit of Vell."])
    assert REFERENT_UNEVIDENCED_KEY not in out[0]["properties"] and stats == GuardStats()
    # a known reference of title and article words only is no evidence (spec D8)
    out, stats = known.guard([rec], ["[S1] 'You spoil her,' said the old man."])
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "Oswin Kell"
    assert stats.names_flagged == 1


def test_a_bare_title_never_names_a_known_entry_even_its_one_holder() -> None:
    # D3's rule 3 (the sole known holder of a title) is removed by D8: Count Rostóv is a
    # holder the list never saw, so the one known count was no evidence
    known = KnownEntities()
    known.add_group([_e("Prince Vasíli Kurágin"), _e("Count Ilyá Rostóv")], [])
    rec = _e("Prince Vasíli Kurágin", aliases=["the old prince"], sent_ref="S1")
    out, stats = known.guard([rec], ["[S1] The old prince sighed."])
    assert out[0]["name"] == "the old prince" and stats.names_demoted == 1
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "Prince Vasíli Kurágin"


# -- the name guard under spec D8 (the v5e join sweep) ----------------------------------------


def _literary() -> NameConventions:
    from tests.fakes.domain_tables import domain_conventions

    return domain_conventions("literary")


def test_rule_one_reads_no_single_word_alias_of_the_entry() -> None:
    # "Ilyá" given to Count Bezúkhov by an earlier group's wrong join certifies nothing
    known = KnownEntities()
    known.add_group([_e("Count Bezúkhov", aliases=["Ilyá"])], [])
    rec = _e("Count Bezúkhov", aliases=["the count", "Ilyá"], sent_ref="S1")
    out, stats = known.guard([rec], ["[S1] The count jumped up and turned to her husband, Ilyá."])
    assert out[0]["name"] == "the count"
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "Count Bezúkhov"
    assert stats.names_demoted == 1


def test_rule_one_reads_no_naming_word_two_known_entries_claim() -> None:
    conv = _literary()
    sentences = ["[S1] The little Princess Bolkónskaya smiled."]
    rec = _e(
        "Princess Mary Bolkónskaya", aliases=["the little Princess Bolkónskaya"], sent_ref="S1"
    )
    alone = KnownEntities(conventions=conv)
    alone.add_group([_e("Princess Mary Bolkónskaya")], [])
    out, stats = alone.guard([rec], sentences)
    assert out[0]["name"] == "Princess Mary Bolkónskaya" and stats == GuardStats()

    # Bolkónski and Bolkónskaya are one family word (the surname forms fold): shared, it names
    # neither, and the phrase that wrote only it is the reference the record is demoted to
    two = KnownEntities(conventions=conv)
    two.add_group([_e("Princess Mary Bolkónskaya"), _e("Prince Andrew Bolkónski")], [])
    out, stats = two.guard([rec], sentences)
    assert out[0]["name"] == "the little Princess Bolkónskaya"
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "Princess Mary Bolkónskaya"
    assert out[0]["aliases"] == [] and stats.names_demoted == 1


def test_rule_one_looks_back_one_sentence_before_the_first_cited() -> None:
    known = KnownEntities()
    known.add_group([_e("Prince Vasíli Kurágin")], [])
    rec = _e("Prince Vasíli Kurágin", aliases=["the prince"], sent_ref="S4")
    sentences = [
        "[S1] It rained.",
        "[S2] Prince Vasíli was at the door.",
        "[S3] Prince Vasíli entered.",
        "[S4] The prince sat down.",
    ]
    out, stats = known.guard([rec], sentences)
    assert out[0]["name"] == "Prince Vasíli Kurágin" and stats == GuardStats()
    # one sentence only: two back is not read, and S1 has none before it
    far = [*sentences[:2], "[S3] Someone entered.", sentences[3]]
    out, stats = known.guard([rec], far)
    assert out[0]["name"] == "the prince" and stats.names_demoted == 1
    first = ["[S1] The prince sat down.", "[S2] Prince Vasíli entered."]
    out, _ = known.guard([{**rec, "sent_ref": "S1"}], first)
    assert out[0]["name"] == "the prince"


def _known_vesk() -> KnownEntities:
    known = KnownEntities()
    known.add_group([_e("Prince Orlan Vesk", aliases=["the King of Valdria"])], [])
    return known


def test_rule_two_carries_a_distinctive_known_reference_by_its_whole_phrase() -> None:
    # spec D8 (review round 1): a description alias is no rule-1 name, it carries as rule 2's phrase
    rec = _e("Prince Orlan Vesk", sent_ref="S1")
    out, stats = _known_vesk().guard([rec], ["[S1] The King of Valdria is clever."])
    assert out[0]["name"] == "Prince Orlan Vesk" and stats == GuardStats()


def test_a_place_word_of_a_description_alias_names_no_one() -> None:
    rec = _e("Prince Orlan Vesk", sent_ref="S1")
    out, stats = _known_vesk().guard([rec], ["[S1] Valdria lost the war."])
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "Prince Orlan Vesk"
    assert stats.names_flagged == 1
    # nor names a known edge endpoint (rule 1 is the edge rule's reading too)
    ref = CitedSentences.of("S1", ["[S1] Valdria lost the war."])
    assert not _known_vesk().named(fold("Prince Orlan Vesk"), "Prince Orlan Vesk", ref)


def test_rule_two_needs_every_reference_phrase_the_record_writes_to_be_known() -> None:
    # "the old prince" is written too and is no known alias: rule 2 does not carry
    rec = _e("Prince Orlan Vesk", aliases=["the King of Valdria", "the old prince"], sent_ref="S1")
    out, stats = _known_vesk().guard(
        [rec], ["[S1] The King of Valdria, the old prince, is clever."]
    )
    assert out[0]["name"] == "Prince Orlan Vesk"
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "the King of Valdria; the old prince"
    assert stats.names_flagged == 1


def test_a_lone_description_is_the_phrase_the_record_is_demoted_to() -> None:
    """Demoted to the one written phrase whatever its shape (Task 14 round 1).

    The entry holds no description alias, so rule 2 does not carry the record. Kept under
    the known name it would still commit onto that entity: it becomes "the King of Valdria",
    ``referent_unevidenced`` holding the known name.
    """
    known = KnownEntities()
    known.add_group([_e("Prince Orlan Vesk")], [])
    described = _e("Prince Orlan Vesk", aliases=["the King of Valdria"], sent_ref="S1")
    out, stats = known.guard([described], ["[S1] The King of Valdria is clever."])
    assert out[0]["name"] == "the King of Valdria" and out[0]["aliases"] == []
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "Prince Orlan Vesk"
    assert (stats.names_demoted, stats.names_flagged) == (1, 0)


def test_a_record_named_by_a_known_alias_is_read_by_the_entrys_names() -> None:
    """A record whose NAME is the entry's description alias: never read by that name's words.

    ``the King of Valdria`` resolves to the Vesk entry through its alias. Rule 1 reads the
    entry's name and its multi-word name-aliases only, so the place word ("Valdria lost the
    war.") names no one; the written name carries as rule 2's whole phrase.
    """
    rec = _e("the King of Valdria", sent_ref="S1")
    assert _known_vesk().resolves("the King of Valdria", "Person") == fold("Prince Orlan Vesk")
    out, stats = _known_vesk().guard([rec], ["[S1] Valdria lost the war."])
    assert out[0]["name"] == "the King of Valdria"
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "the King of Valdria"
    assert stats.names_flagged == 1
    ref = CitedSentences.of("S1", ["[S1] Valdria lost the war."])
    assert not _known_vesk().named(fold("Prince Orlan Vesk"), "the King of Valdria", ref)
    out, stats = _known_vesk().guard([rec], ["[S1] The King of Valdria is clever."])
    assert out[0] == rec and stats == GuardStats()  # rule 2


def test_rule_two_never_carries_a_title_reference() -> None:
    known = KnownEntities()
    known.add_group([_e("Count Bezúkhov", aliases=["the count"])], [])
    rec = _e("Count Bezúkhov", aliases=["the count"], sent_ref="S1")
    out, stats = known.guard([rec], ["[S1] 'Yes,' the count said."])
    assert out[0]["name"] == "the count"
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "Count Bezúkhov"
    assert stats.names_demoted == 1


def test_an_alias_whose_reading_holds_no_naming_word_is_no_name() -> None:
    # "My wife" reads as "My" (alias_as_name): no name for rule 1 nor for the alias guard
    known = KnownEntities()
    known.add_group([_e("Lise Meinen", aliases=["My wife"])], [])
    rec = _e("Lise Meinen", sent_ref="S1")
    out, stats = known.guard([rec], ["[S1] I told my sister."])
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "Lise Meinen"
    assert stats.names_flagged == 1
    other = _e("Mira Tove", aliases=["My wife"], sent_ref="S1")
    out, stats = known.guard([other], ["[S1] Mira Tove smiled."])
    assert out[0]["aliases"] == ["My wife"] and stats == GuardStats()


def test_a_multi_word_name_counts_no_article_possessive_or_particle() -> None:
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        DEFAULT_NAME_CONVENTIONS,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
        alias_name,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
        _names_by_rule_one,
        multi_word_name,
    )

    def rule_one(alias: str, entity_type: str = "") -> bool:
        return _names_by_rule_one(alias, alias_name(alias, conv), entity_type, conv)

    conv = DEFAULT_NAME_CONVENTIONS
    assert multi_word_name("Count Ilyá", conv) == "Count Ilyá"
    assert multi_word_name("the King of Prussia", conv) == "the King of Prussia"
    assert [multi_word_name(a, conv) for a in ("Ilyá", "my dear Annette", "My wife")] == [None] * 3
    # rule 1's names (``_names_by_rule_one``): no reference and no description
    assert rule_one("Count Ilyá")
    assert not rule_one("the King of Prussia")
    assert not rule_one("the old count", "Person")
    # so "my dear Annette" given to an entry names it no more than "Annette" does
    known = KnownEntities()
    known.add_group([_e("Anna Pávlovna Schérer", aliases=["my dear Annette"])], [])
    out, stats = known.guard([_e("Anna Pávlovna Schérer")], ["[S1] 'My dear Annette!' he cried."])
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "Anna Pávlovna Schérer"
    assert stats.names_flagged == 1


def test_resolves_reads_a_records_name_never_a_reference() -> None:
    known = KnownEntities()
    known.add_group([_e("Count Bezúkhov", aliases=["the count", "Count Cyril"])], [])
    assert known.resolves("Count Bezúkhov") == fold("Count Bezúkhov")
    assert known.resolves("Count Cyril", "Person") == fold("Count Bezúkhov")
    assert known.resolves("the count", "Person") is None  # a reference, whoever gave it
    assert known.resolves("Pierre") is None and known.resolves(None) is None
    off = KnownEntities(limit=0)
    off.add_group([_e("Count Bezúkhov")], [])
    assert off.resolves("Count Bezúkhov") is None


def test_the_claims_move_with_the_list() -> None:
    conv = _literary()
    known = KnownEntities(conventions=conv)
    known.add_group([_e("Princess Mary Bolkónskaya")], [])
    rec = _e("Princess Mary Bolkónskaya", sent_ref="S1")
    sentences = ["[S1] Bolkónskaya smiled."]
    assert known.guard([rec], sentences)[1] == GuardStats()
    known.add_group([_e("Prince Andrew Bolkónski")], [])  # a second claimant of the family word
    assert known.guard([rec], sentences)[1].names_flagged == 1


def test_an_unnamed_record_with_no_reference_phrase_is_flagged_by_its_name() -> None:
    known = _known_rostovs()
    rec = _e("Count Ilyá Rostóv", sent_ref="S1")
    out, stats = known.guard([rec], ["[S1] She laughed."])
    assert out[0]["name"] == "Count Ilyá Rostóv"
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "Count Ilyá Rostóv"
    assert stats.names_flagged == 1 and stats.names_demoted == 0


def test_an_alias_given_by_two_entries_crosses_neither() -> None:
    known = KnownEntities()
    known.add_group(
        [
            _e("Anna Pávlovna Schérer", aliases=["Anna"]),
            _e("Anna Mikháylovna", aliases=["Anna"]),
        ],
        [],
    )
    # a record that claimed nothing: a shared alias is ambiguous, so it crosses neither claimant
    rec = _e("Count Ilyá Rostóv", aliases=["Anna"])
    out, stats = known.guard([rec], ["[S1] Count Ilyá Rostóv smiled."])
    assert out[0]["aliases"] == ["Anna"] and stats == GuardStats()


def test_a_record_named_by_a_known_alias_keeps_its_entrys_other_names() -> None:
    known = KnownEntities()
    known.add_group(
        [_e("Count Cyril Bezúkhov", aliases=["Count Cyril Vladímirovich", "Count Bezúkhov"])],
        [],
    )
    rec = _e("Count Cyril Vladímirovich", aliases=["Count Bezúkhov"])
    out, stats = known.guard([rec], ["[S1] Count Cyril Vladímirovich is ill."])
    assert out[0]["aliases"] == ["Count Bezúkhov"] and stats == GuardStats()


def test_a_record_labelled_by_a_reference_is_not_name_guarded() -> None:
    known = KnownEntities()
    known.add_group([_e("Countess Rostóva", aliases=["the countess"])], [])
    rec = _e("the countess", sent_ref="S1")
    out, stats = known.guard([rec], ["[S1] She laughed."])
    assert out[0]["name"] == "the countess" and stats == GuardStats()


def test_the_guard_flags_are_pipeline_written_and_say_nothing_of_a_reference() -> None:
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        PIPELINE_WRITTEN_KEYS,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
        kept_as_reference,
    )

    assert {ALIAS_CROSSED_KNOWN_KEY, REFERENT_UNEVIDENCED_KEY} <= PIPELINE_WRITTEN_KEYS
    known = _known_rostovs()
    rec = _e("Count Ilyá Rostóv", aliases=["the old count"], sent_ref="S1")
    rec["description"] = ""
    (out,), _ = known.guard([rec], ["[S1] The old count laughed."])
    assert out["name"] == "the old count"
    assert not kept_as_reference({**out, "sent_ref": None})  # its flag is no text property


def test_the_name_rung_reads_naming_words_of_the_callers_length() -> None:
    known = KnownEntities()
    known.add_group([_e("Count Ilyá Rostóv"), _e("Count Cyril Bezúkhov")], [])  # two counts
    rec = _e("Count Ilyá Rostóv", aliases=["the old count"], sent_ref="S1")
    sentences = ["[S1] Ilyá, the old count, laughed."]
    out, stats = known.guard([rec], sentences)  # "ilya": four letters, the default
    assert out[0]["name"] == "Count Ilyá Rostóv" and stats == GuardStats()
    out, stats = known.guard([rec], sentences, min_word_length=5)
    assert out[0]["name"] == "the old count" and stats.names_demoted == 1


def test_a_record_citing_no_readable_sentence_is_left_to_the_evidence_filter() -> None:
    known = _known_rostovs()
    for sent_ref in ("", "S1 to S2", "S9"):
        rec = _e("Count Ilyá Rostóv", aliases=["the old count"], sent_ref=sent_ref)
        out, stats = known.guard([rec], ["[S1] The old count laughed."])
        assert out[0]["name"] == "Count Ilyá Rostóv" and stats == GuardStats()


def test_the_guard_does_not_mutate_its_input() -> None:
    known = _known_rostovs()
    rec = _e("Count Cyril Vladímirovich Bezúkhov", aliases=["Ilyá"])
    known.guard([rec], ["[S1] Count Cyril Vladímirovich"])
    assert rec["aliases"] == ["Ilyá"] and rec["properties"] == {}


# -- the filter step --------------------------------------------------------------------------


class _RecordingAdapter:
    """The source-row counter surface, recording each increment."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def increment_source_counter(
        self, *, source_id: str, database_name: str, column: str, n: int
    ) -> None:
        self.counts[column] = self.counts.get(column, 0) + n

    def update_source_columns(
        self, *, source_id: str, database_name: str, updates: dict[str, object]
    ) -> None:
        raise AssertionError(updates)


_CHUNK = "Count Cyril Bezúkhov is ill. The old count laughed. Ilyá wept."
_PASS1 = (
    "E|Count Cyril Bezúkhov|Person|Ilyá|1.0|S1|A count who is ill.\n"
    "E|Count Ilyá Rostóv|Person|the old count|1.0|S2|A count who laughs.\n"
)


@pytest.mark.asyncio
async def test_extract_single_chunk_renders_the_list_and_guards_pass_one() -> None:
    from chaoscypher_core.services.quality.counters import QualityCounter
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )
    from chaoscypher_core.settings import EngineSettings
    from chaoscypher_core.testing.extraction_llm import ScriptedExtractionLLM

    known = KnownEntities()
    known.add_group([_e("Count Ilyá Rostóv", aliases=["Ilyá"]), _e("Count Cyril Bezúkhov")], [])
    extractor = AIEntityExtractor(settings=EngineSettings())
    llm = ScriptedExtractionLLM(pass1_answers=[_PASS1])
    prompts = llm.prompts
    extractor.call_llm = llm  # type: ignore[method-assign]
    adapter = _RecordingAdapter()
    entities, _rels, _in, _out, metrics = await extractor.extract_single_chunk(
        chunk_content=_CHUNK,
        node_templates_formatted="- Person",
        edge_templates_formatted="- knows",
        adapter=adapter,
        source_id="s1",
        database_name="db",
        known=known,
    )
    assert known.block() in prompts[0]
    by_name = {e["name"]: e for e in entities}
    assert by_name["Count Cyril Bezúkhov"]["aliases"] == []
    assert by_name["Count Cyril Bezúkhov"]["properties"][ALIAS_CROSSED_KNOWN_KEY] == "Ilyá"
    assert by_name["the old count"]["properties"][REFERENT_UNEVIDENCED_KEY] == "Count Ilyá Rostóv"
    assert adapter.counts[QualityCounter.ALIASES_CROSSED_KNOWN_DROPPED.value] == 1
    assert adapter.counts[QualityCounter.REFERENTS_UNEVIDENCED_DEMOTED.value] == 1
    assert metrics["evidence_stats"]["known_guard"] == {
        "aliases_crossed": 1,
        "names_demoted": 1,
        "names_flagged": 0,
    }


@pytest.mark.parametrize(
    ("aliases", "name", "known_reason"),
    [
        ("", "Count Bezúkhov", True),  # the guard keeps the name flagged: the edge rule reads it
        ("the count", "the count", False),  # the guard demotes it first: a reference endpoint
    ],
)
@pytest.mark.asyncio
async def test_pass_two_reads_known_endpoints_as_the_guard_left_them(
    aliases: str, name: str, *, known_reason: bool
) -> None:
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )
    from chaoscypher_core.settings import EngineSettings
    from chaoscypher_core.testing.extraction_llm import ScriptedExtractionLLM

    known = KnownEntities()
    known.add_group([_e("Count Bezúkhov", aliases=["the count"])], [])
    extractor = AIEntityExtractor(settings=EngineSettings())
    extractor.call_llm = ScriptedExtractionLLM(  # type: ignore[method-assign]
        pass1_answers=[
            f"E|Natásha|Person||1.0|S1|A girl.\nE|Count Bezúkhov|Person|{aliases}|1.0|S2|A count.\n"
        ],
        pass2_answers=["R|0|1|child_of|0.9|S2|He embraces his daughter.\n"],
    )
    entities, rels, _in, _out, _metrics = await extractor.extract_single_chunk(
        chunk_content="Natásha ran in. The count threw his arms round the little girl.",
        node_templates_formatted="- Person",
        edge_templates_formatted="- child_of",
        known=known,
    )
    assert entities[1]["name"] == name
    (rel,) = rels
    reason = rel["properties"]["evidence_reason"]
    assert reason.endswith("(a known entity needs its name in the sentence)") is known_reason


# -- a known name the guard kept and flagged (final review 2 of 2026-10-12, I1) ---------------

_KEPT_SENTENCES = ["[S1] Ilse Marren ran to the count, her father, and kissed him."]
_KEPT_REASON = (
    '"Count Oswin Kell" is not named in S1 (a known entity needs its name in the sentence)'
)


def _known_kell() -> KnownEntities:
    from tests.fakes.domain_tables import domain_conventions

    known = KnownEntities(conventions=domain_conventions("literary"))
    known.add_group([_e("Count Oswin Kell", aliases=["the count"])], [])
    return known


@pytest.mark.parametrize("mode", ["standard", "narrative"])
def test_a_known_name_kept_and_flagged_names_none_of_its_edges(mode: str) -> None:
    """Two reference phrases written: the guard keeps the known name flagged, and no rung names it.

    The reviewer's reproduction: until this round the daughter's ``child_of`` came out evidenced,
    unflagged, through the one-endpoint rung (her name and "father" written), the v5e
    "Papa", "the count" row.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils import evidence_validator as ev
    from tests.fakes.domain_tables import domain_conventions, domain_vocabulary

    known = _known_kell()
    (count,), stats = known.guard(
        [_e("Count Oswin Kell", aliases=["the count", "her father"])], _KEPT_SENTENCES
    )
    assert stats == GuardStats(names_flagged=1)
    assert count["name"] == "Count Oswin Kell"
    assert count["properties"][REFERENT_UNEVIDENCED_KEY] == "the count; her father"
    ilse = _e("Ilse Marren")
    rel = {"source": 0, "target": 1, "type": "child_of", "sent_ref": "S1"}
    inputs = {
        "vocabulary": domain_vocabulary("literary"),
        "conventions": domain_conventions("literary"),
        "known": known,
    }
    verdict = ev.relationship_evidence(rel, [ilse, count], _KEPT_SENTENCES, mode, **inputs)
    assert verdict.failure is ev.EvidenceFailure.KNOWN_ENDPOINT_NOT_NAMED
    assert verdict.reason == _KEPT_REASON
    # the flag is what refuses it: the same record unflagged stands on the one-endpoint rung
    unflagged = {**count, "properties": {}}
    assert ev.relationship_evidence(
        rel, [ilse, unflagged], _KEPT_SENTENCES, mode, **inputs
    ).evidenced
    # and no rule names it either, not even its full name in the edge's own sentence
    named = ["[S1] Ilse Marren kissed Count Oswin Kell, her father."]
    verdict = ev.relationship_evidence(rel, [ilse, count], named, mode, **inputs)
    assert verdict.failure is ev.EvidenceFailure.KNOWN_ENDPOINT_NOT_NAMED


def test_a_known_name_kept_with_no_phrase_names_none_of_its_edges() -> None:
    """No phrase written: the guard keeps the name (the flag holds it), and its edge is refused."""
    from chaoscypher_core.services.sources.engine.extraction.utils import evidence_validator as ev

    known = _known_kell()
    sentences = ["[S1] Ilse Marren kissed him and laughed."]
    (count,), stats = known.guard([_e("Count Oswin Kell")], sentences)
    assert stats == GuardStats(names_flagged=1)
    assert count["properties"][REFERENT_UNEVIDENCED_KEY] == "Count Oswin Kell"
    assert KnownEntities.kept_unevidenced(count)
    verdict = ev.relationship_evidence(
        {"source": 0, "target": 1, "type": "knows", "sent_ref": "S1"},
        [_e("Ilse Marren"), count],
        sentences,
        "standard",
        known=known,
    )
    assert verdict.failure is ev.EvidenceFailure.KNOWN_ENDPOINT_NOT_NAMED


@pytest.mark.parametrize("mode", ["strict", "standard", "narrative"])
def test_a_record_demoted_to_a_known_alias_is_read_by_its_new_name(mode: str) -> None:
    """Demoted, the record is read as one written under its phrase: never refused as a kept name.

    The coordinator's correction to I1: a record demoted to "the King of Valdria" (another known
    entry's distinctive alias), cited "Ilse Marren bowed to the King of Valdria.", is evidenced.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils import evidence_validator as ev

    known = _known_kell()
    known.add_group([_e("Prince Orlan Vesk", aliases=["the King of Valdria"])], [])
    demoted = _e("the King of Valdria", **{REFERENT_UNEVIDENCED_KEY: "Count Oswin Kell"})
    assert not KnownEntities.kept_unevidenced(demoted)
    assert known.resolves("the King of Valdria") is not None  # a known endpoint, by rule 2
    verdict = ev.relationship_evidence(
        {"source": 0, "target": 1, "type": "knows", "sent_ref": "S1"},
        [_e("Ilse Marren"), demoted],
        ["[S1] Ilse Marren bowed to the King of Valdria."],
        mode,
        known=known,
    )
    assert verdict.evidenced
    # a demoted record whose phrase the sentence does not write falls as any record would
    elsewhere = ev.relationship_evidence(
        {"source": 0, "target": 1, "type": "knows", "sent_ref": "S1"},
        [_e("Ilse Marren"), demoted],
        ["[S1] Ilse Marren rode to Valdria."],
        mode,
        known=known,
    )
    assert elsewhere.failure is ev.EvidenceFailure.KNOWN_ENDPOINT_NOT_NAMED


def test_kept_unevidenced_reads_the_guards_own_writing() -> None:
    """Kept: the flag holds the name or the record's two or more phrases; demoted: the old name."""
    kept_name = _e("Count Oswin Kell", **{REFERENT_UNEVIDENCED_KEY: "Count Oswin Kell"})
    kept_phrases = _e(
        "Count Oswin Kell",
        aliases=["the count", "her father"],
        **{REFERENT_UNEVIDENCED_KEY: "the count; her father"},
    )
    demoted = _e("the count", **{REFERENT_UNEVIDENCED_KEY: "Count Oswin Kell"})
    unflagged = _e("Count Oswin Kell")
    assert [
        KnownEntities.kept_unevidenced(r) for r in (kept_name, kept_phrases, demoted, unflagged)
    ] == [True, True, False, False]


def test_a_demoted_record_reads_the_reference_rungs_as_before() -> None:
    """One phrase written: the record is demoted to it, and its edge is a reference endpoint's."""
    from chaoscypher_core.services.sources.engine.extraction.utils import evidence_validator as ev
    from tests.fakes.domain_tables import domain_conventions, domain_vocabulary

    known = _known_kell()
    sentences = ["[S1] Ilse Marren ran to the count and kissed her father."]
    (count,), stats = known.guard([_e("Count Oswin Kell", aliases=["the count"])], sentences)
    assert stats == GuardStats(names_demoted=1) and count["name"] == "the count"
    verdict = ev.relationship_evidence(
        {"source": 0, "target": 1, "type": "child_of", "sent_ref": "S1"},
        [_e("Ilse Marren"), count],
        sentences,
        "standard",
        vocabulary=domain_vocabulary("literary"),
        conventions=domain_conventions("literary"),
        known=known,
    )
    assert verdict.evidenced


@pytest.mark.asyncio
async def test_a_flagged_known_name_is_counted_and_its_edge_kept_flagged() -> None:
    """``referents_unevidenced_demoted`` counts a known name demoted or kept flagged (ruling (c))."""
    from chaoscypher_core.services.quality.counters import QualityCounter
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )
    from chaoscypher_core.settings import EngineSettings
    from chaoscypher_core.testing.extraction_llm import ScriptedExtractionLLM

    extractor = AIEntityExtractor(settings=EngineSettings())
    extractor.call_llm = ScriptedExtractionLLM(  # type: ignore[method-assign]
        pass1_answers=[
            "E|Ilse Marren|Person||1.0|S1|A girl.\n"
            "E|Count Oswin Kell|Person|the count, her father|1.0|S1|Her father.\n"
        ],
        pass2_answers=["R|0|1|child_of|0.9|S1|She kisses her father.\n"],
    )
    adapter = _RecordingAdapter()
    entities, rels, _in, _out, metrics = await extractor.extract_single_chunk(
        chunk_content=_KEPT_SENTENCES[0].removeprefix("[S1] "),
        node_templates_formatted="- Person",
        edge_templates_formatted="- child_of",
        adapter=adapter,
        source_id="s1",
        database_name="db",
        known=_known_kell(),
    )
    assert entities[1]["name"] == "Count Oswin Kell"
    assert metrics["evidence_stats"]["known_guard"] == {
        "aliases_crossed": 0,
        "names_demoted": 0,
        "names_flagged": 1,
    }
    assert adapter.counts[QualityCounter.REFERENTS_UNEVIDENCED_DEMOTED.value] == 1
    (rel,) = rels
    assert rel["properties"]["evidence_reason"] == _KEPT_REASON


@pytest.mark.asyncio
async def test_the_list_is_the_one_input_and_no_list_guards_nothing() -> None:
    """``known`` alone gives the block and the guards: no second input can disagree with it.

    Until the final review of 2026-10-11 (M3) a ``known_entities`` string beside ``known``
    drove the block while ``known`` drove the guards.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )
    from chaoscypher_core.settings import EngineSettings
    from chaoscypher_core.testing.extraction_llm import ScriptedExtractionLLM

    known = KnownEntities()
    known.add_group([_e("Count Ilyá Rostóv", aliases=["Ilyá"])], [])
    extractor = AIEntityExtractor(settings=EngineSettings())
    llm = ScriptedExtractionLLM(pass1_answers=[_PASS1])
    prompts = llm.prompts
    extractor.call_llm = llm  # type: ignore[method-assign]
    common = {"node_templates_formatted": "- Person", "edge_templates_formatted": "- knows"}
    with pytest.raises(TypeError, match="known_entities"):
        await extractor.extract_single_chunk(
            chunk_content=_CHUNK, **common, known_entities="", known=known
        )
    assert prompts == []  # refused before any call
    entities, *_rest = await extractor.extract_single_chunk(chunk_content=_CHUNK, **common)
    assert KNOWN_ENTITIES_HEADER not in prompts[0]
    assert [e["aliases"] for e in entities if e["name"] == "Count Cyril Bezúkhov"] == [["Ilyá"]]


@pytest.mark.asyncio
async def test_the_filter_step_hands_the_guard_its_naming_word_length() -> None:
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        _filter_entities,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        FilteringConfig,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )

    known = KnownEntities()
    known.add_group([_e("Count Ilyá Rostóv"), _e("Count Cyril Bezúkhov")], [])  # two counts
    sentences = ["[S1] Ilyá, the old count, laughed."]
    names = []
    for length in (4, 5):
        rec = _e("Count Ilyá Rostóv", aliases=["the old count"], sent_ref="S1")
        out, stats = await _filter_entities(
            entities=[rec],
            sentences=sentences,
            chunk_content=sentences[0],
            filtering_config=FilteringConfig(
                evidence_validation_mode="standard",
                min_significant_word_length=length,
                enable_plausibility_filter=False,
            ),
            entity_exclusions=None,
            valid_entity_type_names=None,
            named_referent_types=None,
            normalization_rules=None,
            property_type_mapping=None,
            filtering_log=FilteringLog(),
            known=known,
        )
        names.append(out[0]["name"])
        assert stats["known_guard"]["names_demoted"] == (length == 5)
    assert names == ["Count Ilyá Rostóv", "the old count"]


def test_known_entities_from_rows_folds_one_group_per_row() -> None:
    """The queue path's builder: one ``add_group`` per stored row, chunk-local edges kept."""
    from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
        known_entities_from_rows,
    )

    rows = [
        {
            "chunk_index": 0,
            "raw_entities": [_e("Pierre Bezukhov"), _e("Natásha Rostova")],
            "raw_relationships": [{"source": 0, "target": 1, "type": "friend_of"}],
        },
        {"chunk_index": 1, "raw_entities": None, "raw_relationships": None},
        {"chunk_index": 2, "raw_entities": [_e("Natásha Rostova")], "raw_relationships": []},
    ]
    known = KnownEntities()
    assert known_entities_from_rows(rows, known=known) is known
    assert known.groups == 3
    assert len(known) == 2
    assert known.stats() == {"groups": 3, "entities": 2}
    assert known.lines() == [
        "- Natásha Rostova (Person)",
        "- Pierre Bezukhov (Person) — friend of Natásha Rostova",
    ]


def test_a_zero_limit_turns_the_list_off() -> None:
    """``enabled`` is what the queue path reads to skip loading the lower rows."""
    assert KnownEntities().enabled
    off = KnownEntities(limit=0)
    assert not off.enabled
    off.add_group([_e("Pierre Bezukhov")], [])
    assert off.block() == ""


# -- a record the pipeline doubts never enters the list (final review I1) -----------------------


@pytest.mark.parametrize("flag", DOUBTFUL_NODE_FLAG_KEYS)
def test_a_record_a_per_chunk_filter_doubted_never_enters_the_list(flag: str) -> None:
    """Kept and flagged (keep, flag, never delete) is no name the source gave: not listed.

    Not rendered, no alias registered, no group counted for it, no edge hung on it.
    """
    doubted = _e("Zorbex Quell", aliases=["the stranger", "Zorbex"])
    doubted["properties"] = with_pipeline_flag(doubted["properties"], flag, on=True)
    known = KnownEntities()
    edges = [
        {"source": 0, "target": 1, "type": "friend_of"},
        {"source": 1, "target": 0, "type": "friend_of"},
    ]
    known.add_group([doubted, _e("Oswin Kell")], edges)
    assert known.lines() == ["- Oswin Kell (Person)"]
    assert known._resolve(fold("Zorbex")) is None
    assert known._resolve(fold("the stranger")) is None
    assert known.groups == 1  # the group itself is seen
    known.add_group([_e("Zorbex Quell")], [])  # evidenced later: its entry starts there
    assert known.names()[fold("Zorbex Quell")].groups == 1


def test_a_record_the_alias_guard_repaired_is_listed_without_the_crossed_alias() -> None:
    """A crossed alias is no doubt of the record's name (spec D3): the guard took it off.

    What remains is a correctly named record: listed, its other aliases and its edges kept,
    the alias it borrowed from another entry neither shown nor resolving to it.
    """
    known = KnownEntities()
    known.add_group([_e("Count Ilyá Rostóv", aliases=["Ilyá"]), _e("Pierre")], [])
    rec = _e("Count Cyril Bezúkhov", aliases=["Ilyá", "Kiryusha"])
    out, stats = known.guard([rec, _e("Pierre")], ["[S1] Count Cyril Bezúkhov embraced Pierre."])
    assert out[0]["aliases"] == ["Kiryusha"]
    assert out[0]["properties"][ALIAS_CROSSED_KNOWN_KEY] == "Ilyá" and stats.aliases_crossed == 1
    known.add_group(out, [{"source": 0, "target": 1, "type": "friend_of"}])
    key = fold("Count Cyril Bezúkhov")
    entry = known.names()[key]
    assert (entry.aliases, entry.relations, entry.groups) == (
        ("Kiryusha",),
        ("friend of Pierre",),
        1,
    )
    assert known._resolve(fold("Kiryusha")) == key
    assert known._resolve(fold("Ilyá")) == fold("Count Ilyá Rostóv")
    assert "- Count Cyril Bezúkhov (Person) — friend of Pierre" in known.lines()  # no single word


def test_a_record_the_name_guard_flagged_never_enters_the_list() -> None:
    known = _known_rostovs()
    rec = _e("Semënov regiment", "Organization", aliases=["the regiment", "the guards"])
    out, _ = known.guard([rec], ["[S1] The regiment and the guards marched."])
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "the regiment; the guards"
    known.add_group(out, [])
    entry = known.names()[fold("Semënov regiment")]
    assert entry.groups == 1 and entry.aliases == ("the regiment",)


def test_a_name_the_guard_doubted_does_not_clear_itself_in_the_next_group() -> None:
    """The review's probe: the aliases of a record flagged in one group are no evidence in the next.

    Group 2's record gives ``the old count`` beside a name its sentence does not write, and the
    guard flags it; were it folded in, ``the old count`` would be a known alias of the entry,
    and group 3's sentence writing only that would clear the name it doubted.
    """
    known = KnownEntities()
    known.add_group([_e("Count Ilyá Rostóv"), _e("Count Cyril Bezúkhov")], [])  # two counts
    doubted = _e("Count Ilyá Rostóv", aliases=["the count", "the old count"])
    out, stats = known.guard([doubted], ["[S1] The count smiled, and the old count laughed."])
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "the count; the old count"
    assert stats.names_flagged == 1
    known.add_group(out, [])
    out, stats = known.guard([_e("Count Ilyá Rostóv")], ["[S1] The old count laughed."])
    assert out[0]["properties"][REFERENT_UNEVIDENCED_KEY] == "Count Ilyá Rostóv"
    assert stats.names_flagged == 1


def test_one_reading_of_a_record_the_pipeline_doubts() -> None:
    """``is_doubted_record`` is what the list leaves out; it reads the filters' flags as retrieval does.

    Every :data:`DOUBTFUL_NODE_FLAG_KEYS` flag is doubt for both readers (one predicate,
    ``doubted_by_filters``); the name guard's ``referent_unevidenced`` is doubt for the list
    alone, and the alias guard's ``alias_crossed_known`` is no doubt at all.
    """
    for key in DOUBTFUL_NODE_FLAG_KEYS:
        props = with_pipeline_flag({}, key, on=True)
        assert doubted_by_filters(props) and is_doubted_record(props), key
        assert node_standing(props).doubtful, key
        assert not is_doubted_record({key: "yes"}), key  # a text value is no flag
    assert is_doubted_record({REFERENT_UNEVIDENCED_KEY: "the count"})
    assert not is_doubted_record({REFERENT_UNEVIDENCED_KEY: ""})
    assert not is_doubted_record({REFERENT_UNEVIDENCED_KEY: True})
    assert not node_standing({REFERENT_UNEVIDENCED_KEY: "the count"}).doubtful
    # a crossed alias is repaired, not doubt: the record is named correctly once it is dropped
    assert not is_doubted_record({ALIAS_CROSSED_KNOWN_KEY: "Ilyá"})
    assert not is_doubted_record(None) and not is_doubted_record({})


def test_the_list_imports_no_private_helper_of_another_module() -> None:
    """The naming-word reading it shares with the evidence filter is public there (M5)."""
    import ast
    import inspect

    from chaoscypher_core.services.sources.engine.extraction.utils import known_entities

    tree = ast.parse(inspect.getsource(known_entities))
    private = [
        f"{node.module}:{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("chaoscypher_")
        for alias in node.names
        if alias.name.startswith("_")
    ]
    assert private == []
