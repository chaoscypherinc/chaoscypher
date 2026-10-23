# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Entity naming, audit wave 7: surname forms by gender, bare titles only, elided names.

W7-3: "Dólokhov located_in Moyka Canal" (q114) was kept because the cited
text says "Márya Ivánovna Dólokhova" (his mother) and "he": the validator
compared the family stems alone, while the deduplication's family-form check
already said Dólokhova is not Dólokhov. One predicate now
(``name_identity.names_person``): the edge drops, a roughly true fact given
up for one rule in both places. W7-6: the title rung read any title in the
text, so "Prince Hippolyte spoke with Pierre" named Prince Vasíli (the sole
prince among the candidates); a title opening a name is that name's, never
a bare title. W7-9: the fold drops an apostrophe, so "d'Enghien" named no
Enghien; a text word now also names by its part after an elision or a hyphen.
"""

# The cited sentences keep the book's typographic quotes: they are what the validator reads.
# ruff: noqa: RUF001

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni
from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
    CitedText,
    NamingContext,
    _entity_named,
    validate_relationship_evidence,
)
from tests.fakes.domain_tables import domain_conventions, domain_vocabulary


LITERARY = domain_conventions("literary")
GENERIC = domain_conventions("generic")
VOCABULARY = domain_vocabulary("literary")


def _c(name: str, *aliases: str) -> dict[str, Any]:
    """A Character entity."""
    return {"name": name, "type": "Character", "aliases": list(aliases)}


_Q114 = (
    "“He is a son of Márya Ivánovna Dólokhova, such a worthy woman, but there, just fancy! "
    "Those three got hold of a bear somewhere, put it in a carriage, and set off with it to "
    "visit some actresses! The police tried to interfere, and what did the young men do? They "
    "tied a policeman and the bear back to back and put the bear into the Moyka Canal."
)
"""The v3 citation of ``Dólokhov located_in Moyka Canal`` (S3 of its group)."""


@pytest.mark.parametrize("conventions", [LITERARY, GENERIC], ids=["literary", "generic"])
def test_a_womans_surname_form_never_names_a_man(conventions: ni.NameConventions) -> None:
    """q114: the mother's name in the sentence does not name the son; the edge drops."""
    rel = {"source": 0, "target": 1, "type": "located_in", "sent_ref": "S1"}
    entities = [_c("Dólokhov"), {"name": "Moyka Canal", "type": "Location", "aliases": []}]
    assert not validate_relationship_evidence(
        rel, entities, [_Q114], mode="standard", vocabulary=VOCABULARY, conventions=conventions
    )
    # her name names her
    cited = CitedText.read(_Q114, conventions)
    naming = NamingContext(None, conventions)
    assert _entity_named(cited, _c("Márya Ivánovna Dólokhova"), "standard", naming=naming)


def test_the_dedup_and_the_validator_read_one_surname_predicate() -> None:
    """``names_person``: the family-form check and the validator say the same of every pair."""
    words = ["dolokhov", "dolokhova", "rostov", "rostova", "drubetskoy", "drubetskaya",
             "bolkonski", "bolkonskaya", "pavlovna", "pierre"]  # fmt: skip
    for written in words:
        for name in words:
            same = ni.names_person(written, name, LITERARY)
            cited = CitedText.read(written.capitalize(), LITERARY)
            assert cited.names_word(name, LITERARY) is same, (written, name)
            a = ni.NameIndex([ni.NameRecord(written.capitalize(), "C")], conventions=LITERARY)
            b = ni.NameIndex([ni.NameRecord(name.capitalize(), "C")], conventions=LITERARY)
            pa, pb = a._p[0], b._p[0]
            stems_meet = ni.identity_word(written, LITERARY) == ni.identity_word(name, LITERARY)
            assert ni._forms_clash(pa, pb) is (stems_meet and not same), (written, name)


def test_a_title_opening_a_name_is_no_bare_title() -> None:
    """A titled name ("Prince Hippolyte spoke") never names Prince Vasíli; "the prince said" does."""
    vasili = _c("Prince Vasíli Kurágin", "Prince Vasíli")
    pierre = _c("Pierre")
    rel = {"source": 0, "target": 1, "type": "interacts_with", "sent_ref": "S1"}
    for text, kept in [
        ("Prince Hippolyte spoke with Pierre.", False),
        ("The prince spoke with Pierre.", True),
        ("“Well, Prince,” said Pierre.", True),
        ("Vicomte de Mortemart spoke with Pierre.", False),
    ]:
        entities = [vasili, pierre]
        assert (
            validate_relationship_evidence(
                rel, entities, [text], mode="standard", vocabulary=VOCABULARY, conventions=LITERARY
            )
            is kept
        ), text
    assert CitedText.read("Prince Hippolyte spoke with Pierre.", LITERARY).titles == frozenset()
    assert CitedText.read("“Monsieur,” he said.", LITERARY).titles == {"mister"}
    assert CitedText.read("l’abbé smiled", LITERARY).titles == {"abbe"}


@pytest.mark.parametrize(
    ("name", "text"),
    [("Enghien", "The duc d’Enghien was shot."), ("Artagnan", "The Gascon d'Artagnan bowed."),
     ("Marie", "Anne-Marie sang.")],
)  # fmt: skip
def test_an_elided_or_hyphenated_name_names_its_last_part(name: str, text: str) -> None:
    """The matcher reads the part after an elision apostrophe or a hyphen as a naming word."""
    cited = CitedText.read(text, GENERIC)
    assert _entity_named(cited, _c(name), "standard", naming=NamingContext(None, GENERIC))
    # strict wants the name as written
    assert not _entity_named(cited, _c(name), "strict", naming=NamingContext(None, GENERIC))


def test_attestation_is_a_whole_token_and_never_a_part_of_one() -> None:
    """``attested_names``: "this spelling is written" as whole tokens (one definition).

    The part after an elision or a hyphen *names* an entity (above), but is no
    spelling the corpus writes: "Artagnan" is not attested by "D'Artagnan".
    """
    text = "D'Artagnan met Anne-Marie. Pierre's hat, the Rostóvs' house, the duc d’Enghien."
    names = ["Artagnan", "D'Artagnan", "Marie", "Anne-Marie", "Pierre", "Rostóvs", "Enghien"]
    assert ni.attested_names(names, [text]) == ["D'Artagnan", "Anne-Marie", "Pierre", "Rostóvs"]
    pattern = ni.whole_token_pattern("Pierre")
    assert pattern.search("Pierre.") and pattern.search("“Pierre’s”")
    assert not pattern.search("Pierres") and not pattern.search("Jean-Pierre")
