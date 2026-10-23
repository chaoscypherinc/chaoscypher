# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A title word is a title only in title position (audit wave 8).

Wave 7 made the English and French address titles a floor under every
domain, read in any position: under the generic domain ``John Major`` joined
``John Smith``, ``Carole King`` ``Carole Lombard`` and ``Stephen King``
``Stephen Fry`` (the literary domain, whose own tables hold ``major`` and
``king``, already did), and the floor's ``m.`` (Monsieur) folded to ``m`` and
dropped the initial of ``M. Smith``. Now a title word is a title before the
first capitalised naming word or written lowercase (``name_parts``); a
trailing or inner capitalised one is a surname. Before one word, a man's
title the language also writes as a given name (``Earl Warren``, ``Prince
Fielder``) may be the bearer's given name, so under a domain that does not
declare it a title such a name joins no fuller name that states no gender.

Every pair runs through both real callers (``run_deduplication`` and the
resolver's ``plan_merges``); the v3 pack's names read as they did.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    UNIVERSAL_GIVEN_NAME_TITLES,
    UNIVERSAL_TITLE_WORDS,
    NameConventions,
    NameRecord,
    fold,
    name_parts,
    name_words,
)
from tests.fakes.domain_tables import domain_conventions
from tests.unit.services.sources.engine.deduplication.test_name_rule_holders import (
    _dedup_groups,
    _resolver_groups,
)


GENERIC = domain_conventions("generic")
LITERARY = domain_conventions("literary")

_TRAILING = [
    ("John Major", "John Smith"),
    ("Carole King", "Carole Lombard"),
    ("Stephen King", "Stephen Fry"),
    ("M. Smith", "J. Smith"),
]
"""Kept apart under every domain: a surname that is a title word, and an initial."""

_LEADING = [("Earl Warren", "Warren Burger"), ("Prince Fielder", "Cecil Fielder")]
"""Kept apart under a domain that does not declare the title its own (generic)."""


async def _apart(a: str, b: str, domain: str) -> None:
    """Both passes keep ``a`` and ``b`` two entities under ``domain``, in either input order."""
    two = {frozenset({a}), frozenset({b})}
    for spec in ([(a, ()), (b, ())], [(b, ()), (a, ())]):
        assert await _dedup_groups(spec, domain) == two, (a, b, domain)
        assert _resolver_groups(spec, domain) == two, (a, b, domain)


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["generic", "literary"])
@pytest.mark.parametrize(("a", "b"), _TRAILING)
async def test_a_trailing_title_word_or_an_initial_keeps_its_name(
    a: str, b: str, domain: str
) -> None:
    """``John Major`` is no ``John Smith``, ``M. Smith`` no ``J. Smith``, under every table."""
    await _apart(a, b, domain)


@pytest.mark.asyncio
@pytest.mark.parametrize(("a", "b"), _LEADING)
async def test_a_title_that_may_be_a_given_name_joins_no_unmarked_fuller_name(
    a: str, b: str
) -> None:
    """``Earl Warren`` is no ``Warren Burger``, ``Prince Fielder`` no ``Cecil Fielder`` (generic)."""
    await _apart(a, b, "generic")


@pytest.mark.asyncio
async def test_an_address_title_and_a_declared_title_still_fit() -> None:
    """``Mr Darcy`` is ``Fitzwilliam Darcy`` (generic); ``Prince Vasíli`` is ``Vasíli Kurágin`` (literary)."""
    darcy = [("Mr Darcy", ()), ("Fitzwilliam Darcy", ())]
    assert await _dedup_groups(darcy, "generic") == {frozenset({"Mr Darcy", "Fitzwilliam Darcy"})}
    assert _resolver_groups(darcy, "generic") == {frozenset({"Mr Darcy", "Fitzwilliam Darcy"})}
    vasili = [("Prince Vasíli", ()), ("Vasíli Kurágin", ())]
    joined = {frozenset({"Prince Vasíli", "Vasíli Kurágin"})}
    assert await _dedup_groups(vasili, "literary") == joined
    assert _resolver_groups(vasili, "literary") == joined


def test_title_position() -> None:
    """Leading or lowercase: a title; after a capitalised naming word, capitalised: a name."""
    assert name_words("John Major", GENERIC.titles) == {"john", "major"}
    assert name_words("Carole King", LITERARY.titles) == {"carole", "king"}
    assert name_words("Prince Andrew", GENERIC.titles) == {"andrew"}
    assert name_words("the old Count Rostóv", LITERARY.titles) == {"rostov"}
    # a lowercase word that is no title does not end title position
    assert "princess" not in name_words("the young Princess Bolkónskaya", GENERIC.titles)
    # particles and generation words are no naming word anywhere
    assert name_words("Ludwig Van Beethoven", GENERIC.titles) == {"ludwig", "beethoven"}
    assert name_words("Martin Luther King Jr.", GENERIC.titles) == {"martin", "luther", "king"}
    parts = name_parts("Anna, the countess", frozenset(fold(t) for t in GENERIC.titles))
    assert [(w, naming) for w, _, naming in parts] == [
        ("anna", True),
        ("the", False),
        ("countess", False),
    ]


def test_a_title_word_as_a_surname_states_no_title() -> None:
    """``Carole King`` states no man's title; ``Martin Luther King Jr.`` still states its generation."""
    king = ni._profile(NameRecord("Carole King", "Person"), GENERIC)
    assert king.title_gender is None and king.titles == frozenset()
    junior = ni._profile(NameRecord("Martin Luther King Jr.", "Person"), GENERIC)
    assert junior.generation == "younger" and junior.title_gender is None


def test_m_is_an_initial_and_monsieur_a_title() -> None:
    """``m.`` left the floor (it folds to ``m``, an initial); ``Monsieur`` and ``Mr`` stay."""
    floor = {fold(t) for t in UNIVERSAL_TITLE_WORDS}
    assert "m" not in floor and {"monsieur", "mr", "mister"} <= floor
    assert name_words("M. Smith") == {"m", "smith"}
    assert name_words("Monsieur Pierre", GENERIC.titles) == {"pierre"}
    assert name_words("Mr Darcy", GENERIC.titles) == {"darcy"}


def test_the_given_name_titles_are_the_floors_less_a_domains_own() -> None:
    """Generic declares none of them; literary declares all; the tables round-trip through JSON."""
    assert GENERIC.given_name_titles == UNIVERSAL_GIVEN_NAME_TITLES
    assert LITERARY.given_name_titles == frozenset()
    floor = {fold(t) for t in UNIVERSAL_TITLE_WORDS}
    assert floor >= UNIVERSAL_GIVEN_NAME_TITLES
    own = NameConventions.build(titles=["earl"])
    assert "earl" not in own.given_name_titles and "prince" in own.given_name_titles
    for conv in (GENERIC, LITERARY, own):
        assert NameConventions.from_dict(conv.as_dict()) == conv


_V3 = "internal/benchmarks/reference/war_and_peace_book1-v3/app.db"


def test_the_v3_names_read_as_before_title_position() -> None:
    """Every v3 name and alias has the naming words it had when a title counted anywhere.

    Under the literary domain, all of them; under the generic domain, all but
    ``Dowager Empress`` (``dowager`` is no generic title, so ``Empress`` after
    it is a word of the name).
    """
    import json
    import sqlite3

    from chaoscypher_core.testing.repo_files import repo_file_or_skip

    path = repo_file_or_skip(_V3)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT name, attributes FROM source_entities").fetchall()
    names = {n for n, _ in rows}
    names |= {a for _, attrs in rows for a in json.loads(attrs or "{}").get("aliases") or []}
    assert len(names) > 150

    def _changed(conv: NameConventions) -> set[str]:
        """The names whose naming words title position changed under ``conv``."""
        skip = frozenset(fold(t) for t in conv.titles)
        out = set()
        for n in names:
            anywhere = (
                set()
                if ni.is_bare_role(n)
                else {w for w in fold(ni.bearer_text(n)).split() if w not in skip}
            )
            if anywhere != name_words(n, conv.titles):
                out.add(n)
        return out

    assert _changed(LITERARY) == set()
    assert {n for n in _changed(GENERIC) if not n.startswith("Dowager Empress")} == set()
