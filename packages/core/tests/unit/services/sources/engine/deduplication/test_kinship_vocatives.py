# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A kinship vocative names no one (2026-10-11, the v5d literary pack).

On the v5d pack a ``the countess`` mention given the alias ``Mamma`` folded
onto Borís's unflagged ``Mamma`` node: ``Mamma`` had no title reading, so a
capitalised word was a given name, ``Mamma`` a name, and the alias the
countess's evidence that she was that node. ``Mamma``, ``Papa``, ``Uncle``
and ``Aunt`` are titles of the universal floor now: ``Mamma`` is a
reference, and a reference's referent is always a named record.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni
from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
    CitedText,
    NamingContext,
)
from chaoscypher_core.settings import EngineSettings
from tests.fakes.domain_tables import domain_conventions


VOCATIVES = (
    "mamma", "mama", "maman", "papa", "uncle", "aunt", "auntie", "grandmamma", "grandmama",
    "grandpapa",
)  # fmt: skip
"""The kinship vocatives of the universal floor: titles and relation words alike."""

_V5D = [("the countess", ("Mamma",)), ("Mamma", ())]
"""The v5d pair: Natásha's mother addressed as ``Mamma``, and Borís's ``Mamma`` node."""


@pytest.mark.parametrize("domain", ["literary", "generic"])
def test_a_countess_mention_with_alias_mamma_no_longer_joins_a_mamma_record(domain: str) -> None:
    """``Mamma`` is a reference: no referent of the countess, and no name of its own."""
    index = ni.NameIndex(
        [ni.NameRecord(name, "Person", aliases) for name, aliases in _V5D],
        conventions=domain_conventions(domain),
    )
    assert [index.reference(i) for i in range(2)] == ["countess", "mama"]
    assert [index.referent(i) for i in range(2)] == [None, None]


async def _dedup(domain: str) -> list[dict[str, Any]]:
    out, _rels, _emb, _log = await run_deduplication(
        entities=[
            {"name": n, "type": "Person", "aliases": list(a), "source_chunk_indices": [k]}
            for k, (n, a) in enumerate(_V5D)
        ],
        relationships=[],
        detected_domain=domain,
        settings=EngineSettings(),
        embedding_service=None,
    )
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["literary", "generic"])
async def test_the_v5d_pair_stays_two_flagged_references(domain: str) -> None:
    """The countess keeps a node of her own, and ``Mamma`` is flagged as the reference it is."""
    out = await _dedup(domain)
    assert sorted(e["name"] for e in out) == ["Mamma", "the countess"]
    for entity in out:
        assert ni.pipeline_flag(entity.get("properties"), ni.DESCRIPTOR_REFERENCE_KEY), entity


@pytest.mark.parametrize("domain", ["literary", "generic"])
@pytest.mark.parametrize("vocative", VOCATIVES)
def test_a_bare_vocative_names_no_sole_title_holder(domain: str, vocative: str) -> None:
    """``Grandpapa laughed`` never names the one ``Grandpapa Ilyá`` of the chunk: a relation word.

    Each vocative is a relation word as well as a title, so the title rung
    (``NamingContext.names_by_title``, ``NameIndex.sole_title_holder``) reads
    none of them, as it reads no ``papa`` and no ``father``; until 2026-10-11
    ``mamma``, ``maman``, ``auntie``, ``grandmamma`` and ``grandpapa`` named
    the sole holder while ``papa``, ``uncle`` and ``aunt`` did not.
    """
    conv = domain_conventions(domain)
    assert vocative in conv.relation_words
    assert conv.title_class.get(vocative, vocative) not in conv.naming_titles
    holder = {"name": f"{vocative.capitalize()} Ilyá", "type": "Person", "aliases": []}
    cited = CitedText.read(f"{vocative.capitalize()} laughed.", conv)
    assert NamingContext([holder], conv).names_by_title(0, cited) is False
    # the rung itself works: a bare address title names its one holder
    monsieur = {"name": "Monsieur Pierre", "type": "Person", "aliases": []}
    assert NamingContext([monsieur], conv).names_by_title(
        0, CitedText.read("Monsieur laughed.", conv)
    )


def test_every_spelling_of_a_title_is_a_relation_word_or_none_is() -> None:
    """One title, one reading: the title rung cannot reach through ``mamma`` what ``mama`` withholds.

    ``naming_titles`` leaves out each relation word spelling by spelling and
    then reads the rest in their class spelling, so a class with some
    spellings relation words and some not names its holder by the others
    (the ``mama`` class before 2026-10-11). Every registered domain.
    """
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry

    for info in get_domain_registry(EngineSettings()).list_domain_info():
        conv = domain_conventions(str(info["name"]))
        for group in conv.title_equivalents:
            related = group & conv.relation_words
            assert related in (frozenset(), group), (info["name"], sorted(group))


def test_no_pack_repeats_a_universal_relation_or_role_word() -> None:
    """A pack's ``relation_vocabulary`` and ``role_words`` add to the floor's, never repeat them.

    The floor is the one source of the language's kin and role words: until
    2026-10-11 the literary and biographical packs each declared ``mamma``
    and ``maman`` beside the floor's ``mama``, and a pack's copy is the
    copy a fix to the floor misses.
    """
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry

    registry = get_domain_registry(EngineSettings())
    for info in registry.list_domain_info():
        domain = registry.get_domain(str(info["name"]))
        vocabulary = domain.get_relation_vocabulary() or {}
        own = {ni.fold(w) for w in ni.vocabulary_words(vocabulary)}
        assert own & ni.UNIVERSAL_RELATION_WORDS == set(), info["name"]
        roles = {ni.fold(w) for w in getattr(domain, "get_role_words", list)() or ()}
        assert roles & ni.UNIVERSAL_ROLE_WORDS == set(), info["name"]
