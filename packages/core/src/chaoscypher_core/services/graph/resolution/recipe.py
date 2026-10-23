# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The resolver's recipe: a hash of what it does to fixed samples, never a declared version.

A graph the resolver has run over is another graph (the benchmark keys its
graph cache on it, a reference pack records it), so a change to a merge rule,
an alias rule, a flag rule or the merge policy must change the key without
anyone remembering to bump a number: the recipe is computed from the pass's
own output on samples that exercise each rule.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from chaoscypher_core.services.graph.resolution.plan import plan_resolution
from chaoscypher_core.services.sources.engine.deduplication import service as dedup_service
from chaoscypher_core.services.sources.engine.deduplication.name_guards import (
    GuardSample,
    guard_samples,
    merge_verdicts,
)
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    MERGED_NAMES_KEY,
    NameConventions,
    as_conventions,
)
from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
    registered_name_conventions,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import SUBTYPE_PROPERTY


NAME_RULE_SAMPLES: tuple[GuardSample, ...] = guard_samples()
"""One group of records per registered guard of the name rule (``name_guards.NAME_GUARDS``).

Shared by this recipe and the extraction recipe (``benchmark.extraction_recipe``),
so both move with every guard the deduplication and the resolver share; a
guard test turns each registered guard off and asserts both move.
"""


def _n(
    nid: str,
    label: str,
    aliases: list[str],
    node_type: str = "Person",
    subtype: str | None = "character",
    **props: object,
) -> dict[str, object]:
    """A sample node in the planner's shape: a core type and subtype (a literary character)."""
    if subtype:
        props = {SUBTYPE_PROPERTY: subtype, **props}
    return {"id": nid, "label": label, "type": node_type, "aliases": aliases, "properties": props}


def _sample_node(nid: str, sample: GuardSample) -> dict[str, object]:
    """A guard sample as a committed node: its core type, subtype and merged names."""
    props: dict[str, object] = {MERGED_NAMES_KEY: list(sample.merged)} if sample.merged else {}
    return _n(nid, sample.label, list(sample.aliases), sample.type, sample.subtype, **props)


_RECIPE_NODES = [
    _n("a", "Anna Pávlovna Schérer", ["Annette", "Anna Mikháylovna"]),
    _n("b", "Mlle Schérer", ["the hostess"]),
    _n("c", "Princess Anna Mikháylovna Drubetskáya", []),
    _n("d", "Julie Karágina", []),
    _n("e", "Márya Lvóvna Karágina", []),
    _n("f", "Borís", ["Bóry"]),
    _n("g", "Bóry", []),
    _n("n", "Nicholas", []),
    _n("p", "Pierre", []),
    _n("r1", "Count Rostóv", []),
    _n("r2", "Countess Rostóva", []),
    _n("r3", "Nicholas Rostóv", []),
    _n("k1", "Kurágin", []),
    _n("k2", "Anatole", ["Kurágin"]),
    _n("k3", "Vasíli", ["Kurágin"]),
    _n("q1", "King of Prussia", []),
    _n("q2", "Queen of Prussia", []),
    {**_n("h", "Hélène", ["Bóry", "Princess Hélène"]), "aliases_given": ["Princess Hélène"]},
    _n("hub", "Grand Ledger", []),
    *(_sample_node(f"s{i}", sample) for i, sample in enumerate(NAME_RULE_SAMPLES)),
]
_RECIPE_EDGES = [
    {"id": "e1", "source": "g", "target": "p", "label": "interacts_with", "properties": {}},
    {
        "id": "e2",
        "source": "n",
        "target": "p",
        "label": "sibling_of",
        "properties": {
            "justification": "Nicholas is the count's eldest son and Pétya is his youngest boy."
        },
    },
    # A sample's support is its degree here (the resolver ranks a survivor by it): one edge
    # to a hub per unit, so a sample's support decides its group as the deduplication's does.
    *(
        {"id": f"w{i}.{k}", "source": f"s{i}", "target": "hub", "label": "interacts_with",
         "properties": {}}
        for i, sample in enumerate(NAME_RULE_SAMPLES)
        for k in range(sample.support)
    ),
]  # fmt: skip
_RECIPE_PROPERTIES = (
    {
        "definition": "A member of the Rostóv household.",
        "title": "Princess",
        "aliases": ["x"],
        SUBTYPE_PROPERTY: "character",
    },
    {
        "definition": "An elderly lady who has become poor.",
        "rank": "maid of honour",
        "aliases": ["y"],
        SUBTYPE_PROPERTY: "historical_figure",
    },
)
"""Two nodes' properties the resolver merges: the policy, the descriptions and the subtypes (both kept)."""
_RECIPE_ORPHAN_MERGES = (
    ({"definition": "Ilse's mother.", "orphan": True}, {"definition": "A visitor."}),
    ({"orphan": True, "definition": "A guest."}, {"orphan": True}),
    ({"orphan": "yes"}, {"orphan": True}),
    ({"low_plausibility": True, "plausibility": 0.2}, {"definition": "A room."}),
    ({"unplaced_type": True, "written_type": "Quux"}, {"unplaced_type": True}),
    ({"unplaced_type": "Niece"}, {"unplaced_type": True, "written_type": "Quux"}),
    (
        {"unevidenced": True, "evidence_reason": '"Mavra" is not named in S2'},
        {"definition": "The maid."},
    ),
    ({"unevidenced": True, "definition": "A maid."}, {"unevidenced": True}),
    ({"excluded_name": True, "excluded_by": "Chapter headings"}, {"definition": "The soirée."}),
    ({"excluded_name": "no"}, {"excluded_name": True, "excluded_by": "Chapter headings"}),
)
"""Merges of a node carrying a unanimous flag (``UNANIMOUS_FLAG_KEYS``: orphan, low plausibility,
unplaced type, unevidenced, excluded name): into an unflagged node it loses the flag, two flagged
stay flagged, and a value of the text's under the key is kept (2026-10-07, 2026-10-08)."""
_RECIPE_EDGE_MERGES: tuple[tuple[dict[str, object], dict[str, object]], ...] = (
    (
        {"justification": "Anna Pávlovna received Prince Vasíli first.", "sent_ref": "S3"},
        {"justification": "The prince was the first to arrive.", "sent_ref": "S4"},
    ),
    (
        {
            "justification": "Countess Rostóva keeps the name day.",
            "sent_ref": "S2",
            "unevidenced": True,
            "evidence_reason": "neither endpoint is named in S2",
        },
        {"justification": "The count and the countess keep the name day.", "sent_ref": "S5"},
    ),
    (
        {"justification": "A.", "unevidenced": True, "evidence_reason": "r1"},
        {"justification": "A.", "unevidenced": True, "evidence_reason": "r2"},
    ),
    (
        {"justification": "B.", "capped": True},
        {"justification": "B.", "capped": True, "unevidenced": True, "evidence_reason": "r3"},
    ),
    ({"justification": "C.", "capped": True}, {"justification": "C."}),
    (
        {"justification": "D.", "unmatched_label": True, "written_label": "owes_money_to"},
        {"justification": "D."},
    ),
    (
        {"justification": "E.", "type_mismatch": True, "type_mismatch_reason": "r4"},
        {"justification": "E.", "type_mismatch": True, "type_mismatch_reason": "r5"},
    ),
    ({"justification": "F.", "type_mismatch": True, "type_mismatch_reason": "r6"}, {}),
)
"""Duplicate edges a merge folds into one (``merge_edge_properties``): both justifications kept,
an evidenced copy's sentence leading over an unevidenced one and clearing the flag, every edge
flag (``PIPELINE_EDGE_FLAG_KEYS``: capped, unevidenced, unmatched label, type mismatch) kept only
when both copies carry it, by the nodes' unanimous rule, a flag's reason going with it and the
written label staying (keep, flag, never delete, 2026-10-07, 2026-10-08)."""


def resolution_recipe(
    conventions: NameConventions | None = None, titles: Iterable[str] | None = None
) -> str:
    """What the resolver would do to the samples under ``conventions``, as a 12-hex hash.

    See the module docstring; ``conventions`` are the domain naming tables
    the resolver runs with (``resolve_graph(..., conventions=)``), and every
    table is hashed too. With neither ``conventions`` nor ``titles`` the
    samples run under the universal conventions and every registered
    domain's (``registered_name_conventions``), so a guard that only fires on
    a domain's tables moves the recipe of a caller that names no domain
    (the benchmark's graph cache).
    """
    if conventions is None and titles is None:
        sets = registered_name_conventions()
    else:
        sets = [as_conventions(conventions, titles)]
    parts = [_plan_part(conv) for conv in sets]
    parts.append(
        json.dumps(
            dedup_service.merge_node_properties(*_RECIPE_PROPERTIES),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    parts.append(
        json.dumps(
            [
                dedup_service.merge_node_properties(dict(k), dict(d))
                for k, d in _RECIPE_ORPHAN_MERGES
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    parts.append(
        json.dumps(
            [dedup_service.merge_edge_properties(k, d) for k, d in _RECIPE_EDGE_MERGES],
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def _plan_part(conv: NameConventions) -> str:
    """The tables and the plan over the samples under ``conv``, as one string."""
    plan = plan_resolution(_RECIPE_NODES, _RECIPE_EDGES, conventions=conv)
    return "\x1f".join(
        [
            json.dumps(conv.as_dict(), sort_keys=True),
            json.dumps([(m.keep, m.drop, m.why) for m in plan.merges], ensure_ascii=False),
            json.dumps([(d.node, d.alias, d.owner) for d in plan.alias_drops], ensure_ascii=False),
            json.dumps([(f.edge, f.why) for f in plan.flags], ensure_ascii=False),
            json.dumps(merge_verdicts(conv)),
        ]
    )


__all__ = ["NAME_RULE_SAMPLES", "resolution_recipe"]
