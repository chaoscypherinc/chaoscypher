# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A tiny fixed world the retrieval recipe runs the real retrieval over.

:func:`~chaoscypher_core.benchmark.pins.retrieval_recipe` used to hash the
renderer, the word lists and a few constants. The handler fixes of
2026-10-06 changed which chunks graphrag retrieved on 83 of 120 benchmark
questions and left the fingerprint as it was (audit wave 2): the code that
selects seeds, gates entities, runs PageRank, picks triples, fills the budget
and orders the chunks was in no hash.

:func:`sample_retrievals` runs the product's own
:meth:`~chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers.GraphRAGToolHandlers.graphrag_search`
and ``hybrid_retrieval`` end to end over thirty-four entities, eighty-one edges (one
flagged unevidenced, two synthetic inverses) and
nineteen chunks with fixed vectors, under seven lever sets, two source-scoped runs,
two with a source switched off, the universal naming conventions and four whose
shape router asks a chat model, on an in-memory SQLite database holding the
product's own schema and read by the product's own repositories: a real
:class:`~chaoscypher_core.adapters.sqlite.repos.search.SearchRepository` (so
the keyword query, BM25 weights, rank fusion, ``k = limit * HYBRID_OVERFETCH``
and ``min_similarity`` are the real ones), the SQLite graph repository (node
and edge listings) and the SQLite storage adapter (chunks, citations,
sources; audit wave 4: a citation fake returning rows in the order asked for
hid that the adapter does not). It returns each result as canonical text. Any change to what a question
retrieves, or how it is rendered, changes that text. Scores are left out
(the orders they produce are in): floats are the one part a second machine
might print differently.

The world is big enough for every rule to bite (audit wave 3: with 11 nodes,
12 edges and every node near a seed, switching the question gate to whole
words, dropping the connected triple filter or turning keyword seeds off left
the recipe unchanged while they changed 32 of 120 real contexts): more
entities than ``ppr_top_k`` and edges than ``max_triples``, edges between
entities no question seeds, an entity only a keyword seed reaches, entities
below the seed threshold, labels and aliases of titles alone, a family's
surname forms, a three-letter name, a summary long enough to be cut, a stale
chunk vector, a source scope, runs that fall back to ``vector_only`` and
``keyword_only``, and the universal naming conventions beside the literary
domain's. ``test_pins`` replaces each rule in turn and checks the recipe moves,
and measures the handler lines the sample reaches.

Nothing here touches the network or a model: the vectors are constants and
the embedding callback looks them up.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, NamedTuple


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from sqlalchemy.engine import Engine

    from chaoscypher_core.settings import EngineSettings


DIM = 6
"""Size of every sample vector (axes: war, court, family, marriage, place, noise)."""

_PERSON = "Person"
_PLACE = "Location"
_PERSON_SUBTYPE = "character"
"""The sample people's pack subtype (literary's ``Character``: Person/character, spec 2026-10-07 § 6)."""

SAMPLE_NODES: tuple[
    tuple[str, str, str, tuple[str, ...], str | tuple[str, ...], tuple[float, ...]], ...
] = (
    (
        "corvin",
        "Prince Corvin",
        _PERSON,
        ("Corvin", "Corvin Halváne"),
        "The son of the prince. He went to war with Brannoch. "
        "The prince's son went to war with Brannoch. He went to the war with Brannoch.",
        (1.0, 0.0, 0.2, 0.0, 0.0, 0.0),
    ),
    (
        "mira",
        "Princess Mira",
        _PERSON,
        ("Mira", "Princess Míra Halváne"),
        "Corvin's sister, pious and plain. She lived with her father.",
        (0.2, 0.0, 1.0, 0.0, 0.1, 0.0),
    ),
    (
        "odile",
        "Odile Ferrant",
        _PERSON,
        ("the little princess", "Odile"),
        "Prince Corvin's wife.",
        (0.4, 0.0, 0.5, 0.0, 0.0, 0.3),
    ),
    (
        "oldprince",
        "the prince",
        _PERSON,
        ("the old prince",),
        "The father of Corvin and Mira, who lives at Greyhollow and never forgives a weakness.",
        (0.3, 0.0, 0.6, 0.0, 0.2, 0.0),
    ),
    (
        "teodor",
        "Prince Teodór",
        _PERSON,
        ("Teodór Dask",),
        "A courtier. The father of Séverine.",
        (0.0, 1.0, 0.0, 0.2, 0.0, 0.0),
    ),
    (
        "severine",
        "Séverine",
        _PERSON,
        ("Séverine Dask",),
        "Daughter of Prince Teodór. She married Lucan.",
        (0.0, 0.6, 0.0, 0.8, 0.0, 0.0),
    ),
    (
        "lucan",
        "Lucan Varre",
        _PERSON,
        ("Lucan",),
        "The count's son, who married Séverine.",
        (0.0, 0.1, 0.0, 1.0, 0.2, 0.0),
    ),
    (
        "ann",
        "Ann Varre",
        _PERSON,
        ("Ann",),
        "Lucan's sister, who kept his letters.",
        (0.0, 0.0, 0.4, 0.1, 0.0, 0.9),
    ),
    (
        "ostrov",
        "Count Ostróv",
        _PERSON,
        ("Ilyán Ostróv",),
        "Wren's father, a host in Velmar.",
        (0.0, 0.0, 0.2, 0.0, 0.8, 0.1),
    ),
    (
        "ostrova",
        "Countess Ostróva",
        _PERSON,
        (),
        "Wren's mother, mistress of the Ostróv house, who danced at the ball.",
        (0.0, 0.6, 0.0, 0.5, 0.0, 0.6),
    ),
    (
        "ostrovs",
        "Ostróvs",
        "Family",
        (),
        "The count's household in Velmar.",
        (0.0, 0.0, 0.5, 0.0, 0.5, 0.0),
    ),
    (
        "wren",
        "Wren Ostróva",
        _PERSON,
        ("Wren",),
        "A girl of Velmar, the count's daughter.",
        (0.0, 0.0, 0.2, 0.1, 0.7, 0.5),
    ),
    (
        "pavel",
        "Pavel Ostróv",
        _PERSON,
        ("Pavel",),
        "The count's elder son, a cadet.",
        (0.5, 0.0, 0.2, 0.0, 0.3, 0.4),
    ),
    ("velmar", "Velmar", _PLACE, (), "The old capital.", (0.0, 0.0, 0.0, 0.0, 1.0, 0.0)),
    (
        "brannoch",
        "General Brannoch",
        _PERSON,
        (),
        "The commander-in-chief.",
        (0.1, 0.0, 0.0, 0.0, 0.0, 1.0),
    ),
    (
        "rifles",
        "Kessel Rifles",
        "Organization",
        (),
        "A regiment that marched with its general.",
        (0.0, 0.0, 0.0, 0.0, 0.1, 1.0),
    ),
    (
        "emperor",
        "Emperor Aurel",
        _PERSON,
        ("the Emperor",),
        "The sovereign the army served.",
        (0.6, 0.5, 0.0, 0.0, 0.0, 0.2),
    ),
    (
        "queen",
        "the Queen",
        _PERSON,
        (),
        "The Emperor's mother, at court.",
        (0.0, 0.7, 0.3, 0.0, 0.0, 0.2),
    ),
    (
        "hesk",
        "Old Hesk",
        _PERSON,
        ("Hesk",),
        "Corvin's grandfather, a miller at Greyhollow.",
        (0.0, 0.0, 0.3, 0.0, 0.3, 0.6),
    ),
    (
        "greyhollow",
        "Greyhollow",
        _PLACE,
        (),
        "The prince's estate in the hills.",
        (0.0, 0.0, 0.4, 0.0, 0.8, 0.0),
    ),
    (
        "aubrac",
        "Madame Aubrac",
        _PERSON,
        ("Aubrac",),
        "A hostess at court who gave the evening for the families and countesses of Velmar.",
        (0.0, 0.9, 0.0, 0.1, 0.1, 0.3),
    ),
    (
        "petra",
        "Petra Lisk",
        _PERSON,
        ("Petra",),
        "Mira's friend, who writes to her.",
        (0.0, 0.0, 0.5, 0.2, 0.0, 0.6),
    ),
    (
        "dasks",
        "Dasks",
        "Family",
        (),
        "Teodór's family.",
        (0.0, 0.5, 0.3, 0.3, 0.0, 0.3),
    ),
    (
        "bors",
        "Bors",
        _PERSON,
        ("the sentry",),
        ("a soldier of the Rifles", "the sentry at Kessel"),
        (0.3, 0.0, 0.0, 0.0, 0.0, 0.9),
    ),
    ("tam", "Tam", _PERSON, (), "Entity: Tam", (0.0, 0.0, 0.1, 0.0, 0.2, 1.0)),
    (
        "kessel",
        "Kessel",
        _PLACE,
        (),
        "A town on the frontier.",
        (0.2, 0.0, 0.0, 0.0, 0.5, 0.8),
    ),
    (
        "ivo",
        "Ivo Brask",
        _PERSON,
        ("Ivo",),
        "A courier between Velmar and Kessel.",
        (0.1, 0.1, 0.0, 0.0, 0.4, 0.9),
    ),
    (
        "ilse",
        "Mother Ilse",
        _PERSON,
        ("Ilse",),
        "The abbess at Greyhollow.",
        (0.0, 0.2, 0.3, 0.0, 0.3, 0.7),
    ),
    (
        "fenn",
        "Fenn Dorrel",
        _PERSON,
        ("Fenn", "Teodór's secretary"),
        "A notary who drew the marriage contract.",
        (0.0, 0.3, 0.0, 0.6, 0.0, 0.6),
    ),
    ("arrow", "Grey Arrow", "Animal", (), "Corvin's horse.", (0.5, 0.0, 0.0, 0.0, 0.0, 0.7)),
    (
        "lorme",
        "Vicomte de Lorme",
        _PERSON,
        ("the Viscount", "Lorme"),
        "An émigré who told stories at Madame Aubrac's evening.",
        (0.1, 0.8, 0.0, 0.0, 0.0, 0.5),
    ),
    (
        "steward",
        "Prince Teodór's steward",
        _PERSON,
        (),
        "The steward of Prince Teodór, who kept the accounts of his daughter's marriage.",
        (0.3, 0.7, 0.4, 0.5, 0.0, 0.1),
    ),
    (
        "thecount",
        "the count",
        _PERSON,
        ("Papa",),
        "The host of a dinner in Velmar, who sent letters by courier.",
        (0.0, 0.1, 0.5, 0.0, 0.9, 0.3),
    ),
    (
        "orsa",
        "Dame Orsa Velk",
        _PERSON,
        ("Orsa",),
        "The Ostróvs' old governess in Velmar, who taught the count's daughter to dance.",
        (0.0, 0.0, 0.3, 0.0, 0.9, 0.3),
    ),
)
"""``(id, label, entity type, aliases, definition, vector)`` per entity: an invented cast
(no benchmark corpus's names ship in the recipe). A definition given as a tuple is
stored as ``descriptors``; ``Entity: <name>`` is the commit's placeholder; an entity
without aliases stores none.

Thirty-four entities, more than ``ppr_top_k``; most below the seed threshold for
any one question. The cast carries what the name matcher must read right: a
label of titles alone (``the prince``, ``the Queen``) and one-title aliases
(``the Emperor``, ``the little princess``), surname forms of one family
(``Count Ostróv``, ``Countess Ostróva``, ``Wren Ostróva``, the plural
``Ostróvs``), a three-letter name (``Ann``), a description holding a query
word inside another word (``grandfather``), a node reachable only through
an entity no vector seeds (``Kessel Rifles``, through ``General Brannoch``),
and a descriptor alias (``Teodór's secretary``: it names Fenn by his relation
to Teodór, so a question naming Teodór does not name Fenn). Prince Corvin's
description carries a sentence that repeats one before it (its content words
all a kept sentence's: dropped and counted, ``descriptions_dropped``) and one
that overlaps two without repeating either (kept).

Every naming table the handlers read a name by is exercised (audit wave 5:
dropping ``vicomte`` from the literary titles moved 4 of 120 benchmark
contexts and no sample entity carried one): a title only a domain has
(``Vicomte de Lorme``: a title and a particle under literary, a naming word
under the universal conventions, which the run with no domain reads), a
title equivalent (``the Viscount``), a feminine surname form (``Countess
Ostróva``, ``Wren Ostróva``), a generation word (``Old Hesk``, ``the old
prince``), a relation word (``Teodór's secretary``) and gendered titles
(``Princess Mira``, ``Prince Corvin``).

Two are flagged references (:data:`SAMPLE_REFERENCES`, the extraction's
``descriptor_reference``): ``Prince Teodór's steward`` (a descriptor whose
inner name is the first question's Teodór), embedded as the first question
is, closer than every named entity, and ``the count`` (a bare title the
second question writes, in "the count's daughter", with the alias ``Papa``),
embedded close to the second question but below the named ``Ostróv``.
Neither may take a seed slot by a name. The steward seeds the first question
by similarity, as its closest match (B0 q088: "Princess Mary's father" is
``Prince Andrew's father``), and is ranked after the named entities; the
count takes no vector slot from a closer name (B0 q099) and seeds on its
written label after the named seeds; the steward, below a name for the fifth
and seventh questions, seeds neither (2026-10-07: on the B0 literary pack
every reference above the threshold seeded, on 84 of 120 questions).

One is a flagged orphan (:data:`SAMPLE_ORPHANS`, the commit's ``orphan``: no
edge when its source was committed): ``Dame Orsa Velk``, embedded close to
the second question, a vector seed like any named node, ranked after every
connected seed and before the reference ``the count`` (keep, flag, never
delete, 2026-10-07: the commit used to delete such an entity, description
and all).

One is flagged ``low_plausibility`` (:data:`SAMPLE_DOUBTFUL`: its name scored
below the per-chunk plausibility bar, kept for its description), ranked
after every undoubted entity of its standing, and one edge is flagged
``capped`` (:data:`SAMPLE_CAPPED_EDGES`: past a relationship cap when it was
extracted), walked at a tenth of an edge's weight (keep, flag, never delete,
2026-10-07). Two more are flagged by the strict type check
(:data:`SAMPLE_MISFIT_EDGES`, 2026-10-08): one kept as ``related_to`` for a
label no template answers to, one whose endpoints its template refuses; with
the capped one they rank after every unflagged line of their standing, in the
``max_triples`` selection and the summary alike.
"""

SAMPLE_REFERENCES: frozenset[str] = frozenset({"steward", "thecount"})
"""The sample entities stored with the reference flag (``descriptor_reference: true``)."""

SAMPLE_ORPHANS: frozenset[str] = frozenset({"orsa"})
"""The sample entities stored with the orphan flag (``orphan: true``); no sample edge touches them."""

SAMPLE_DOUBTFUL: frozenset[str] = frozenset({"greyhollow"})
"""The sample entities stored with the plausibility flag (``low_plausibility: true``)."""

SAMPLE_CAPPED_EDGES: frozenset[tuple[str, str, str]] = frozenset({("corvin", "serves", "emperor")})
"""The sample edges stored with the cap flag (``capped: true``)."""

SAMPLE_MISFIT_EDGES: dict[tuple[str, str, str], dict[str, Any]] = {
    ("ivo", "related_to", "fenn"): {"unmatched_label": True, "written_label": "owes_money_to"},
    ("arrow", "located_in", "kessel"): {
        "type_mismatch": True,
        "type_mismatch_reason": "located_in takes a source of type Person, not Object",
    },
}
"""The sample edges the strict type check kept flagged, with each flag's detail (2026-10-08)."""

SAMPLE_SOURCES: dict[str, tuple[str, ...]] = {"s1": (), "s2": ("queen", "tam")}
"""Each source's entities beyond the default ``s1`` (what a ``source_ids`` scope filters)."""

S2_CHUNKS = frozenset({"c8", "c9", "c14"})
"""The chunks of source ``s2`` (every other chunk is ``s1``'s): fewer than a scoped run's
overfetch finds among the whole graph's best, so a scope applied after the cut comes up
short (audit wave 5)."""

SAMPLE_DOMAIN = "literary"
"""The domain the sample's sources are forced to: the handler reads names by its conventions."""

SCOPED_RUN = ("s2",)
"""The source scope two runs are limited to (``the Queen``, ``Tam``, ``c8``, ``c9`` and ``c14``)."""


SAMPLE_EDGES: tuple[tuple[str, str, str], ...] = (
    ("corvin", "sibling_of", "mira"),
    ("odile", "spouse_of", "corvin"),
    ("oldprince", "parent_of", "corvin"),
    ("oldprince", "parent_of", "mira"),
    ("oldprince", "located_in", "greyhollow"),
    ("corvin", "serves", "brannoch"),
    ("corvin", "serves", "emperor"),
    ("brannoch", "commands", "rifles"),
    ("rifles", "located_in", "kessel"),
    ("bors", "member_of", "rifles"),
    ("ivo", "travels_to", "kessel"),
    ("ivo", "travels_to", "velmar"),
    ("teodor", "parent_of", "severine"),
    ("lucan", "spouse_of", "severine"),
    ("lucan", "interacts_with", "corvin"),
    ("corvin", "interacts_with", "lucan"),
    ("ann", "sibling_of", "lucan"),
    ("ann", "related_to", "petra"),
    ("mira", "mentions", "severine"),
    ("mira", "interacts_with", "petra"),
    ("petra", "related_to", "mira"),
    ("ostrov", "parent_of", "wren"),
    ("ostrov", "parent_of", "pavel"),
    ("ostrova", "spouse_of", "ostrov"),
    ("ostrova", "parent_of", "wren"),
    ("ostrov", "member_of", "ostrovs"),
    ("ostrova", "member_of", "ostrovs"),
    ("wren", "member_of", "ostrovs"),
    ("pavel", "member_of", "ostrovs"),
    ("ostrovs", "located_in", "velmar"),
    ("ostrov", "located_in", "velmar"),
    ("wren", "related_to", "lucan"),
    ("pavel", "serves", "brannoch"),
    ("pavel", "friend_of", "lucan"),
    ("teodor", "member_of", "dasks"),
    ("severine", "member_of", "dasks"),
    ("teodor", "attends", "aubrac"),
    ("severine", "attends", "aubrac"),
    ("aubrac", "located_in", "velmar"),
    ("queen", "parent_of", "emperor"),
    ("emperor", "interacts_with", "teodor"),
    ("teodor", "serves", "emperor"),
    ("hesk", "parent_of", "oldprince"),
    ("hesk", "located_in", "greyhollow"),
    ("tam", "works_for", "hesk"),
    ("ilse", "located_in", "greyhollow"),
    ("ilse", "interacts_with", "mira"),
    ("fenn", "works_for", "teodor"),
    ("fenn", "interacts_with", "lucan"),
    ("corvin", "owns", "arrow"),
    ("bors", "interacts_with", "ivo"),
    ("tam", "interacts_with", "ilse"),
    ("kessel", "located_in", "velmar"),
    ("odile", "located_in", "greyhollow"),
    ("odile", "interacts_with", "mira"),
    ("ann", "interacts_with", "fenn"),
    ("ivo", "works_for", "ostrov"),
    ("emperor", "located_in", "velmar"),
    ("queen", "interacts_with", "aubrac"),
    ("pavel", "interacts_with", "bors"),
    ("arrow", "located_in", "kessel"),
    ("tam", "related_to", "bors"),
    ("ilse", "related_to", "petra"),
    ("fenn", "located_in", "velmar"),
    ("ivo", "interacts_with", "tam"),
    ("bors", "located_in", "kessel"),
    ("petra", "located_in", "velmar"),
    ("aubrac", "interacts_with", "fenn"),
    ("hesk", "interacts_with", "ilse"),
    ("dasks", "located_in", "velmar"),
    ("arrow", "related_to", "bors"),
    ("ivo", "related_to", "fenn"),
    ("tam", "located_in", "greyhollow"),
    ("lorme", "attends", "aubrac"),
    ("lorme", "interacts_with", "severine"),
    ("teodor", "interacts_with", "steward"),
    ("fenn", "interacts_with", "steward"),
    ("thecount", "located_in", "velmar"),
    ("corvin", "betrothed_to", "severine"),
    ("corvin", "child_of", "oldprince"),
    ("severine", "child_of", "teodor"),
)
"""``(source, relation, target)`` per edge: more than ``max_triples``, edges between
entities no question seeds, a symmetric generic pair stated both ways and generic relations,
one no cited sentence states (:data:`SAMPLE_UNEVIDENCED`) and two synthetic inverses
(:data:`SAMPLE_INVERSE_EDGES`)."""

SAMPLE_INVERSE_EDGES: dict[tuple[str, str, str], str] = {
    ("corvin", "child_of", "oldprince"): "parent_of",
    ("severine", "child_of", "teodor"): "parent_of",
}
"""The sample edges the commit wrote as synthetic inverses, each with the forward relation it mirrors (``inverse_of``).

Both have their forward twin (``the prince parent_of Corvin``, ``Prince Teodór
parent_of Séverine``), so the graph blocks fold them: an aggregate of Teodór's
or Corvin's parents and children counts one under ``inverse`` (not a second copy
of the fact), and every walk counts both under ``inverse`` (not parallel edges).
"""

SAMPLE_UNEVIDENCED: dict[tuple[str, str, str], str] = {
    ("corvin", "betrothed_to", "severine"): (
        '"Prince Corvin" is named in S2 but "Séverine" is not, and no word of the '
        "domain's relation vocabulary for betrothed_to stands there"
    ),
}
"""The sample edges stored flagged ``unevidenced: true``, each with its ``evidence_reason``.

Keep, flag, never delete (2026-10-07): the evidence filter keeps a relationship
no cited sentence states instead of deleting it. ``Prince Corvin betrothed_to
Séverine`` joins two seeds of the first question, so it would enter PageRank, a
seed path and the relationship lines; by default it enters none of them, and
under ``include_unevidenced_edges`` (the third lever set) it is listed after
every evidenced line and is the first line the summary's budget cuts."""

SAMPLE_CHUNKS: tuple[tuple[str, str, tuple[float, ...]], ...] = (
    (
        "c1",
        "Prince Teodór came to Madame Aubrac's. His daughter Séverine was the beauty of the evening.",
        (0.0, 0.9, 0.0, 0.3, 0.0, 0.1),
    ),
    (
        "c2",
        "Lucan married Séverine in the winter. Everyone said the match was Prince Teodór's doing.",
        (0.0, 0.5, 0.0, 0.9, 0.0, 0.0),
    ),
    (
        "c3",
        "Princess Mira wrote to Petra. She thought the marriage would not make Lucan happy.",
        (0.1, 0.1, 0.8, 0.5, 0.0, 0.1),
    ),
    (
        "c4",
        "Prince Corvin left for the army. He told his sister to care for the little princess.",
        (0.9, 0.0, 0.5, 0.0, 0.0, 0.0),
    ),
    (
        "c5",
        "Odile wept when her husband left. The little princess stayed at Greyhollow.",
        (0.4, 0.0, 0.6, 0.0, 0.0, 0.3),
    ),
    (
        "c6",
        "The count gave a dinner in Velmar. Wren ran into the drawing room.",
        (0.0, 0.0, 0.1, 0.0, 0.9, 0.4),
    ),
    (
        "c7",
        "Brannoch reviewed the regiment at Kessel. The soldiers' boots were worn.",
        (0.8, 0.0, 0.0, 0.0, 0.0, 0.6),
    ),
    (
        "c8",
        "Snow fell over the old capital. Nobody went out.",
        (0.0, 0.0, 0.0, 0.0, 0.5, 0.9),
    ),
    ("c9", "A letter arrived that nobody read.", (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)),
    (
        "c10",
        "The prince shouted at his son. The old prince never forgave a weakness.",
        (0.3, 0.0, 0.7, 0.0, 0.1, 0.1),
    ),
    (
        "c11",
        "The Ostróvs gave a ball in Velmar. Countess Ostróva danced with Pavel.",
        (0.0, 0.1, 0.3, 0.1, 0.8, 0.2),
    ),
    (
        "c12",
        "Ann kept every letter Lucan sent. She showed them to nobody.",
        (0.0, 0.0, 0.4, 0.2, 0.0, 0.8),
    ),
    (
        "c13",
        "Old Hesk, the grandfather, ground corn at Greyhollow mill. Tam carried the sacks.",
        (0.0, 0.0, 0.4, 0.0, 0.5, 0.6),
    ),
    (
        "c14",
        "The Emperor reviewed the guard. The Queen watched from the window.",
        (0.5, 0.6, 0.1, 0.0, 0.0, 0.3),
    ),
    (
        "c15",
        "Fenn Dorrel drew up the marriage contract for Prince Teodór.",
        (0.0, 0.4, 0.0, 0.8, 0.0, 0.3),
    ),
    (
        "c16",
        "Ivo Brask rode from Velmar to Kessel with the count's letters.",
        (0.1, 0.0, 0.0, 0.0, 0.7, 0.6),
    ),
    (
        "c17",
        "Mother Ilse prayed with Princess Mira at the abbey.",
        (0.0, 0.1, 0.6, 0.0, 0.3, 0.5),
    ),
    (
        "c18",
        "Grey Arrow, Corvin's horse, threw a shoe on the road to Kessel.",
        (0.6, 0.0, 0.0, 0.0, 0.3, 0.6),
    ),
    (
        "c19",
        "The vicomte told the story of the duc at Madame Aubrac's. Séverine listened.",
        (0.0, 0.8, 0.0, 0.1, 0.0, 0.4),
    ),
)
"""``(chunk id, text, vector)`` per chunk: more than any lever set's budget."""

STALE_CHUNK_VECTORS: tuple[tuple[str, tuple[float, ...]], ...] = (
    ("c99", (0.1, 0.6, 0.2, 0.6, 0.1, 0.1)),
)
"""Chunk vectors left in the index for chunks no longer stored (the hybrid arm skips them)."""

SAMPLE_CITATIONS: dict[str, tuple[str, ...]] = {
    "corvin": ("c4", "c7", "c18"),
    "mira": ("c3", "c17"),
    "odile": ("c5", "c4"),
    "oldprince": ("c10",),
    "teodor": ("c1", "c2", "c15"),
    "severine": ("c1", "c2"),
    "lucan": ("c2", "c3", "c12"),
    "ann": ("c12",),
    "ostrov": ("c6", "c16"),
    "ostrova": ("c11",),
    "ostrovs": ("c11",),
    "wren": ("c6",),
    "pavel": ("c11",),
    "velmar": ("c6", "c8", "c11"),
    "brannoch": ("c7",),
    "rifles": ("c7",),
    "emperor": ("c14",),
    "queen": ("c14",),
    "hesk": ("c13",),
    "greyhollow": ("c5", "c13"),
    "aubrac": ("c1",),
    "petra": ("c3",),
    "bors": (),
    "tam": ("c13",),
    "kessel": ("c7", "c16", "c18"),
    "ivo": ("c16",),
    "ilse": ("c17",),
    "fenn": ("c15",),
    "arrow": ("c18",),
    "lorme": ("c19",),
    "steward": ("c15",),
    "thecount": ("c6", "c16"),
    "orsa": ("c11",),
}
"""The chunks each entity is cited in (the provenance arm)."""

SAMPLE_RELATIONSHIP_CITATIONS: dict[tuple[str, str, str], tuple[tuple[str, int], ...]] = {
    ("teodor", "parent_of", "severine"): (("c1", 1),),
    ("lucan", "spouse_of", "severine"): (("c2", 0), ("c3", 1)),
    ("oldprince", "parent_of", "corvin"): (("c10", 0),),
    ("corvin", "sibling_of", "mira"): (("c4", 1),),
    ("brannoch", "commands", "rifles"): (("c7", 0),),
}
"""Stored evidence of some sample edges: ``(chunk, sentence index)`` pairs, the sentence being
that chunk's sentence at that index (``split_into_sentences``), as the commit writes them.

``Prince Teodór parent_of Séverine`` and ``Lucan spouse_of Séverine`` are the two hops of the
walk from Teodór to Lucan (the second cited in two chunks, so an edge's first sentence is
the one in the lower chunk id); ``the old prince parent_of Corvin`` is one of the old
prince's two ``parent_of`` edges, so an aggregate of his children has a row with evidence
and one without. Edges not listed have no citation, like an inverse edge or a relationship
kept flagged."""

SAMPLE_CITATION_CONFIDENCE: dict[tuple[str, str], float] = {
    ("corvin", "c4"): 0.6,
    ("corvin", "c18"): 0.8,
}
"""Citations below the default confidence (1.0): ``Prince Corvin``'s by confidence are
``c7``, ``c18``, ``c4``, and by chunk id ``c18``, ``c4``, ``c7``, so the confidence half of
the handler's citation order decides the provenance arm (audit wave 5)."""

AGGREGATE_QUESTION = "Which people does Lucan know?"
"""The sample's aggregate question: one named entity and an aggregate cue (``which people``), no relation word.

Lucan's block lists every label: ``spouse_of`` with its stored sentence (a
marker once its chunk is in), the rest without one, and ``interacts_with``
three facts deep, so a lever set listing one row a group says how many more.
"""

PATH_QUESTION = "How is Prince Teodór connected to Lucan?"
"""The sample's path question: two named entities and a path cue (``connected``).

The shortest walk is the two cited hops through Séverine
(:data:`SAMPLE_RELATIONSHIP_CITATIONS`), the folded inverse beside the first.
"""

GAP_QUESTION = "How is Orsa connected to Wren?"
"""The sample's path question no walk answers: Dame Orsa Velk is an orphan (no edge), so the pair is a gap.

The context says so (``- no chain of up to … relations connects …``), never silence.
"""

ABSTAIN_QUESTION = "What did Wren write to Pavel?"
"""The sample's question the rules abstain on: two named entities, no cue and no relation word.

A lookup under ``router="rules"`` and without a chat model; the router's model decides it otherwise.
"""

SAMPLE_QUESTIONS: dict[str, tuple[float, ...] | None] = {
    "Whom did Prince Teodór's daughter marry, and what did Corvin's sister think of it?": (
        0.3,
        0.7,
        0.4,
        0.5,
        0.0,
        0.1,
    ),
    "What did the Ostróv family give in Velmar, and what did the count's daughter do?": (
        0.0,
        0.0,
        0.2,
        0.0,
        0.9,
        0.3,
    ),
    'Who is "the little princess" married to, and what did the prince tell his son?': (
        0.5,
        0.0,
        0.6,
        0.0,
        0.0,
        0.2,
    ),
    "What did Brannoch order the regiment to do?": (1.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "Who is Mira's father, and what did Ann tell Lucan?": (0.1, 0.0, 0.8, 0.3, 0.0, 0.2),
    # the parts of the two-part questions, searched on their own under ``question_parts``
    "Who is Mira's father?": (0.1, 0.0, 0.9, 0.0, 0.0, 0.1),
    "what did Ann tell Lucan?": (0.0, 0.0, 0.4, 0.6, 0.0, 0.3),
    'Who is "the little princess" married to?': (0.5, 0.0, 0.7, 0.0, 0.0, 0.0),
    "what did the prince tell his son?": (0.2, 0.0, 0.3, 0.0, 0.0, 0.6),
    "What did the Ostróv family give in Velmar?": (0.0, 0.7, 0.0, 0.2, 0.0, 0.1),
    "what did the count's daughter do?": (0.3, 0.1, 0.0, 0.6, 0.0, 0.0),
    "Whom did Prince Teodór's daughter marry?": (0.4, 0.0, 0.0, 0.0, 0.7, 0.1),
    "what did Corvin's sister think of it?": (0.0, 0.2, 0.0, 0.0, 0.5, 0.5),
    "What did the Emperor say to the Queen?": None,
    "What story did the vicomte tell at Madame Aubrac's evening?": (
        0.0,
        0.8,
        0.0,
        0.0,
        0.1,
        0.4,
    ),
    AGGREGATE_QUESTION: (0.0, 0.1, 0.0, 1.0, 0.2, 0.0),
    PATH_QUESTION: (0.0, 0.5, 0.0, 0.6, 0.1, 0.0),
    ABSTAIN_QUESTION: (0.2, 0.0, 0.2, 0.0, 0.6, 0.5),
    GAP_QUESTION: (0.0, 0.0, 0.3, 0.0, 0.9, 0.3),
}
"""The questions and their fixed query vectors; None is a question the embedder fails on.

A possessive; a surname in its family's other forms and a plural; a quoted
one-title alias and a title alone; a name no vector seeds (only the keyword
seed reaches it and what hangs off it); a short name and a query word inside
another word of a description (the question gate); and a question with no
vector (``keyword_only``, its titles naming no one); and a title only the
domain has (``vicomte``: no keyword seed under literary, a naming word under
the universal conventions); then the shapes' questions (:data:`AGGREGATE_QUESTION`,
:data:`PATH_QUESTION`, :data:`ABSTAIN_QUESTION`, :data:`GAP_QUESTION`), each embedded
closest to a named entity (or to Séverine, between the path's two), so the steward, a
reference, is below a name and seeds none of them."""

LEVER_SETS: tuple[dict[str, Any], ...] = (
    {},
    {
        "chunk_limit": 4,
        "vector_reserve": 2,
        "entity_aliases": True,
        "clean_descriptions": True,
        "relationship_evidence": True,
        "question_entities_first": True,
        "dedupe_graph_lines": True,
        "generic_relations_last": True,
        "paths_to_top": True,
        "entity_description_chars": 40,
        "question_gate": False,
        "max_triples": 25,
    },
    {
        "chunk_limit": 6,
        "vector_reserve": 0,
        "triple_selection": "all_edges",
        "question_gate": False,
        "keyword_seeds": False,
        "rank_chunks_by_seeds": False,
        "explain_lines": False,
        "graph_block_last": False,
        "seed_similarity_threshold": 0.5,
        "max_triples": 80,
        "ppr_top_k": 30,
        "include_unevidenced_edges": True,
        "seed_paths": False,
        "entity_descriptions": False,
    },
    {
        "seed_similarity_threshold": 0.99,
        "keyword_seeds": False,
        "chunk_limit": 3,
    },
    {"chunk_limit": 5, "vector_reserve": 8},
    {
        "router": "rules",
        "evidence_chunks": 3,
        "max_hops": 2,
        "max_walks": 1,
        "aggregate_per_label": 1,
        "question_aware_descriptions": True,
        "question_parts": True,
        "clean_descriptions": True,
    },
    {"router": "off"},
)
"""The GraphRAG levers each sample run sets over the defaults (the defaults first).

The second keeps every connected triple (no gate), so the cap and the
connected filter decide; the third lists every edge of thirty-four entities, the
unevidenced one too and last (the summary is cut, by rank); the fourth seeds nothing (``vector_only``); the fifth has a
reserve larger than the budget with provenance chunks competing (the reserve
is cut to the budget: until 2026-10-06 hybrid's sixth to eighth chunks were
protected too, and hybrid's own top five was not inside graphrag's five on 17
of 120 benchmark questions at budget 8 and reserve 10, audit wave 4). The sixth
routes by the rules alone (the two-name question with no cue is a lookup, not
the model's) with a lookup's evidence slots, walks of at most two relations and
one walk a question, an aggregate group of one row and its count above it, and question-aware
descriptions; the seventh has the router off (every question a lookup).
Every lever set but the seventh routes by the rules (the default ``router``
since 2026-10-19; before it ``rules_then_llm``, which without a chat model
decided the same way): the rules decide, and where they abstain the question
is a lookup. The sixth sets ``router`` and ``question_aware_descriptions``
explicitly, so it reads the same whatever the defaults are."""

_FILE = "book.txt"
_DATABASE = "sample"


def _memory_engine() -> Engine:
    """A single-connection in-memory SQLite engine with sqlite-vec loaded."""
    import sqlite_vec  # type: ignore[import-untyped]
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _load_vec(dbapi_connection: Any, _record: object) -> None:
        """Load sqlite-vec into each new connection."""
        dbapi_connection.enable_load_extension(True)
        sqlite_vec.load(dbapi_connection)
        dbapi_connection.enable_load_extension(False)

    return engine


def _nodes() -> list[Any]:
    """The sample entities as graph nodes, with their vectors."""
    from chaoscypher_core.models import Node

    return [
        Node(
            id=nid,
            label=label,
            template_id="t-entity",
            entity_type=kind,
            properties=_properties(
                aliases,
                definition,
                reference=nid in SAMPLE_REFERENCES,
                orphan=nid in SAMPLE_ORPHANS,
                doubtful=nid in SAMPLE_DOUBTFUL,
                subtype=_PERSON_SUBTYPE if kind == _PERSON else None,
            ),
            embedding=list(vector),
            source_id=_source_of(nid),
        )
        for nid, label, kind, aliases, definition, vector in SAMPLE_NODES
    ]


def _properties(
    aliases: tuple[str, ...],
    definition: str | tuple[str, ...],
    *,
    reference: bool = False,
    orphan: bool = False,
    doubtful: bool = False,
    subtype: str | None = None,
) -> dict[str, Any]:
    """A sample entity's stored properties: its aliases (when it has any), its subtype, its description and the flags."""
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        DESCRIPTOR_REFERENCE_KEY,
        LOW_PLAUSIBILITY_KEY,
        ORPHAN_KEY,
        PLAUSIBILITY_SCORE_KEY,
    )

    props: dict[str, Any] = {"aliases": list(aliases)} if aliases else {}
    if subtype:
        props["entity_subtype"] = subtype
    if reference:
        props[DESCRIPTOR_REFERENCE_KEY] = True
    if orphan:
        props[ORPHAN_KEY] = True
    if doubtful:
        props[LOW_PLAUSIBILITY_KEY] = True
        props[PLAUSIBILITY_SCORE_KEY] = 0.3
    if isinstance(definition, tuple):
        props["descriptors"] = list(definition)
    else:
        props["definition"] = definition
    return props


def _source_of(node_id: str) -> str:
    """The sample source an entity was extracted from (:data:`SAMPLE_SOURCES`; ``s1`` by default)."""
    return next((sid for sid, ids in SAMPLE_SOURCES.items() if node_id in ids), "s1")


def _edges() -> list[Any]:
    """The sample edges as graph edges (ids ``e00``.. in sample order)."""
    from chaoscypher_core.models import Edge
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        CAPPED_KEY,
        EVIDENCE_REASON_KEY,
        INVERSE_OF_KEY,
        UNEVIDENCED_KEY,
    )

    def flags(edge: tuple[str, str, str]) -> dict[str, Any]:
        props: dict[str, Any] = {CAPPED_KEY: True} if edge in SAMPLE_CAPPED_EDGES else {}
        props.update(SAMPLE_MISFIT_EDGES.get(edge, {}))
        if edge in SAMPLE_INVERSE_EDGES:
            props[INVERSE_OF_KEY] = SAMPLE_INVERSE_EDGES[edge]
        reason = SAMPLE_UNEVIDENCED.get(edge)
        if reason is not None:
            props.update({UNEVIDENCED_KEY: True, EVIDENCE_REASON_KEY: reason})
        return props

    return [
        Edge(
            id=f"e{i:02d}",
            template_id="t-rel",
            source_node_id=s,
            target_node_id=t,
            label=rel,
            properties=flags((s, rel, t)),
        )
        for i, (s, rel, t) in enumerate(SAMPLE_EDGES)
    ]


def _chunk_source(chunk: str) -> str:
    """The sample source a chunk belongs to: ``s2`` for :data:`S2_CHUNKS`, ``s1`` for every other."""
    return "s2" if chunk in S2_CHUNKS else "s1"


def _citation_id(entity: str, chunk: str) -> str:
    """A citation row's id, chosen so the table's rowid order is neither entity nor lookup order.

    The rows are inserted in reverse sample order: a reader that kept the
    table's order (or the ``entity_uri`` index's) would hand the handler its
    citations in an order no rule chose, and the recipe would show it.
    """
    return f"cit-{chunk}-{entity}"


def _storage_adapter(engine: Engine) -> Any:
    """A real :class:`~chaoscypher_core.adapters.sqlite.adapter.SqliteAdapter` over ``engine``."""
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.safe_session import SafeSession

    class MemoryAdapter(SqliteAdapter):
        """A storage adapter connected to an engine it was handed, not a file."""

        def __init__(self, bound: Engine) -> None:
            """Bind to ``bound`` (never opens a file)."""
            super().__init__(f"/{_DATABASE}/app.db", database_name=_DATABASE)
            self._engine = bound
            self.session = SafeSession(bound)
            self._connected = True

    return MemoryAdapter(engine)


def _store_world(engine: Engine) -> None:
    """Write the sample world into ``engine`` as the product's tables hold it.

    Every table of the product's schema (``SQLModel.metadata``), then the
    sources (``s1``, ``s2``), the two templates, the entities, the edges,
    the chunks, the citations and the relationship citations as rows of
    ``sources``, ``graph_templates``, ``graph_nodes``, ``graph_edges``,
    ``document_chunks``, ``source_citations`` and ``relationship_citations``,
    read back by the product's own repositories.
    """
    from sqlmodel import SQLModel

    from chaoscypher_core.adapters.sqlite import models
    from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences,
    )

    SQLModel.metadata.create_all(engine)
    with SafeSession(engine) as session:  # the adapter's own session class (CC005)
        session.add_all(
            models.SourceRow(
                id=sid,
                database_name=_DATABASE,
                filename=_FILE,
                filepath=_FILE,
                forced_domain=SAMPLE_DOMAIN,
                enabled=True,
            )
            for sid in SAMPLE_SOURCES
        )
        session.add_all(
            models.GraphTemplate(id=tid, database_name=_DATABASE, name=tid, template_type=kind)
            for tid, kind in (("t-entity", "node"), ("t-rel", "edge"))
        )
        session.add_all(
            models.GraphNode(
                id=n.id,
                database_name=_DATABASE,
                graph_name="knowledge",
                template_id=n.template_id,
                label=n.label,
                entity_type=n.entity_type,
                properties=n.properties,
                embedding=n.embedding,
                source_id=n.source_id,
            )
            for n in _nodes()
        )
        session.add_all(
            models.GraphEdge(
                id=e.id,
                database_name=_DATABASE,
                template_id=e.template_id,
                source_node_id=e.source_node_id,
                target_node_id=e.target_node_id,
                label=e.label,
                properties=e.properties,
                source_id="s1",
            )
            for e in _edges()
        )
        session.add_all(
            models.DocumentChunk(
                id=cid,
                database_name=_DATABASE,
                source_id=_chunk_source(cid),
                chunk_index=i,
                content=text,
            )
            for i, (cid, text, _) in enumerate(SAMPLE_CHUNKS)
        )
        session.add_all(
            models.SourceCitation(
                id=_citation_id(entity, chunk),
                database_name=_DATABASE,
                entity_uri=entity,
                entity_label=entity,
                source_id=_chunk_source(chunk),
                chunk_id=chunk,
                extraction_method="ai_extraction",
                confidence=SAMPLE_CITATION_CONFIDENCE.get((entity, chunk), 1.0),
            )
            for entity, chunks in reversed(SAMPLE_CITATIONS.items())
            for chunk in reversed(chunks)
        )
        edge_ids = {(e.source_node_id, e.label, e.target_node_id): e.id for e in _edges()}
        sentences = {cid: split_into_sentences(text) for cid, text, _ in SAMPLE_CHUNKS}
        session.add_all(
            models.RelationshipCitation(
                id=f"rc-{edge_ids[key]}-{chunk}-{idx}",
                database_name=_DATABASE,
                edge_id=edge_ids[key],
                edge_label=key[1],
                edge_type=None,
                source_entity_label=key[0],
                target_entity_label=key[2],
                source_id=_chunk_source(chunk),
                chunk_id=chunk,
                confidence=1.0,
                extraction_method="ai_extraction",
                justification=None,
                citation_metadata={
                    "sent_ref": f"S{idx + 1}",
                    "sentences": [
                        {"text": sentences[chunk][idx], "chunk_id": chunk, "local_index": idx}
                    ],
                },
            )
            for key, pairs in SAMPLE_RELATIONSHIP_CITATIONS.items()
            for chunk, idx in pairs
        )
        session.commit()


def _set_disabled(engine: Engine, disabled: tuple[str, ...]) -> None:
    """Switch the sample sources in ``disabled`` off (``enabled = 0``) and every other one on."""
    from sqlalchemy import bindparam, text

    statement = text("UPDATE sources SET enabled = id NOT IN :disabled").bindparams(
        bindparam("disabled", expanding=True)
    )
    with engine.begin() as conn:
        conn.execute(statement, {"disabled": list(disabled)})


def _set_domain(engine: Engine, domain: str | None) -> None:
    """Force every sample source to ``domain`` (None: no domain, the universal conventions)."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("UPDATE sources SET forced_domain = :domain"), {"domain": domain})


class _WithoutMinimalListings:
    """A graph repository seen without its minimal listings (``list_nodes_minimal``, ``list_edges_minimal``), its node listing without labels.

    The handler falls back to the plain listings for a repository that lacks
    them, and fetches the nodes a walk passes through when the listing
    carries no label (``list_nodes`` here answers ids, sources and templates
    only); the sample runs both paths over the real repository.
    """

    _HIDDEN = frozenset({"list_nodes_minimal", "list_edges_minimal"})

    def __init__(self, graph: Any) -> None:
        """Wrap ``graph``."""
        self._graph = graph

    def list_nodes(self, *args: Any, **kwargs: Any) -> list[Any]:
        """The wrapped repository's node listing, each node as its id, source and template, no label."""
        from types import SimpleNamespace

        return [
            SimpleNamespace(
                id=n.id,
                label=None,
                source_id=n.source_id,
                member_source_ids=getattr(n, "member_source_ids", []),
                template_id=n.template_id,
            )
            for n in self._graph.list_nodes(*args, **kwargs)
        ]

    def __getattr__(self, name: str) -> Any:
        """Every attribute of the wrapped repository but the minimal listings."""
        if name in self._HIDDEN:
            raise AttributeError(name)
        return getattr(self._graph, name)


def _search_repository(engine: Engine) -> Any:
    """A real search repository over ``engine`` (in memory) holding the sample's index."""
    from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository

    repo = SearchRepository(engine, vector_dim=DIM)
    repo.index_nodes_batch(_nodes())
    repo.index_embeddings_batch(
        [(f"chunk:{cid}", list(vector)) for cid, _, vector in SAMPLE_CHUNKS]
        + [(f"chunk:{cid}", list(vector)) for cid, vector in STALE_CHUNK_VECTORS],
        item_type="chunk",
    )
    return repo


EMBEDDING_REPLIES: tuple[str, ...] = ("dict", "attribute", "bare")
"""The reply shapes a sample embedder answers in: ``{"embedding": v}``, an object's ``.embedding``, or the vector itself."""


def _embedder(reply: str = "dict") -> Callable[[str], Coroutine[Any, Any, Any]]:
    """An embedding callback answering each sample question's fixed vector in the ``reply`` shape (:data:`EMBEDDING_REPLIES`).

    No vector for a question whose vector is None: a provider that returns
    none is the embedder failing without a traceback, so the pipeline runs
    ``keyword_only`` and the hybrid arm on its keyword half
    (``hybrid_search:no_query_vector``). The callback raises ``KeyError``
    for any other text (the sample embeds nothing else).

    The handler reads the first two shapes; a bare vector it reads as no
    embedding (no vector seeds), while the search repository reads it as
    the vector, so under ``bare`` the hybrid arm still searches by it. That
    split is a known defect the ``bare`` run pins as it stands (the fix, one
    shared reply parser, is in ``internal/TODO.md``: "Three parsers of an
    embedding reply disagree on a bare vector").
    """
    from types import SimpleNamespace

    if reply not in EMBEDDING_REPLIES:
        msg = f"unknown embedding reply {reply!r}; expected one of {', '.join(EMBEDDING_REPLIES)}"
        raise ValueError(msg)

    async def embed(text: str) -> Any:
        """The fixed vector of ``text``, in the run's reply shape."""
        vector = SAMPLE_QUESTIONS[text]
        found = None if vector is None else list(vector)
        if reply == "attribute":
            return SimpleNamespace(embedding=found)
        if reply == "bare":
            return found
        return {"embedding": found}

    return embed


def run_without_loop[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine that never suspends, without an event loop.

    The recipe is computed from synchronous code that may itself run inside
    an event loop (a chat suite computing a fingerprint), where
    ``asyncio.run`` is refused. The sample's awaits all complete at once
    (constant vectors, an in-memory database), so the coroutine finishes on
    its first step.

    Raises:
        RuntimeError: When the coroutine suspends (it awaited real I/O).
    """
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value  # type: ignore[no-any-return]
    coro.close()
    msg = "the retrieval sample suspended: it must not await real I/O"
    raise RuntimeError(msg)


def _settings(levers: dict[str, Any]) -> EngineSettings:
    """Default engine settings with ``levers`` set on GraphRAG, validated."""
    from chaoscypher_core.settings import EngineSettings, GraphRAGSettings

    settings = EngineSettings()
    settings.graphrag = GraphRAGSettings.model_validate(
        {**settings.graphrag.model_dump(), "profiles": {}, "profile_rules": [], **levers}
    )
    return settings


def _canonical_graphrag(result: dict[str, Any]) -> str:
    """A graphrag result as canonical text: ids, orders, rendered context and stats, no scores."""
    gc = result.get("graph_context") or {}
    stats = dict(result.get("retrieval_stats") or {})
    return json.dumps(
        {
            "context_text": result.get("context_text"),
            "chunks": [
                [c.get("chunk_id"), c.get("chunk_alias"), c.get("retrieval_origin")]
                for c in result.get("chunks") or []
            ],
            "seeds": [e.get("id") for e in gc.get("seed_entities") or []],
            "related": [e.get("id") for e in gc.get("related_entities") or []],
            "relationships": [
                [t.get("source_id"), t.get("label"), t.get("target_id"), t.get("evidence")]
                for t in gc.get("relationships") or []
            ],
            "paths": gc.get("paths") or [],
            "aggregates": gc.get("aggregates") or [],
            "walks": gc.get("walks") or [],
            "walk_gaps": gc.get("walk_gaps") or [],
            "summary": gc.get("summary"),
            "shape": result.get("shape"),
            "stats": stats,
        },
        sort_keys=True,
        ensure_ascii=False,
    )


class _Run(NamedTuple):
    """One pass of every sample question through the handlers."""

    levers: dict[str, Any]
    """GraphRAG levers over the defaults."""
    domain: str | None = SAMPLE_DOMAIN
    """The domain the sources were extracted under (None: unknown, the universal conventions)."""
    scope: tuple[str, ...] | None = None
    """The ``source_ids`` scope, or None for the whole graph."""
    plain_graph: bool = False
    """Whether the graph repository is seen without its minimal listings (:class:`_WithoutMinimalListings`)."""
    embedder: bool = True
    """Whether the handlers have an embedding callback."""
    citations: bool = True
    """Whether the handlers have a source storage (the provenance arm)."""
    settings: bool = True
    """Whether the handlers get settings (False: the handler's own defaults)."""
    disabled: tuple[str, ...] = ()
    """The sample sources switched off (``enabled = 0``) for the run."""
    router: str | None = None
    """The shape the sample's chat model names for every question the router asks it (:func:`_router_chat`); None: no chat model."""
    embedding_reply: str = "dict"
    """The shape the embedder answers in (:data:`EMBEDDING_REPLIES`, :func:`_embedder`)."""


RUNS: tuple[_Run, ...] = (
    *(_Run(levers) for levers in LEVER_SETS),
    _Run(LEVER_SETS[0], scope=SCOPED_RUN, plain_graph=True),
    _Run({"chunk_limit": 2}, scope=SCOPED_RUN),
    _Run(LEVER_SETS[0], domain=None, settings=False),
    _Run(LEVER_SETS[0], embedder=False, citations=False),
    _Run(LEVER_SETS[0], disabled=SCOPED_RUN),
    _Run(LEVER_SETS[0], scope=SCOPED_RUN, disabled=SCOPED_RUN),
    _Run({**LEVER_SETS[0], "router": "llm"}, router="aggregate"),
    _Run({**LEVER_SETS[0], "router": "llm"}, router="global"),
    _Run({**LEVER_SETS[0], "router": "llm", "ppr_top_k": 2}, router="path"),
    _Run(
        {**LEVER_SETS[0], "router": "llm", "ppr_top_k": 2},
        plain_graph=True,
        router="path",
        embedding_reply="attribute",
    ),
    _Run(LEVER_SETS[0], embedding_reply="bare"),
)
"""Every lever set under :data:`SAMPLE_DOMAIN`; then the defaults scoped to
:data:`SCOPED_RUN` over a graph without the minimal listings (every seed of most
questions out of scope: ``vector_only``); a budget of two scoped to it (the hybrid
arm's overfetch of four holds few of the scope's chunks among the whole graph's
best: a scope applied after the cut returns fewer, audit wave 5); with no domain
on the sources (the universal naming conventions) and no settings; with no
embedder and no citations (``keyword_only``, the hybrid arm's keyword half); and
with :data:`SCOPED_RUN`'s source switched off, unscoped and scoped to it (none of
its entities or chunks may answer: until 2026-10-06 both chunk arms served them,
audit wave 6); and with the router asking a chat model for every question
(``router="llm"``, :func:`_router_chat`): once naming every question an aggregate
(a question naming no seed assembles as a lookup, said so); once a global one (the
lookup assembly on the global bundle, the community block said to wait for phase B);
once a path with the context cut to the top two entities by PageRank (the walk's
ends are the named seeds in PageRank order wherever they rank, so a question
naming fewer than two entities assembles as a lookup and says so, and a named
seed outside the context's nodes is fetched for the walk); and so again over a
graph without the minimal listings whose node listing has no labels (the walk
fetches the nodes it passes through outside the context), the embedder
answering an object.
Last, the defaults with an embedder answering a bare vector (:func:`_embedder`:
the handler reads none, ``keyword_only``, the hybrid arm searches by it)."""


def _router_chat(shape: str) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    """The sample's chat model: it names ``shape`` for any question, at once (it never suspends, :func:`run_without_loop`)."""
    content = json.dumps({"shape": shape, "reason": "stub"})

    async def chat(messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """Answer the router's prompt with ``{"shape": shape, "reason": "stub"}``."""
        return {"content": content}

    return chat


def _handlers(
    run: _Run, search: Any, graph: Any, storage: Any, wrap: Callable[[str, Any], Any] | None
) -> Any:
    """The product's retrieval handlers over the sample's stores, wired as ``run`` says."""
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        GraphRAGToolHandlers,
    )

    graph_store: Any = _WithoutMinimalListings(graph) if run.plain_graph else graph
    if wrap is not None:
        graph_store = wrap("graph", graph_store)
        storage = wrap("storage", storage)
    return GraphRAGToolHandlers(
        graph_repository=graph_store,
        search_repository=search,
        indexing_repository=storage,
        source_storage=storage if run.citations else None,
        embedding_callback=_embedder(run.embedding_reply) if run.embedder else None,
        settings=_settings(run.levers) if run.settings else None,
        database_name=_DATABASE,
        chat_callback=_router_chat(run.router) if run.router else None,
    )


def _canonical_hybrid(handlers: Any, question: str, scope: list[str] | None) -> str:
    """The hybrid arm alone as canonical text: chunk ids, their rendered content and the stats.

    A scoped run reads it through ``hybrid_chunk_search`` too (the chunks only), which
    must give the same chunks.
    """
    hybrid = run_without_loop(handlers.hybrid_retrieval(question, source_ids=scope))
    chunks = [(c["chunk_id"], c["content"]) for c in hybrid["chunks"]]
    if scope:
        alone = run_without_loop(handlers.hybrid_chunk_search(question, source_ids=scope))
        if [(c["chunk_id"], c["content"]) for c in alone] != chunks:
            msg = "hybrid_chunk_search and hybrid_retrieval disagree on a scoped question"
            raise RuntimeError(msg)
    return repr([*chunks, sorted(hybrid["retrieval_stats"].items())])


def sample_retrievals(wrap: Callable[[str, Any], Any] | None = None) -> list[str]:
    """Every sample question retrieved in every run (:data:`RUNS`), as canonical text.

    Per run and question: the full ``graphrag_search`` result
    (:func:`_canonical_graphrag`) and the hybrid arm alone
    (:func:`_canonical_hybrid`); then the search repository's own keyword
    ranking of each question over nodes, chunks and both.

    The world lives in the product's own tables, read by the product's own
    repositories (the graph repository's node and edge listings, the storage
    adapter's chunk, citation and source readers), never by stand-ins: until
    2026-10-06 the sample's citation fake returned the rows in the order
    asked for, which the adapter never does, so the recipe could not see
    the handler trusting the adapter's row order (audit wave 4).

    Args:
        wrap: For the guard tests, ``(role, store) -> store``: each store
            passes through it before the handlers get it, ``role`` being
            ``"graph"`` (the graph repository) or ``"storage"`` (the storage
            adapter: chunks, citations, sources). The tests reverse the
            stores' row order, or swap in plain fakes, and check the
            retrieval reads the same. None in the recipe.
    """
    from chaoscypher_core.repo_factories import get_transaction_graph_repository

    engine = _memory_engine()
    out: list[str] = []
    try:
        _store_world(engine)
        search = _search_repository(engine)
        storage = _storage_adapter(engine)
        graph = get_transaction_graph_repository(storage, _DATABASE)
        for run in RUNS:
            _set_domain(engine, run.domain)
            _set_disabled(engine, run.disabled)
            handlers = _handlers(run, search, graph, storage, wrap)
            scope = list(run.scope) if run.scope else None
            for question in SAMPLE_QUESTIONS:
                full = run_without_loop(handlers.graphrag_search(query=question, source_ids=scope))
                out.append(_canonical_graphrag(full))
                out.append(_canonical_hybrid(handlers, question, scope))
        _set_disabled(engine, ())
        for question in SAMPLE_QUESTIONS:
            for item_type in ("node", "chunk", None):
                ranked = search.keyword_search(question, limit=20, item_type=item_type)
                out.append(repr([item_id for item_id, _ in ranked]))
    finally:
        engine.dispose()
    return out


__all__ = [
    "ABSTAIN_QUESTION",
    "AGGREGATE_QUESTION",
    "DIM",
    "EMBEDDING_REPLIES",
    "GAP_QUESTION",
    "LEVER_SETS",
    "PATH_QUESTION",
    "RUNS",
    "SAMPLE_CAPPED_EDGES",
    "SAMPLE_CHUNKS",
    "SAMPLE_CITATIONS",
    "SAMPLE_CITATION_CONFIDENCE",
    "SAMPLE_DOMAIN",
    "SAMPLE_DOUBTFUL",
    "SAMPLE_EDGES",
    "SAMPLE_INVERSE_EDGES",
    "SAMPLE_MISFIT_EDGES",
    "SAMPLE_NODES",
    "SAMPLE_ORPHANS",
    "SAMPLE_QUESTIONS",
    "SAMPLE_REFERENCES",
    "SAMPLE_SOURCES",
    "SAMPLE_UNEVIDENCED",
    "SCOPED_RUN",
    "STALE_CHUNK_VECTORS",
    "run_without_loop",
    "sample_retrievals",
]
