# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A kin word states a kinship relation for the one-endpoint evidence rule (audit W2-12).

The kin words are the source domain's ``relation_vocabulary`` (the literary
domain's here, read the way production reads it), extending the universal
kin vocabulary; the labels' own words are never evidence (wave 5).

Standard mode accepted a relationship whose cited sentences name one
endpoint only when they also carried a word of the label itself
("sibling", "parent"), which prose almost never uses: on pack v3, 81 of 330
relationships would drop in standard mode, among them true kin facts the
book states with "elder sister", "a father" and "your son". The three
Book One sentences below are those facts, cited as the extractor cited them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
    filter_relationships_by_evidence,
    validate_relationship_evidence,
)
from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
    EMPTY_RELATION_VOCABULARY,
    UNIVERSAL_VOCABULARY,
    RelationVocabulary,
    extend_vocabulary,
    states_relation,
)
from tests.fakes.domain_tables import domain_vocabulary


LITERARY = domain_vocabulary("literary")
"""The literary domain's relation vocabulary."""


_PLUGINS = (
    Path(__file__).parents[7]
    / "src/chaoscypher_core/services/sources/engine/extraction/domains/plugins"
)

# Book One, as the v3 extraction cited them (one endpoint named in each).
VERA_SENTENCES = [
    "With her elder sister I was stricter.” “Yes, I was brought up quite "
    "differently,” remarked the handsome elder daughter, Countess Véra, with a smile.",
]
VASILI_SENTENCES = [
    "“You know I did all a father could for their education, and they have both turned out fools.",
    "Hippolyte is at least a quiet fool, but Anatole is an active one.",
]
DRUBETSKAYA_SENTENCES = [
    "“My dear Anna Mikháylovna,” said he with his usual familiarity and "
    "weariness of tone, “it is almost impossible for me to do what you ask; but to "
    "prove my devotion to you and how I respect your father\u2019s memory, I will do the "
    "impossible—your son shall be transferred to the Guards.",
]

ENTITIES: list[dict[str, Any]] = [
    {"name": "Countess Véra", "aliases": ["Véra"]},
    {"name": "Natásha"},
    {"name": "Prince Vasíli Kurágin", "aliases": ["Prince Vasíli"]},
    {"name": "Prince Hippolyte", "aliases": ["Hippolyte"]},
    {"name": "Princess Drubetskáya", "aliases": ["Anna Mikháylovna"]},
    {"name": "Borís", "aliases": ["Bóry"]},
]

CASES = [
    ({"source": 0, "target": 1, "type": "sibling_of", "sent_ref": "S1"}, VERA_SENTENCES),
    ({"source": 2, "target": 3, "type": "parent_of", "sent_ref": "S1-S2"}, VASILI_SENTENCES),
    ({"source": 4, "target": 5, "type": "parent_of", "sent_ref": "S1"}, DRUBETSKAYA_SENTENCES),
]


@pytest.mark.parametrize(("rel", "sentences"), CASES)
@pytest.mark.parametrize("mode", ["standard", "narrative"])
def test_a_kin_word_with_one_named_endpoint_supports_the_kinship_edge(
    rel: dict[str, Any], sentences: list[str], mode: str
) -> None:
    """Each audit sentence names one endpoint and a kin word of the label: accepted."""
    assert validate_relationship_evidence(rel, ENTITIES, sentences, mode=mode, vocabulary=LITERARY)


@pytest.mark.parametrize(("rel", "sentences"), CASES)
def test_strict_still_needs_both_endpoints(rel: dict[str, Any], sentences: list[str]) -> None:
    """The kin word is a standard-rung allowance; strict is unchanged."""
    assert not validate_relationship_evidence(
        rel, ENTITIES, sentences, mode="strict", vocabulary=LITERARY
    )


@pytest.mark.asyncio
async def test_the_filter_keeps_the_three_kin_facts_and_flags_one_endpoint_alone() -> None:
    """Through the filter the pipeline calls: kin-worded facts stay, a bare one is kept flagged.

    Keep, flag, never delete (2026-10-07): the bare copy of the worded fact is
    folded into it (its justification kept), and the bare fact alone is kept
    flagged ``unevidenced`` with the reason; nothing is dropped.
    """
    sentences = [*VERA_SENTENCES, "Véra smiled at the guests and said nothing."]
    worded = {"source": 0, "target": 1, "type": "sibling_of", "sent_ref": "S1"}
    bare = {**worded, "sent_ref": "S2", "justification": "Véra is Natásha's sister."}
    kept, stats = await filter_relationships_by_evidence(
        [worded, bare], ENTITIES, sentences, mode="standard", vocabulary=LITERARY
    )
    assert kept == [{**worded, "properties": {"justifications": ["Véra is Natásha's sister."]}}]
    assert stats["relationships_dropped"] == 0
    assert stats["relationships_unevidenced"] == 1
    kept, stats = await filter_relationships_by_evidence(
        [bare], ENTITIES, sentences, mode="standard", vocabulary=LITERARY
    )
    assert [r["properties"]["unevidenced"] for r in kept] == [True]
    assert (
        "no word of the domain's relation vocabulary for sibling_of"
        in (kept[0]["properties"]["evidence_reason"])
    )


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Hippolyte embraced Sónya by the window.", "parent_of"),  # "son" inside a name
        ("Hippolyte spoke to every person there.", "parent_of"),  # "son" inside a word
        ("Hippolyte called on his godfather.", "parent_of"),  # a godfather is no parent
        ("Hippolyte had an elder sister.", "parent_of"),  # another relation's kin word
        ("Hippolyte had an elder sister.", "interacts_with"),  # not a kinship label
    ],
)
def test_a_kin_word_counts_only_whole_and_only_for_its_own_relation(text: str, label: str) -> None:
    """Whole words only, and a sibling word never states a parent edge."""
    rel = {"source": 2, "target": 3, "type": label, "sent_ref": "S1"}
    assert not LITERARY.states_relation(text, label)
    assert not validate_relationship_evidence(
        rel, ENTITIES, [text], mode="standard", vocabulary=LITERARY
    )


def test_both_labels_of_a_pair_share_their_relation_words() -> None:
    """A son states child_of as much as parent_of; spouse_of and married_to agree."""
    assert LITERARY.relation_words("parent_of") == LITERARY.relation_words("child_of")
    assert {"son", "father", "daughter"} <= LITERARY.relation_words("parent_of")
    assert LITERARY.relation_words("Sibling_Of") == frozenset(
        {"brother", "brothers", "sister", "sisters", "sibling", "siblings", "half-brother",
         "half-sister", "stepbrother", "stepsister"}
    )  # fmt: skip
    biographical = domain_vocabulary("biographical")
    assert biographical.relation_words("married_to") == LITERARY.relation_words("spouse_of")
    assert LITERARY.relation_words("interacts_with") == frozenset()


def test_a_domain_without_a_vocabulary_runs_the_rule_on_the_universal_one() -> None:
    """No declared vocabulary: the universal kin words count (generic, no domain); no vocabulary at all, none.

    The label's own words are never evidence (wave 5): with no vocabulary a
    relationship needs both endpoints named.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        UNIVERSAL_VOCABULARY,
    )

    rel = {"source": 0, "target": 1, "type": "sibling_of", "sent_ref": "S1"}
    assert domain_vocabulary("generic") == UNIVERSAL_VOCABULARY
    assert domain_vocabulary(None) == UNIVERSAL_VOCABULARY
    for vocabulary in (UNIVERSAL_VOCABULARY, domain_vocabulary("generic")):
        assert validate_relationship_evidence(
            rel, ENTITIES, VERA_SENTENCES, mode="standard", vocabulary=vocabulary
        )
    for vocabulary in (None, EMPTY_RELATION_VOCABULARY):
        assert not validate_relationship_evidence(
            rel, ENTITIES, VERA_SENTENCES, mode="standard", vocabulary=vocabulary
        )
        assert not validate_relationship_evidence(
            rel, ENTITIES, ["Véra is her sibling."], mode="standard", vocabulary=vocabulary
        )
    assert not states_relation("her elder sister", "sibling_of")


def test_every_domain_extends_the_one_universal_vocabulary() -> None:
    """The universal kin words are every domain's base (one source, wave 4): literary adds, never repeats."""
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        UNIVERSAL_RELATION_VOCABULARY,
        UNIVERSAL_RELATION_WORDS,
        fold,
    )

    for name, config in _plugins():
        vocabulary = domain_vocabulary(name)
        excluded = _two_endpoint_labels(config)
        for entry in UNIVERSAL_RELATION_VOCABULARY.values():
            for label in set(entry["labels"]) - excluded:
                assert set(entry["words"]) <= vocabulary.relation_words(label), (name, label)
        for relation, entry in (config.get("relation_vocabulary") or {}).items():
            base = set(UNIVERSAL_RELATION_VOCABULARY.get(relation, {}).get("words", []))
            assert not set(entry["words"]) & base, (name, relation, set(entry["words"]) & base)
    # the name rule's universal descriptor words are the same vocabulary's words
    assert {fold(w) for e in UNIVERSAL_RELATION_VOCABULARY.values() for w in e["words"]} <= (
        UNIVERSAL_RELATION_WORDS
    )


@pytest.mark.parametrize(
    ("text", "label", "states"),
    [
        ("the little princess' sister-in-law came in", "sibling_of", False),
        ("the little princess' sister-in-law came in", "related_to", True),
        ("Hippolyte proposed marriage to her.", "spouse_of", False),
        ("Hippolyte proposed marriage to her.", "betrothed_to", True),
        ("her godfather sent a letter", "godparent_of", True),
        ("her godfather sent a letter", "parent_of", False),
    ],
)
def test_hyphenated_and_betrothal_words_state_their_own_relation(
    text: str, label: str, states: bool
) -> None:
    """``sister-in-law`` is one word (no sibling); a proposed marriage is a betrothal, not a marriage."""
    assert LITERARY.states_relation(text, label) is states


@pytest.mark.parametrize("mode", ["standard", "narrative"])
def test_a_proposal_naming_one_person_is_no_spouse_edge(mode: str) -> None:
    """Through the validator: one endpoint and "proposed marriage" support betrothed_to only."""
    sentences = ["Prince Hippolyte proposed marriage to her at the ball."]
    spouse = {"source": 3, "target": 1, "type": "spouse_of", "sent_ref": "S1"}
    betrothed = {**spouse, "type": "betrothed_to"}
    assert not validate_relationship_evidence(
        spouse, ENTITIES, sentences, mode=mode, vocabulary=LITERARY
    )
    assert validate_relationship_evidence(
        betrothed, ENTITIES, sentences, mode=mode, vocabulary=LITERARY
    )
    in_law = {"source": 0, "target": 1, "type": "sibling_of", "sent_ref": "S1"}
    assert not validate_relationship_evidence(
        in_law, ENTITIES, ["Countess Véra greeted the little princess' sister-in-law."],
        mode=mode, vocabulary=LITERARY,
    )  # fmt: skip


def test_the_vocabulary_round_trips_its_json_shape() -> None:
    """``as_config`` is what the extraction plan stores; ``from_config`` reads it back."""
    assert RelationVocabulary.from_config(LITERARY.as_config()) == LITERARY
    assert RelationVocabulary.from_config(LITERARY) is LITERARY


_KIN_STEMS = ("parent", "child", "sibling", "spouse", "married", "cousin", "grand", "uncle",
              "aunt", "nephew", "niece")  # fmt: skip


def _two_endpoint_labels(config: dict[str, Any]) -> set[str]:
    """The edge templates a plugin marks ``"two_endpoints_only": true``."""
    templates = (config.get("templates") or {}).get("edge_templates") or []
    return {t["name"] for t in templates if t.get("two_endpoints_only") is True}


def _plugins() -> list[tuple[str, dict[str, Any]]]:
    """Every shipped domain plugin as ``(name, config)``."""
    out = []
    for plugin in sorted(_PLUGINS.glob("*.jsonld")):
        config = json.loads(plugin.read_text(encoding="utf-8"))
        out.append((str(config["name"]), config))
    assert out, "no plugins found: the plugin path moved"
    return out


def test_every_domains_kinship_label_has_its_own_kin_words() -> None:
    """A family edge template a domain declares is in that domain's vocabulary, unless it is not kin."""
    checked = 0
    for name, config in _plugins():
        templates = config.get("templates") or {}
        labels = {t["name"] for t in templates.get("edge_templates") or [] if t.get("name")}
        family = {label for label in labels if any(stem in label for stem in _KIN_STEMS)}
        vocabulary = domain_vocabulary(name)
        not_kin = _two_endpoint_labels(config)
        for label in sorted(family):
            if label in not_kin:  # e.g. a financial parent_of: a parent company
                assert not vocabulary.relation_words(label), (name, label)
                continue
            checked += 1
            assert vocabulary.relation_words(label), (name, label)
    assert checked, "no family labels found"
    financial = dict(_plugins())["financial"]
    # a parent company is no parent: renamed off the core kin label (spec 2026-10-07 § 2)
    assert "parent_company_of" in _two_endpoint_labels(financial)
    assert "vocabulary_exclusions" not in financial  # one mechanism: the template's mark


def test_a_vocabulary_names_only_its_domains_edge_labels() -> None:
    """Every label a ``relation_vocabulary`` lists is an edge template of the domain or a core label.

    A pack's vocabulary may say which words state a core label (literary's
    "marriage" for the floor's ``spouse_of``): the floor declares the label for
    it. Never one the floor marks two-endpoints-only (``test_domain_resolver``).
    """
    from chaoscypher_core.services.sources.engine.extraction.domains.core_types import CORE_LABELS

    for name, config in _plugins():
        templates = config.get("templates") or {}
        labels = {t["name"] for t in templates.get("edge_templates") or [] if t.get("name")}
        labels |= CORE_LABELS
        for relation, entry in (config.get("relation_vocabulary") or {}).items():
            assert set(entry["labels"]) <= labels, (name, relation, set(entry["labels"]) - labels)


def test_the_extraction_plan_carries_the_domains_vocabulary() -> None:
    """``build_extraction_plan`` decides it, and the queue's config JSON round-trips it."""
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
    plan = build_extraction_plan(DomainStack.resolve(registry, ["literary"]), settings)
    assert plan.chunk_kwargs["relation_vocabulary"] == LITERARY.as_config()
    stored = json.loads(json.dumps(chunk_kwargs_to_config(plan.chunk_kwargs)))
    assert chunk_kwargs_from_config(stored)["relation_vocabulary"] == LITERARY.as_config()
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        UNIVERSAL_VOCABULARY,
    )

    # the floor alone runs the universal vocabulary
    floor = build_extraction_plan(DomainStack.resolve(registry, []), settings)
    assert floor.chunk_kwargs["relation_vocabulary"] == UNIVERSAL_VOCABULARY.as_config()


@pytest.mark.asyncio
async def test_the_chunk_extractor_reads_the_vocabulary_it_is_handed() -> None:
    """Pass 2's evidence filter (``parse_harvest_outputs``) keeps a kin-worded edge only with the words."""
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )
    from chaoscypher_core.settings import EngineSettings

    extractor = AIEntityExtractor(settings=EngineSettings())
    sentences = [VERA_SENTENCES[0], "Natásha laughed."]
    entities = "E|Countess Véra|Character|Véra|0.9|S1|The elder daughter\nE|Natásha|Character||0.9|S2|A girl"
    relationships = "R|0|1|sibling_of|0.9|S1|With her elder sister I was stricter"

    async def kept(vocabulary: dict[str, Any] | None) -> int:
        """Relationships the pass keeps under ``vocabulary``."""
        limits = extractor.resolve_harvest_limits(
            evidence_validation_mode="standard", relation_vocabulary=vocabulary
        )
        parsed = await extractor.parse_harvest_outputs(
            entities,
            relationships,
            sentences=sentences,
            chunk_content=" ".join(sentences),
            numbered_text="",
            edge_templates_formatted="",
            limits=limits,
        )
        assert len(parsed.entities) == 2
        return len(
            [r for r in parsed.relationships if not (r.get("properties") or {}).get("unevidenced")]
        )

    assert await kept(LITERARY.as_config()) == 1
    assert await kept(None) == 0  # kept, but flagged unevidenced


# -- wave 5: the vocabulary is the only evidence, matched whole-word ---------------------------


def test_every_registered_edge_template_is_stated_by_the_vocabulary_or_marked_two_endpoints() -> (
    None
):
    """Every edge template of every registered domain: a vocabulary entry XOR ``two_endpoints_only``.

    A new edge template forces the choice: either the domain says which words
    state it on one named endpoint, or it marks it as needing both. A label
    the domain both marks and declares in its own vocabulary is a
    contradiction.
    """
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        domain_relation_vocabulary,
    )
    from chaoscypher_core.settings import EngineSettings

    registry = get_domain_registry(EngineSettings())
    checked = 0
    for info in registry.list_domain_info():
        domain = registry.get_domain(str(info["name"]))
        vocabulary = domain_relation_vocabulary(domain)
        marked = set(domain.get_two_endpoint_relations())
        declared = {
            label
            for entry in domain.get_relation_vocabulary().values()
            for label in entry["labels"]
        }
        assert not marked & declared, (info["name"], marked & declared)
        for template in domain.config["templates"]["edge_templates"]:
            label = template["name"]
            stated = bool(vocabulary.relation_words(label))
            assert stated != (label in marked), (info["name"], label, stated)
            checked += 1
    assert checked > 400


_ONE_ENDPOINT = [
    {"name": "Anna Pávlovna Schérer", "type": "Character"},
    {"name": "Prince Vasíli Kurágin", "type": "Character"},
    {"name": "the soirée", "type": "Event"},
]


@pytest.mark.parametrize(
    ("label", "target", "sentence"),
    [
        ("interacts_with", 1, "Anna Pávlovna Schérer sat with the old aunt."),
        ("heir_of", 1, "Anna Pávlovna Schérer took their coats."),
        ("parent_of", 1, "Anna Pávlovna Schérer was in apparent distress."),
        ("member_of", 1, "Anna Pávlovna Schérer did not remember."),
        ("hosts", 2, "Anna Pávlovna Schérer told stories of ghosts."),
        ("patron_of", 1, "Anna Pávlovna Schérer used his patronymic."),
        ("betrothed_to", 1, "Anna Pávlovna Schérer was engaged in conversation with him."),
        ("betrothed_to", 1, "Anna Pávlovna Schérer promised him a visit."),
    ],
)
@pytest.mark.parametrize("mode", ["standard", "narrative"])
def test_a_label_word_inside_another_word_is_no_evidence(
    label: str, target: int, sentence: str, mode: str
) -> None:
    """The wave-5 audit's examples: one endpoint named, the label's word only as a substring."""
    rel = {"source": 0, "target": target, "type": label, "sent_ref": "S1"}
    assert not validate_relationship_evidence(
        rel, _ONE_ENDPOINT, [sentence], mode=mode, vocabulary=LITERARY
    )


@pytest.mark.parametrize(
    ("label", "sentence"),
    [
        ("parent_of", "Anna Pávlovna Schérer spoke warmly of his father."),
        ("spouse_of", "Anna Pávlovna Schérer had never met her husband."),
        ("betrothed_to", "Anna Pávlovna Schérer was engaged to him that spring."),
        ("betrothed_to", "Anna Pávlovna Schérer said that he had proposed marriage."),
        ("godparent_of", "Anna Pávlovna Schérer is his godmother."),
    ],
)
@pytest.mark.parametrize("mode", ["standard", "narrative"])
def test_true_one_endpoint_evidence_still_passes(label: str, sentence: str, mode: str) -> None:
    """A vocabulary word or phrase of the relation, whole, carries the unnamed end."""
    rel = {"source": 0, "target": 1, "type": label, "sent_ref": "S1"}
    assert validate_relationship_evidence(
        rel, _ONE_ENDPOINT, [sentence], mode=mode, vocabulary=LITERARY
    )


@pytest.mark.parametrize("mode", ["standard", "narrative"])
def test_the_floors_two_endpoints_only_rule_holds_under_a_pack(mode: str) -> None:
    """``hosts`` needs both ends under literary too: a pack cannot loosen a floor rule (spec § 2).

    Until 2026-10-08 literary's words for ``hosts`` ("hostess") let one named
    end carry the edge, though the floor marks the label two-endpoints-only.
    """
    rel = {"source": 0, "target": 2, "type": "hosts", "sent_ref": "S1"}
    sentence = "Anna Pávlovna Schérer was a fine hostess."
    assert not LITERARY.relation_words("hosts")
    assert not validate_relationship_evidence(
        rel, _ONE_ENDPOINT, [sentence], mode=mode, vocabulary=LITERARY
    )


def test_a_phrase_matches_as_consecutive_whole_words() -> None:
    """``engaged to`` matches "engaged to him", never "engaged in ... to"."""
    assert LITERARY.states_relation("she was engaged to him", "betrothed_to")
    assert not LITERARY.states_relation("she was engaged in talk to him", "betrothed_to")


def test_function_words_are_never_evidence() -> None:
    """A vocabulary entry made only of function words is dropped on read and refused on load."""
    from pydantic import ValidationError as PydanticValidationError

    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        RelationVocabularyEntry,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        FUNCTION_WORDS,
    )

    vocabulary = RelationVocabulary.from_config(
        {"x": {"labels": ["interacts_with"], "words": ["with", "of the", "beside"]}}
    )
    assert vocabulary.relation_words("interacts_with") == frozenset({"beside"})
    assert not vocabulary.states_relation("he sat with her", "interacts_with")
    with pytest.raises(PydanticValidationError):
        RelationVocabularyEntry(labels=["interacts_with"], words=["with"])
    for name, config in _plugins():
        for relation, entry in (config.get("relation_vocabulary") or {}).items():
            for word in entry["words"]:
                assert set(word.split()) - FUNCTION_WORDS, (name, relation, word)


# -- wave 6: a two-endpoints-only label that carries an evidence word says why -----------------


def test_a_two_endpoints_only_label_with_an_evidence_word_records_why() -> None:
    """Every registered domain: a marked template whose label carries a plausible evidence word gives a reason.

    Audit wave 6: ``rivals`` and ``member_of`` were marked two-endpoints-only
    for want of a vocabulary entry, and "the rivals recognized one another"
    and "as a member of the household" dropped two true edges on gold paths.
    A label word prose may use for the relation (``label_evidence_words``)
    either states it through the vocabulary (the template is then not
    marked) or the template records why not: a shared reason by name
    (``TWO_ENDPOINTS_REASONS``) or a sentence of its own. A reason on a
    template that is not marked is a contradiction.
    """
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        TWO_ENDPOINTS_REASONS,
        label_evidence_words,
    )
    from chaoscypher_core.settings import EngineSettings

    registry = get_domain_registry(EngineSettings())
    checked = 0
    missing: list[tuple[str, str, tuple[str, ...]]] = []
    for info in registry.list_domain_info():
        domain = registry.get_domain(str(info["name"]))
        marked = set(domain.get_two_endpoint_relations())
        reasons = domain.get_two_endpoint_reasons()
        assert set(reasons) <= marked, (info["name"], set(reasons) - marked)
        for label in sorted(marked):
            reason = reasons.get(label, "")
            assert not reason or reason in TWO_ENDPOINTS_REASONS or len(reason) >= 40, (
                info["name"],
                label,
                reason,
            )
            words = label_evidence_words(label)
            if words:
                checked += 1
                if not reason:
                    missing.append((str(info["name"]), label, words))
    assert missing == []
    assert checked >= 40


@pytest.mark.parametrize(
    ("label", "words"),
    [
        ("rivals", ("rivals",)),
        ("member_of", ("member",)),
        ("staff_member", ("staff", "member")),
        ("mentors", ("mentors",)),
        ("interacts_with", ()),
        ("located_in", ()),
        ("wrote", ()),
        ("possesses", ()),
        ("authored_by", ()),
        ("serves", ()),
    ],
)
def test_label_evidence_words_reads_the_relation_noun_not_the_verb(
    label: str, words: tuple[str, ...]
) -> None:
    """The head noun of a relational label and a person-noun plural; never a leading verb or a past form."""
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        label_evidence_words,
    )

    assert label_evidence_words(label) == words


_V6_ENTITIES = [
    {"name": "the Duc d\u2019Enghien", "type": "Character"},
    {"name": "Buonaparte", "type": "Character"},
    {"name": "Princess Anna Mikháylovna Drubetskáya", "type": "Character",
     "aliases": ["Anna Mikháylovna"]},
    {"name": "Rostóvs", "type": "Social Group"},
]  # fmt: skip


@pytest.mark.parametrize(
    ("source", "target", "label", "sentence"),
    [
        # q020 (chapter III): only the duke is named where the rivals meet.
        (0, 1, "rivals", "The Duc d\u2019Enghien and the rivals recognized one another at once."),
        # q104 (chapter XI): the household is the Rostóvs, named elsewhere.
        (2, 3, "member_of", "Anna Mikháylovna lived there as a member of the household."),
    ],
)
@pytest.mark.parametrize("mode", ["standard", "narrative"])
def test_rivalry_and_membership_are_stated_by_their_words(
    source: int, target: int, label: str, sentence: str, mode: str
) -> None:
    """The two wave-6 edges: one endpoint named and the relation's own noun, through the literary vocabulary."""
    rel = {"source": source, "target": target, "type": label, "sent_ref": "S1"}
    assert validate_relationship_evidence(
        rel, _V6_ENTITIES, [sentence], mode=mode, vocabulary=LITERARY
    )
    # every registered domain with the template states it (the words are universal)
    for name in ("generic", "political", "intelligence"):
        if label == "member_of":
            assert domain_vocabulary(name).relation_words(label), name


@pytest.mark.parametrize(
    ("word", "relation", "label"),
    [
        ("marriage", "spouse", "spouse_of"),
        ("marry", "spouse", "spouse_of"),
        ("marries", "spouse", "married_to"),
        ("wed", "spouse", "spouse_of"),
        ("wedded", "spouse", "spouse_of"),
        ("friendship", "friend", "friendship"),
        ("friendships", "friend", "friend_of"),
        ("inheritance", "heir", "heir_of"),
        ("heiresses", "heir", "heir_of"),
        ("patrons", "patron", "patron_of"),
        ("proteges", "patron", "patron_of"),
        ("fiances", "betrothed", "betrothed_to"),
    ],
)
def test_each_inflection_stems_to_its_entry(word: str, relation: str, label: str) -> None:
    """The inflections the wave-6 audit listed belong to their relation and state its labels."""
    assert LITERARY.word_relation[word] == relation
    assert LITERARY.states_relation(f"Anna Pávlovna spoke of the {word} at length.", label)


def test_a_word_inside_another_relations_phrase_states_only_that_relation() -> None:
    """The "marriage" of "proposed marriage" is a betrothal word there; alone it is a spouse word."""
    assert not LITERARY.states_relation("Prince Hippolyte proposed marriage to her.", "spouse_of")
    assert LITERARY.states_relation("Prince Hippolyte proposed marriage to her.", "betrothed_to")
    assert LITERARY.states_relation("Their marriage was a happy one.", "spouse_of")
    assert LITERARY.states_relation(
        "After the offer of marriage their marriage followed.", "spouse_of"
    )


@pytest.mark.parametrize(
    "text",
    ["his sis\u00adter came", "his sister\u200bcame", "his half\u2010brother came"],
)
def test_the_vocabulary_reads_text_through_the_shared_fold(text: str) -> None:
    """A soft hyphen splits no word, a zero-width space breaks one, U+2010 is a hyphen."""
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import fold
    from chaoscypher_core.services.sources.engine.extraction.utils import relation_vocabulary

    assert not hasattr(relation_vocabulary, "fold") or relation_vocabulary.fold is fold
    assert LITERARY.states_relation(text, "sibling_of")


# -- single-valued relations (spec 2026-10-20 decision 6): a flag an entry may declare ---------


@pytest.mark.xfail(
    strict=True,
    raises=(PydanticValidationError, AttributeError),
    reason="the schema field waits for the campaign: adding it moves the extraction recipe",
)
def test_an_entry_may_declare_single_valued() -> None:
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        RelationVocabularyEntry,
    )

    entry = RelationVocabularyEntry(labels=["patron_of", "sponsor_of"], words=["patron"])
    assert entry.single_valued is False
    flagged = RelationVocabularyEntry(
        labels=["patron_of", "sponsor_of"], words=["patron"], single_valued=True
    )
    assert flagged.single_valued is True


def test_a_vocabulary_lists_the_labels_of_its_single_valued_relations() -> None:
    vocabulary = RelationVocabulary.from_config(
        {
            "patronage": {
                "labels": ["patron_of", "sponsor_of"],
                "words": ["patron"],
                "single_valued": True,
            },
            "knowing": {"labels": ["knows"], "words": ["knows"]},
        }
    )
    assert vocabulary.single_valued_labels == frozenset({"patron_of", "sponsor_of"})
    assert vocabulary.as_config()["patronage"]["single_valued"] is True
    assert "single_valued" not in vocabulary.as_config()["knowing"]


def test_extend_vocabulary_carries_the_flag_and_the_universal_one_declares_none() -> None:
    extended = extend_vocabulary(
        {"patronage": {"labels": ["patron_of"], "words": ["patron"], "single_valued": True}}
    )
    assert extended["patronage"]["single_valued"] is True
    assert all(
        "single_valued" not in entry for name, entry in extended.items() if name != "patronage"
    )
    assert UNIVERSAL_VOCABULARY.single_valued_labels == frozenset()
    assert all("single_valued" not in entry for entry in UNIVERSAL_VOCABULARY.as_config().values())
