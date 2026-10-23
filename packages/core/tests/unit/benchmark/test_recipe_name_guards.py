# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Every guard of the name rule moves both recipes (audit W3-6, W4-5).

The extraction recipe (``benchmark.extraction_recipe``) and the resolver's
(``resolution.resolution_recipe``) are computed from what the code does to
fixed samples. A guard no sample reaches can be reverted with both keys
unchanged, so a cached graph built under the old rule is served as the new
one: wave 4 found five (the W3-4 same-words checks, the titles clash,
``_title_fit`` saying "family" for "no", ``_fuller_alias_elsewhere``, the
``title_class`` path). Each mutation below turns one guard off; both recipes
must move, under no domain, the literary domain and the generic one.

Since audit wave 5 the guards are a registry in product code
(``name_guards.NAME_GUARDS``, which the recipes read their samples from):
this test turns off every registered guard, and a predicate of a scanned
module (``name_guards.GUARDED_MODULES``) that is neither registered nor
listed in ``NOT_GUARDS`` fails it (the ``merged_names`` guard was in no
list, and turning it off moved neither recipe). Since wave 6 the scan reads
``-> bool | None``, string annotations and unannotated functions too, and
the evidence validator and the relation vocabulary beside the name rule.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import yaml

from chaoscypher_core.benchmark.extraction_recipe import extraction_recipe
from chaoscypher_core.services.graph.resolution.recipe import resolution_recipe
from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni
from chaoscypher_core.services.sources.engine.deduplication.name_guards import (
    GUARDED_MODULES,
    NAME_GUARDS,
    NOT_GUARDS,
    guard_samples,
    guard_target,
    target_module,
)
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
    CORE_TYPES,
    subtype_key,
)
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.repo_files import repo_file_or_skip


def _clear_name_caches() -> None:
    """Clear every ``lru_cache`` of the name module: a mutated guard must not read a cached verdict.

    One named cache was cleared until 2026-10-12, when the surname guard's case failed in the
    docker CI run alone: a worker whose earlier tests had warmed another cache saw the recipe
    unmoved. Every cached function of the module is cleared, whichever the guard reads through.
    """
    for value in vars(ni).values():
        clear = getattr(value, "cache_clear", None)
        if callable(clear):
            clear()


def _recipes() -> tuple[str, ...]:
    """Both recipes, the extraction one under no domain, the literary and the generic domain."""
    _clear_name_caches()
    settings = EngineSettings()
    return (
        extraction_recipe(settings),
        extraction_recipe(settings, domain="literary"),
        extraction_recipe(settings, domain="generic"),
        resolution_recipe(),
    )


@pytest.fixture(scope="module")
def baseline() -> tuple[str, ...]:
    """The four recipes with every guard on."""
    return _recipes()


@pytest.fixture
def clean_caches() -> Iterator[None]:
    """A mutated guard must not leave a cached descriptor verdict behind (it is ``lru_cache``d)."""
    _clear_name_caches()
    yield
    _clear_name_caches()


@pytest.mark.parametrize("guard", NAME_GUARDS, ids=[g.name for g in NAME_GUARDS])
def test_turning_a_guard_off_moves_both_recipes(
    monkeypatch: pytest.MonkeyPatch,
    baseline: tuple[str, ...],
    clean_caches: None,
    guard: Any,
) -> None:
    """The extraction recipe (every arm) and the resolver's both see every registered guard."""
    owner, attr = guard_target(guard)
    assert hasattr(owner, attr), guard.target
    monkeypatch.setattr(owner, attr, guard.off)
    after = _recipes()
    names = (
        ("extraction", "extraction (no domain)"),
        ("extraction", "extraction (literary)"),
        ("extraction", "extraction (generic)"),
        ("resolution", "resolution"),
    )
    unmoved = [
        name
        for (recipe, name), before, now in zip(names, baseline, after, strict=True)
        if recipe in guard.recipes and before == now
    ]
    assert unmoved == []


_GUARD_SHAPED = {
    "bool",
    "bool | None",
    "str | None",
    "Fit",
    "frozenset[str]",
    "frozenset[str] | None",
    "frozenset[int]",
    "int",
    "_Pair | None",
}
"""Return annotations of a predicate: what a guard returns (written as code or as a string).

Since audit wave 7 a choice (``int``: ``pick_survivor``), a set of positions
(``frozenset[int]``: ``NamingContext.named_by_name``), a set of words
(``frozenset[str]``: the bare titles) and any tuple or list
(:data:`_GUARD_SHAPED_GENERICS`: ``surname_key``, ``attested_names``) are
guard-shaped too: each of those decided a graph and moved no recipe."""

_GUARD_SHAPED_GENERICS = ("tuple", "list")
"""Generic return types whose every parametrisation is guard-shaped (``tuple[str, bool]``, ``list[int]``)."""


def _shaped(fn: Any) -> bool:
    """A function returns what a guard returns, or says nothing (unannotated: it may be one)."""
    import ast

    if fn.returns is None:
        return not fn.name.startswith("__")
    returns = fn.returns
    text = (
        returns.value
        if isinstance(returns, ast.Constant) and isinstance(returns.value, str)
        else ast.unparse(returns)
    )
    return text in _GUARD_SHAPED or text.split("[", 1)[0] in _GUARD_SHAPED_GENERICS


def _predicates(source: str | None = None, prefix: str = "") -> set[str]:
    """Every function and method of a scanned module shaped like a guard, as ``[module:][Class.]name``."""
    import ast
    import inspect

    found: set[str] = set()
    modules = GUARDED_MODULES if source is None else {prefix: ""}
    for key in modules:
        text = source if source is not None else inspect.getsource(target_module(f"{key}:x")[0])
        qual = "" if key in ("name_identity", "") else f"{key}:"
        tree = ast.parse(text)
        for node in tree.body:
            scopes = (
                [("", node)] if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) else []
            )
            if isinstance(node, ast.ClassDef):
                scopes = [
                    (f"{node.name}.", m)
                    for m in node.body
                    if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)
                ]
            for scope, fn in scopes:
                if _shaped(fn):
                    found.add(qual + scope + fn.name)
    return found


def test_every_predicate_of_the_rule_is_a_registered_guard_or_says_why_not() -> None:
    """A new guard-shaped function fails here until it is registered (with samples) or classified."""
    registered = {g.target for g in NAME_GUARDS}
    unclassified = _predicates() - registered - set(NOT_GUARDS)
    assert unclassified == set()
    assert not registered & set(NOT_GUARDS)
    for target in registered | set(NOT_GUARDS):
        owner, attr = guard_target(target)
        assert hasattr(owner, attr), target


@pytest.mark.parametrize(
    "source",
    [
        "def guard(a, b) -> int:\n    return 0\n",
        "def guard(a, b) -> frozenset[int]:\n    return frozenset()\n",
        "def guard(a, b) -> tuple[str, bool]:\n    return ('', False)\n",
        "def guard(a, b) -> list[str]:\n    return []\n",
        "def guard(a, b) -> bool | None:\n    return None\n",
        "def guard(a, b) -> 'bool':\n    return True\n",
        "def guard(a, b):\n    return True\n",
        "class K:\n    def guard(self) -> 'str | None':\n        return None\n",
    ],
)
def test_the_scan_sees_every_shape_a_guard_is_written_in(source: str) -> None:
    """``-> bool | None``, a string annotation, no annotation, a method (wave 6); ``int``, sets, tuples, lists (wave 7)."""
    found = _predicates(source)
    assert found and all(name.endswith("guard") for name in found)


def test_every_module_holding_a_guard_is_scanned() -> None:
    """A registered target outside ``name_identity`` names a module the scan reads."""
    for guard in NAME_GUARDS:
        module, _, _ = guard.target.rpartition(":")
        assert (module or "name_identity") in GUARDED_MODULES, guard.target


def test_the_semantic_steps_withholding_guards_move_the_extraction_recipe(
    monkeypatch: pytest.MonkeyPatch, baseline: tuple[str, ...], clean_caches: None
) -> None:
    """``NameIndex.withheld`` (what a merge without name evidence needs) is seen by the semantic sample."""
    monkeypatch.setattr(ni.NameIndex, "withheld", lambda self, i, j: None)
    after = _recipes()
    assert after[1] != baseline[1]
    assert after[2] != baseline[2]


_V4_PACKS = (
    "internal/benchmarks/reference/war_and_peace_book1-v4/manifest.yaml",
    "internal/benchmarks/reference/war_and_peace_book1-v4-generic/manifest.yaml",
)
"""The two packs built before the core types (spec 2026-10-07): their recipes are the old graphs'."""


def test_every_guard_sample_is_a_core_type() -> None:
    """The guards' records are typed as entities leave the filters: a core type and its subtype."""
    samples = guard_samples()
    assert {s.type for s in samples} <= set(CORE_TYPES)
    assert all(s.subtype is None or s.subtype == subtype_key(s.subtype) for s in samples)
    person_guard = next(g for g in NAME_GUARDS if g.target == "NameConventions.is_person")
    assert {(s.type, s.subtype) for s in person_guard.samples} == {("Work", "literary_work")}


def test_the_guards_and_both_recipes_move(baseline: tuple[str, ...]) -> None:
    """The resolution recipe and both extraction recipes differ from the v4 packs' (pinned in their manifests).

    A graph built before the core types (types compared as strings,
    ``Character`` a person type) is another graph: the graph cache never
    serves one as a graph of this tree.
    """
    literary, generic = (
        yaml.safe_load(repo_file_or_skip(path).read_text(encoding="utf-8")) for path in _V4_PACKS
    )
    assert (literary["extraction_domain"], generic["extraction_domain"]) == ("literary", "generic")
    _, now_literary, now_generic, now_resolution = baseline
    assert now_literary != literary["extraction_recipe"]
    assert now_generic != generic["extraction_recipe"]
    assert now_resolution not in {literary["resolution"], generic["resolution"]}


def test_the_subtype_merge_moves_both_recipes(
    monkeypatch: pytest.MonkeyPatch, baseline: tuple[str, ...], clean_caches: None
) -> None:
    """A merge keeps both subtypes (``merge_subtypes``): the dedup and the resolver samples see it."""
    from chaoscypher_core.services.sources.engine.deduplication import service

    monkeypatch.setattr(service, "merge_subtypes", lambda merged, kept, duplicate: None)
    after = _recipes()
    assert [now != before for now, before in zip(after, baseline, strict=True)] == [True] * 4


def test_the_recipe_samples_write_the_rescues_subtype_property() -> None:
    """The extraction recipe imports lazily, so it spells the subtype key itself: the two agree."""
    from chaoscypher_core.benchmark import extraction_recipe as recipe
    from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
        SUBTYPE_PROPERTY,
    )

    assert recipe._SUBTYPE == SUBTYPE_PROPERTY
    assert {e["type"] for e in recipe._DEDUP_ENTITIES} <= {*CORE_TYPES, "UNKNOWN"}
