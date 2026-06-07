# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""extraction_config snapshots all settings at job creation; mid-job changes don't leak.

Workstream 8 (2026-05-07) Task 8.4 — pin LLM tuning + loop-detector
thresholds in the job's ``extraction_config`` JSON column at creation
time. The chunk handler reads from the snapshot with a fall-through to
live settings, so a mid-job edit to ``settings.yaml`` cannot drift
across in-flight chunks.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.app_config import Settings


def _make_settings(
    *,
    extraction_temperature: float = 0.1,
    extraction_max_tokens: int = 32768,
    extraction_examples_enabled: bool = True,
    extraction_examples_max_chars: int = 800,
    loop_max_out_of_bounds: int = 3,
    loop_max_source_type_repeat: int = 4,
    loop_max_property_repeat: int = 5,
    loop_invalid_relationship_rate_warmup: int = 20,
    loop_invalid_relationship_rate_threshold: float = 0.7,
) -> Settings:
    """Build a real ``app_config.Settings`` for ``_build_extraction_config``.

    Uses a real instance — not ``MagicMock`` — so attribute typos surface
    here instead of in production. Workstream 8 (a8a024ce2) shipped a
    regression where the function read ``settings.extraction.loop_*``
    against the backend ``Settings`` (no ``.extraction`` field). The
    earlier MagicMock fixture invented the missing attribute; a real
    instance does not, which is why a typo-class bug leaked to runtime.

    The function under test calls ``build_engine_settings(settings)``
    internally; the factory copies retry-loop thresholds from the backend
    ``Settings`` extraction group, so the loop_* defaults below come from
    ``EngineSettings`` defaults unless overridden on the backend object.
    The args are accepted for forward compatibility.
    """
    # Suppress unused-arg lint — the loop_* knobs are not yet plumbed
    # through this fixture; build_engine_settings falls back to
    # ``EngineSettings.extraction`` defaults. Tests assert the defaults
    # those produce, which is the contract we ship today.
    del (
        loop_max_out_of_bounds,
        loop_max_source_type_repeat,
        loop_max_property_repeat,
        loop_invalid_relationship_rate_warmup,
        loop_invalid_relationship_rate_threshold,
    )
    settings = Settings()
    settings.llm.extraction_temperature = extraction_temperature
    settings.llm.extraction_max_tokens = extraction_max_tokens
    settings.llm.extraction_examples_enabled = extraction_examples_enabled
    settings.llm.extraction_examples_max_chars = extraction_examples_max_chars
    return settings


def _make_domain() -> Any:
    domain = MagicMock()
    domain.metadata = MagicMock(plugin_id="generic")
    domain.get_extraction_limits = MagicMock(return_value={})
    domain.get_filtering_mode = MagicMock(return_value=None)
    domain.get_entity_exclusions = MagicMock(return_value=[])
    domain.get_evidence_validation_mode = MagicMock(return_value=None)
    domain.get_strict_entity_types = MagicMock(return_value=False)
    domain.get_edge_type_constraints = MagicMock(return_value={})
    domain.get_templates = MagicMock(return_value={"node_templates": [], "edge_templates": []})
    return domain


def test_snapshot_carries_llm_tuning_and_loop_thresholds(monkeypatch) -> None:
    from chaoscypher_core.operations.importing import import_service

    settings = _make_settings()
    domain = _make_domain()

    # Stub out the template formatter so we don't need a fully-wired
    # domain plugin in the unit test.
    monkeypatch.setattr(
        import_service,
        "format_extraction_templates"
        if hasattr(import_service, "format_extraction_templates")
        else "_NOPE",
        lambda *_a, **_kw: {
            "node_templates": "",
            "edge_templates": "",
            "entity_examples": "",
            "relationship_examples": "",
        },
        raising=False,
    )
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.engine.extraction.orchestration.format_extraction_templates",
        lambda *_a, **_kw: {
            "node_templates": "",
            "edge_templates": "",
            "entity_examples": "",
            "relationship_examples": "",
        },
    )

    cfg_json = import_service._build_extraction_config(
        domain=domain,
        entity_guidance="ent",
        relationship_guidance="rel",
        settings=settings,
        file_info={},
        source_row=None,
    )
    cfg = json.loads(cfg_json)

    # Workstream 8 keys.
    # LLM tuning comes from the backend Settings overrides we set above.
    assert cfg["extraction_temperature"] == 0.1
    assert cfg["extraction_max_tokens"] == 32768
    assert cfg["extraction_examples_enabled"] is True
    assert cfg["extraction_examples_max_chars"] == 800
    # Loop thresholds come from EngineSettings.extraction defaults today —
    # the backend Settings shape does not yet carry these knobs, so the
    # converter falls through to defaults. If/when an `extraction` group
    # is added to backend settings these defaults stay correct because
    # the field defaults are a single source of truth.
    assert cfg["loop_max_out_of_bounds"] == 3
    assert cfg["loop_max_source_type_repeat"] == 10
    assert cfg["loop_max_property_repeat"] == 5
    assert cfg["loop_invalid_relationship_rate_warmup"] == 10
    assert cfg["loop_invalid_relationship_rate_threshold"] == 0.5
    assert cfg["snapshot_version"] == 2


def test_build_extraction_config_accepts_backend_settings_without_attribute_error(
    monkeypatch,
) -> None:
    """Regression: ``_build_extraction_config`` must accept ``app_config.Settings``.

    The backend ``Settings`` shape does not carry an ``extraction`` field
    today; the function must convert to ``EngineSettings`` internally
    before reading ``extraction.*`` knobs. Workstream 8 (a8a024ce2)
    shipped a regression where ``settings.extraction.loop_*`` was read
    against the backend object directly, raising ``AttributeError`` on
    every real import. The earlier MagicMock-based test fixture invented
    the missing attribute and let the bug through.

    This test exists to fail loudly if the converter call is ever
    removed again — pass a real ``app_config.Settings`` and assert no
    AttributeError. Field-value assertions live in the sibling test;
    this one is purely about type safety at the function boundary.
    """
    from chaoscypher_core.operations.importing import import_service

    settings = Settings()  # real backend Settings, no `.extraction`
    domain = _make_domain()
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.engine.extraction.orchestration.format_extraction_templates",
        lambda *_a, **_kw: {
            "node_templates": "",
            "edge_templates": "",
            "entity_examples": "",
            "relationship_examples": "",
        },
    )

    # Must not raise. The pre-fix code raised
    # ``AttributeError: 'Settings' object has no attribute 'extraction'``.
    cfg_json = import_service._build_extraction_config(
        domain=domain,
        entity_guidance=None,
        relationship_guidance=None,
        settings=settings,
        file_info={},
        source_row=None,
    )
    cfg = json.loads(cfg_json)
    # Sanity-check that the snapshot keys are present (not just that
    # the call returned). A future regression that silently dropped the
    # extraction.* reads would still pass an "it didn't crash" assertion.
    for required_key in (
        "loop_max_out_of_bounds",
        "loop_max_source_type_repeat",
        "loop_max_property_repeat",
        "loop_invalid_relationship_rate_warmup",
        "loop_invalid_relationship_rate_threshold",
    ):
        assert required_key in cfg, f"snapshot missing {required_key}"


def test_snapshot_does_not_drift_when_settings_change_after_build(
    monkeypatch,
) -> None:
    """The snapshot is a JSON string — mutating settings after build is a no-op."""
    from chaoscypher_core.operations.importing import import_service

    settings = _make_settings()
    domain = _make_domain()
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.engine.extraction.orchestration.format_extraction_templates",
        lambda *_a, **_kw: {
            "node_templates": "",
            "edge_templates": "",
            "entity_examples": "",
            "relationship_examples": "",
        },
    )

    cfg_json = import_service._build_extraction_config(
        domain=domain,
        entity_guidance=None,
        relationship_guidance=None,
        settings=settings,
        file_info={},
        source_row=None,
    )

    # Mutate settings AFTER building the snapshot. The stored JSON must
    # reflect creation-time values.
    settings.llm.extraction_temperature = 0.9
    settings.llm.extraction_max_tokens = 1024

    cfg = json.loads(cfg_json)
    assert cfg["extraction_temperature"] == 0.1
    assert cfg["extraction_max_tokens"] == 32768


def _build_cfg_with_domain(monkeypatch, domain) -> dict[str, Any]:
    """Helper: build the extraction config dict from a given domain mock."""
    from chaoscypher_core.operations.importing import import_service

    settings = _make_settings()
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.engine.extraction.orchestration.format_extraction_templates",
        lambda *_a, **_kw: {
            "node_templates": "",
            "edge_templates": "",
            "entity_examples": "",
            "relationship_examples": "",
        },
    )
    cfg_json = import_service._build_extraction_config(
        domain=domain,
        entity_guidance=None,
        relationship_guidance=None,
        settings=settings,
        file_info={},
        source_row=None,
    )
    return json.loads(cfg_json)


def test_strict_edge_type_constraints_inherits_domain_strict_entity_types(
    monkeypatch,
) -> None:
    """When the domain declares ``strict_entity_types=True``, the same contract
    propagates to the edge-type validator via
    ``extraction_limits.strict_edge_type_constraints``.

    Regression context: a 2026-05-18 audit found that even with literary's
    ``strict_entity_types: true`` set, the edge-type validator ran in
    fall-through mode (``strict_edge_type_constraints=False``) for any
    non-strict preset (balanced/lenient/minimal). Relationships violating
    domain edge-template constraints — e.g. ``Vienna -[interacts_with]->
    Empress`` where ``interacts_with`` requires both endpoints to be
    Character/Historical Figure/Narrator — passed silently. The domain
    plugin's strictness flag must drive the edge validator too.
    """
    domain = _make_domain()
    domain.get_strict_entity_types = MagicMock(return_value=True)

    cfg = _build_cfg_with_domain(monkeypatch, domain)

    limits = cfg.get("extraction_limits") or {}
    assert limits.get("strict_edge_type_constraints") is True


def test_strict_edge_type_constraints_false_when_domain_not_strict(
    monkeypatch,
) -> None:
    """When the domain declares ``strict_entity_types=False``, the edge
    validator stays in fall-through mode unless the preset enables it.
    """
    domain = _make_domain()
    domain.get_strict_entity_types = MagicMock(return_value=False)

    cfg = _build_cfg_with_domain(monkeypatch, domain)

    limits = cfg.get("extraction_limits") or {}
    # Either absent or explicitly False — both mean fall-through.
    assert limits.get("strict_edge_type_constraints", False) is False


def test_strict_edge_type_constraints_domain_limits_override_wins(
    monkeypatch,
) -> None:
    """An explicit ``strict_edge_type_constraints`` in the domain's
    ``extraction_limits`` is not overwritten by the strict_entity_types
    flag. Lets a domain declare ``strict_entity_types=true`` for the
    entity-type validator while keeping the edge validator lenient — useful
    for domains in transition.
    """
    domain = _make_domain()
    domain.get_strict_entity_types = MagicMock(return_value=True)
    domain.get_extraction_limits = MagicMock(return_value={"strict_edge_type_constraints": False})

    cfg = _build_cfg_with_domain(monkeypatch, domain)

    limits = cfg.get("extraction_limits") or {}
    assert limits.get("strict_edge_type_constraints") is False


def test_apply_snapshot_overrides_returns_unchanged_when_snapshot_empty() -> None:
    """Legacy snapshots (no v2 keys) are pass-through — fall back to live settings."""
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        _apply_snapshot_overrides,
    )

    engine_settings = MagicMock(
        extraction=MagicMock(),
        llm=MagicMock(),
    )
    engine_settings.extraction.model_copy = MagicMock(side_effect=AssertionError("should not copy"))
    engine_settings.llm.model_copy = MagicMock(side_effect=AssertionError("should not copy"))
    engine_settings.model_copy = MagicMock(side_effect=AssertionError("should not copy"))

    result = _apply_snapshot_overrides(engine_settings, {})
    assert result is engine_settings


def test_apply_snapshot_overrides_patches_extraction_thresholds() -> None:
    """A snapshot v2 carrying loop_* keys patches engine_settings.extraction."""
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        _apply_snapshot_overrides,
    )

    extraction = MagicMock()
    extraction_copy = MagicMock(name="extraction_copy")
    extraction.model_copy = MagicMock(return_value=extraction_copy)

    llm = MagicMock()
    llm.model_copy = MagicMock(return_value=llm)

    engine_settings = MagicMock(extraction=extraction, llm=llm)
    new_settings = MagicMock(name="new_settings")
    engine_settings.model_copy = MagicMock(return_value=new_settings)

    snapshot = {
        "loop_max_out_of_bounds": 9,
        "loop_max_source_type_repeat": 8,
        "extraction_examples_enabled": False,
    }

    result = _apply_snapshot_overrides(engine_settings, snapshot)

    extraction.model_copy.assert_called_once_with(
        update={
            "loop_max_out_of_bounds": 9,
            "loop_max_source_type_repeat": 8,
        }
    )
    llm.model_copy.assert_called_once_with(update={"extraction_examples_enabled": False})
    engine_settings.model_copy.assert_called_once()
    assert result is new_settings


@pytest.mark.asyncio
async def test_chunk_handler_uses_snapshot_temperature_and_max_tokens(
    tmp_path,
) -> None:
    """The chunk handler forwards snapshot temperature/max_tokens to the extractor."""
    from unittest.mock import AsyncMock, patch

    from sqlmodel import SQLModel

    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.engine import get_engine
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        adapter.create_source(
            {
                "id": "s1",
                "database_name": "default",
                "filename": "s1.txt",
                "filepath": "/tmp/s1.txt",
                "file_type": "txt",
                "file_size": 1,
                "content_hash": "h",
                "status": "extracting",
            }
        )
        adapter.create_extraction_job(job_id="j1", source_id="s1", database_name="default")
        adapter.create_chunk_task(task_id="t1", job_id="j1", database_name="default", chunk_index=0)
        adapter.create_chunk(
            {
                "id": "ch1",
                "database_name": "default",
                "source_id": "s1",
                "chunk_index": 0,
                "content": "Alice met Bob in Paris." * 20,
            }
        )
        snapshot = {
            "node_templates_formatted": "",
            "extraction_temperature": 0.42,
            "extraction_max_tokens": 12345,
            "snapshot_version": 2,
        }
        adapter.update_extraction_job(
            "j1", {"extraction_config": json.dumps(snapshot), "status": "in_progress"}
        )

        service = ChunkExtractionOperationsService(source_repository=adapter)

        captured: dict[str, Any] = {}

        async def _capture(**kwargs: Any) -> tuple[Any, ...]:
            captured["temperature_override"] = kwargs.get("temperature_override")
            captured["max_tokens_override"] = kwargs.get("max_tokens_override")
            return (
                [],
                [],
                10,
                20,
                {
                    "raw_llm_response": "",
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "entity_count": 0,
                    "relationship_count": 0,
                    "invalid_relationship_count": 0,
                    "evidence_stats": {},
                    "sentences": [],
                    "filtering_log": None,
                    "_prompt_data": {},
                    "finish_reason": "stop",
                    "aborted_by_loop": False,
                },
            )

        with (
            patch(
                "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor.extract_single_chunk",
                new=AsyncMock(side_effect=_capture),
            ),
            patch(
                "chaoscypher_core.queue.queue_client.track_tokens",
                new=AsyncMock(return_value=None),
            ),
            patch.object(service, "queue_finalize_extraction", new=AsyncMock(return_value="qid")),
        ):
            await service._extract_chunk_handler(
                data={
                    "chunk_task_id": "t1",
                    "job_id": "j1",
                    "database_name": "default",
                    "chunk_index": 0,
                    "small_chunk_ids": ["ch1"],
                }
            )

        assert captured["temperature_override"] == 0.42
        assert captured["max_tokens_override"] == 12345
    finally:
        adapter.disconnect()
