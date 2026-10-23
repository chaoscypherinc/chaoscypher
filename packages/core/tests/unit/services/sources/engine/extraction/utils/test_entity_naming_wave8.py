# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The evidence validator's naming rungs after audit wave 8.

* **The phrase rung read a title as a name.** "Prince Harry visited Paisley
  Park" named Prince the musician: the word ``Prince`` was matched as the
  whole name ``Prince``, though it opens the name ``Prince Harry``. A name of
  title words alone is written only where it stands alone ("Prince recorded
  at Paisley Park" still names him; so does "The doctor arrived").
* **Any one or two letters before an apostrophe were an elision.** "Tip
  O'Neil" named ``Neil`` and "D'Angelo" named ``Angelo``. The elided
  particles are a table (``d'``, ``l'``, ``dell'``, ``nell'``, ``all'``,
  written lowercase); ``O'``, ``D'`` and ``Mc``/``Mac`` open a surname.
"""

# ruff: noqa: RUF001

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
    ELIDED_PARTICLES,
    CitedText,
    NamingContext,
    _entity_named,
    _variants,
    validate_relationship_evidence,
)
from tests.fakes.domain_tables import domain_conventions, domain_vocabulary


LITERARY = domain_conventions("literary")
GENERIC = domain_conventions("generic")
VOCABULARY = domain_vocabulary("generic")


def _e(name: str, kind: str = "Person", *aliases: str) -> dict[str, Any]:
    """An entity."""
    return {"name": name, "type": kind, "aliases": list(aliases)}


def _kept(entities: list[dict[str, Any]], text: str, conventions: Any) -> bool:
    """``0 located_in 1`` survives the standard filter on ``text``."""
    rel = {"source": 0, "target": 1, "type": "located_in", "sent_ref": "S1"}
    return validate_relationship_evidence(
        rel, entities, [text], "standard", vocabulary=VOCABULARY, conventions=conventions
    )


@pytest.mark.parametrize("conventions", [LITERARY, GENERIC], ids=["literary", "generic"])
def test_a_title_opening_another_name_names_no_one_of_title_words(conventions: Any) -> None:
    """The text "Prince Harry" does not write the name "Prince"; "Prince recorded" does."""
    prince = [_e("Prince"), _e("Paisley Park", "Location")]
    assert not _kept(prince, "Prince Harry visited Paisley Park.", conventions)
    assert _kept(prince, "Prince recorded at Paisley Park.", conventions)
    assert not CitedText.read("Prince Harry visited.", conventions).has_phrase("Prince")
    assert CitedText.read("Prince Harry visited.", conventions).has_phrase("Prince Harry")
    # a name of title words alone, standing alone
    doctor = [_e("The Doctor", "Character"), _e("London", "Location")]
    assert _kept(doctor, "The doctor arrived in London.", conventions)
    # a name that has a naming word is written wherever its words are
    grey = [_e("Earl Grey", "Product"), _e("London", "Location")]
    assert _kept(grey, "Earl Grey Tea was sold in London.", conventions)


def test_a_dash_between_words_ends_a_titles_phrase() -> None:
    """In "Excuse me, Vicomte—I must" the title stands alone (the dash breaks the phrase)."""
    cited = CitedText.read("“Excuse me, Vicomte—I must tell it in Russian.”", LITERARY)
    assert cited.has_phrase("Vicomte")
    assert cited.titles == {"vicomte"}


@pytest.mark.parametrize(
    ("name", "text"),
    [("Neil Armstrong", "Tip O'Neil praised the crew."),
     ("Angelo Dundee", "D'Angelo moved to Miami."),
     ("Angelo Dundee", "D’Angelo moved to Miami.")],
)  # fmt: skip
def test_a_capital_before_an_apostrophe_opens_a_surname(name: str, text: str) -> None:
    """``O'Neil`` and ``D'Angelo`` are one word each: no ``Neil``, no ``Angelo``."""
    cited = CitedText.read(text, GENERIC)
    assert not _entity_named(cited, _e(name), "standard", naming=NamingContext(None, GENERIC))


@pytest.mark.parametrize(
    ("token", "part"),
    [("d'Enghien", "enghien"), ("l’Arno", "arno"), ("dell'Orto", "orto"),
     ("nell'Isola", "isola"), ("all'Antica", "antica")],
)  # fmt: skip
def test_the_elided_particles_name_by_the_part_after_them(token: str, part: str) -> None:
    """Each particle of the table, written lowercase, names by what follows it."""
    assert part in _variants(token)
    assert set(ELIDED_PARTICLES) == {"d", "l", "dell", "nell", "all"}


@pytest.mark.parametrize("token", ["O'Neil", "D'Angelo", "McNeil", "MacLeod", "qu'Anne"])
def test_a_name_prefix_is_no_elision(token: str) -> None:
    """A name prefix (and an apostrophe after any other letters) leaves the token whole."""
    assert len(_variants(token)) == 1
