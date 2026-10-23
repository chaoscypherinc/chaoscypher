# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Plan the resolution of a graph: pure functions over plain node and edge dicts.

A node is ``{"id", "label", "type", "aliases", "properties"}`` (and
``aliases_given`` when the commit recorded which aliases extraction gave
under the node's own name); an edge is
``{"id", "source", "target", "label", "properties"}`` with the extraction's
``justification`` in its properties.

Evidence, not structure: a merge needs one node's name to be an alias the
other was given (``aliases_given``, never one a merge acquired), or a name
that is the other's name with words left out and nothing added, and not a
short name another node could hold, under the graph's domain's naming
conventions (``domain_resolver.domain_name_conventions``; the one name rule, shared with the pre-commit deduplication:
:mod:`chaoscypher_core.services.sources.engine.deduplication.name_identity`); a family edge is flagged, never deleted, when its own justification
names someone other than its ends (a node of the graph, or a name the graph
lacks) and leaves an end unnamed; nothing here counts parents. The
extraction's ``sent_ref`` does not line up with the stored chunk's sentences
(internal/TODO.md, 2026-10-05), so cited sentences are not read.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    NameConventions,
    NameIndex,
    NameRecord,
    as_conventions,
    fold,
    identity_words,
    name_record,
    name_words,
    plan_name_pairs,
    record_given_aliases,
    record_order_key,
    survivor_candidate,
)


FAMILY_LABELS = frozenset({"parent_of", "child_of", "sibling_of", "spouse_of", "married_to"})

_CAPITALISED = re.compile(r"(?<!\w)([A-ZÀ-ÖØ-Þ][\wÀ-ɏ'\u2019-]*)")
_NOT_A_NAME = frozenset(
    {"i", "he", "she", "it", "they", "we", "you", "his", "her", "its", "their", "this", "that",
     "both", "and", "but", "then", "there", "here", "when", "while", "as", "in", "on", "at", "a", "an"}
)  # fmt: skip
"""Sentence openers and pronouns that are capitalised without naming anyone."""


@dataclass(frozen=True)
class Merge:
    """``drop`` is the same entity as ``keep``: fold it in."""

    keep: str
    drop: str
    why: str


@dataclass(frozen=True)
class AliasDrop:
    """``alias`` on ``node`` names ``owner`` (another node) and comes off."""

    node: str
    alias: str
    owner: str


@dataclass(frozen=True)
class EdgeFlag:
    """An edge a reader should look at, with what was found."""

    edge: str
    why: str


@dataclass
class ResolutionPlan:
    """What the pass would do, strong actions apart from the review list."""

    merges: list[Merge] = field(default_factory=list)
    alias_drops: list[AliasDrop] = field(default_factory=list)
    flags: list[EdgeFlag] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """Nothing to apply and nothing to review."""
        return not (self.merges or self.alias_drops or self.flags)


def node_given_aliases(node: dict[str, Any]) -> list[str]:
    """The aliases extraction gave ``node`` under its own name: its alias evidence.

    ``aliases_given`` when the commit recorded it (aliases a merge acquired
    are the merged-in names' words, not evidence); otherwise every alias was
    given (a node no merge has touched, or one a person wrote). The
    deduplication's reading (``name_identity.record_given_aliases``).
    """
    return record_given_aliases(node)


def _record(node: dict[str, Any]) -> NameRecord:
    """A node as the shared name rule sees it: its name, its type, its given aliases."""
    return name_record(node)


def member_order(node: dict[str, Any]) -> tuple[Any, ...]:
    """Where a node stands in its merge group: what its record is (``record_order_key``), then its id.

    The one order of a group's members: which of them keeps its node
    (:func:`_keep`), the order the merges are listed in, and the order the
    dropped nodes' properties and descriptions fold into the kept one
    (``apply._apply_merges``). Only two nodes alike in name, aliases and
    description fall to the id, and either order then merges the same text.
    """
    return (record_order_key(_record(node)), str(node.get("id") or ""))


def _keep(
    members: list[str],
    by_id: dict[str, dict[str, Any]],
    degree: dict[str, int],
    conventions: NameConventions,
) -> str:
    """Which node of a merge group keeps its label: the one survivor ranking the deduplication uses.

    ``name_identity.pick_survivor`` (own name over a given alias, the spelling
    the corpus writes, the fuller name, then the better connected name, the
    longer label, accents kept), then the label's codepoints; never the node id, which
    depends on the database a graph was built in (until audit wave 7 a tie
    fell to the id, so one graph kept different labels in two databases).
    Members are read in the order of what their records are
    (:func:`member_order`: names, aliases, descriptors and description);
    only two nodes alike in every respect fall to the id, and either keeps
    the same label and the same description. Until
    audit wave 6 the resolver ranked by naming-word count, so ``prodigal son
    Anatole`` kept the label over ``Anatole``.
    """
    from chaoscypher_core.services.sources.engine.deduplication import name_identity

    ordered = sorted(members, key=lambda m: member_order(by_id[m]))
    candidates = [survivor_candidate(by_id[m], support=degree.get(m, 0)) for m in ordered]
    return ordered[name_identity.pick_survivor(candidates, conventions)]


def _find(parent: dict[str, str], x: str) -> str:
    """Union-find root of ``x`` with path halving."""
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def plan_merges(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    conventions: NameConventions | None = None,
    titles: Iterable[str] | None = None,
) -> list[Merge]:
    """Every pair that is one entity, as merges into one surviving node per group.

    ``conventions`` are the graph's domain's naming tables, the ones its
    extraction's deduplication used; ``titles`` alone is the convenience
    form (those title words, every other table universal).
    """
    conv = as_conventions(conventions, titles)
    degree: dict[str, int] = defaultdict(int)
    for e in edges:
        degree[e["source"]] += 1
        degree[e["target"]] += 1
    by_id = {n["id"]: n for n in nodes}
    parent = {n["id"]: n["id"] for n in nodes}
    reasons: dict[tuple[str, str], str] = {}
    ordered = sorted(nodes, key=lambda n: n["id"])

    for i, j, evidence in plan_name_pairs([_record(n) for n in ordered], conventions=conv):
        a, b = ordered[i]["id"], ordered[j]["id"]
        keep = _keep([a, b], by_id, degree, conv)
        drop = b if keep == a else a
        ra, rb = _find(parent, keep), _find(parent, drop)
        if ra != rb:
            parent[rb] = ra
            reasons[(keep, drop)] = evidence.why
    groups: dict[str, list[str]] = defaultdict(list)
    for nid in parent:
        groups[_find(parent, nid)].append(nid)
    merges: list[Merge] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        keep = _keep(members, by_id, degree, conv)
        for m in sorted(members, key=lambda m: member_order(by_id[m])):
            if m == keep:
                continue
            why = reasons.get((keep, m)) or reasons.get((m, keep)) or "joined through another merge"
            merges.append(Merge(keep=keep, drop=m, why=why))
    return merges


def plan_alias_drops(
    nodes: list[dict[str, Any]],
    *,
    conventions: NameConventions | None = None,
    titles: Iterable[str] | None = None,
) -> list[AliasDrop]:
    """An alias of two words or more that is another node's name, and not this node's, comes off.

    "Anna Mikháylovna" on the hostess Anna Pávlovna Schérer names Princess Anna
    Mikháylovna Drubetskáya; a one-word alias ("Kurágin" on Anatole) is a
    surname the family shares and stays. Names are compared as the name rule
    compares them (``identity_words``: titles left out, surname forms folded,
    so "Natálya Rostóva" on one node names "Countess Natálya Rostóv"), and
    only the aliases the node was given (``aliases_given``) are judged: an
    alias a merge acquired is a merged-in name, one entity with this node by
    the rule's own evidence. Until 2026-10-06 every alias was judged by its
    raw words (audit W4-14).
    """
    conv = as_conventions(conventions, titles)
    out: list[AliasDrop] = []
    labels = {n["id"]: identity_words(n["label"], conv) for n in nodes}
    for n in nodes:
        own = labels[n["id"]]
        for alias in node_given_aliases(n):
            words = identity_words(alias, conv)
            if len(words) < 2 or words <= own:
                continue
            owners = [m for m in nodes if m["id"] != n["id"] and words <= labels[m["id"]]]
            if len(owners) == 1:
                out.append(AliasDrop(node=n["id"], alias=alias, owner=owners[0]["id"]))
    return out


def plan_flags(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    conventions: NameConventions | None = None,
    titles: Iterable[str] | None = None,
) -> list[EdgeFlag]:
    """Family edges a reader should look at: the justification names someone other than the ends.

    Two shapes: it names another node of the graph and leaves an end unnamed
    ("Nicholas is the count's eldest son and Pétya is his youngest boy" on an
    edge to Pierre, when Pétya is a node), or it names someone the graph has
    no node for and leaves an end unnamed (the same sentence when it does
    not). A justification that names both ends is left alone, right or wrong:
    nothing here can judge it. Nothing is deleted.
    """
    title_set = as_conventions(conventions, titles).titles
    folded_titles = {fold(t) for t in title_set}
    by_id = {n["id"]: n for n in nodes}
    words_of = {n["id"]: name_words(n["label"], title_set) for n in nodes}
    known = set().union(*words_of.values()) if words_of else set()
    known |= {w for n in nodes for a in n.get("aliases") or [] for w in name_words(a, title_set)}
    out: list[EdgeFlag] = []
    for e in edges:
        if e["label"] not in FAMILY_LABELS:
            continue
        src, tgt = by_id.get(e["source"]), by_id.get(e["target"])
        raw = str((e.get("properties") or {}).get("justification") or "")
        if src is None or tgt is None or not raw:
            continue
        jwords = set(fold(raw).split())
        ends = words_of[src["id"]] | words_of[tgt["id"]]
        missing = [
            n["label"] for n in (src, tgt) if words_of[n["id"]] and not (words_of[n["id"]] & jwords)
        ]
        if not missing:
            continue
        named_other = [
            n["label"]
            for n in nodes
            if n["id"] not in (src["id"], tgt["id"])
            and words_of[n["id"]]
            and words_of[n["id"]] <= jwords
            and not (words_of[n["id"]] & ends)
        ]
        unknown = sorted(
            {
                w
                for w in _CAPITALISED.findall(raw)
                if fold(w)
                and fold(w) not in known
                and fold(w) not in _NOT_A_NAME
                and fold(w) not in folded_titles
            }
        )
        head = f"{src['label']} {e['label']} {tgt['label']}: the justification names"
        if named_other:
            out.append(
                EdgeFlag(
                    e["id"], f"{head} {', '.join(named_other[:2])} and not {', '.join(missing)}"
                )
            )
        elif unknown:
            out.append(
                EdgeFlag(
                    e["id"],
                    f"{head} {', '.join(unknown[:2])}, no node of the graph, and not {', '.join(missing)}",
                )
            )
    return out


def resolution_scope(
    nodes: list[dict[str, Any]],
    seeds: Iterable[str],
    *,
    conventions: NameConventions | None = None,
    titles: Iterable[str] | None = None,
) -> set[str]:
    """The node ids a pass seeded by ``seeds`` may touch: the seeds and every node sharing evidence.

    A node shares evidence with a seed when it shares a naming word, a label
    or an alias with it (``NameIndex.candidates``, the same postings the rule
    joins on), so every pair the rule could join that involves a seed, and
    every holder its guards count, is inside the scope.
    """
    conv = as_conventions(conventions, titles)
    ordered = sorted(nodes, key=lambda n: n["id"])
    index = NameIndex([_record(n) for n in ordered], conventions=conv)
    position = {n["id"]: i for i, n in enumerate(ordered)}
    scope = {nid for nid in seeds if nid in position}
    for nid in list(scope):
        scope |= {ordered[j]["id"] for j in index.candidates(position[nid])}
    return scope


def _within(
    plan: ResolutionPlan, scope: set[str], edges: list[dict[str, Any]], seeds: set[str]
) -> ResolutionPlan:
    """``plan`` without what lies outside ``scope``: whole merge groups and flags.

    Alias drops stay on the ``seeds`` (the nodes the pass is about): an
    alias another source gave its own node is that source's to keep, even
    when it names a node this source wrote (until audit wave 5 the scoped
    pass dropped aliases from every name-word neighbour, other sources'
    nodes included).
    """
    groups: dict[str, list[Merge]] = defaultdict(list)
    for m in plan.merges:
        groups[m.keep].append(m)
    merges = [
        m
        for keep, members in groups.items()
        if keep in scope or any(m.drop in scope for m in members)
        for m in members
    ]
    ends = {e["id"]: (e["source"], e["target"]) for e in edges}
    return ResolutionPlan(
        merges=merges,
        alias_drops=[d for d in plan.alias_drops if d.node in seeds],
        flags=[f for f in plan.flags if set(ends.get(f.edge, ())) & scope],
    )


def plan_resolution(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    conventions: NameConventions | None = None,
    titles: Iterable[str] | None = None,
    scope: Iterable[str] | None = None,
) -> ResolutionPlan:
    """Merges, alias drops and flags for a graph; see the module docstring.

    ``conventions`` are the graph's domain's naming tables (:func:`plan_merges`).
    ``scope``, when given, is the node ids a pass is about (the nodes a
    commit wrote): the plan is made over the whole graph, so every guard
    counts every holder, and keeps only what touches those nodes or a node
    sharing evidence with them (:func:`resolution_scope`); its alias drops
    stay on the scope's own nodes.
    """
    conv = as_conventions(conventions, titles)
    plan = ResolutionPlan(
        merges=plan_merges(nodes, edges, conventions=conv),
        alias_drops=plan_alias_drops(nodes, conventions=conv),
        flags=plan_flags(nodes, edges, conventions=conv),
    )
    if scope is None:
        return plan
    seeds = set(scope)
    return _within(plan, resolution_scope(nodes, seeds, conventions=conv), edges, seeds)


__all__ = [
    "FAMILY_LABELS",
    "AliasDrop",
    "EdgeFlag",
    "Merge",
    "ResolutionPlan",
    "fold",
    "member_order",
    "name_words",
    "node_given_aliases",
    "plan_alias_drops",
    "plan_flags",
    "plan_merges",
    "plan_resolution",
    "resolution_scope",
]
