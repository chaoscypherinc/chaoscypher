# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The evidence validator names an entity by whole words and the domain's tables (audit wave 6).

W6-3: the entity half of the evidence rule matched name words as substrings,
titles included: "prince" inside "princess" named every prince of a chunk in
which a princess spoke, "Ann" named Anna, and "Monsieur" (of "Monsieur
Buonaparte") named Buonaparte in a sentence about Monsieur Pierre. Now a
naming word (titles left out) names an entity as a whole word, read through
the domain's surname forms and plurals, and a title alone names only the one
candidate holding it (the title rung).

The Book One sentences are the v3 extraction's citations. Three of the
relationships the substring rule kept name their second endpoint only by a
pronoun ("he", "him"); they are true facts of the book, but nothing in the
cited sentences names that endpoint, and the validator does no coreference:
they drop, as every relationship of a two-endpoints-only label resting on a
pronoun does.
"""

# The cited sentences keep the book's typographic quotes: they are what the validator reads.
# ruff: noqa: RUF001

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication.name_identity import NameConventions
from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
    CitedText,
    NamingContext,
    _entity_named,
    validate_entity_evidence,
    validate_relationship_evidence,
)
from tests.fakes.domain_tables import domain_conventions, domain_vocabulary


LITERARY = domain_conventions("literary")
GENERIC = domain_conventions("generic")
VOCABULARY = domain_vocabulary("literary")


def _c(name: str, *aliases: str, kind: str = "Character") -> dict[str, Any]:
    """An entity of the sample chunk."""
    return {"name": name, "type": kind, "aliases": list(aliases)}


PIERRE = _c("Pierre", "Monsieur Pierre", "Pierre Bezúkhov", "Bezúkhov’s son")
ANDREW = _c("Prince Andrew", "Andrew", "André")
VASILI = _c("Prince Vasíli Kurágin", "Vasíli", "Vasíli Kurágin", "Prince Vasíli")
CYRIL = _c("Cyril Vladímirovich Bezúkhov", "Count Cyril", "Count Cyril Vladímirovich")
ANNA_M = _c("Princess Anna Mikháylovna Drubetskáya", "Anna Mikháylovna")
BUONAPARTE = _c("Buonaparte", "Monsieur Buonaparte")


def _kept(
    source: dict[str, Any],
    target: dict[str, Any],
    label: str,
    sentences: list[str],
    *,
    others: tuple[dict[str, Any], ...] = (),
    conventions: NameConventions = LITERARY,
    mode: str = "standard",
) -> bool:
    """The relationship ``source -label-> target`` cited on all ``sentences``, among ``others``."""
    entities = [source, target, *others]
    rel = {"source": 0, "target": 1, "type": label, "sent_ref": f"S1-S{len(sentences)}"}
    return validate_relationship_evidence(
        rel, entities, sentences, mode=mode, vocabulary=VOCABULARY, conventions=conventions
    )


# -- the auditor's Book One cases ---------------------------------------------------------------


def test_pierre_and_andrew_both_named_stay() -> None:
    """S1-S3 of chapter IV: "said Prince Andrew" and "said Pierre"."""
    sentences = [
        "Pierre gazed at her with rapturous, almost frightened, eyes as she passed him.",
        "“Very lovely,” said Prince Andrew.",
        "“Very,” said Pierre.",
    ]
    assert _kept(PIERRE, ANDREW, "interacts_with", sentences)


@pytest.mark.parametrize(
    ("source", "target", "label", "sentences"),
    [
        (
            PIERRE,
            ANDREW,
            "interacts_with",
            [
                "“When are you starting?” he asked.",
                "Pierre looked over his spectacles with naïve surprise, now at him and now at "
                "her, moved as if about to rise too, but changed his mind.",
                "Pierre, who had been growing more and more agitated as he listened to all "
                "this, rose and approached the princess.",
            ],
        ),
        (
            VASILI,
            CYRIL,
            "interacts_with",
            [
                "“Yes, but between ourselves,” said the princess, “that is a pretext. The fact "
                "is he has come to see Count Cyril Vladímirovich, hearing how ill he is.”",
            ],
        ),
        (
            VASILI,
            ANNA_M,
            "related_to",
            [
                "“He is just the same as ever,” replied Anna Mikháylovna, “overflowing with "
                "amiability. He said to me, ‘I am sorry I can do so little for you, dear "
                "Princess.’ Yes, he is a fine fellow and a very kind relation.”",
            ],
        ),
    ],
)
def test_an_endpoint_named_only_by_a_pronoun_is_not_named(
    source: dict[str, Any], target: dict[str, Any], label: str, sentences: list[str]
) -> None:
    """The three edges the substring rule kept on "prince" inside "princess" (q103 among them).

    True facts, but their cited sentences name the prince only as "he" or
    "him"; "princess" holds no "prince", and the relation is either
    two-endpoints-only (``interacts_with``) or not stated by a kin word
    ("relation" left the vocabulary in wave 5: "in relation to").
    """
    assert not _kept(source, target, label, sentences)
    assert not _kept(source, target, label, sentences, mode="narrative")
    # the endpoint the text does name is still found
    cited = CitedText.read(" ".join(sentences), LITERARY)
    naming = NamingContext([source, target], LITERARY)
    named = [_entity_named(cited, e, "standard", naming=naming, position=k)
             for k, e in enumerate((source, target))]  # fmt: skip
    assert named.count(True) == 1


def test_a_bare_title_held_by_two_candidates_names_neither() -> None:
    """In "declared Monsieur Pierre" beside Monsieur Buonaparte: "Monsieur" alone names no one."""
    sentence = ["“I say so,” declared Monsieur, “because the Bourbons fled.”"]
    cited = CitedText.read(sentence[0], LITERARY)
    both = NamingContext([PIERRE, BUONAPARTE], LITERARY)
    assert not _entity_named(cited, BUONAPARTE, "standard", naming=both, position=1, alongside=(0,))
    assert not _entity_named(cited, PIERRE, "standard", naming=both, position=0, alongside=(1,))
    alone = NamingContext([BUONAPARTE, _c("the Bourbons", kind="Social Group")], LITERARY)
    assert _entity_named(cited, BUONAPARTE, "standard", naming=alone, position=0)
    # strict never reads a title alone
    assert not _entity_named(cited, BUONAPARTE, "strict", naming=alone, position=0)


def test_a_title_names_the_one_prince_the_text_has() -> None:
    """In "replied the prince": Prince Vasíli, the only prince among the chunk's candidates the text names."""
    sentences = ["“Don’t tease! What has been decided about Novosíltsev’s dispatch?”",
                 "“What can one say about it?” replied the prince in a cold tone."]  # fmt: skip
    novosiltsev = _c("Novosíltsev")
    assert _kept(VASILI, novosiltsev, "references", sentences, others=(PIERRE,))
    # another prince named in the same text makes "the prince" ambiguous
    sentences_two = [*sentences, "Prince Andrew looked away."]
    assert not _kept(VASILI, novosiltsev, "interacts_with", sentences_two, others=(ANDREW,))


def test_the_title_rung_counts_a_holder_the_text_names_by_name() -> None:
    """In "Dear Countess ... and Countess Apráksina": the countess addressed may be either."""
    rostova = _c("Countess Rostóva")
    apraksina = _c("Countess Apráksina")
    sentences = ["“Dear Countess, what an age... and Countess Apráksina ... I was so delighted...”"]
    assert not _kept(rostova, apraksina, "references", sentences)
    karagina = _c("Márya Lvóvna Karágina", "Márya Lvóvna")
    footman = ["“Márya Lvóvna Karágina and her daughter!” announced the countess’ footman."]
    # Apráksina is a candidate of the chunk, but this text names neither her nor anyone else titled
    assert _kept(karagina, rostova, "interacts_with", footman, others=(apraksina,))


# -- whole words through the domain's tables ------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "text", "named"),
    [
        ("Ann", "Anna arrived.", False),
        ("Prince Hippolyte", "The princess arrived.", False),
        ("Count Rostóv", "They dined with the Rostóvs.", True),
        ("Count Rostóv", "They called on the Rostóvs’ house.", True),
        # wave 7: one surname form names one gender (``name_identity.names_person``)
        ("Count Rostóv", "Countess Rostóva smiled.", False),
        ("Countess Rostóva", "Countess Rostóva smiled.", True),
        ("Countess Rostóva", "They dined with the Rostóvs.", True),  # the family's plural
        ("Princess Drubetskáya", "Borís Drubetskóy bowed.", False),
        ("Kurágin", "The Kuráginsky affair.", False),
        ("Dólokhov", "He is a son of Márya Ivánovna Dólokhova.", False),
    ],
)
def test_a_naming_word_names_whole_through_surname_forms_and_plurals(
    name: str, text: str, named: bool
) -> None:
    """Under the literary tables: "Rostóv" names the Rostóvs, "Ann" never names Anna.

    Since audit wave 7 a woman's surname form never names a man, nor a man's
    a woman (the dedup's family-form check, one predicate); the plural names
    the family, any member of it.
    """
    cited = CitedText.read(text, LITERARY)
    assert (
        _entity_named(cited, {"name": name}, "standard", naming=NamingContext(None, LITERARY))
        is named
    )


def test_the_universal_tables_know_no_russian_surname_forms() -> None:
    """Under the generic arm "Dólokhova" is no form of "Dólokhov" (the auditor's one generic drop)."""
    cited = CitedText.read("He is a son of Márya Ivánovna Dólokhova.", GENERIC)
    assert not _entity_named(
        cited, {"name": "Dólokhov"}, "standard", naming=NamingContext(None, GENERIC)
    )


def test_entity_evidence_reads_the_chunks_candidates() -> None:
    """``validate_entity_evidence`` with the chunk's entities: a title alone names a sole holder only."""
    sentences = ["“What can one say about it?” replied the prince to Prince Andrew."]
    vasili = dict(VASILI, sent_ref="S1")
    andrew = dict(ANDREW, sent_ref="S1")
    assert validate_entity_evidence(vasili, sentences, "standard", conventions=LITERARY,
                                    candidates=[vasili])  # fmt: skip
    # Prince Andrew, named in the same sentence, holds the title too
    assert not validate_entity_evidence(vasili, sentences, "standard", conventions=LITERARY,
                                        candidates=[vasili, andrew])  # fmt: skip
    assert not validate_entity_evidence(vasili, sentences, "standard", conventions=LITERARY)


# -- the plan carries the tables to both evidence filters ---------------------------------------


def test_the_plan_carries_the_domains_naming_conventions() -> None:
    """``build_extraction_plan`` stores them; the queue's config JSON round-trips them unchanged."""
    import json

    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
    from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
        build_extraction_plan,
        chunk_kwargs_from_config,
        chunk_kwargs_to_config,
    )
    from chaoscypher_core.settings import EngineSettings

    settings = EngineSettings()
    registry = get_domain_registry(settings)
    for info in registry.list_domain_info():
        name = str(info["name"])
        plan = build_extraction_plan(DomainStack.from_domain_name(registry, name), settings)
        tables = plan.chunk_kwargs["name_conventions"]
        assert tables == domain_conventions(name).as_dict(), name
        assert NameConventions.from_dict(tables).as_dict() == tables, name
        stored = json.loads(json.dumps(chunk_kwargs_to_config(plan.chunk_kwargs)))
        assert chunk_kwargs_from_config(stored)["name_conventions"] == tables


@pytest.mark.asyncio
async def test_the_chunk_extractor_names_endpoints_by_the_plans_tables() -> None:
    """Pass 2's evidence filter (``parse_harvest_outputs``) reads the plan's surname forms.

    Without the tables "the Rostóvs" names no Countess Rostóva: since 2026-10-08
    (keep, flag, never delete) she is kept flagged ``unevidenced`` for her
    description, and the relationship to her is kept flagged too, so the
    tables decide what is evidenced, not what survives.
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        UNEVIDENCED_KEY,
        pipeline_flag,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )
    from chaoscypher_core.settings import EngineSettings

    extractor = AIEntityExtractor(settings=EngineSettings())
    sentences = ["Pierre dined with the Rostóvs."]
    entities = (
        "E|Countess Rostóva|Character||0.9|S1|His hostess\nE|Pierre|Character||0.9|S1|A young man"
    )
    relationships = "R|0|1|interacts_with|0.9|S1|Pierre dined with her family"

    async def kept(tables: dict[str, Any] | None) -> tuple[int, int, int]:
        """Evidenced relationships, all relationships and flagged entities under naming ``tables``."""
        limits = extractor.resolve_harvest_limits(
            evidence_validation_mode="standard",
            relation_vocabulary=VOCABULARY.as_config(),
            name_conventions=tables,
        )
        parsed = await extractor.parse_harvest_outputs(
            entities,
            relationships,
            sentences=sentences,
            chunk_content=sentences[0],
            numbered_text="",
            edge_templates_formatted="",
            limits=limits,
        )
        evidenced = [
            r
            for r in parsed.relationships
            if not pipeline_flag(r.get("properties"), UNEVIDENCED_KEY)
        ]
        flagged = [
            e for e in parsed.entities if pipeline_flag(e.get("properties"), UNEVIDENCED_KEY)
        ]
        return len(evidenced), len(parsed.relationships), len(flagged)

    assert await kept(LITERARY.as_dict()) == (1, 1, 0)
    assert await kept(None) == (0, 1, 1)
