# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The resolver's recipe hash is computed from its behaviour and is stable within one build."""

from __future__ import annotations

import re
from unittest.mock import patch

from chaoscypher_core.services.graph.resolution import resolution_recipe
from chaoscypher_core.services.graph.resolution.plan import Merge, ResolutionPlan


def test_recipe_is_a_stable_twelve_hex_hash() -> None:
    assert re.fullmatch(r"[0-9a-f]{12}", resolution_recipe())
    assert resolution_recipe() == resolution_recipe()


def test_recipe_changes_when_the_planner_would_do_something_else() -> None:
    before = resolution_recipe()
    with patch(
        "chaoscypher_core.services.graph.resolution.recipe.plan_resolution",
        return_value=ResolutionPlan(merges=[Merge("a", "b", "another rule")]),
    ):
        assert resolution_recipe() != before


def test_recipe_moves_with_the_duplicate_edge_fold() -> None:
    """The merge's edge policy (``merge_edge_properties``) is sampled: dropping text moves it."""
    before = resolution_recipe()
    with patch(
        "chaoscypher_core.services.sources.engine.deduplication.service.merge_edge_properties",
        side_effect=lambda kept, duplicate, **_: dict(kept),  # the old drop: the copy's text lost
    ):
        assert resolution_recipe() != before


def test_recipe_moves_with_every_naming_table() -> None:
    """A domain's tables are hashed: a changed gendered title or name form is another resolver."""
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        NameConventions,
    )
    from tests.fakes.domain_tables import domain_conventions

    literary = domain_conventions("literary")
    base = resolution_recipe(literary)
    assert base != resolution_recipe()
    assert resolution_recipe(titles=literary.titles) not in (base, resolution_recipe())
    fewer = NameConventions(
        titles=literary.titles,
        feminine_titles=literary.feminine_titles - {"countess"},
        masculine_titles=literary.masculine_titles,
        generation_words=literary.generation_words,
        name_forms=literary.name_forms,
    )
    no_forms = NameConventions(
        titles=literary.titles,
        feminine_titles=literary.feminine_titles,
        masculine_titles=literary.masculine_titles,
        generation_words=literary.generation_words,
        name_forms=(),
    )
    assert len({base, resolution_recipe(fewer), resolution_recipe(no_forms)}) == 3
