# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The finalize, quality and fingerprint lookups read the source's stack, not one pack (Task 7).

Until 2026-10-08 a finalize path resolved ``registry.get_domain(label)``,
which is None for a label of two packs (``historical+literary``): the type
aliases, the normalization rules, the templates, the quality config and the
fingerprint fell back to nothing, and the content exclusions read the first
pack only. Each now reads ``DomainStack``.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.quality import QualityScorer
from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
    DomainResolver,
    quality_config_of,
)
from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import CORE_TYPES
from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
    stack_content_hash,
)
from chaoscypher_core.services.sources.engine.extraction.domains.packs import InvalidPacksError
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
from chaoscypher_core.services.sources.engine.extraction.service import ExtractionService
from chaoscypher_core.services.sources.engine.extraction.utils.post_extraction import (
    apply_domain_type_aliases,
    get_domain_structural_filters,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import TypeVocabulary
from chaoscypher_core.settings import EngineSettings


def _stack(*packs: str) -> DomainStack:
    return DomainStack.resolve(get_domain_registry(), packs)


def test_the_quality_scores_are_the_floors_per_core_type_and_the_packs_per_subtype() -> None:
    scoring = _stack("historical", "literary").get_quality_scoring()
    floor = _stack().get_quality_scoring()
    assert set(scoring["entity_scores"]) == set(CORE_TYPES)
    assert scoring["entity_scores"] == floor["entity_scores"]
    assert {"character", "historical_figure"} <= set(scoring["subtype_scores"])
    assert floor["subtype_scores"] == {}
    # the floor's labels, plus each pack's extra ones
    assert floor["relationship_scores"].items() <= scoring["relationship_scores"].items()
    assert "betrothed_to" in scoring["relationship_scores"]
    assert scoring["default_entity_score"] == floor["default_entity_score"]


def test_the_scorer_reads_a_subtype_score_before_the_core_types() -> None:
    scorer = QualityScorer({"entity_scores": {"Person": 25}, "subtype_scores": {"servant": 10}})
    assert scorer.get_entity_type_score("Person") == 25
    assert scorer.get_entity_type_score("Person", "servant") == 10
    assert scorer.get_entity_type_score("Person", "character") == 25
    servant = scorer.score_entity(
        {"name": "Mavra", "type": "Person", "properties": {"entity_subtype": "servant"}}
    )
    assert servant.type_value_score == 10


@pytest.mark.parametrize("name", ["literary", "historical+literary", "generic"])
def test_quality_config_of_reads_the_label_as_its_stack(name: str) -> None:
    registry = get_domain_registry()
    assert (
        quality_config_of(name)
        == DomainStack.from_domain_name(registry, name).get_quality_scoring()
    )


def test_no_name_keeps_the_scorer_defaults() -> None:
    assert quality_config_of(None) == {}


def test_the_content_exclusions_are_every_members() -> None:
    stack = _stack("historical", "literary")
    union = stack.get_content_exclusions()
    for member in stack.members:
        own = member.get_content_exclusions() or {}
        assert set(own.get("categories") or []) <= set(union.get("categories") or [])
        for pattern in own.get("custom_patterns") or []:
            assert pattern in union["custom_patterns"]
    categories = union.get("categories") or []
    assert len(categories) == len(set(categories))


def test_the_templates_are_the_core_types_and_every_members_labels() -> None:
    stack = _stack("historical", "literary")
    templates = stack.get_templates()
    assert [t["name"] for t in templates["node_templates"]] == list(CORE_TYPES)
    labels = [t["name"] for t in templates["edge_templates"]]
    assert len(labels) == len(set(labels))
    for pack in stack.packs:
        assert {t["name"] for t in pack.extra_edge_templates()} <= set(labels)
    assert stack.edge_lines() == _stack("historical", "literary").edge_lines()


def test_the_type_aliases_and_vocabulary_are_the_stacks() -> None:
    stack = _stack("historical", "literary")
    assert TypeVocabulary.from_domain(stack) == TypeVocabulary.from_stack(stack)
    entities: list[dict[str, Any]] = [
        {"name": "Kutuzov", "type": "Historical Figure"},
        {"name": "Pierre", "type": "Character"},
    ]
    apply_domain_type_aliases(entities, stack)
    assert [e["type"] for e in entities] == ["Person", "Person"]
    assert entities[1]["properties"]["entity_subtype"] == "character"


def test_the_structural_filters_read_every_member() -> None:
    stack = _stack("literary")
    structural, generic = get_domain_structural_filters(stack)
    assert (structural, generic) == stack.structural_filters()


def test_the_service_and_the_resolver_resolve_a_label_to_its_stack() -> None:
    service = ExtractionService(
        graph_repository=None,  # type: ignore[arg-type]
        llm_provider=None,  # type: ignore[arg-type]
        settings=EngineSettings(),
        embedding_service=None,
    )
    stack = service._resolve_domain("historical+literary")
    assert isinstance(stack, DomainStack)
    assert stack.names == ("historical", "literary")
    assert service._get_domain_normalization_rules("historical+literary") == (
        stack.normalization_rules()
    )
    resolver = DomainResolver(EngineSettings())
    assert resolver._resolve_domain("historical+literary").names == stack.names
    assert service._resolve_domain(None) is None


def test_a_forced_unknown_pack_is_refused_not_downgraded() -> None:
    service = ExtractionService(
        graph_repository=None,  # type: ignore[arg-type]
        llm_provider=None,  # type: ignore[arg-type]
        settings=EngineSettings(),
        embedding_service=None,
    )
    with pytest.raises(InvalidPacksError, match="not a registered pack"):
        service._resolve_domain("no_such_pack", forced=True)
    downgraded = service._resolve_domain("no_such_pack")
    assert isinstance(downgraded, DomainStack)
    assert downgraded.packs == ()  # not forced: the floor alone


def test_the_fingerprint_of_a_label_is_one_hash_over_its_packs() -> None:
    registry = get_domain_registry()
    literary = registry.get_domain_fingerprint("literary")
    historical = registry.get_domain_fingerprint("historical")
    both = registry.get_domain_fingerprint("historical+literary")
    assert literary is not None and historical is not None and both is not None
    hashes = {"historical": historical.content_hash, "literary": literary.content_hash}
    assert both.content_hash == stack_content_hash(hashes, ["literary", "historical"])
    assert both.content_hash not in hashes.values()
    assert both.version == f"{historical.version}+{literary.version}"
    # one pack keeps its own hash; an unknown member is "plugin gone", never stale
    assert stack_content_hash(hashes, ["literary"]) == literary.content_hash
    assert stack_content_hash(hashes, ["literary", "gone"]) is None
    assert registry.get_domain_fingerprint("gone+literary") is None


def test_the_domain_info_lists_a_packs_subtypes_with_their_icons() -> None:
    info = {d["name"]: d for d in get_domain_registry().list_domain_info()}
    assert info["generic"]["subtypes"] == []
    character = next(s for s in info["literary"]["subtypes"] if s["key"] == "character")
    assert character["of"] == "Person"
    assert character["name"] == "Character"
    assert "icon" in character
