# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The extraction recipe: what shapes a cached graph besides the extractor (audit H7)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from chaoscypher_core.benchmark.extraction_recipe import (
    extraction_code_digest,
    extraction_prompts_digest,
    extraction_recipe,
    extraction_recipe_payload,
)
from chaoscypher_core.settings import EngineSettings


def test_the_app_and_engine_views_of_one_configuration_give_one_recipe() -> None:
    """The export keys with the app settings, the sweep with either: one slot for both."""
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.app_config.engine_factory import build_engine_settings

    app = Settings()
    assert extraction_recipe(app) == extraction_recipe(build_engine_settings(app))


def test_a_dedup_threshold_and_the_pinned_thinking_move_it_and_the_domain_is_optional() -> None:
    settings = EngineSettings()
    base = extraction_recipe(settings, thinking=False)
    settings.extraction.semantic_dedup_threshold -= 0.05
    assert extraction_recipe(settings, thinking=False) != base
    assert extraction_recipe(EngineSettings(), thinking=True) != base
    assert extraction_recipe(EngineSettings(), thinking=False, domain="literary") != base
    assert "resolve_identities" not in extraction_recipe_payload(EngineSettings())["graph"]


def test_the_prompts_digest_moves_with_the_system_prompt() -> None:
    settings = EngineSettings()
    before = extraction_prompts_digest(settings)
    settings.extraction.system_prompt = "Extract carefully."
    assert extraction_prompts_digest(settings) != before


@pytest.mark.parametrize("field", ["llm_backoff_max_seconds", "llm_healthy_pause_seconds"])
def test_retry_timing_is_not_part_of_the_graph(field: str) -> None:
    settings = EngineSettings()
    before = extraction_recipe(settings)
    setattr(settings.extraction, field, getattr(settings.extraction, field) + 1)
    assert extraction_recipe(settings) == before


_KNOWN = "chaoscypher_core.services.sources.engine.extraction.utils.known_entities"


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("chaoscypher_core.services.sources.engine.deduplication.name_identity", "same_entity"),
        (
            "chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator",
            "validate_entity_evidence",
        ),
        (
            "chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator",
            "relationship_evidence",
        ),
        (
            "chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator",
            "sort_relationships_by_evidence",
        ),
        (
            "chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator",
            "flag_unevidenced",
        ),
        # keep, flag, never delete (2026-10-08): the entity evidence filter, the exclusion
        # filter and the strict type check keep a record flagged; each decision and writer
        (
            "chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator",
            "entity_evidence",
        ),
        (
            "chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator",
            "flag_unevidenced_entity",
        ),
        (
            "chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner",
            "carries_evidence",
        ),
        (
            "chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner",
            "flag_excluded_name",
        ),
        (
            "chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner",
            "flag_unmatched_label",
        ),
        (
            "chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner",
            "flag_type_mismatch",
        ),
        ("chaoscypher_core.services.sources.engine.commit.relation", "fold_fact_copies"),
        (
            "chaoscypher_core.services.sources.engine.extraction.extraction_plan",
            "build_extraction_plan",
        ),
        # known entities (spec 2026-10-11): the list's predicates are registered guards
        # (``name_guards.NAME_GUARDS``, scanned since final review 2 of 2026-10-12, M2:
        # test_recipe_name_guards turns each off); here the one reading of an alias as a name
        # (the filter's names and the list's) and the queue path's fold of stored rows
        (
            "chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator",
            "alias_name",
        ),
        (_KNOWN, "known_entities_from_rows"),
        (_KNOWN, "is_doubted_record"),
        (
            "chaoscypher_core.services.sources.engine.deduplication.name_identity",
            "UNIVERSAL_TITLE_WORDS",
        ),
    ],
)
def test_a_change_to_the_code_that_shapes_the_graph_moves_the_recipe(
    monkeypatch: pytest.MonkeyPatch, module: str, name: str
) -> None:
    """Computed from behaviour: EXT_PIPELINE_VERSION never needs a bump for a code change (H7)."""
    import importlib

    before = extraction_recipe(EngineSettings())
    target: Any = importlib.import_module(module)
    attr = name
    if "." in name:  # ``Class.method``: the method on the class
        owner, attr = name.split(".")
        target = getattr(target, owner)
    original = getattr(target, attr)
    with monkeypatch.context() as m:
        if name == "alias_name":  # every alias reads as a name again ("My wife")
            m.setattr(target, attr, lambda alias, conventions: alias)
        elif name == "known_entities_from_rows":  # the queue path folds no row in
            m.setattr(target, attr, lambda rows, *, known: known)
        elif name == "is_doubted_record":  # the list takes in the records the pipeline doubts
            m.setattr(target, attr, lambda properties: False)
        elif name == "UNIVERSAL_TITLE_WORDS":  # ``Mamma`` no title: a vocative names a person again
            # The feminine titles join the title set in ``NameConventions.build``, so the floor
            # loses the word only when both tables do (the title words alone is no change).
            m.setattr(target, attr, original - {"mamma"})
            m.setattr(
                target, "UNIVERSAL_FEMININE_TITLES", target.UNIVERSAL_FEMININE_TITLES - {"mamma"}
            )
        elif name == "same_entity":  # the rule now joins no pair
            m.setattr(target, attr, lambda *a, **kw: None)
        elif name == "build_extraction_plan":  # another plan (the prompt digest reads it too)
            import dataclasses

            m.setattr(
                target,
                attr,
                lambda *a, **kw: dataclasses.replace(
                    original(*a, **kw), edge_type_constraints={"changed": {}}
                ),
            )
        elif name == "relationship_evidence":  # every relationship now evidenced
            m.setattr(target, attr, lambda *a, **kw: target.RelationshipEvidence())
        elif name == "sort_relationships_by_evidence":  # nothing kept flagged, folded or dropped
            m.setattr(
                target,
                attr,
                lambda relationships, *a, **kw: target.EvidenceSorting(
                    list(relationships), [], [], []
                ),
            )
        elif name == "flag_unevidenced":  # an unevidenced relationship kept unflagged
            m.setattr(target, attr, lambda rel, verdict: dict(rel))
        elif name == "entity_evidence":  # every entity now evidenced
            m.setattr(target, attr, lambda *a, **kw: target.EntityEvidence())
        elif name == "carries_evidence":  # nothing carries evidence: every flagged record dropped
            m.setattr(target, attr, lambda entity: False)
        elif name in {
            "flag_unevidenced_entity",
            "flag_excluded_name",
            "flag_unmatched_label",
            "flag_type_mismatch",
        }:  # the record kept unflagged, as written
            m.setattr(target, attr, lambda record, *a: dict(record))
        elif name == "fold_fact_copies":  # first write wins again
            m.setattr(target, attr, lambda edges: edges)
        else:
            m.setattr(target, attr, lambda *a, **kw: ("changed", original(*a, **kw)))
        assert extraction_recipe(EngineSettings()) != before, name
    assert extraction_recipe(EngineSettings()) == before, name


def test_the_recipe_moves_with_the_commit_orientation_and_a_domain_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chaoscypher_core.services.sources.engine.commit.relation import RelationshipCommitHandler
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry

    before = extraction_recipe(EngineSettings())
    with monkeypatch.context() as m:
        m.setattr(
            RelationshipCommitHandler,
            "orient_relationships_for_commit",
            staticmethod(lambda relationships, **kw: list(reversed(relationships))),
        )
        assert extraction_recipe(EngineSettings()) != before
    registry = get_domain_registry(EngineSettings())
    real = registry.get_domain_fingerprint

    def edited(name: str) -> object:
        fp = real(name)
        if name != "literary" or fp is None:
            return fp
        return type(fp)(version=fp.version, content_hash="edited")

    monkeypatch.setattr(registry, "get_domain_fingerprint", edited)
    assert extraction_recipe(EngineSettings()) != before


# --- Audit W2-12: the recipe samples the parser, the dedup, the cleaner, the chunker,
# --- the group packer and the splitter; each mutation below must move it.


def _mark_entities(entities: Any) -> Any:
    """``entities`` with a field added to each dict: a dedup that now keeps something else."""
    return [{**e, "mutated": True} if isinstance(e, dict) else e for e in entities]


def _sync_mutations() -> list[tuple[str, str, str, Callable[[Callable[..., Any]], Any]]]:
    """``(module, owner, attribute, make_replacement)``: each replacement changes the output."""
    lp = "chaoscypher_core.services.sources.engine.extraction.utils.line_parser"
    ec = "chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner"
    ss = "chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter"
    orch = "chaoscypher_core.services.sources.engine.extraction.orchestration"
    dedup = "chaoscypher_core.services.sources.engine.deduplication.service"
    return [
        (lp, "", "parse_entity_line", lambda f: lambda *a, **k: _upper_name(f(*a, **k))),
        (lp, "", "parse_relationship_line", lambda f: lambda *a, **k: _bump(f(*a, **k))),
        (lp, "", "parse_property_line", lambda f: lambda *a, **k: _bump(f(*a, **k))),
        (ec, "", "deduplicate_relationships", lambda f: lambda *a, **k: f(*a, **k)[:-1]),
        (ec, "", "clean_descriptor_aliases", lambda f: lambda *a, **k: f(*a, **k) + 1),
        # 2026-10-07: the per-chunk name filters decide what pass 2 lists (``the count`` was
        # dropped by the plausibility score and no key moved)
        (ec, "", "filter_excluded_entities", lambda f: lambda *a, **k: (f(*a, **k)[0][1:], {})),
        (ec, "", "filter_implausible_entities", lambda f: lambda *a, **k: (f(*a, **k)[0][1:], {})),
        (ec, "", "kept_as_reference", lambda f: lambda *a, **k: False),
        (ss, "", "split_into_sentences", lambda f: lambda *a, **k: f(*a, **k)[1:]),
        (ss, "", "split_into_sentences_with_offsets", lambda f: lambda *a, **k: f(*a, **k)[:-1]),
        (orch, "", "build_extraction_groups", lambda f: lambda *a, **k: f(*a, **k)[:-1]),
        (orch, "", "build_group_text", lambda f: lambda *a, **k: f(*a, **k) + " x"),
        (
            dedup,
            "EntityProcessor",
            "deduplicate_entities_with_mapping",
            lambda f: lambda self, *a, **k: _mark_first(f(self, *a, **k)),
        ),
        (
            dedup,
            "EntityProcessor",
            "resolve_hierarchical_names",
            lambda f: lambda self, *a, **k: _mark_first(f(self, *a, **k)),
        ),
        (
            dedup,
            "EntityProcessor",
            "merge_entities",
            lambda f: lambda self, *a, **k: {**f(self, *a, **k), "mutated": True},
        ),
    ]


def _upper_name(parsed: Any) -> Any:
    """An entity line parsed with its name upper-cased."""
    return {**parsed, "name": str(parsed.get("name", "")).upper()} if parsed else parsed


def _bump(parsed: Any) -> Any:
    """A parsed line carrying one more field."""
    return {**parsed, "mutated": True} if parsed else parsed


def _mark_first(result: Any) -> Any:
    """A ``(entities, ...)`` result whose entities are marked."""
    return (_mark_entities(result[0]), *result[1:])


@pytest.mark.parametrize(
    ("module", "owner", "attr", "make"),
    _sync_mutations(),
    ids=[m[2] for m in _sync_mutations()],
)
def test_each_sampled_component_moves_the_recipe(
    monkeypatch: pytest.MonkeyPatch,
    module: str,
    owner: str,
    attr: str,
    make: Callable[[Callable[..., Any]], Any],
) -> None:
    """A change to the parser, dedup, cleaner, splitter or group packer moves the key (W2-12)."""
    import importlib

    before = extraction_recipe(EngineSettings())
    target: Any = importlib.import_module(module)
    if owner:
        target = getattr(target, owner)
    monkeypatch.setattr(target, attr, make(getattr(target, attr)))
    assert extraction_recipe(EngineSettings()) != before


def test_the_semantic_dedup_the_pipeline_and_the_chunker_move_the_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The async components: semantic dedup, ``run_deduplication`` and the chunker."""
    from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor
    from chaoscypher_core.services.sources.engine.extraction import extractor
    from chaoscypher_core.utils.chunk import ChunkingService

    before = extraction_recipe(EngineSettings())

    semantic = EntityProcessor.deduplicate_entities_semantic

    async def marked_semantic(self: Any, *a: Any, **k: Any) -> Any:
        """Semantic dedup whose survivors carry one more field."""
        return _mark_first(await semantic(self, *a, **k))

    with monkeypatch.context() as m:
        m.setattr(EntityProcessor, "deduplicate_entities_semantic", marked_semantic)
        assert extraction_recipe(EngineSettings()) != before

    pipeline = extractor.run_deduplication

    async def fewer_edges(**k: Any) -> Any:
        """The pipeline dropping its last relationship."""
        entities, relationships, embeddings, log = await pipeline(**k)
        return entities, relationships[:-1], embeddings, log

    with monkeypatch.context() as m:
        m.setattr(extractor, "run_deduplication", fewer_edges)
        assert extraction_recipe(EngineSettings()) != before

    chunker = ChunkingService.create_chunks

    async def edited_chunks(self: Any, *a: Any, **k: Any) -> Any:
        """The chunker with its first chunk's text changed."""
        result = await chunker(self, *a, **k)
        result.small_chunks[0]["content"] += " x"
        return result

    with monkeypatch.context() as m:
        m.setattr(ChunkingService, "create_chunks", edited_chunks)
        assert extraction_recipe(EngineSettings()) != before


def test_the_vector_size_the_chunking_and_the_domain_move_the_recipe() -> None:
    """Semantic dedup embeds at ``search.vector_dimensions``; the domain picks the plan and titles."""
    base = EngineSettings()
    before = extraction_recipe(base)
    smaller = EngineSettings()
    smaller.search.vector_dimensions = 768
    assert extraction_recipe(smaller) != before
    assert extraction_recipe_payload(smaller)["search"] == {"vector_dimensions": 768}
    chunked = EngineSettings()
    chunked.chunking.target_group_tokens = 400
    assert extraction_recipe(chunked) != before
    literary = extraction_code_digest(base, domain="literary")
    assert literary != extraction_code_digest(base, domain="generic")
    assert literary != extraction_code_digest(base)


@pytest.mark.asyncio
async def test_the_recipe_is_deterministic_and_safe_inside_a_running_loop() -> None:
    """Twice the same; and an async caller (a running loop) gets the same key."""
    first = extraction_recipe(EngineSettings(), domain="literary")
    assert extraction_recipe(EngineSettings(), domain="literary") == first
    # called from inside this running event loop
    assert extraction_recipe(EngineSettings(), domain="literary") == first


def test_the_recipe_plans_as_the_queue_plans_a_stored_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the stored row's preset, which wins over the domain's (the recipe once hashed ``lenient``)."""
    from chaoscypher_core.services.sources.engine.extraction import extraction_plan

    seen: list[Any] = []
    real = extraction_plan.build_extraction_plan

    def spy(*args: Any, **kwargs: Any) -> Any:
        """The real plan, its source row recorded."""
        seen.append(kwargs.get("source_row"))
        return real(*args, **kwargs)

    monkeypatch.setattr(extraction_plan, "build_extraction_plan", spy)
    extraction_code_digest(EngineSettings(), domain="literary")
    # the prompt digest renders every domain's prompts from a plan (no row: prompts read none)
    rows = [row for row in seen if row is not None]
    assert rows == [extraction_plan.stored_source_row()]
    assert rows[0]["filtering_mode"] == extraction_plan.SOURCE_ROW_FILTERING_MODE == "balanced"


@pytest.mark.parametrize("domain", ["literary", "generic"])
@pytest.mark.parametrize(
    ("name", "edit"),
    [
        ("_EXAMPLE_RELATIONSHIPS", lambda rels: rels[:-1]),
        (
            "_EXAMPLE_ENTITIES",
            lambda ents: (
                type(ents[0])(**{**ents[0].__dict__, "description": "A different summary."}),
                *ents[1:],
            ),
        ),
    ],
)
def test_editing_a_format_example_moves_the_recipe(
    monkeypatch: pytest.MonkeyPatch, domain: str, name: str, edit: Callable[[Any], Any]
) -> None:
    """The digest renders every domain's prompts from its plan, examples included (audit wave 5)."""
    from chaoscypher_core.services.sources.engine.extraction.utils import prompts

    before = (
        extraction_prompts_digest(EngineSettings()),
        extraction_recipe(EngineSettings(), domain=domain),
    )
    monkeypatch.setattr(prompts, name, edit(getattr(prompts, name)))
    after = (
        extraction_prompts_digest(EngineSettings()),
        extraction_recipe(EngineSettings(), domain=domain),
    )
    assert after[0] != before[0]
    assert after[1] != before[1]


def test_the_prompts_digest_renders_each_domains_examples() -> None:
    """The rendered prompts carry the real format examples, not only the schematic line."""
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
    from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
        build_extraction_plan,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.prompts import (
        render_relationship_format_example,
    )

    settings = EngineSettings()
    stack = DomainStack.resolve(get_domain_registry(settings), ["literary"])
    plan = build_extraction_plan(stack, settings)
    example = render_relationship_format_example(plan.chunk_kwargs["edge_templates_formatted"])
    assert "R|0|2|participates_in|" in example and "R|0|1|located_in|" in example


# -- audit wave 7: the retry ladder shapes a rescued group's graph -----------------------------


@pytest.mark.parametrize(
    ("name", "value"), [("FALLBACK_TEMPERATURE", 0.5), ("REPEAT_PENALTY", 1.3)]
)
def test_the_ladder_constants_move_the_recipe(
    monkeypatch: pytest.MonkeyPatch, name: str, value: float
) -> None:
    """A rung's decoding is the settings part of the recipe: a rescued group was extracted at it."""
    from chaoscypher_core.services.sources.engine.extraction import chunk_failures

    before = extraction_recipe(EngineSettings())
    monkeypatch.setattr(chunk_failures, name, value)
    assert extraction_recipe(EngineSettings()) != before
    assert extraction_recipe_payload(EngineSettings())["ladder"][name.lower()] == value


def test_the_ladder_rungs_are_the_ones_the_failure_policy_sends() -> None:
    """The recorded rungs are ``decode_fallback``'s: the pinned try, then the next seeds."""
    from chaoscypher_core.benchmark.extraction_recipe import decode_ladder

    ladder = decode_ladder()
    assert ladder["rungs"][0] is None
    assert ladder["rungs"][1] == {"seed": 43, "temperature": 0.0, "step": 1}
    assert ladder["rungs"][2]["repeat_penalty"] == ladder["repeat_penalty"]


def test_the_known_entities_limit_is_in_the_recipe() -> None:
    base = EngineSettings()
    assert base.extraction.known_entities_limit == 80
    moved = base.model_copy(deep=True)
    moved.extraction.known_entities_limit = 0
    assert extraction_recipe(moved) != extraction_recipe(base)


def test_where_the_known_entities_block_sits_in_pass_one_moves_the_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompts digest renders a block: its slot and its wrapper are what group 2 on is sent.

    With only an empty block rendered, the slot rendered as nothing, and moving it (here from
    before ``OUTPUT FORMAT:`` to before ``Entity types:``) left the recipe where it was (final
    review of 2026-10-11, I2).
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )

    before = extraction_recipe(EngineSettings())
    template = AIEntityExtractor.ENTITY_HARVEST_TEMPLATE
    assert template.count("{known_entities}OUTPUT FORMAT:") == 1
    moved = template.replace("{known_entities}OUTPUT FORMAT:", "OUTPUT FORMAT:").replace(
        "Entity types:", "{known_entities}Entity types:", 1
    )
    with monkeypatch.context() as m:
        m.setattr(AIEntityExtractor, "ENTITY_HARVEST_TEMPLATE", moved)
        assert extraction_recipe(EngineSettings()) != before
    assert extraction_recipe(EngineSettings()) == before


def test_listing_a_record_the_alias_guard_repaired_is_in_the_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crossed alias is no doubt (spec D3): reading it as doubt again moves the recipe."""
    from chaoscypher_core.services.sources.engine.extraction.utils import known_entities

    before = extraction_recipe(EngineSettings())
    original = known_entities.is_doubted_record
    with monkeypatch.context() as m:
        m.setattr(
            known_entities,
            "is_doubted_record",
            lambda p: original(p) or bool((p or {}).get("alias_crossed_known")),
        )
        assert extraction_recipe(EngineSettings()) != before
    assert extraction_recipe(EngineSettings()) == before
