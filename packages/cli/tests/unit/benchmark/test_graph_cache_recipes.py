# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Every guard of the name rule moves both graph-cache recipes, called as the cache calls them (audit wave 3).

The graph cache keys a slot on ``extraction_recipe_for(settings, domain=...)`` (the
corpus's extraction domain since wave 4; the benchmark corpus's is :data:`DOMAIN`) and
``resolution_for(settings)``, which names no domain. Until wave 3
both recipes ran the name rule's samples under the universal conventions (the
extraction recipe built its processor from title words only), so a guard that
only fires on the literary domain's tables (gendered titles, generations,
name forms, relation words) could change without moving either key. Each
mutation below breaks one guard; both recipes must move. The commit steps
and the evidence validator's relation words are extraction-only, so only
the extraction recipe moves for those.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from chaoscypher_cli.benchmark.graph_cache import extraction_recipe_for, resolution_for
from chaoscypher_core.services.sources.engine.commit import payload as commit_payload
from chaoscypher_core.services.sources.engine.commit import service as commit_service
from chaoscypher_core.services.sources.engine.deduplication import name_identity
from chaoscypher_core.services.sources.engine.deduplication import service as dedup_service
from chaoscypher_core.services.sources.engine.extraction.content_categories import (
    CONTENT_CATEGORIES,
)
from chaoscypher_core.services.sources.engine.extraction.utils import (
    entity_cleaner,
    relation_vocabulary,
    type_rescue,
)
from chaoscypher_core.settings import EngineSettings


DOMAIN = "literary"
"""The extraction domain the benchmark corpus forces (its dataset manifest's ``domain``)."""

Mutation = tuple[Any, str, Callable[..., Any]]
"""``(owner, attribute, replacement)``: the guard ``monkeypatch.setattr`` swaps out."""

NAME_RULE_GUARDS: dict[str, Mutation] = {
    "given names": (name_identity, "_given_names", lambda *_a, **_k: frozenset()),
    "generation": (name_identity, "_generation_fits", lambda *_a, **_k: True),
    "groups apart": (name_identity.NameIndex, "groups_apart", lambda *_a, **_k: False),
    "no fuller holder": (name_identity.NameIndex, "no_fuller_holder", lambda *_a, **_k: True),
    "forms clash": (name_identity, "_forms_clash", lambda *_a, **_k: False),
    "sole holder": (name_identity.NameIndex, "sole_holder", lambda *_a, **_k: True),
    "descriptor": (name_identity, "descriptor_possessor", lambda *_a, **_k: None),
    "title fit": (name_identity, "_title_fit", lambda *_a, **_k: "fits"),
    "family free": (name_identity.NameIndex, "family_free", lambda *_a, **_k: True),
}
"""The guards the deduplication and the resolver share."""

EXTRACTION_ONLY: dict[str, Mutation] = {
    "states relation": (
        relation_vocabulary.RelationVocabulary,
        "states_relation",
        lambda *_a, **_k: False,
    ),
    "entity citation chunks": (commit_service, "entity_citation_chunks", lambda *_a, **_k: []),
    "flag orphan entities": (
        commit_service,
        "flag_orphan_entities",
        lambda entities, relationships, **_k: commit_service.OrphanCommit(
            list(entities), list(relationships), 0, 0
        ),
    ),
    # keep, flag, never delete (2026-10-07): what an orphan must carry to be kept
    "orphan carries evidence": (commit_service, "carries_evidence", lambda *_a, **_k: True),
    "build commit payload": (commit_payload, "build_commit_payload", lambda **_k: {}),
    # keep, flag, never delete (2026-10-07): each extraction drop site's keep rule
    "low plausibility kept flagged": (
        entity_cleaner,
        "flag_low_plausibility",
        lambda entity, score: dict(entity),
    ),
    "unplaced type kept flagged": (
        type_rescue,
        "flag_unplaced_type",
        lambda entity, **_kw: dict(entity),
    ),
    "capped edge kept flagged": (entity_cleaner, "flag_capped", lambda rel, *_a: dict(rel)),
    "a reference of every type": (
        name_identity,
        "labelled_by_reference",
        lambda label, conventions=None, entity_type=None: (
            name_identity.reference_key(label, conventions, entity_type) is not None
        ),
    ),
    "attested aliases kept": (name_identity, "record_attested_names", lambda *_a: frozenset()),
    "an unplaced mention joins its name": (dedup_service, "_unplaced", lambda *_a: False),
    "code runs, not indented prose": (
        CONTENT_CATEGORIES["code_blocks"],
        "line_classifier",
        lambda lines: [line.startswith("    ") for line in lines],
    ),
}
"""Steps only the extraction runs: the evidence validator's relation words and the commit."""


@pytest.fixture(scope="module")
def baseline() -> tuple[str, str | None]:
    """Both recipes as the graph cache computes them today."""
    settings = EngineSettings()
    return extraction_recipe_for(settings, domain=DOMAIN), resolution_for(settings)


@pytest.mark.parametrize("guard", list(NAME_RULE_GUARDS))
def test_a_name_rule_guard_moves_both_recipes(
    guard: str, baseline: tuple[str, str | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutating the guard changes both keys of a graph-cache slot."""
    owner, attribute, replacement = NAME_RULE_GUARDS[guard]
    monkeypatch.setattr(owner, attribute, replacement)
    settings = EngineSettings()
    extraction, resolution = baseline
    assert resolution is not None
    assert extraction_recipe_for(settings, domain=DOMAIN) != extraction, guard
    assert resolution_for(settings) != resolution, guard


@pytest.mark.parametrize("step", list(EXTRACTION_ONLY))
def test_an_extraction_step_moves_the_extraction_recipe(
    step: str, baseline: tuple[str, str | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The commit's steps and the relation words are in the extraction recipe, not the resolver's."""
    owner, attribute, replacement = EXTRACTION_ONLY[step]
    monkeypatch.setattr(owner, attribute, replacement)
    settings = EngineSettings()
    extraction, resolution = baseline
    assert extraction_recipe_for(settings, domain=DOMAIN) != extraction, step
    assert resolution_for(settings) == resolution, step


def test_the_recipes_are_stable(baseline: tuple[str, str | None]) -> None:
    """Unmutated, both recipes repeat (a moved key above is the mutation, not noise)."""
    settings = EngineSettings()
    assert (extraction_recipe_for(settings, domain=DOMAIN), resolution_for(settings)) == baseline
