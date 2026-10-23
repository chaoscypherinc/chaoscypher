# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The packs a source extracts with: how a request names them, how a row stores them.

``domains.packs`` is the one definition every surface and the storage layer
read (spec 2026-10-07 § 5): ``requested_packs`` for a request's ``packs`` /
``domain`` fields, ``pack_columns`` for a write (the list columns and the old
single-name columns in step for one release), ``source_packs`` /
``job_packs`` / ``forced_packs_of`` for a read. ``None`` is "not chosen"
(detect); ``()`` is "the floor alone, chosen".
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    InvalidPacksError,
    chosen_packs_of,
    detected_packs_of,
    forced_packs_of,
    job_pack_labels,
    job_packs,
    labels_of,
    pack_columns,
    packs_choice,
    packs_of_name,
    proposal_packs,
    requested_packs,
    source_packs,
    stored_name_packs,
)


# --- requested_packs: a surface's packs / domain fields ---------------------


@pytest.mark.parametrize(
    ("packs", "domain", "expected"),
    [
        (None, None, None),  # neither: detect
        (None, "", None),
        (None, "__auto__", None),  # the UI's sentinel
        (None, "auto", None),  # the CLI's choice
        (None, "generic", ()),  # the floor alone
        (None, "literary", ("literary",)),  # one element
        (["literary", "historical"], None, ("literary", "historical")),  # the order given
        (["literary", "literary"], None, ("literary",)),  # once
        ([], None, ()),  # the floor alone, chosen
        (["generic"], None, ()),  # the floor adds no pack
        (["generic", "legal"], None, ("legal",)),
        (["__auto__"], None, None),
        (["legal"], "__auto__", ("legal",)),  # the sentinel beside packs is no second choice
    ],
)
def test_a_request_names_packs_or_one_domain(
    packs: list[str] | None, domain: str | None, expected: tuple[str, ...] | None
) -> None:
    assert requested_packs(packs, domain) == expected


@pytest.mark.parametrize(
    ("packs", "domain", "match"),
    [
        (["legal"], "medical", "not both"),
        (["auto", "legal"], None, "not both"),
        (["a+b"], None, "cannot contain"),
        ([""], None, "non-empty"),
        (None, "a+b", "cannot contain"),
    ],
)
def test_a_malformed_request_is_refused(
    packs: list[str] | None, domain: str | None, match: str
) -> None:
    with pytest.raises(InvalidPacksError, match=match) as raised:
        requested_packs(packs, domain)
    # A structured 400 on the request's packs field for the HTTP error mapper,
    # and still a ValueError for the CLI and MCP handlers that catch one.
    assert isinstance(raised.value, ValidationError)
    assert isinstance(raised.value, ValueError)
    assert raised.value.details == {"field": "packs"}


# --- pack_columns: one write keeps the list and the old name in step --------


def test_a_forced_list_writes_its_first_as_the_old_name() -> None:
    """The list keeps its order; the old name is its first element."""
    assert pack_columns({"forced_packs": ["literary", "historical"]}) == {
        "forced_packs": ["literary", "historical"],
        "forced_domain": "literary",
    }
    assert pack_columns({"forced_packs": []}) == {"forced_packs": [], "forced_domain": "generic"}
    assert pack_columns({"forced_packs": None}) == {"forced_packs": None, "forced_domain": None}


def test_the_extraction_name_is_the_packs_label() -> None:
    """``extraction_domain`` records the label (sorted: an identity) the benchmark reads; the list keeps its order."""
    assert pack_columns({"extraction_packs": ["literary", "historical"]}) == {
        "extraction_packs": ["literary", "historical"],
        "extraction_domain": "historical+literary",
    }
    assert pack_columns({"extraction_packs": []})["extraction_domain"] == "generic"
    assert pack_columns({"extraction_packs": ["literary"]})["extraction_domain"] == "literary"


def test_a_job_writes_its_first_detected_pack_as_the_old_name() -> None:
    """The score order: the old name is the highest-scoring pack, not the alphabetical first."""
    assert pack_columns({"detected_packs": ["literary", "historical"]}) == {
        "detected_packs": ["literary", "historical"],
        "detected_domain": "literary",
    }


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"forced_domain": "legal"}, {"forced_domain": "legal", "forced_packs": ["legal"]}),
        ({"forced_domain": "generic"}, {"forced_domain": "generic", "forced_packs": []}),
        ({"forced_domain": None}, {"forced_domain": None, "forced_packs": None}),
        ({"forced_domain": "__auto__"}, {"forced_domain": None, "forced_packs": None}),
        # a one-name parameter carrying the packs' label is split
        (
            {"forced_domain": "literary+historical"},  # an ordered choice, read in its order
            {"forced_domain": "literary", "forced_packs": ["literary", "historical"]},
        ),
        (
            {"extraction_domain": "historical+literary"},
            {
                "extraction_domain": "historical+literary",
                "extraction_packs": ["historical", "literary"],
            },
        ),
        ({"detected_domain": "news"}, {"detected_domain": "news", "detected_packs": ["news"]}),
    ],
)
def test_an_old_name_alone_derives_its_list(
    values: dict[str, Any], expected: dict[str, Any]
) -> None:
    """A write from an older caller names only the old column; the list follows it."""
    assert pack_columns(values) == expected


def test_the_list_wins_and_other_keys_pass_through() -> None:
    out = pack_columns({"forced_packs": ["legal"], "forced_domain": "medical", "status": "indexed"})
    assert out == {"forced_packs": ["legal"], "forced_domain": "legal", "status": "indexed"}


# --- the reads: source rows, job rows, payloads -----------------------------


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (None, None),
        ({}, None),
        ({"forced_packs": None, "forced_domain": None}, None),
        ({"forced_packs": ["b", "a"]}, ("b", "a")),  # the stored order
        ({"forced_packs": []}, ()),  # the floor alone, chosen
        ({"forced_domain": "legal"}, ("legal",)),  # a row written before the list
        ({"forced_domain": "generic"}, ()),
        ({"forced_domain": "__auto__"}, None),
    ],
)
def test_the_forced_packs_of_a_row(row: dict[str, Any] | None, expected: Any) -> None:
    assert forced_packs_of(row) == expected


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (None, ()),
        ({}, ()),
        ({"forced_packs": ["legal"], "extraction_packs": ["news"]}, ("legal",)),
        ({"forced_packs": [], "extraction_packs": ["news"]}, ()),  # forced floor wins
        ({"forced_packs": None, "extraction_packs": ["news", "legal"]}, ("news", "legal")),
        ({"extraction_packs": []}, ()),
        # rows written before the lists: the old columns, a label read as its packs
        ({"forced_domain": "medical", "extraction_domain": "medical"}, ("medical",)),
        ({"extraction_domain": "historical+literary"}, ("historical", "literary")),
        ({"extraction_domain": "generic"}, ()),
    ],
)
def test_the_packs_a_source_extracts_with(row: dict[str, Any] | None, expected: Any) -> None:
    assert source_packs(row) == expected


def test_the_packs_a_job_extracts_with() -> None:
    assert job_packs({"forced_packs": None, "detected_packs": ["b", "a"]}) == ("b", "a")
    assert job_packs({"forced_packs": ["legal"], "detected_packs": ["news"]}) == ("legal",)
    assert job_packs({"forced_domain": None, "detected_domain": "news"}) == ("news",)  # old row
    assert job_packs({"detected_domain": "generic"}) == ()
    assert job_packs(None) == ()


def test_a_jobs_one_name_strings_are_its_packs_labels() -> None:
    """A single-pack job reads exactly its old columns; two packs read their label."""
    assert job_pack_labels({"forced_domain": None, "detected_domain": "literary"}) == (
        None,
        "literary",
    )
    assert job_pack_labels({"forced_packs": [], "detected_packs": []}) == ("generic", "generic")
    assert job_pack_labels({"detected_packs": ["literary", "historical"]}) == (
        None,
        "historical+literary",  # the label sorts: an identity
    )


def test_the_packs_a_user_chose() -> None:
    """Forced, else the extraction packs of a row extracted under a user's choice."""
    assert chosen_packs_of({"forced_packs": ["legal"]}) == ("legal",)
    assert chosen_packs_of({"extraction_packs": ["news"], "extraction_domain_auto": True}) is None
    assert chosen_packs_of({"extraction_packs": ["news"], "extraction_domain_auto": False}) == (
        "news",
    )
    assert chosen_packs_of({"extraction_domain": "news", "extraction_domain_auto": False}) == (
        "news",
    )


def test_a_stored_name_is_read_leniently() -> None:
    assert stored_name_packs("literary+historical") == ("literary", "historical")
    assert stored_name_packs("generic") == ()
    assert stored_name_packs(None) == ()
    assert stored_name_packs(" legal + ") == ("legal",)
    assert labels_of(None) is None
    assert labels_of(()) == "generic"
    assert labels_of(("literary", "historical")) == "historical+literary"


def test_one_name_reads_and_the_ordered_choice() -> None:
    """``packs_of_name`` is the one reading of a one-name value; ``packs_choice`` writes one in order."""
    assert packs_of_name(None) is None
    assert packs_of_name("") is None
    assert packs_of_name("__auto__") is None
    assert packs_of_name("auto") is None
    assert packs_of_name("generic") == ()
    assert packs_of_name("legal") == ("legal",)
    assert packs_of_name("literary+investigation") == ("literary", "investigation")
    assert packs_choice(("literary", "investigation")) == "literary+investigation"
    assert packs_of_name(packs_choice(("literary", "investigation"))) == (
        "literary",
        "investigation",
    )
    assert packs_choice(()) == "generic"
    assert packs_choice(None) is None


def test_a_proposal_names_its_packs_in_score_order() -> None:
    assert proposal_packs({"packs": ["literary", "investigation"]}) == ["literary", "investigation"]
    assert proposal_packs({"detected_domain": "legal"}) == ["legal"]  # stored before packs
    assert proposal_packs({}) is None
    assert detected_packs_of({"packs": ("literary", "investigation")}) == (
        "literary",
        "investigation",
    )
    assert detected_packs_of({}) == ()
