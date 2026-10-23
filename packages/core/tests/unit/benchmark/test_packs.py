# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The packs a benchmark corpus is extracted with, and the label ``extraction_domain`` records."""

from __future__ import annotations

import pytest

from chaoscypher_core.benchmark.packs import (
    first_domain_name,
    manifest_packs,
    packs_label,
    packs_of_label,
)


def test_the_label_is_the_sorted_packs_joined_and_generic_for_the_floor() -> None:
    assert packs_label(["literary", "historical"]) == "historical+literary"
    assert packs_label(["literary", "literary"]) == "literary"
    assert packs_label([]) == "generic"


def test_one_pack_and_the_floor_keep_the_label_recorded_before_packs() -> None:
    """Every pack and slot recorded with one domain name keeps its identity."""
    for name in ("literary", "technical", "generic"):
        assert packs_label(packs_of_label(name)) == name


def test_a_label_reads_back_as_its_packs() -> None:
    assert packs_of_label("historical+literary") == ("historical", "literary")
    assert packs_of_label("literary+historical") == ("historical", "literary")
    assert packs_of_label("generic") == ()
    assert packs_of_label(None) == ()
    assert packs_of_label("") == ()


def test_a_manifest_lists_packs_or_names_one_domain() -> None:
    assert manifest_packs({"packs": ["literary", "historical"]}, where="m") == (
        "historical",
        "literary",
    )
    assert manifest_packs({"packs": []}, where="m") == ()
    assert manifest_packs({"packs": None}, where="m") == ()
    assert manifest_packs({"domain": "literary"}, where="m") == ("literary",)
    assert manifest_packs({"domain": "generic"}, where="m") == ()
    assert manifest_packs({}, where="m", default=["literary"]) == ("literary",)


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({}, "missing 'packs'"),
        ({"packs": ["literary"], "domain": "literary"}, "not both"),
        ({"packs": "literary"}, "must be a list"),
        ({"packs": ["generic"]}, "the floor"),
        ({"packs": ["a+b"]}, "cannot contain"),
        ({"packs": [""]}, "non-empty"),
        ({"domain": 3}, "non-empty name"),
    ],
)
def test_a_malformed_manifest_is_refused(data: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        manifest_packs(data, where="m")


def test_the_label_is_the_products_own() -> None:
    """One definition: a benchmark slot and a source row record the same label for the same packs."""
    from chaoscypher_core.benchmark import packs as benchmark_packs
    from chaoscypher_core.services.sources.engine.extraction.domains import packs as product_packs

    for name in ("packs_label", "packs_of_label", "normalized_packs", "first_domain_name"):
        assert getattr(benchmark_packs, name) is getattr(product_packs, name)
    assert not hasattr(benchmark_packs, "one_domain_name")  # sources carry a list of packs


def test_a_one_domain_caller_reads_the_first_pack_or_the_floor() -> None:
    assert first_domain_name(()) == "generic"
    assert first_domain_name(("historical", "literary")) == "historical"
