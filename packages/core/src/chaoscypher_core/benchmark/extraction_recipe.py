# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""What shapes an extracted graph besides the extractor and its decoding: one recipe hash.

The benchmark caches a corpus's extracted graph and reuses it for hours of
chat runs. Until 2026-10-06 the cache key named the corpus, the extractor,
the seed, the temperature, the dedup embedder and a hand-bumped
``EXT_PIPELINE_VERSION``, and nothing else: a changed chunk size, dedup
threshold, context window, thinking switch or extraction prompt served the
old graph under the new settings (audit H7). :func:`extraction_recipe` is the
rest of the key: every settings section that shapes the graph, and the
behaviour of the code that shapes it on fixed samples, like the resolver's
``resolution_recipe``: the extraction prompts as rendered, the extraction
plan (``build_extraction_plan``) a source runs with, every registered
domain's content fingerprint, the name-identity rule dedup and the resolver
share, the evidence validator in each mode, and the relationship orientation
the commit applies; and, since wave 2 of the audit (W2-12), the line
parser, exact and semantic deduplication (on fixed fake embeddings at
``search.vector_dimensions``, the size semantic dedup embeds at), the
hierarchical name merge, the relationship dedup and the descriptor-alias
cleaner, the deduplication pipeline as ``run_deduplication`` runs it, the
chunker, the extraction-group packer and the sentence splitter, each under
the extraction domain's plan when the domain is known; and, since wave 3, the
name rule's guards under every registered domain's full naming conventions
and the universal ones (``registered_name_conventions``), the evidence
validator under every registered relation vocabulary, and the commit's
citation chunks, orphan filter and payload; and, since keep, flag, never delete
(2026-10-07), the evidence filter's verdicts with their reasons and what it keeps
flagged, folds and drops, and the commit's fold of a fact's copies into one edge;
and, since known entities (2026-10-11), the list pass 1 is told the earlier
groups named, its block and the alias and name guards it puts on the next
group's records. An edit to any of them moves the key without anyone
remembering a number; ``EXT_PIPELINE_VERSION`` is left for schema-level breaks
no sample can see.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib
import json
import math
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any


EXTRACTION_SETTINGS_SECTIONS = (
    "chunking",
    "extraction",
    "source_processing",
    "normalizer",
    "graph",
)
"""Settings sections whose every field is in the recipe (over-inclusion asks for a rebuild, never a stale graph)."""

EXTRACTION_LLM_FIELDS = (
    "ollama_num_ctx",
    "thinking_for_extraction",
    "extraction_max_tokens",
    "extraction_examples_enabled",
    "extraction_examples_max_chars",
)
"""``LLMSettings`` fields that shape what the extractor is asked and can answer."""

KEYED_ELSEWHERE = frozenset({"resolve_identities"})
"""Fields with their own cache-key component (the resolver's recipe), left out here."""

_TIMING_MARKERS = ("backoff", "timeout", "pause")
"""A field naming one of these only times the calls (retry waits), it never shapes the graph."""

_SUBTYPE = "entity_subtype"
"""``type_rescue.SUBTYPE_PROPERTY`` (a test holds the two equal; this module imports lazily)."""


def _person(
    name: str, aliases: list[str] | None = None, *, subtype: str = "character"
) -> dict[str, Any]:
    """A sample entity as it leaves the per-chunk filters: core type Person and its subtype."""
    return {
        "name": name,
        "type": "Person",
        "aliases": list(aliases or []),
        "properties": {_SUBTYPE: subtype},
    }


_SAMPLE_CHUNK = (
    "Prince Andrew Bolkónski married Lise, the little princess. "
    'Dr. Lorrain said: "She is well." They lived at Bald Hills with the old prince.'
)
_SAMPLE_ENTITIES: list[dict[str, Any]] = [
    _person("Prince Andrew Bolkónski", ["Andrew"]),
    _person("Lise"),
    {"name": "Bald Hills", "type": "Location"},
]


def _engine_settings(settings: Any) -> Any:
    """``settings`` as EngineSettings: the app Settings map some fields across groups first."""
    from chaoscypher_core.settings import EngineSettings

    if isinstance(settings, EngineSettings):
        return settings
    from chaoscypher_core.app_config.engine_factory import build_engine_settings

    return build_engine_settings(settings)


def _section(section: Any) -> dict[str, Any]:
    """One settings section as sorted JSON, without the timing-only and separately keyed fields."""
    dumped = section.model_dump(mode="json")
    return {
        k: v
        for k, v in sorted(dumped.items())
        if k not in KEYED_ELSEWHERE and not any(m in k for m in _TIMING_MARKERS)
    }


def extraction_prompts_digest(settings: Any) -> str:
    """The extraction prompts as the extractor renders them for every registered domain, as a hash.

    For every registered domain (``generic`` is the floor alone, any other
    the floor plus that pack), the system prompt and both passes on a fixed
    chunk, rendered by the extractor (``render_chunk_prompts``, the one
    mapping of a plan's prompt inputs onto the prompt builders) from the
    stack's extraction plan
    (``build_extraction_plan``: its formatted templates, guidance, examples,
    exclusions and strict-type switch), exactly as a chunk task renders them.
    Pass 1 is rendered twice: as group 1 is sent (no block) and as a later
    group is (the KNOWN ENTITIES block of a list holding the sample's
    entities, ``KnownEntities.for_plan`` under the plan), so where the block
    sits and how it is wrapped are in the digest; an empty block renders as
    nothing, and until the final review of 2026-10-11 moving its slot moved
    no recipe. Until audit wave 5 the digest rendered hand-written sample
    templates the format examples could not read, so only the placeholder
    example was hashed and editing the shared examples moved no recipe.
    """
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
        build_extraction_plan,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
        KnownEntities,
    )

    engine = _engine_settings(settings)
    extractor = AIEntityExtractor(settings=engine)
    registry = get_domain_registry(engine)
    names = sorted(str(info["name"]) for info in registry.list_domain_info())
    blobs = []
    for name in names:
        kwargs = build_extraction_plan(_stack_for(registry, name), engine).chunk_kwargs
        prompts = extractor.render_chunk_prompts(kwargs, _SAMPLE_CHUNK, _SAMPLE_ENTITIES)
        known = KnownEntities.for_plan(kwargs, engine)
        known.add_group([dict(e) for e in _SAMPLE_ENTITIES], [])
        later = extractor.render_chunk_prompts(
            kwargs, _SAMPLE_CHUNK, _SAMPLE_ENTITIES, known_entities=known.block()
        )
        blobs.append(
            f"{name}\x1e{prompts.system_prompt}\x1f{prompts.entity_prompt}"
            f"\x1f{prompts.relationship_prompt}\x1f{later.entity_prompt}"
        )
    return hashlib.sha256("\x1d".join(blobs).encode("utf-8")).hexdigest()[:16]


_NAME_PAIRS = (
    (("Prince Andrew Bolkónski", "Person", ("Andrew",)), ("Andrew", "Person", ())),
    (("Princess Mary Bolkónskaya", "Person", ("Mary",)), ("Lise", "Person", ())),
    (("Count Bezúkhov", "Person", ()), ("Pierre Bezúkhov", "Person", ("Pierre",))),
    (("Nicholas", "Person", ()), ("Prince Nicholas Bolkónski", "Person", ())),
    (("Bald Hills", "Location", ()), ("Bald Hills", "Person", ())),
    (("Anna Pávlovna Schérer", "Person", ()), ("Mlle Schérer", "Person", ("the hostess",))),
)
"""Name pairs the identity rule decides: an alias, a title, a subset, two people, two types."""


_EVIDENCE_SENTENCES = [
    "Prince Andrew married Lise, the little princess.",
    "He went to Bald Hills with her.",
    "Natásha was there.",
    "Lise wept beside her sister.",
    "Anna Pávlovna laughed, and the princess smiled at the Rostóvs.",
    "“Very lovely,” said Monsieur, and the prince bowed.",
    # wave 7: a holder named by name beside a bare title, a titled name (no bare title), an
    # elided name, and a woman's surname form beside a man's name
    "Hippolyte smiled, and the prince bowed to Lise.",
    "Prince Ippolit bowed to Lise.",
    "The duc d\u2019Enghien was shot.",
    "He is a son of Márya Ivánovna Dólokhova.",
]
_EVIDENCE_ENTITIES: list[dict[str, Any]] = [
    {"name": "Prince Andrew Bolkónski", "type": "Character", "sent_ref": "S1"},
    {"name": "Lise", "type": "Character", "sent_ref": "S1"},
    {"name": "Bald Hills", "type": "Location", "sent_ref": "S2"},
    {"name": "Natalya Ilyinichna Rostova", "type": "Character", "sent_ref": "S3"},
    {"name": "Lise", "type": "Character", "sent_ref": "S9"},
    # wave 6: a name inside a word ("Ann" in "Anna", "Prince" in "princess"), a
    # family name through its plural, and a bare title held by one or by two
    {"name": "Ann", "type": "Character", "sent_ref": "S5"},
    {"name": "Pávlov", "type": "Character", "sent_ref": "S5"},
    {"name": "Count Rostóv", "type": "Character", "sent_ref": "S5"},
    {"name": "Prince Hippolyte", "type": "Character", "sent_ref": "S6"},
    {"name": "Monsieur Pierre", "type": "Character", "sent_ref": "S6"},
    {"name": "Monsieur Buonaparte", "type": "Character", "sent_ref": "S6"},
    {"name": "Enghien", "type": "Character", "sent_ref": "S9"},
    {"name": "Dólokhov", "type": "Character", "sent_ref": "S10"},
]
_EVIDENCE_RELATIONSHIPS: list[dict[str, Any]] = [
    {"source": 0, "target": 1, "type": "married_to", "sent_ref": "S1"},
    {"source": 0, "target": 2, "type": "went_to", "sent_ref": "S2"},
    {"source": 1, "target": 3, "type": "knows", "sent_ref": "S1-S3"},
    {"source": 0, "target": 3, "type": "loves", "sent_ref": "S3"},
    {"source": 0, "target": 1, "type": "married_to", "sent_ref": "S1, sentence 3"},
    {"source": 1, "target": 3, "type": "sibling_of", "sent_ref": "S4"},
    {"source": 1, "target": 3, "type": "Sibling Of", "sent_ref": "S4"},
    {"source": 5, "target": 0, "type": "interacts_with", "sent_ref": "S5"},
    {"source": 9, "target": 10, "type": "interacts_with", "sent_ref": "S6"},
    {"source": 2, "target": 1, "type": "interacts_with", "sent_ref": "S2"},
    {"source": 0, "target": 1, "type": "interacts_with", "sent_ref": "S7"},
    {"source": 0, "target": 1, "type": "interacts_with", "sent_ref": "S8"},
]
_EVIDENCE_MODES = ("strict", "standard", "narrative", "relaxed")
_UNEVIDENCED_ENTITIES: list[dict[str, Any]] = [
    {
        "name": "Mavra Kuzmínichna",
        "type": "Character",
        "sent_ref": "S3",
        "description": "The maid.",
    },
    {"name": "Hippolyte Kurágin", "type": "Character", "sent_ref": "S1", "description": ""},
    {"name": "Sónya", "type": "Character", "description": "A cousin.", "properties": {"x": 1}},
    {"name": "Natásha", "type": "Character", "sent_ref": "S3", "properties": {"unevidenced": "no"}},
]
"""Beside the evidence entities, what the entity filter keeps flagged and drops (2026-10-08): one
its sentence does not name with a description, one with none, one citing nothing with a
description, and an evidenced one carrying the text's value under the flag's key."""

_ATTESTED_TEXT = (
    "Otto Lindqvist bowed. Ilse Brandauer smiled at him and showed him Mitzi, her doll."
)
_ATTESTED_ENTITIES: list[dict[str, Any]] = [
    _person("Ottó Lindqvist"),
    _person("Otto Lindqvist", ["Brandauer", "Herr Lindqvist"]),
    _person("Ilse Brandauer", ["Mitzi"]),
    {"name": "Mitzi", "type": "Object", "aliases": []},
]
"""One group's entities and text: the spelling the text writes beats an accented invention, an
alias the text does not write (``Herr Lindqvist``) is no evidence, and neither is one the output
lists as an entity of its own (the doll ``Mitzi`` beside ``Ilse Brandauer``)."""
_FUNCTION_WORD_VOCABULARY = {"x": {"labels": ["interacts_with"], "words": ["with", "beside"]}}
"""A vocabulary that lists function words: the reader must drop them (``is_function_phrase``)."""

_COMMIT_ENTITIES: list[dict[str, Any]] = [
    {
        "name": "Lise",
        "cited_chunk_id": "c2",
        "evidence": [{"chunk_id": "c1"}, {"chunk_id": "c2"}],
        "properties": {"orphan": "no"},
    },
    {
        "name": "Bald Hills",
        "evidence": [{"chunk_id": "c3"}, "not evidence"],
        "properties": {"orphan": True},
    },
    {"name": "the hostess", "evidence": [{"chunk_id": "c3", "text": " "}]},
    {"name": "Prince Andrew Bolkónski", "cited_chunk_id": "c1"},
    {"name": "Dame Orsa Velk", "description": "Ilse's mother, who called on the countess."},
    {"name": "the ferryman", "evidence": [{"chunk_id": "c3", "text": "The ferryman waited."}]},
]
"""Two connected (an extracted ``orphan`` value of the text's, a stale flag the commit clears),
an orphan carrying nothing (a blank sentence is none), and orphans kept flagged for a
description and for an evidence sentence (keep, flag, never delete: 2026-10-07)."""
_COMMIT_RELATIONSHIPS: list[dict[str, Any]] = [
    {"source": 0, "target": 3, "type": "spouse_of"},
    {"source": 3, "target": 1, "type": "located_in"},
    {"source": "x", "target": 1, "type": "knows"},
]
_COMMIT_EDGE_COPIES: list[dict[str, Any]] = [
    {"template_id": "t1", "source_node_id": "a", "target_node_id": "b", "label": "spouse_of",
     "properties": {"justification": "They argue.", "sent_ref": "S2", "unevidenced": True,
                    "evidence_reason": "neither endpoint is named in S2"}},
    {"template_id": "t1", "source_node_id": "a", "target_node_id": "b", "label": "spouse_of",
     "properties": {"justification": "Lise is his wife.", "sent_ref": "S4", "chunk_index": 3}},
    {"template_id": "t1", "source_node_id": "b", "target_node_id": "c", "label": "knows",
     "properties": {"justification": "Met once.", "unevidenced": True, "evidence_reason": "r"}},
    {"template_id": "t1", "source_node_id": "b", "target_node_id": "c", "label": "knows",
     "properties": {"justification": "Met twice.", "unevidenced": True, "evidence_reason": "s"}},
]  # fmt: skip
"""Copies of one fact the stable edge stores once (``relation.fold_fact_copies``): an
unevidenced copy first and an evidenced one after (the evidenced sentence leads, the flag
clears), and two unevidenced copies (the first kept, flagged)."""
_COMMIT_RESULTS: dict[str, Any] = {
    "suggested_templates": [{"name": "Person"}],
    "inverse_relationships": {"parent_of": "child_of"},
    "symmetric_relationships": ["spouse_of"],
    "ignored": True,
}

_COMMIT_TYPED_ENTITIES: list[dict[str, Any]] = [
    {"name": "Pierre", "type": "Person", "properties": {"entity_subtype": "character"}},
    {"name": "Tolstoy", "type": "Person", "properties": {"entity_subtype": "author"}},
    {"name": "Moscow", "type": "Location"},
    {"name": "Natásha", "type": "Character"},
    {"name": "Mavra", "type": "person"},
    {"name": "the glow", "type": "Xyzzy"},
    {"name": "Andrei", "type": "Person/character"},
]
"""What the commit types and templates (spec 2026-10-07 § 6): two Person subtypes, a core type
alone, an old per-type name (Person/character), a lowercase core type, a type no core type
answers (no template, the matcher's fallback) and a ``Core/subtype`` no pack offers there."""

_ORIENTATION_RELATIONSHIPS: list[dict[str, Any]] = [
    {"source": "Pierre", "target": "Andrew", "type": "friend_of"},
    {"source": "Andrew", "target": "Pierre", "type": "friend_of"},
    {"source": "Nicholas", "target": "Count Rostóv", "type": "child_of"},
    {"source": 1, "target": 0, "type": "sibling_of"},
    {"source": "Andrew", "target": "Lise", "type": "married_to"},
]


_PARSER_LINES = (
    "E|Prince Andrew Bolkónski|Character|Andrew; Prince Andrew; the prince|0.9|S1|Lise's husband",
    "1. E|Lise|Character||0.8|S1-S2|The little princess",
    "E|Bald Hills|Location|Lysyya Gory|S2|0.7|the estate",
    "E|**Dr. Lorrain**|Character|the doctor|0.6|S3|A physician",
    "E|missing sent ref|Character||0.9||no sentence",
    "E|Count Ilyá Rostóv|Character|Natásha's Father; Ilyá|0.8|S2|The count",
    "E|Pierre|Person/character||0.9|S1|A count's son",
)
_PARSER_RELATIONSHIP_LINES = (
    "R|0|1|married_to|0.9|S1|Prince Andrew married Lise",
    "R|1|2|located_in|S2|0.7|They lived at Bald Hills",
    "R|0|x|knows|0.5|S1|bad endpoint",
    "R|3|0|treats|0.6|S3|Dr. Lorrain said she is well",
)
_PARSER_PROPERTY_LINES = ("P|0|title|Prince", "P|1|age|young", "P|x|bad|line")

_DEDUP_ENTITIES: list[dict[str, Any]] = [
    _person("Vera Dárinovna Melzér", ["Verochka"]),
    _person("Mlle Melzér", ["the hostess"]),
    _person("vera dárinovna melzér", ["Vera Darinovna"], subtype="historical_figure"),
    _person("Prince Radím Tarágin", ["Tarágin", "Radím"]),
    _person("Lucien", ["Tarágin", "Lucien Tarágin"]),
    _person("Tarágin"),
    _person("Princess Zvaróvskaya", ["Elke", "the little princess"]),
    _person("Princess Dora Zvaróvskaya", ["Dora"]),
    _person("Count Velúrov"),
    _person("Anselm Velúrov", ["Anselm", "Velúrov's son"]),
    _person("Kasimir Volódimirovich Velúrov"),
    {"name": "Korgrad", "type": "Location", "aliases": ["Korhrad", "the old capital"]},
    _person("Korgrad"),
    # A bare title its own sentence writes with a name (``text_referent_names``), and the name.
    {
        **_person("the Viscount"),
        "evidence": [{"text": "“Come,” said the Viscount Arlen Tove, and bowed."}],
    },
    _person("Arlen Tove"),
    # The parser's placeholder type takes the stated one; an anonymous mention's alias that
    # identified no one is no evidence once it joins the others written alike.
    {"name": "Arlen Tove", "type": "UNKNOWN", "aliases": []},
    _person("the Viscount", ["Corvin"]),
    _person("Viscount"),
]
"""Entities the deduplication decides on: same names, aliases, a title, surnames, two core types,
two subtypes of one (both kept), a reference."""

_DEDUP_VECTOR_GROUPS = (0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 10, 11, 11, 12, 12)
"""The fake embedding each :data:`_DEDUP_ENTITIES` row gets: equal numbers share a vector."""

_DEDUP_RELATIONSHIPS: list[dict[str, Any]] = [
    {"source": 0, "target": 3, "type": "knows", "confidence": 0.8, "chunk_index": 0},
    {"source": 2, "target": 3, "type": "knows", "confidence": 0.9, "chunk_index": 0},
    {"source": 3, "target": 4, "type": "parent_of", "confidence": 0.9, "chunk_index": 1},
    {"source": 4, "target": 3, "type": "child_of", "confidence": 0.7, "chunk_index": 2},
    {"source": 6, "target": 7, "type": "sibling_of", "confidence": 0.6, "chunk_index": 1},
    {"source": 7, "target": 6, "type": "sibling_of", "confidence": 0.6, "chunk_index": 1},
    {"source": 9, "target": 10, "type": "child_of", "confidence": 0.9, "chunk_index": 3},
    {"source": 1, "target": 11, "type": "located_in", "confidence": 0.5, "chunk_index": 0},
]
_DEDUP_SYMMETRIC = frozenset({"sibling_of", "married_to", "friend_of"})
_DEDUP_INVERSE = {"parent_of": "child_of", "child_of": "parent_of"}

_SPLITTER_TEXT = (
    "“Yes.”\n\n“No,” said he. Dr. Lorrain bowed. Prince Andrew married Lise, "
    "the little princess!\n\nWas she well? “She is well,” said Anna Pávlovna. "
    "Mr. Smith arrived at 3 p.m. and left.\n\nThe end"
)
_CHUNKER_TEXT = (
    "CHAPTER I\n\n"
    + "“Well, Prince, so Genoa and Lucca are now just family estates of the Buonapartes.” "
    "It was in July, 1805, and the speaker was the well-known Anna Pávlovna Schérer. "
    "Prince Vasíli Kurágin was the first to arrive.\n\n" * 12 + "“Yes.”\n\n“No,” said he. The end."
)
_GROUP_CHUNKS: list[dict[str, Any]] = [
    {"id": f"c{i}", "content": text}
    for i, text in enumerate(
        (
            "Anna Pávlovna Schérer received her guests. " * 40,
            "Prince Vasíli arrived first. " * 60,
            "  The vicomte told a story.  \n\n\n" * 50,
            "",
            "Pierre stood by the window, watching. " * 45,
            "Lise spoke to the little princess. " * 12,
        )
    )
]


def _run_sync[T](make: Callable[[], Awaitable[T]]) -> T:
    """Run the coroutine ``make()`` builds to completion, inside or outside a running loop.

    The recipe is synchronous, the chunker and semantic dedup are async: with
    no loop running the coroutine runs on a private one; inside a running loop
    (an async caller) it runs on a private loop in a worker thread.
    """

    async def _wrap() -> T:
        """Await the coroutine (``asyncio.run`` needs a coroutine, not an awaitable)."""
        return await make()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_wrap())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _wrap()).result()


def _canon(value: Any) -> Any:
    """``value`` as stable JSON input: embeddings and ids left out, sets sorted.

    Lists keep their order: a merged entity's aliases and descriptors are
    built in first-seen order and reach the stored node and its embedding
    text, so their order is behaviour (until 2026-10-06 they were set unions
    and compared sorted here, which hid a hash-seed dependence: audit W4-4).
    """
    if isinstance(value, dict):
        return {
            key: _canon(item)
            for key, item in value.items()
            if key not in {"embedding", "id", "source_id"}
        }
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    return value


def _plain(value: Any) -> Any:  # noqa: PLR0911 - one return per kind of value
    """``value`` as plain JSON values: dataclasses and models expanded, sets sorted.

    ``repr`` of a plan holding a set (a domain's valid type names) differs
    between processes with string-hash randomisation; this does not.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(v) for v in value), key=lambda v: json.dumps(v, default=str))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _dumps(value: Any) -> str:
    """One sample as canonical JSON."""
    return json.dumps(_canon(value), ensure_ascii=False, sort_keys=True, default=str)


def _fake_embeddings(dimensions: int) -> list[list[float]]:
    """One fixed vector per :data:`_DEDUP_ENTITIES` row, ``dimensions`` long (the index size)."""
    return [
        [math.sin((group + 1) * (j + 1) * 0.7) for j in range(dimensions)]
        for group in _DEDUP_VECTOR_GROUPS
    ]


def _stack_for(registry: Any, domain: str | None) -> Any:
    """The domain stack a source of ``domain`` extracts with (the floor alone for None or generic)."""
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    return DomainStack.from_domain_name(registry, domain)


def _parser_sample() -> str:
    """The line parser on fixed E|, R| and P| lines, malformed ones included.

    The parser logs a warning per malformed line; those are the sample's, not
    an extraction's, so they are captured instead of reaching the log.
    """
    from structlog.testing import capture_logs

    from chaoscypher_core.services.sources.engine.extraction.utils import line_parser

    with capture_logs():
        return _parser_dumps(line_parser)


def _parser_dumps(line_parser: Any) -> str:
    """:func:`_parser_sample`'s body: every parser entry point on the fixed lines."""
    return _dumps(
        [
            [line_parser.parse_entity_line(line) for line in _PARSER_LINES],
            [line_parser.parse_relationship_line(line) for line in _PARSER_RELATIONSHIP_LINES],
            [line_parser.parse_property_line(line) for line in _PARSER_PROPERTY_LINES],
            line_parser.parse_extraction_output(
                "\n".join((*_PARSER_LINES, *_PARSER_PROPERTY_LINES)),
                "\n".join(_PARSER_RELATIONSHIP_LINES),
            ),
        ]
    )


def _processor(engine: Any, conventions: Any) -> Any:
    """An :class:`EntityProcessor` under ``conventions`` and ``engine``'s dedup settings."""
    from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor

    return EntityProcessor(
        name_conventions=conventions,
        max_description_length=engine.source_processing.entity_max_description_length,
        dedup_type_partition_cutoff=engine.extraction.dedup_type_partition_cutoff,
        dedup_no_overlap_boost=engine.extraction.dedup_no_overlap_boost,
        dedup_borderline_penalty=engine.extraction.dedup_borderline_penalty,
    )


def _fresh_entities() -> list[dict[str, Any]]:
    """A copy of the sample entities (the dedup mutates in place)."""
    return copy.deepcopy(_DEDUP_ENTITIES)


def _rule_sample(engine: Any, conventions: Any) -> list[Any]:
    """Every name-rule entry point on the samples under one set of naming conventions.

    The pairwise verdicts (:data:`_NAME_PAIRS`), the joins over the records
    every registered guard brings and the one merge predicate's verdicts on
    them (``name_guards``, shared with the resolver recipe, so both recipes
    move with every guard), the exact dedup (untyped and typed),
    the hierarchical pass and the descriptor-alias cleaner.
    """
    from chaoscypher_core.services.sources.engine.deduplication import name_identity
    from chaoscypher_core.services.sources.engine.deduplication.name_guards import (
        NAME_GUARDS,
        guard_records,
        merge_verdicts,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils import entity_cleaner

    processor = _processor(engine, conventions)
    names = []
    for a, b in _NAME_PAIRS:
        verdict = name_identity.same_entity(
            name_identity.NameRecord(*a), name_identity.NameRecord(*b), conventions=conventions
        )
        names.append(None if verdict is None else (verdict.kind, verdict.why))
    guards = name_identity.plan_name_pairs(guard_records(), conventions=conventions)
    survivors = [
        name_identity.pick_survivor(
            [name_identity.survivor_candidate({"name": g.label, "aliases": list(g.aliases),
                                               "source_chunk_indices": list(range(g.support))})
             for g in guard.samples],
            conventions,
        )
        for guard in NAME_GUARDS
        if guard.samples
    ]  # fmt: skip
    cleaned = [*_fresh_entities(), *copy.deepcopy(_ALIAS_ENTITIES)]
    removed = entity_cleaner.clean_descriptor_aliases(
        cleaned, conventions.titles, conventions=conventions
    )
    return [
        conventions.as_dict(),
        names,
        [(i, j, e.kind, e.why) for i, j, e in guards],
        survivors,
        merge_verdicts(conventions),
        processor.deduplicate_entities_with_mapping(_fresh_entities()),
        processor.deduplicate_entities_with_mapping(
            _fresh_entities(), require_type_compatibility=True
        ),
        processor.deduplicate_entities_with_mapping(
            copy.deepcopy(_UNPLACED_ENTITIES), require_type_compatibility=True
        ),
        processor.resolve_hierarchical_names(
            _fresh_entities(), [dict(r) for r in _DEDUP_RELATIONSHIPS]
        ),
        [cleaned, removed],
    ]


def _dedup_sample(engine: Any, domain: str | None) -> str:
    """The name rule under every registered domain's conventions, then the pipeline under ``domain``.

    Each component on its own fixed input (so a change to one moves the
    recipe even when another hides it downstream): the name-rule samples
    (:func:`_rule_sample`) under the universal conventions and every
    registered domain's (``registered_name_conventions``: a guard that only
    fires on a domain's gendered titles, generations, name forms or relation
    words moves the recipe whichever domain the caller names), semantic
    dedup and the relationship dedup under ``domain``'s, then the pipeline
    as ``run_deduplication`` runs it under ``engine`` and ``domain``.
    """
    from chaoscypher_core.services.sources.engine.extraction import extractor
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        DomainResolver,
        domain_name_conventions,
        registered_name_conventions,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils import entity_cleaner

    resolver = DomainResolver(engine)
    processor = _processor(engine, domain_name_conventions(domain, resolver))
    rule = [_rule_sample(engine, conv) for conv in registered_name_conventions(engine)]
    semantic = _run_sync(
        lambda: processor.deduplicate_entities_semantic(
            _fresh_entities(),
            None,
            engine.extraction.semantic_dedup_threshold,
            require_type_compatibility=True,
            precomputed_embeddings=_fake_embeddings(engine.search.vector_dimensions),
        )
    )
    relationships = entity_cleaner.deduplicate_relationships(
        [dict(r) for r in _DEDUP_RELATIONSHIPS],
        symmetric_types=_DEDUP_SYMMETRIC,
        inverse_map=_DEDUP_INVERSE,
    )
    attested = _attested_sample(processor)
    pipeline = _run_sync(
        lambda: extractor.run_deduplication(
            entities=_fresh_entities(),
            relationships=[dict(r) for r in _DEDUP_RELATIONSHIPS],
            detected_domain=domain,
            settings=engine,
            embedding_service=None,
            domain_resolver=resolver,
        )
    )
    return _dumps(
        [
            rule,
            [semantic[0], semantic[1]],
            relationships,
            attested,
            [pipeline[0], pipeline[1], pipeline[3]],
        ]
    )


def _attested_sample(processor: Any) -> list[Any]:
    """One group aggregated as the finalizer does (attested names stamped), then deduplicated.

    The stamp (``orchestration.aggregate_chunk_results`` through
    ``name_identity.attested_names``) is what the survivor ranking's attested
    rank reads: the spelling the text writes keeps the label.
    """
    from chaoscypher_core.services.sources.engine.extraction import orchestration

    aggregated = orchestration.aggregate_chunk_results(
        [
            {
                "raw_entities": [
                    {**copy.deepcopy(e), "sent_ref": "S1"} for e in _ATTESTED_ENTITIES
                ],
                "raw_relationships": [],
                "input_text": _ATTESTED_TEXT,
                "small_chunk_ids": [],
            }
        ]
    )
    entities = aggregated["entities"]
    return [entities, processor.deduplicate_entities_with_mapping([dict(e) for e in entities])]


def _commit_sample(registry: Any, domain: str | None) -> str:
    """The commit's own steps on fixed input: citations, orphans, payload, a fact's copies, core-type templates."""
    from chaoscypher_core.models import EdgeCreate
    from chaoscypher_core.services.sources.engine.commit.payload import build_commit_payload
    from chaoscypher_core.services.sources.engine.commit.relation import fold_fact_copies
    from chaoscypher_core.services.sources.engine.commit.service import (
        entity_citation_chunks,
        flag_orphan_entities,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.template_extractor import (
        TemplateExtractor,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
        committed_type,
    )

    entities = [dict(e) for e in _COMMIT_ENTITIES]
    relationships = [dict(r) for r in _COMMIT_RELATIONSHIPS]
    stack = _stack_for(registry, domain)
    return _dumps(
        [
            # one template per core type, the floor's visuals, the packs' subtype icons
            TemplateExtractor.generate_suggestions_from_entities(
                [dict(e) for e in _COMMIT_TYPED_ENTITIES],
                domain_templates=stack.get_templates()["node_templates"],
                subtype_visuals=stack.subtype_visuals(),
            ),
            [committed_type(e) for e in _COMMIT_TYPED_ENTITIES],
            [entity_citation_chunks(dict(e)) for e in _COMMIT_ENTITIES],
            flag_orphan_entities(entities, relationships, keep_evidence_less=False),
            flag_orphan_entities(entities, relationships, keep_evidence_less=True),
            build_commit_payload(
                entities=entities,
                relationships=relationships,
                extraction_results=_COMMIT_RESULTS,
                auto_enable=False,
            ),
            build_commit_payload(entities=[], relationships=[]),
            [
                edge.properties
                for edge in fold_fact_copies([EdgeCreate(**dict(e)) for e in _COMMIT_EDGE_COPIES])
            ],
        ]
    )


_FILTER_ENTITIES: list[dict[str, Any]] = [
    {"name": "the count", "type": "Character", "description": "The father of Nicholas."},
    {"name": "the count", "type": "Character", "description": ""},
    {"name": "Prince Andrew's wife", "type": "Character", "description": "A wife."},
    {"name": "the vicomte", "type": "Location", "description": "Not a person."},
    {"name": "The Armchair", "type": "Character", "description": "A chair."},
    {"name": "The Sword", "type": "Character", "description": ""},
    {"name": "Count Ilyá Rostóv", "type": "Character", "description": "The count."},
    {"name": "the army", "type": "Organization", "description": "Marched at dawn."},
    {"name": "the village", "type": "Location", "description": ""},
    {"name": "drawing room", "type": "Location", "description": "Where the guests met."},
    {"name": "doll", "type": "Character", "description": "Natásha's doll, Mimi."},
    {
        "name": "the conservatory",
        "type": "Character",
        "description": "",
        "evidence": [{"text": "They went into the conservatory."}],
        "properties": {"orphan": "yes", "low_plausibility": "no"},
    },
]
"""What the per-chunk name filters decide on: references that say something (a person's and a
group's), one that does not, names below the plausibility bar kept flagged for a description or an
evidence sentence (an extracted value under a flag's key is the text's) and one carrying nothing."""

_EXCLUSION_ENTITIES: list[dict[str, Any]] = [
    {"name": "Chapter One", "type": "Work", "description": "Where the soirée opens."},
    {"name": "Book Two", "type": "Work", "description": ""},
    {"name": "Pierre", "type": "Character", "aliases": ["Chapter One", "Petya"]},
    {"name": "Anna", "type": "Character", "properties": {"excluded_name": "no"}},
]
"""Beside the filter entities, what the exclusion filter keeps flagged, drops and cleans
(2026-10-08): an excluded name with a description, one without, an excluded alias, and the
text's value under the flag's key."""

_TYPE_CONSTRAINT_ENTITIES: list[dict[str, Any]] = [
    {"name": "Natásha", "type": "Person"},
    {"name": "Count Rostóv", "type": "Person"},
    {"name": "Moscow", "type": "Location"},
    {"name": "Untyped"},
]
_TYPE_CONSTRAINT_RELATIONSHIPS: list[dict[str, Any]] = [
    {"source": 1, "target": 0, "type": "parent_of", "justification": "Her father."},
    {"source": 0, "target": 1, "type": "owes_money_to", "justification": "She owed him."},
    {"source": 0, "target": 1, "type": "spouse", "justification": "A near miss."},
    {"source": 2, "target": 0, "type": "parent_of", "justification": "Moscow raised her."},
    {"source": 2, "target": 0, "type": "located_in", "justification": "Reversed."},
    {"source": 3, "target": 2, "type": "located_in", "justification": "No type."},
    {"source": 1, "target": 0, "type": "parent_of", "properties": {"type_mismatch": "no"}},
]
"""What the type-constraint check decides on (2026-10-08): a fit, a label no template answers to
(kept as ``related_to``, flagged), a near miss, endpoints the template refuses (flagged), a
reversed edge (corrected, or flagged with the correction off), an untyped endpoint, and the text's
value under the flag's key."""

_FILTER_SENTENCES = [
    "Count Ilyá Rostóv met the guests.",
    "The count laughed in The Armchair.",
    "Natásha ran to the conservatory with her doll.",
]

_RESCUE_ENTITIES: list[dict[str, Any]] = [
    {"name": "Lise", "type": "Character", "description": "The little princess."},
    {"name": "Historical Lise", "type": "Historical Figure", "description": ""},
    {"name": "The Times", "type": "Publication", "description": ""},
    {"name": "the count", "type": "UNKNOWN", "description": "He went to the Archives."},
    {"name": "the army", "type": "UNKNOWN", "description": "Marched at dawn."},
    {"name": "Rostov Family", "type": "Noble Family", "description": ""},
    {"name": "Bald Hills", "type": "Quux", "description": "An estate near a city."},
    {"name": "Glorp", "type": "Quux", "description": "Nothing said."},
    {"name": "Zyx", "type": "Quux", "description": "", "properties": {"unplaced_type": "Niece"}},
    {"name": "the", "type": "Misc", "description": ""},
    {"name": "Pierre", "type": "Person/character", "description": ""},
    {"name": "The Lancers", "type": "Location/military_unit", "description": ""},
    {"name": "Mavra", "type": "Person/wizard", "description": ""},
]
"""What the type rescue decides on: subtypes, aliases, qualified rows, references, keywords, junk,
written types no core type answers to (kept as Concept, flagged ``unplaced_type``), and the
``Core/subtype`` the prompt teaches (offered, offered under another core type, offered by no
pack: kept on its core type, flagged)."""

_ALIAS_ENTITIES: list[dict[str, Any]] = [
    {
        "name": "Princess Bolkónskaya",
        "type": "Person",
        "aliases": ["Lise", "Nieces", "Bolkónskaya"],
        "attested_names": ["Princess Bolkónskaya", "Lise"],
    },
    {"name": "Moscow", "type": "Location", "aliases": ["Moskva"], "attested_names": ["Moskva"]},
]
"""What the alias cleaner decides on beside the dedup samples: an attested given name and spelling
sharing no word with the label (kept), an unattested descriptor (moved to ``descriptors``)."""

_UNPLACED_ENTITIES: list[dict[str, Any]] = [
    {
        "name": "Natásha",
        "type": "Concept",
        "description": "She ran in laughing.",
        "properties": {"unplaced_type": True, "written_type": "UNKNOWN"},
    },
    _person("Natásha"),
    {"name": "Moscow", "type": "Location"},
    _person("Moscow"),
    {"name": "Moscow", "type": "Concept", "properties": {"unplaced_type": True}},
]
"""What the exact dedup decides on for mentions kept unplaced (a Concept no type): one joins its
name's one typed entity, one stays apart from a name of two core types."""

_CAP_RELATIONSHIPS: list[dict[str, Any]] = [
    {"source": 0, "target": t, "type": "knows", "confidence": 1.0 - t / 10} for t in range(1, 6)
] + [{"source": 1, "target": 2, "type": "knows", "confidence": 0.5, "properties": {"capped": "x"}}]
"""What the relationship caps decide on: a hub past the same-source and degree caps."""

_CONTENT_TEXTS = (
    "    For the sword outwears its sheath,\n    And the soul wears out the breast,\n"
    "If ever two were one, then surely we.\nWhile the army waited, the prince wrote.",
    "import os\n\ndef main() -> None:\n    path = os.getcwd()\n    print(path)\n"
    '\nif __name__ == "__main__":\n    main()',
    "| Year | Battle |\n|---|---|\n| 1805 | Austerlitz |\nThe year was eventful.",
    "Copyright 2024. All rights reserved. Licensed under MIT. Terms of use apply.",
)
"""Chunks every content category decides on: verse, code, a table, boilerplate."""


def _rescue_sample(registry: Any) -> list[Any]:
    """The per-chunk type rescue under every registered domain's stack: each entity's core type and subtype."""
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
    from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
        TypeVocabulary,
        rescue_invalid_entity_types,
    )

    out = []
    for name in sorted(registry.names()):
        stack = DomainStack.from_domain_name(registry, name)
        rescued, _, _, _ = rescue_invalid_entity_types(
            [dict(e) for e in _RESCUE_ENTITIES], [], TypeVocabulary.from_stack(stack)
        )
        out.append(
            [
                name,
                [[e["name"], e["type"], e.get("properties")] for e in rescued],
            ]
        )
    return out


def _filter_sample(engine: Any, domain: str | None = None) -> str:
    """The per-chunk name filters (the domain's exclusion examples, the plausibility score), the type rescue, the caps and the type check.

    Under the universal naming conventions and every registered domain's, on
    fixed entities: what pass 2 lists, so what every edge can attach to (until
    2026-10-07 neither filter was in the recipe, and a filter that dropped
    ``the count`` moved no key; the type rescue joined it when it became the
    core-type rescue, spec 2026-10-07 § 6).
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        DESCRIPTOR_REFERENCE_KEY as REFERENCE,
    )
    from chaoscypher_core.services.sources.engine.deduplication.service import (
        publish_descriptor_references,
    )
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        registered_name_conventions,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        ExclusionRule,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils import entity_cleaner

    rules = [
        ExclusionRule(description="Bare titles", examples=["The Count", "The Armchair"]),
        ExclusionRule(description="Chapter headings", examples=["Chapter One", "Book Two"]),
    ]
    out = []
    for conv in registered_name_conventions(engine):
        excluded, _ = entity_cleaner.filter_excluded_entities(
            [dict(e) for e in [*_FILTER_ENTITIES, *_EXCLUSION_ENTITIES]], rules, conventions=conv
        )
        plausible, _ = entity_cleaner.filter_implausible_entities(
            [dict(e) for e in _FILTER_ENTITIES],
            _FILTER_SENTENCES,
            named_referent_types={"Character", "Organization", "Location"},
            conventions=conv,
        )
        referenced = [dict(e) for e in _FILTER_ENTITIES]
        publish_descriptor_references(referenced, conv)
        out.append(
            [
                [[e["name"], e.get("aliases"), e.get("properties")] for e in excluded],
                [[e["name"], e.get("properties")] for e in plausible],
                [e["name"] for e in referenced if (e.get("properties") or {}).get(REFERENCE)],
            ]
        )
    registry = get_domain_registry(engine)
    return _dumps(
        [out, _rescue_sample(registry), _cap_sample(), _type_constraint_sample(registry, domain)]
    )


def _type_constraint_sample(registry: Any, domain: str | None) -> list[Any]:
    """The cross-chunk type-constraint check under ``domain``'s stack, strict and not.

    With the direction correction on and off: what it keeps, flags (2026-10-08),
    corrects and lets through.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils import entity_cleaner

    constraints = _stack_for(registry, domain).edge_type_constraints()
    return [
        entity_cleaner.validate_relationship_type_constraints(
            [dict(r) for r in _TYPE_CONSTRAINT_RELATIONSHIPS],
            _TYPE_CONSTRAINT_ENTITIES,
            constraints,
            strict_edge_type_constraints=strict,
            enable_direction_correction=correct,
        )
        for strict, correct in ((True, True), (True, False), (False, True), (False, False))
    ]


def _cap_sample() -> list[Any]:
    """The relationship caps on a fixed hub: what each cap keeps within it and flags past it."""
    from chaoscypher_core.services.sources.engine.extraction.utils import entity_cleaner

    return [
        entity_cleaner.enforce_relationship_limits(
            [dict(r) for r in _CAP_RELATIONSHIPS],
            entity_count=6,
            max_relationship_ratio=ratio,
            max_entity_degree=degree,
            max_same_source_type=same,
            protect_orphans=protect,
        )
        for ratio, degree, same, protect in ((8.0, 25, 3, True), (0.5, 2, 12, False))
    ]


def _content_sample() -> str:
    """Every content category (``content_categories.CONTENT_CATEGORIES``) on fixed chunks.

    What the pre-extraction content filter strips from a chunk the domain's
    ``content_exclusions`` name: the model never reads a stripped line, so a
    matcher's change moves the graph (until 2026-10-07 none was in the
    recipe, and the ``code_blocks`` rule that took indented verse for code
    moved no key).
    """
    from chaoscypher_core.services.sources.engine.extraction.content_categories import (
        CONTENT_CATEGORIES,
    )

    return _dumps(
        [
            [name, [CONTENT_CATEGORIES[name].match_and_strip(text) for text in _CONTENT_TEXTS]]
            for name in sorted(CONTENT_CATEGORIES)
        ]
    )


def _text_sample(engine: Any) -> str:
    """The sentence splitter, the chunker under ``engine.chunking`` and the group packer."""
    from chaoscypher_core.services.sources.engine.extraction import orchestration
    from chaoscypher_core.services.sources.engine.extraction.utils import sentence_splitter
    from chaoscypher_core.utils.chunk import ChunkingService

    chunks = _run_sync(
        lambda: ChunkingService(engine).create_chunks(
            _CHUNKER_TEXT, source_id="recipe", store=False
        )
    )
    # Ids are fresh on every run: chunks and groups point at each other by position here.
    chunk_at = {c.get("id"): i for i, c in enumerate(chunks.small_chunks)}
    group_at = {g.get("id"): i for i, g in enumerate(chunks.hierarchical_groups)}
    small = [
        {
            **c,
            "chunk_metadata": {
                **(c.get("chunk_metadata") or {}),
                "group_ids": [
                    group_at.get(gid)
                    for gid in (c.get("chunk_metadata") or {}).get("group_ids") or []
                ],
            },
        }
        for c in chunks.small_chunks
    ]
    groups = [
        {**g, "small_chunk_ids": [chunk_at.get(cid) for cid in g.get("small_chunk_ids") or []]}
        for g in chunks.hierarchical_groups
    ]
    packed = orchestration.build_extraction_groups(
        [dict(c) for c in _GROUP_CHUNKS if c["content"]],
        target_tokens=engine.chunking.target_group_tokens,
        overlap=engine.chunking.group_overlap,
    )
    return _dumps(
        [
            sentence_splitter.split_into_sentences(_SPLITTER_TEXT),
            sentence_splitter.split_into_sentences_with_offsets(_SPLITTER_TEXT),
            [small, groups, chunks.total_original_chunks, chunks.chunks_filtered],
            packed,
            orchestration.build_group_text(_GROUP_CHUNKS),
        ]
    )


def _evidence_sample(engine: Any, registry: Any, evidence_validator: Any) -> dict[str, Any]:
    """The evidence validator in every mode, under every registered vocabulary and naming tables.

    Entities are validated with the sample's entities as the title rung's
    candidates, relationships under each registered relation vocabulary (and
    one that lists function words), each under the universal naming
    conventions and every registered domain's (``registered_name_conventions``:
    the title rung reads a domain's gendered titles, the word rung its titles
    and surname forms).
    """
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        registered_name_conventions,
    )

    vocabularies = [*_registered_vocabularies(registry), _FUNCTION_WORD_VOCABULARY]
    conventions = registered_name_conventions(engine)
    out: dict[str, Any] = {}
    for mode in _EVIDENCE_MODES:
        entities = []
        relationships = []
        for conv in conventions:
            candidates = [dict(e) for e in _EVIDENCE_ENTITIES]
            sorted_entities = evidence_validator.sort_entities_by_evidence(
                [dict(e) for e in [*_EVIDENCE_ENTITIES, *_UNEVIDENCED_ENTITIES]],
                _EVIDENCE_SENTENCES,
                mode,
                conventions=conv,
            )
            entities.append(
                [
                    [
                        evidence_validator.validate_entity_evidence(
                            e, _EVIDENCE_SENTENCES, mode, conventions=conv, candidates=candidates
                        )
                        for e in candidates
                    ],
                    # keep, flag, never delete (2026-10-08): what the entity filter keeps,
                    # flags (with its reason) and drops
                    sorted_entities.entities,
                    sorted_entities.index_mapping,
                ]
            )
            relationships.append(
                [
                    [
                        [
                            [verdict.failure, verdict.reason]
                            for verdict in (
                                evidence_validator.relationship_evidence(
                                    dict(r),
                                    _EVIDENCE_ENTITIES,
                                    _EVIDENCE_SENTENCES,
                                    mode,
                                    vocabulary=vocab,
                                    conventions=conv,
                                )
                                for r in _EVIDENCE_RELATIONSHIPS
                            )
                        ],
                        _sorting_sample(evidence_validator, mode, vocab, conv),
                    ]
                    for vocab in vocabularies
                ]
            )
        out[mode] = (entities, relationships)
    return out


def _sorting_sample(evidence_validator: Any, mode: str, vocabulary: Any, conventions: Any) -> Any:
    """What the evidence filter keeps, flags, folds and drops of the sample relationships.

    Keep, flag, never delete (2026-10-07): ``sort_relationships_by_evidence``
    is the filter's one decision (a relationship without evidence kept
    flagged with its reason, an unevidenced copy folded into its evidenced
    twin, an invalid endpoint dropped).
    """
    sorting = evidence_validator.sort_relationships_by_evidence(
        [dict(r) for r in _EVIDENCE_RELATIONSHIPS],
        _EVIDENCE_ENTITIES,
        _EVIDENCE_SENTENCES,
        mode,
        vocabulary=vocabulary,
        conventions=conventions,
    )
    return [
        sorting.relationships,
        [rel for rel, _ in sorting.folded],
        [rel for rel, _ in sorting.dropped],
    ]


def _registered_vocabularies(registry: Any) -> list[Any]:
    """No vocabulary, then every registered domain's relation vocabulary, each distinct one once."""
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        domain_relation_vocabulary,
    )

    found: dict[str, Any] = {}
    for info in registry.list_domain_info():
        vocab = domain_relation_vocabulary(registry.get_domain(str(info["name"])))
        if vocab.relations:
            found.setdefault(json.dumps(vocab.as_config(), sort_keys=True), vocab)
    return [None, *(found[key] for key in sorted(found))]


_KNOWN_GUARD_ENTITIES: list[dict[str, Any]] = [
    {"name": "Lucien", "type": "Character", "aliases": ["Radím", "Tarágin"], "sent_ref": "S5"},
    {
        "name": "Princess Dora Zvaróvskaya",
        "type": "Character",
        "aliases": ["the princess"],
        "sent_ref": "S8",
    },
    {"name": "Prince Ansel Dravin", "type": "Character", "sent_ref": "S5"},
    {"name": "Elke", "type": "Character", "sent_ref": "S5"},
    {"name": "Lucien", "type": "Character", "sent_ref": "S7"},
]
"""What the known-entities guards decide over :data:`_KNOWN_GUARD_SENTENCES` (invented names):
another known entry's name given as an alias (``Radím``, dropped) beside a surname three entries
gave (``Tarágin``, kept) on a known name its sentence does not write (flagged), a known name its
sentence does not write demoted to the one reference it does (``the princess``: a bare title
never names a known entry, spec D8), a name no entry holds (left alone), a known entry's
single-word alias written as a record's name (it names no one: flagged), and a known name its
sentence does not write but the sentence before does (named)."""


_KNOWN_D8_ENTITIES: list[dict[str, Any]] = [
    {"name": "Lady Mirela Varn", "type": "Character", "aliases": ["Lela"], "sent_ref": "S1"},
    {"name": "Lord Tomas Varn", "type": "Character", "sent_ref": "S1"},
    {
        "name": "Prince Orlan Vesk",
        "type": "Character",
        "aliases": ["the King of Valdria"],
        "sent_ref": "S1",
    },
    {"name": "Count Varo Tesk", "type": "Character", "aliases": ["the count"], "sent_ref": "S1"},
    {
        "name": "Teodor Brask",
        "type": "Character",
        "aliases": ["Halvard Orrin", "her old tutor"],
        "sent_ref": "S1",
    },
]
"""A third group of the list (invented names): a single-word alias (``Lela``), a family word two
entries claim (``Varn``), a description alias (``the King of Valdria``), a title alias, a
multi-word name alias that shares no word with its entry's name (``Halvard Orrin``: one of rule
1's names, ``known_entities._names_by_rule_one``, which names by its naming word ``Orrin`` where
rule 2 needs the whole phrase) and a speaker-relative alias the block never offers (``her old
tutor``)."""

_KNOWN_GUARD_SENTENCES = [
    "Lela, the young lady, smiled at Varn.",
    "The King of Valdria is clever.",
    "Valdria lost the war.",
    "The King of Valdria, the old prince, is clever.",
    "Prince Ansel Dravin married Elke, the little princess.",
    "Lucien Tarágin came in.",
    "The duke was shot.",
    "Vera Dárinovna laughed, and the princess smiled at the Velúrovs.",
    "The Lord of Dunmere rode out.",
    "Orrin drew his sword.",
]
_KNOWN_D8_GUARD_ENTITIES: list[dict[str, Any]] = [
    {
        "name": "Lady Mirela Varn",
        "type": "Character",
        "aliases": ["the young lady"],
        "sent_ref": "S1",
    },
    {"name": "Prince Orlan Vesk", "type": "Character", "sent_ref": "S2"},
    {"name": "Prince Orlan Vesk", "type": "Character", "sent_ref": "S3"},
    {
        "name": "Prince Orlan Vesk",
        "type": "Character",
        "aliases": ["the King of Valdria", "the old prince"],
        "sent_ref": "S4",
    },
    {"name": "the King of Valdria", "type": "Character", "sent_ref": "S2"},
    {"name": "the King of Valdria", "type": "Character", "sent_ref": "S3"},
    {
        "name": "Count Varo Tesk",
        "type": "Character",
        "aliases": ["the Lord of Dunmere"],
        "sent_ref": "S9",
    },
    {"name": "Teodor Brask", "type": "Character", "sent_ref": "S10"},
]
"""The name guard's D8 rules over :data:`_KNOWN_GUARD_SENTENCES`: a known name its sentence
writes only by a single-word alias and a shared family word (demoted to ``the young lady``), a
description alias carrying as rule 2's whole phrase, its place word alone naming no one
(flagged), and rule 2 refused when the record writes another reference phrase (flagged); a
record named by that description alias, read by the entry's names (its place word flagged, its
whole phrase carried by rule 2); a known name its sentence writes only by a description the
entry never gave (demoted to it, whatever its shape); and a known name its sentence writes only
by a naming word of the entry's multi-word name alias (named by rule 1)."""


_KNOWN_EDGE_SENTENCES = [
    "The count threw his arms round the little girl.",
    "Count Varo Tesk came in.",
    "The count kissed his little daughter.",
    "Ilse Marren laughed at the count, her father.",
    "Ilse Marren met his wife at the door.",
    "Ilse Marren bowed to the King of Valdria.",
    "Ilse Marren rode to Valdria.",
    "Ilse Marren kissed Count Varo Tesk, her father.",
    "Ilse Marren rode beside Orrin.",
]
_KNOWN_EDGE_ENTITIES: list[dict[str, Any]] = [
    {"name": "Ilse Marren", "type": "Character", "sent_ref": "S1"},
    {"name": "Count Varo Tesk", "type": "Character", "aliases": ["the count"], "sent_ref": "S2"},
    {"name": "the count", "type": "Character", "sent_ref": "S1"},
    {"name": "Mira Tove", "type": "Character", "aliases": ["My wife"], "sent_ref": "S5"},
    {"name": "the King of Valdria", "type": "Character", "sent_ref": "S6"},
    {
        "name": "Count Varo Tesk",
        "type": "Character",
        "aliases": ["the count", "her father"],
        "sent_ref": "S8",
        "properties": {"referent_unevidenced": "the count; her father"},
    },
    {"name": "Teodor Brask", "type": "Character", "sent_ref": "S9"},
    {
        "name": "the King of Valdria",
        "type": "Character",
        "sent_ref": "S6",
        "properties": {"referent_unevidenced": "Count Varo Tesk"},
    },
]
_KNOWN_EDGE_RELATIONSHIPS: list[dict[str, Any]] = [
    {"source": 0, "target": 1, "type": "child_of", "sent_ref": "S1"},
    {"source": 0, "target": 2, "type": "child_of", "sent_ref": "S1"},
    {"source": 0, "target": 1, "type": "child_of", "sent_ref": "S3"},
    {"source": 0, "target": 1, "type": "child_of", "sent_ref": "S4"},
    {"source": 0, "target": 2, "type": "child_of", "sent_ref": "S4"},
    {"source": 0, "target": 3, "type": "knows", "sent_ref": "S5"},
    {"source": 0, "target": 4, "type": "knows", "sent_ref": "S6"},
    {"source": 0, "target": 4, "type": "knows", "sent_ref": "S7"},
    {"source": 0, "target": 5, "type": "child_of", "sent_ref": "S8"},
    {"source": 0, "target": 6, "type": "knows", "sent_ref": "S9"},
    {"source": 0, "target": 7, "type": "knows", "sent_ref": "S6"},
]
"""The relationship evidence filter's known endpoints (spec D8), with the list of the
known-entities sample: an endpoint whose record name is a known entry (``Count Varo Tesk``)
named only by a title and its own reference alias (flagged ``known_endpoint_not_named``), by the
sentence before the cited one, and by neither beside a stated relation; the same edges to a
record named by the reference (``the count``), which reads the title rung as before; and an
alias whose reading holds no naming word (``My wife`` reads as ``My``: ``alias_name``), which
names by no word ("wife" of S5); and a record written as a known entry's distinctive alias
(``the King of Valdria``), named by rule 2 where the alias is written whole (S6) and not by its
place word alone (S7): the name guard's two rules (``KnownEntities.identified``); a known name the
name guard kept and flagged (``referent_unevidenced``), named by no rung even where its name is
written (S8, final review 2 of 2026-10-12); a known name written only by a naming word of its
entry's multi-word name alias, named by rule 1 at standard (S9); and a record the guard demoted
to another entry's distinctive alias, read by its new name (rule 2 where it is written whole, S6),
never refused as a kept name is (``KnownEntities.kept_unevidenced``)."""


_KNOWN_FLAGGED_ENTITIES: list[dict[str, Any]] = [
    {
        "name": "Zorbex Quell",
        "type": "Character",
        "aliases": ["the stranger"],
        "sent_ref": "S1",
        "properties": {"unevidenced": True},
    },
    {
        "name": "Princess Dora Zvaróvskaya",
        "type": "Character",
        "aliases": ["the little princess"],
        "sent_ref": "S1",
        "properties": {"referent_unevidenced": "the little princess"},
    },
    {
        "name": "Margrave Oswin Kell",
        "type": "Character",
        "aliases": ["Oswin", "the margrave"],
        "sent_ref": "S1",
        "properties": {"alias_crossed_known": "Radím"},
    },
]
"""Beside the first group's entries, records the guards and filters flagged: one the evidence
filter kept flagged and one the name guard kept flagged (``is_doubted_record``: neither is
listed, and the second's alias is no evidence for her name in the next group), and one the
alias guard repaired (its crossed ``Radím`` already dropped: listed, with its other aliases)."""


def _known_entities_sample(engine: Any, plan: Any) -> str:
    """The known-entities list of two groups, its block and its guards, under ``plan``.

    Spec 2026-10-11: the list built as every extraction path builds it
    (``KnownEntities.for_plan`` on the plan the recipe hashes, the stored
    source's under the caller's domain: so the stack's naming conventions
    and ``extraction.known_entities_limit``), folded as the queue path folds
    its completed chunk-task rows (``known_entities_from_rows``) from two
    groups of the dedup sample (the second re-citing names of the first, so
    the ranking reads the groups cited), a third (:data:`_KNOWN_D8_ENTITIES`)
    and a row with no output (a group seen), its rendered lines and block
    (a single-word alias is never offered, spec D8), the alias and name
    guards with their counts over :data:`_KNOWN_GUARD_ENTITIES` (an alias
    that is another entry's name, a known name its cited sentence does not
    write, kept flagged or demoted) and the name guard's D8 rules over
    :data:`_KNOWN_D8_GUARD_ENTITIES`. Every name is invented (no corpus name).
    The first group also holds :data:`_KNOWN_FLAGGED_ENTITIES`: records the
    pipeline doubts, which the list leaves out, and one the alias guard
    repaired, which it lists. Then the relationship evidence filter's known
    endpoints over :data:`_KNOWN_EDGE_RELATIONSHIPS` with that list, at
    strict and standard, under the plan's vocabulary and conventions (spec D8).
    """
    from chaoscypher_core.services.sources.engine.extraction.utils import evidence_validator
    from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
        KnownEntities,
        known_entities_from_rows,
    )

    rows: list[dict[str, Any]] = [
        {
            "raw_entities": [*_fresh_entities()[:6], *copy.deepcopy(_KNOWN_FLAGGED_ENTITIES)],
            "raw_relationships": [dict(r) for r in _DEDUP_RELATIONSHIPS[:3]],
        },
        # the second group: entries 3-8, its edges rebased onto them
        {
            "raw_entities": _fresh_entities()[3:9],
            "raw_relationships": [
                {**r, "source": r["source"] - 3, "target": r["target"] - 3}
                for r in _DEDUP_RELATIONSHIPS[2:6]
            ],
        },
        {"raw_entities": copy.deepcopy(_KNOWN_D8_ENTITIES), "raw_relationships": []},
        {"raw_entities": None, "raw_relationships": None},
    ]
    known = known_entities_from_rows(rows, known=KnownEntities.for_plan(plan.chunk_kwargs, engine))
    records, stats = known.guard([dict(e) for e in _KNOWN_GUARD_ENTITIES], _KNOWN_GUARD_SENTENCES)
    d8_records, d8_stats = known.guard(
        [dict(e) for e in _KNOWN_D8_GUARD_ENTITIES], _KNOWN_GUARD_SENTENCES
    )
    edge_inputs = {
        "vocabulary": plan.chunk_kwargs.get("relation_vocabulary"),
        "conventions": plan.chunk_kwargs.get("name_conventions"),
        "known": known,
    }
    edges = [
        [
            [
                [verdict.failure, verdict.reason]
                for verdict in (
                    evidence_validator.relationship_evidence(
                        dict(r), _KNOWN_EDGE_ENTITIES, _KNOWN_EDGE_SENTENCES, mode, **edge_inputs
                    )
                    for r in _KNOWN_EDGE_RELATIONSHIPS
                )
            ],
            # what the filter keeps flagged (an unevidenced copy folds into its evidenced twin)
            evidence_validator.sort_relationships_by_evidence(
                [dict(r) for r in _KNOWN_EDGE_RELATIONSHIPS],
                [dict(e) for e in _KNOWN_EDGE_ENTITIES],
                _KNOWN_EDGE_SENTENCES,
                mode,
                **edge_inputs,
            ).relationships,
        ]
        for mode in ("strict", "standard")
    ]
    return _dumps(
        _plain(
            [
                known.stats(),
                known.lines(),
                known.block(),
                records,
                stats,
                d8_records,
                d8_stats,
                edges,
            ]
        )
    )


def extraction_code_digest(settings: Any, *, domain: str | None = None) -> str:
    """What the extraction and commit code does to fixed samples, as a hash.

    The rendered prompts (:func:`extraction_prompts_digest`), the extraction
    plan for ``domain`` (the generic fallback when None), every registered
    domain's ``(name, version, content hash)``, the evidence validator in
    every mode under every registered relation vocabulary, the commit's
    relationship orientation on a fixed symmetric and inverse map, the line
    parser, the name rule and the deduplication components under every
    registered domain's naming conventions and the pipeline under
    ``domain`` (:func:`_dedup_sample`), the commit's citation chunks, orphan
    filter and payload (:func:`_commit_sample`), the sentence splitter, the
    chunker and the group packer, the per-chunk name filters, the type
    rescue, the relationship caps and the type-constraint check
    (:func:`_filter_sample`), every content category
    (:func:`_content_sample`), and the known-entities list of two groups
    under that plan, its block and its alias and name guards
    (:func:`_known_entities_sample`).
    """
    from chaoscypher_core.services.sources.engine.commit.relation import (
        RelationshipCommitHandler,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
        build_extraction_plan,
        stored_source_row,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils import evidence_validator

    engine = _engine_settings(settings)
    registry = get_domain_registry(engine)
    domains = []
    for info in sorted(registry.list_domain_info(), key=lambda d: str(d.get("name"))):
        fp = registry.get_domain_fingerprint(str(info.get("name")))
        domains.append(
            (info.get("name"), fp.version if fp else None, fp.content_hash if fp else None)
        )
    evidence = _evidence_sample(engine, registry, evidence_validator)
    oriented = RelationshipCommitHandler.orient_relationships_for_commit(
        [dict(r) for r in _ORIENTATION_RELATIONSHIPS],
        symmetric_relationships=["friend_of", "sibling_of", "married_to"],
        inverse_relationships={"child_of": "parent_of", "parent_of": "child_of"},
        entity_name_to_node_id={"Pierre": "n2", "Andrew": "n1", "Lise": "n3"},
        entity_index_to_node_id={0: "n1", 1: "n2"},
    )
    # As the queue plans a stored source: with the row's preset, which wins over the domain's.
    plan = build_extraction_plan(
        _stack_for(registry, domain), engine, source_row=stored_source_row()
    )
    parts = [
        extraction_prompts_digest(engine),
        _dumps(_plain(plan)),
        json.dumps(domains, ensure_ascii=False),
        json.dumps(evidence, sort_keys=True),
        json.dumps(oriented, ensure_ascii=False, sort_keys=True, default=str),
        _parser_sample(),
        _dedup_sample(engine, domain),
        _commit_sample(registry, domain),
        _text_sample(engine),
        _filter_sample(engine, domain),
        _content_sample(),
        _known_entities_sample(engine, plan),
        json.dumps({"domain": domain, "vector_dimensions": engine.search.vector_dimensions}),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


LADDER_SAMPLE_RUNGS = 4
"""Rungs of the retry ladder the recipe records (the pinned try and three fallbacks)."""


def decode_ladder() -> dict[str, Any]:
    """The extraction retry ladder (``chunk_failures``) as plain values: its constants and rungs.

    A group rescued on a rung was extracted at another decoding than the
    pinned one, so the ladder shapes the graph: :data:`FALLBACK_TEMPERATURE`,
    :data:`REPEAT_PENALTY` and what each rung sends (on a fixed seed and the
    pinned temperature 0). Until audit wave 7 none of it was in the recipe,
    so a changed fallback temperature served the old graph.
    """
    from chaoscypher_core.services.sources.engine.extraction import chunk_failures

    rungs = []
    for step in range(LADDER_SAMPLE_RUNGS):
        fallback = chunk_failures.decode_fallback(step, seed=42, temperature=0.0)
        rungs.append(None if fallback is None else fallback.record())
    return {
        "fallback_temperature": chunk_failures.FALLBACK_TEMPERATURE,
        "repeat_penalty": chunk_failures.REPEAT_PENALTY,
        "rungs": rungs,
    }


def extraction_recipe_payload(
    settings: Any, *, thinking: bool | None = None, domain: str | None = None
) -> dict[str, Any]:
    """Everything :func:`extraction_recipe` hashes, as plain JSON values.

    Args:
        settings: Engine or app settings the extraction runs with.
        thinking: The extraction thinking switch the stage pins, when it pins
            one (it then wins over the settings' ``thinking_for_extraction``).
        domain: The extraction domain the corpus is extracted under, when the
            caller knows it (a cache key per corpus leaves it to the corpus id).
    """
    engine = _engine_settings(settings)
    llm = {name: getattr(engine.llm, name) for name in EXTRACTION_LLM_FIELDS}
    if thinking is not None:
        llm["thinking_for_extraction"] = thinking
    payload: dict[str, Any] = {
        name: _section(getattr(engine, name)) for name in EXTRACTION_SETTINGS_SECTIONS
    }
    payload["llm"] = llm
    payload["search"] = {"vector_dimensions": engine.search.vector_dimensions}
    payload["ladder"] = decode_ladder()
    payload["code"] = extraction_code_digest(engine, domain=domain)
    if domain is not None:
        payload["domain"] = domain
    return payload


def extraction_recipe(
    settings: Any, *, thinking: bool | None = None, domain: str | None = None
) -> str:
    """A 12-character hash of :func:`extraction_recipe_payload`: the graph cache key's recipe part."""
    blob = json.dumps(
        extraction_recipe_payload(settings, thinking=thinking, domain=domain), sort_keys=True
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "EXTRACTION_LLM_FIELDS",
    "EXTRACTION_SETTINGS_SECTIONS",
    "KEYED_ELSEWHERE",
    "LADDER_SAMPLE_RUNGS",
    "decode_ladder",
    "extraction_code_digest",
    "extraction_prompts_digest",
    "extraction_recipe",
    "extraction_recipe_payload",
]
