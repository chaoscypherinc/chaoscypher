# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The four extraction paths run every chunk with the same inputs (audits E5, W2-12).

Before the shared ``extraction_plan`` the CLI (the benchmark's path) ran the
literary domain's ``lenient`` preset while the source row said ``balanced``,
never set ``strict_edge_type_constraints``, and none of the queue planner,
the CLI and ``AIEntityExtractor.extract_from_chunks`` passed the domain's
``named_referent_types``, ``normalization_rules`` or
``property_type_mapping``. Here the real literary domain goes through all
three callers and the per-chunk keyword arguments are compared:

* the queue: ``_build_extraction_config`` (the planner) then
  ``chunk_kwargs_from_config`` (what the chunk handler calls);
* the CLI: ``extract_entities`` with the extractor faked at its boundary;
* the standalone extractor: ``extract_from_chunks``;
* the MCP path: ``ExtractionOrchestrator.get_tasks`` (whose prompts render
  from the plan) then ``finalize``, which used to build its FilteringConfig
  from the row's mode alone, without the domain's extraction limits, and
  dropped the forced domain on its way to the deduplication.

The finalize side is compared too: the FilteringConfig, edge-type
constraints and domain each path hands ``finalize_distributed_extraction``
(and so ``run_deduplication``), with the queue's read back from its stored
``extraction_config`` as the finalizer reads it.

And the running list (spec 2026-10-11): every path that calls
``extract_single_chunk`` hands each group the source's ``KnownEntities``
(``KnownEntities.for_plan``), and on the CLI (the benchmark's path) group n+1's
pass-1 prompt carries what groups 1..n named, a group joining the list once,
after its final successful attempt. The MCP path's client extracts, so it
calls no ``extract_single_chunk``; the queue's chunk handler is pinned with
the queue path's list.
"""

from __future__ import annotations

import contextlib
import json
from functools import cache
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cli.sources.service import CLISourceProcessingService, IncompleteExtractionError
from chaoscypher_core.mcp.extraction import ExtractionOrchestrator
from chaoscypher_core.services.sources.engine.extraction.domains.registry import DomainRegistry
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
    CHUNK_KWARG_NAMES,
    chunk_kwargs_from_config,
    domain_guidance,
    plan_filtering_config_from_config,
)
from chaoscypher_core.services.sources.engine.extraction.utils import ai_entities
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
)
from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
    KNOWN_ENTITIES_HEADER,
    KnownEntities,
)
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.extraction_llm import ScriptedExtractionLLM


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
        ConfigurableDomain,
    )


_BASE = "chaoscypher_core.services.sources.engine.extraction"


@cache
def _registry() -> DomainRegistry:
    """The shipped floor and packs (every path below resolves its stack from it)."""
    return DomainRegistry()


def _literary() -> ConfigurableDomain:
    """The shipped literary pack (each path stacks it on the floor)."""
    return cast("ConfigurableDomain", _registry().get_domain("literary"))


def _stack(domain: ConfigurableDomain) -> DomainStack:
    """The stack a source of ``domain`` extracts with."""
    return DomainStack.from_domain_name(_registry(), domain.name)


def _row(mode: str) -> dict[str, Any]:
    """A stored source row as both paths read it."""
    return {
        "id": "src",
        "status": "indexed",
        "filename": "book1.txt",
        "metadata": {},
        "filtering_mode": mode,
        "content_filtering": False,
        "extraction_depth": "full",
    }


def _queue_config(domain: ConfigurableDomain, row: dict[str, Any]) -> dict[str, Any]:
    """The ``extraction_config`` the queue's planner stores for this source."""
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.operations.importing.import_service import _build_extraction_config

    raw = _build_extraction_config(
        stack=_stack(domain),
        settings=Settings(),
        file_info={},
        source_row=row,
    )
    config: dict[str, Any] = json.loads(raw)
    return config


def _queue_kwargs(domain: ConfigurableDomain, row: dict[str, Any]) -> dict[str, Any]:
    """What the queue's chunk handler passes the extractor for this source."""
    return chunk_kwargs_from_config(_queue_config(domain, row))


def _queue_finalize(domain: ConfigurableDomain, row: dict[str, Any]) -> dict[str, Any]:
    """What the queue's finalizer dedups and filters with (read back from the stored config)."""
    config = _queue_config(domain, row)
    return {
        "filtering_config": plan_filtering_config_from_config(config, EngineSettings()),
        "edge_type_constraints": config.get("edge_type_constraints") or None,
        "domain": "literary",
    }


def _finalize_part(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The ``finalize_distributed_extraction`` inputs the plan and the domain decide."""
    return {
        "filtering_config": kwargs["filtering_config"],
        "edge_type_constraints": kwargs["edge_type_constraints"],
        "domain": kwargs.get("forced_domain") or kwargs.get("detected_domain"),
    }


def _fake_extractor(seen: list[dict[str, Any]]) -> MagicMock:
    """An extractor that records each call's kwargs and extracts nothing."""

    async def _extract(**kwargs: Any) -> tuple[Any, ...]:
        """Record and return an empty, non-empty-output result."""
        seen.append(kwargs)
        return [], [], 10, 5, {}

    extractor = MagicMock()
    extractor.extract_single_chunk = AsyncMock(side_effect=_extract)
    return extractor


def _cli_kwargs(domain: ConfigurableDomain, row: dict[str, Any]) -> dict[str, Any]:
    """What the CLI's extract_entities passes the extractor for this source."""
    return _cli_run(domain, row)[0]


def _cli_extract(
    domain: ConfigurableDomain,
    row: dict[str, Any],
    chunks: list[str],
    extractor_patch: Any,
    *extra_patches: Any,
) -> tuple[MagicMock, MagicMock]:
    """Run the CLI's ``extract_entities`` over ``chunks``; return the adapter and the finalizer.

    ``extractor_patch`` stands the extractor in (the class faked whole, or its
    ``call_llm`` scripted); ``extra_patches`` are entered beside it.
    """
    ctx = MagicMock()
    ctx.has_llm = True
    ctx.database_name = "default"
    ctx.settings = EngineSettings()
    adapter = ctx.storage_adapter
    adapter.get_file = MagicMock(return_value=dict(row))
    adapter.get_chunks_for_extraction = MagicMock(
        return_value=[
            {"id": f"c{i}", "chunk_index": i, "content": content}
            for i, content in enumerate(chunks)
        ]
    )
    adapter.compute_llm_summary = MagicMock(return_value={})
    extraction_service = MagicMock()
    extraction_service.finalize_distributed_extraction = AsyncMock(
        return_value={"entities": [], "relationships": [], "metadata": {}}
    )
    entity_guidance, relationship_guidance = domain_guidance(domain)
    domain_result = {
        "domain": domain,
        "detected_domain": "literary",
        "entity_guidance": entity_guidance,
        "relationship_guidance": relationship_guidance,
    }
    with contextlib.ExitStack() as stack:
        for each in (
            patch(f"{_BASE}.domains.get_domain_registry", return_value=_registry()),
            patch(f"{_BASE}.orchestration.detect_extraction_domain", return_value=domain_result),
            extractor_patch,
            patch(f"{_BASE}.service.ExtractionService", return_value=extraction_service),
            patch("chaoscypher_core.services.llm.spend.get_llm_spend_tracker"),
            patch(f"{_BASE}.orchestration.cache_quality_scores"),
            *extra_patches,
        ):
            stack.enter_context(each)
        service = CLISourceProcessingService(ctx)
        try:
            service.extract_entities("src")
        finally:
            service.close()
    return adapter, extraction_service


def _cli_run(
    domain: ConfigurableDomain, row: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The CLI's per-chunk extractor kwargs and its ``finalize_distributed_extraction`` kwargs."""
    seen: list[dict[str, Any]] = []
    adapter, extraction_service = _cli_extract(
        domain,
        row,
        ["Anna met Pierre. He bowed."],
        patch(f"{_BASE}.utils.ai_entities.AIEntityExtractor", return_value=_fake_extractor(seen)),
    )
    assert len(seen) == 1
    # The row now records the preset the run used.
    updates = [c.kwargs["updates"] for c in adapter.update_source_columns.call_args_list]
    assert {"filtering_mode": seen[0]["filtering_mode"]} in updates
    finalize = extraction_service.finalize_distributed_extraction.call_args.kwargs
    assert finalize["filtering_config"].semantic_dedup_threshold == pytest.approx(
        {"lenient": 0.93, "balanced": 0.90}[seen[0]["filtering_mode"]]
    )
    return seen[0], dict(finalize)


async def _mcp_run(
    domain: ConfigurableDomain, row: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, str]]:
    """The MCP path's plan kwargs, its finalize kwargs and the prompts ``get_tasks`` served.

    The row forces the literary domain (an MCP client picks it, the CLI's
    ``--domain``); ``finalize`` runs in a fresh orchestrator, as when it lands
    in another process than ``get_tasks``, so the plan is rebuilt from the row.
    """
    from chaoscypher_core.mcp.extraction import _render_extraction_instructions

    source = {**row, "forced_domain": "literary", "extraction_domain": None, "chunk_count": 1}
    engine = MagicMock()
    engine.settings = EngineSettings()
    adapter = engine.storage_adapter
    adapter.get_source = MagicMock(return_value=source)
    adapter.transition_source_status = MagicMock(return_value=True)
    for name in ("start_stage", "complete_stage", "tick_stage", "update_stage_extras"):
        setattr(adapter, name, AsyncMock())
    adapter.list_extraction_submissions = MagicMock(
        return_value=[{"chunk_group_index": 0, "entities_text": "", "relationships_text": ""}]
    )
    adapter.get_chunks_for_extraction = MagicMock(return_value=[])
    engine.graph_repository.list_templates = MagicMock(return_value=[])
    engine.extraction_service.finalize_distributed_extraction = AsyncMock(
        return_value={"entities": [], "relationships": [], "metadata": {}}
    )
    engine.commit_service.commit = AsyncMock(
        return_value={"created_nodes": [], "created_edges": [], "created_templates": []}
    )
    group = {"group_index": 0, "combined_content": "Anna met Pierre.", "small_chunk_ids": []}
    with (
        patch("chaoscypher_core.mcp.extraction.get_domain_registry", return_value=_registry()),
        patch.object(ExtractionOrchestrator, "_build_source_groups", return_value=[group]),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.fingerprint"
            ".resolve_domain_fingerprint",
            return_value=(None, None),
        ),
        patch(f"{_BASE}.orchestration.cache_quality_scores"),
    ):
        tasks_side = ExtractionOrchestrator(engine)
        tasks = await tasks_side.get_tasks("src")
        plan = tasks_side._plans["src"]
        # The served prompts are the plan's render.
        assert (tasks["entity_instructions"], tasks["relationship_instructions"]) == (
            _render_extraction_instructions(engine.settings, plan)
        )
        await ExtractionOrchestrator(engine).finalize("src")
    finalize = engine.extraction_service.finalize_distributed_extraction.call_args.kwargs
    prompts = (tasks["entity_instructions"], tasks["relationship_instructions"])
    return dict(plan.chunk_kwargs), dict(finalize), prompts


async def _standalone_kwargs(domain: ConfigurableDomain) -> dict[str, Any]:
    """What ``extract_from_chunks`` passes the extractor (no source row)."""
    seen: list[dict[str, Any]] = []
    extractor = AIEntityExtractor.__new__(AIEntityExtractor)
    extractor.settings = EngineSettings()  # type: ignore[attr-defined]
    extractor.extract_single_chunk = _fake_extractor(seen).extract_single_chunk  # type: ignore[method-assign]
    with (
        patch.object(
            ai_entities,
            "detect_domain",
            return_value=(domain, 1.0, () if domain.name == "generic" else (domain.name,)),
        ),
        patch(f"{_BASE}.domains.get_domain_registry", return_value=_registry()),
    ):
        await extractor.extract_from_chunks(["Anna met Pierre. He bowed."])
    assert len(seen) == 1
    return seen[0]


def _plan_part(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The kwargs the extraction plan decides."""
    return {name: kwargs[name] for name in CHUNK_KWARG_NAMES}


def test_queue_and_cli_extract_with_identical_inputs() -> None:
    """Same domain, same settings, same row mode: the same per-chunk inputs on the queue and CLI paths.

    The CLI service drives its own event loop inside ``extract_entities``, so this
    test stays synchronous; the standalone path is compared in the async test below.
    """
    domain = _literary()
    row = _row("lenient")
    queue = _plan_part(_queue_kwargs(domain, row))
    cli = _plan_part(_cli_kwargs(domain, row))

    assert cli == queue
    # The fields the CLI (and both others) used to drop, now present on all three.
    assert queue["domain_extraction_limits"]["strict_edge_type_constraints"] is True
    assert queue["named_referent_types"]


@pytest.mark.asyncio
async def test_standalone_extracts_with_the_queues_inputs() -> None:
    """``extract_from_chunks`` has no row: it falls to the domain's mode and matches the queue.

    The literary domain declares no mode of its own since wave 4 (its dead
    ``lenient`` key went), so the standalone path runs the settings' default:
    the queue matches it with a row of that mode.
    """
    from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
        resolve_filtering_mode,
    )
    from chaoscypher_core.settings import EngineSettings

    domain = _literary()
    row = _row(resolve_filtering_mode(_stack(domain), EngineSettings()))
    queue = _plan_part(_queue_kwargs(domain, row))
    standalone = _plan_part(await _standalone_kwargs(domain))

    assert standalone == queue
    # The fields the CLI (and both others) used to drop, now present on all three.
    assert queue["domain_extraction_limits"]["strict_edge_type_constraints"] is True
    assert queue["named_referent_types"]
    assert queue["normalization_rules"] is not None or not domain.get_normalization_rules()
    assert queue["property_type_mapping"] is not None or not domain.get_property_type_mapping()


def test_the_cli_runs_the_mode_the_source_row_records() -> None:
    """The row's ``balanced`` wins over the domain's default, as on the queue."""
    domain = _literary()
    row = _row("balanced")
    cli = _cli_kwargs(domain, row)
    queue = _queue_kwargs(domain, row)
    assert cli["filtering_mode"] == queue["filtering_mode"] == "balanced"
    assert _plan_part(cli) == _plan_part(queue)


@pytest.mark.parametrize("mode", ["lenient", "balanced"])
def test_cli_and_queue_finalize_with_one_filtering_config(mode: str) -> None:
    """The CLI's dedup and cross-chunk filters run with the queue finalizer's config."""
    domain = _literary()
    row = _row(mode)
    _chunk, finalize = _cli_run(domain, row)
    assert _finalize_part(finalize) == _queue_finalize(domain, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["lenient", "balanced"])
async def test_the_mcp_path_extracts_and_finalizes_with_the_queues_plan(mode: str) -> None:
    """MCP prompts, FilteringConfig, constraints and domain are the queue's (audit W2-12).

    MCP finalize used to resolve its FilteringConfig from the row's mode
    alone: the literary domain's limits (``strict_edge_type_constraints``,
    its dedup threshold) never reached the deduplication, and the forced
    domain never reached it either (no literary title words).
    """
    domain = _literary()
    row = _row(mode)
    chunk_kwargs, finalize, _prompts = await _mcp_run(domain, row)

    assert _plan_part(chunk_kwargs) == _plan_part(_queue_kwargs(domain, row))
    assert _finalize_part(finalize) == _queue_finalize(domain, row)
    config = finalize["filtering_config"]
    assert config.strict_edge_type_constraints is True
    assert config.semantic_dedup_threshold == pytest.approx(
        {"lenient": 0.93, "balanced": 0.90}[mode]
    )


def test_the_cli_hands_every_group_the_known_entities() -> None:
    """``known`` reaches the CLI's ``extract_single_chunk`` (the benchmark's path)."""
    assert isinstance(_cli_kwargs(_literary(), _row("balanced"))["known"], KnownEntities)


@pytest.mark.asyncio
async def test_the_standalone_path_hands_every_group_the_known_entities() -> None:
    """``known`` reaches ``extract_from_chunks``' ``extract_single_chunk``."""
    assert isinstance((await _standalone_kwargs(_literary()))["known"], KnownEntities)


def _one_group_per_chunk() -> Any:
    """``build_extraction_groups`` packing one stored chunk per group, no overlap."""
    from chaoscypher_core.services.sources.engine.extraction import orchestration

    real = orchestration.build_extraction_groups

    def _build(chunks: list[dict[str, Any]], **_: Any) -> list[dict[str, Any]]:
        return real(chunks, target_tokens=1, overlap=0)

    return patch(f"{_BASE}.orchestration.build_extraction_groups", side_effect=_build)


_ROSTOV = "E|Count Ilyá Rostóv|Person|the count|1.0|S1|A count who laughs.\n"
_PIERRE = "E|Pierre Bezúkhov|Person||1.0|S1|A man who weeps.\n"
_PIERRE_S2 = "E|Pierre Bezúkhov|Person||1.0|S2|A man who weeps.\n"
"""Pierre citing the sentence that names him when it is a chunk's second: a record citing one
that does not is flagged unevidenced, and a doubted record never joins the list."""
_THE_COUNT = "E|the count|Person||0.8|S1|The count laughs.\n"
_ROSTOV_LINE = f"{KNOWN_ENTITIES_HEADER}\n- Count Ilyá Rostóv (Person) — also: the count"


def _cli_scripted(llm: ScriptedExtractionLLM, chunks: list[str]) -> tuple[list[int], MagicMock]:
    """Run the CLI over ``chunks`` with ``llm`` at ``call_llm``; the groups each add_group saw."""
    added: list[int] = []
    real_add = KnownEntities.add_group

    def _add(self: KnownEntities, entities: Any, relationships: Any) -> None:
        added.append(len(entities))
        real_add(self, entities, relationships)

    _adapter, extraction_service = _cli_extract(
        _literary(),
        _row("balanced"),
        chunks,
        patch.object(AIEntityExtractor, "call_llm", llm),
        _one_group_per_chunk(),
        patch.object(KnownEntities, "add_group", _add),
    )
    return added, extraction_service


def test_the_cli_shows_group_two_what_group_one_named() -> None:
    """The benchmark's path: group 2's pass-1 prompt carries group 1's entities."""
    llm = ScriptedExtractionLLM(pass1_answers=[_ROSTOV, _THE_COUNT])
    added, extraction_service = _cli_scripted(
        llm, ["Count Ilyá Rostóv, the count, laughed.", "The count laughed."]
    )
    assert len(llm.pass1_prompts) == 2
    assert KNOWN_ENTITIES_HEADER not in llm.pass1_prompts[0]
    assert _ROSTOV_LINE in llm.pass1_prompts[1]
    assert added == [1, 1]
    assert extraction_service.finalize_distributed_extraction.await_count == 1
    # The run's stats carry the list's size (the finalized dict the loop returns).
    finalized = extraction_service.finalize_distributed_extraction.return_value
    assert finalized["stats"]["known_entities"] == {"groups": 2, "entities": 1}


def test_a_retried_cli_group_joins_the_list_once_from_its_final_attempt() -> None:
    """Group 1's first answer is loop-aborted and retried: only the kept answer joins the list.

    The aborted try returned records (it was not an exception), so a list fed
    per attempt would show group 2 that try's Count Rostóv.
    """
    llm = ScriptedExtractionLLM(
        pass1_answers=[_ROSTOV, _PIERRE_S2, _THE_COUNT], pass1_aborted=frozenset({0})
    )
    added, _ = _cli_scripted(
        llm, ["Count Ilyá Rostóv, the count, laughed. Pierre Bezúkhov wept.", "The count laughed."]
    )
    assert len(llm.pass1_prompts) == 3  # group 1 twice, then group 2
    assert KNOWN_ENTITIES_HEADER not in llm.pass1_prompts[1]
    assert f"{KNOWN_ENTITIES_HEADER}\n- Pierre Bezúkhov (Person)" in llm.pass1_prompts[2]
    assert "Rostóv" not in llm.pass1_prompts[2].split(KNOWN_ENTITIES_HEADER)[1]
    assert added == [1, 1]


def test_a_failed_cli_group_adds_nothing() -> None:
    """Group 2 fails every attempt: group 3 is not shown its names, and the run fails after."""
    from chaoscypher_core.services.sources.engine.extraction.chunk_failures import (
        max_chunk_retries,
    )

    attempts = max_chunk_retries(EngineSettings()) + 1
    llm = ScriptedExtractionLLM(
        pass1_answers=[_ROSTOV, *([_PIERRE] * attempts), _THE_COUNT],
        pass2_failing=frozenset(range(1, 1 + attempts)),
    )
    with pytest.raises(IncompleteExtractionError):
        _cli_scripted(
            llm,
            [
                "Count Ilyá Rostóv, the count, laughed.",
                "Pierre Bezúkhov wept.",
                "The count laughed.",
            ],
        )
    assert len(llm.pass1_prompts) == 2 + attempts
    assert _ROSTOV_LINE in llm.pass1_prompts[-1]
    assert "Pierre" not in llm.pass1_prompts[-1]
