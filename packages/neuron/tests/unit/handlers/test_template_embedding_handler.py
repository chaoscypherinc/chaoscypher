# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the regenerate_template_embeddings handler closure.

Exercises ``register_template_embedding_handler``'s inner
``regenerate_template_embeddings_handler`` across every branch:

- no templates short-circuits to ``{"success": True, "count": 0, ...}``;
- templates with embeddings update the graph + search repositories;
- ``engine_settings=None`` falls back to ``build_engine_settings``;
- a falsy embedding for a template is skipped (not counted, not indexed);
- an exception inside the loop is logged and re-raised;
- crossing the batch boundary emits a progress log and yields.

The handler is obtained by calling ``register_template_embedding_handler`` and
pulling the recorded closure off the ``worker_harness`` recording queue.
Lazily-imported deps (``create_embedding_provider``,
``TemplateEmbeddingService``, ``build_engine_settings``) are patched at
their source module path.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs


# worker_harness.py lives in packages/neuron/tests/fixtures/ (added to sys.path
# by conftest.py); the same insert is mirrored here for the type-only import.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "fixtures"))
from worker_harness import WorkerHarness  # type: ignore[import-not-found]

# Import for side-effect (configure_logging at import time)
import chaoscypher_neuron.worker  # noqa: F401


_TMPL_OP = "regenerate_template_embeddings"


def _make_template(template_id: str, name: str, description: str) -> MagicMock:
    """A template stand-in exposing .id / .name / .description."""
    tmpl = MagicMock(name=f"template-{template_id}")
    tmpl.id = template_id
    tmpl.name = name
    tmpl.description = description
    return tmpl


def _make_settings(*, batch_size: int = 50) -> MagicMock:
    """Settings double whose batching knob drives the loop yield cadence."""
    settings = MagicMock(name="settings")
    settings.batching.template_embedding_batch_size = batch_size
    return settings


def _register_handler(
    harness: WorkerHarness,
    *,
    graph_repository: MagicMock,
    search_repository: MagicMock,
    settings: MagicMock,
    engine_settings: Any = None,
) -> Any:
    """Register the handler and return the recorded closure."""
    from chaoscypher_neuron.handlers.template_embedding import (
        register_template_embedding_handler,
    )

    register_template_embedding_handler(
        graph_repository,
        search_repository,
        settings,
        "test_db",
        engine_settings=engine_settings,
    )
    handler = harness.queue.registered_on("llm")[_TMPL_OP]
    return getattr(handler, "handler", handler)


def _patch_embedding_seams(
    *,
    embedding_return: Any = None,
    embedding_side_effect: list[Any] | None = None,
    embedding_model: str = "text-embed-test",
) -> tuple[Any, Any, MagicMock]:
    """Build patch CMs for the lazily-imported embedding seams.

    Pass ``embedding_return`` for a fixed per-call vector, or
    ``embedding_side_effect`` for a per-call sequence of vectors.

    Returns (create_embedding_provider_patch, TemplateEmbeddingService_patch,
    template_service_instance).
    """
    service_instance = MagicMock(name="template_service")
    if embedding_side_effect is not None:
        service_instance.generate_embedding = AsyncMock(side_effect=embedding_side_effect)
    else:
        service_instance.generate_embedding = AsyncMock(return_value=embedding_return)
    service_instance.get_embedding_model.return_value = embedding_model

    service_cls = MagicMock(name="TemplateEmbeddingService", return_value=service_instance)
    provider_factory = MagicMock(
        name="create_embedding_provider", return_value=MagicMock(name="provider")
    )

    create_patch = patch(
        "chaoscypher_core.adapters.embedding.create_embedding_provider",
        provider_factory,
    )
    service_patch = patch(
        "chaoscypher_core.services.graph.management.embedding.TemplateEmbeddingService",
        service_cls,
    )
    return create_patch, service_patch, service_instance


@pytest.mark.asyncio
async def test_no_templates_short_circuits(
    worker_harness: WorkerHarness,
) -> None:
    """An empty template list returns the zero-count success payload."""
    graph_repo = MagicMock(name="graph_repository")
    graph_repo.list_templates.return_value = []
    search_repo = MagicMock(name="search_repository")

    handler = _register_handler(
        worker_harness,
        graph_repository=graph_repo,
        search_repository=search_repo,
        settings=_make_settings(),
        engine_settings=MagicMock(name="engine_settings"),
    )

    result = await handler({"database_name": "test_db"})

    assert result == {"success": True, "count": 0, "message": "No templates found"}
    graph_repo.update_template.assert_not_called()
    search_repo.index_template.assert_not_called()


@pytest.mark.asyncio
async def test_templates_embedded_update_and_index(
    worker_harness: WorkerHarness,
) -> None:
    """Each template with an embedding updates the graph + search repos."""
    templates = [
        _make_template("t1", "Alpha", "first"),
        _make_template("t2", "Beta", "second"),
    ]
    graph_repo = MagicMock(name="graph_repository")
    graph_repo.list_templates.return_value = templates
    search_repo = MagicMock(name="search_repository")

    vector = [0.1, 0.2, 0.3]
    create_patch, service_patch, _ = _patch_embedding_seams(embedding_return=vector)

    # Non-None engine_settings: build_engine_settings must NOT be called.
    handler = _register_handler(
        worker_harness,
        graph_repository=graph_repo,
        search_repository=search_repo,
        settings=_make_settings(),
        engine_settings=MagicMock(name="cached_engine_settings"),
    )

    with create_patch, service_patch:
        result = await handler({"database_name": "test_db"})

    assert result["success"] is True
    assert result["count"] == 2
    assert result["total"] == 2
    assert graph_repo.update_template.call_count == 2
    assert search_repo.index_template.call_count == 2
    # The first update carried the embedding + its dimensions.
    first_call = graph_repo.update_template.call_args_list[0]
    assert first_call.args[0] == "t1"
    payload = first_call.args[1]
    assert payload["embedding"] == vector
    assert payload["embedding_dimensions"] == len(vector)
    assert payload["embedding_model"] == "text-embed-test"
    search_repo.index_template.assert_any_call("t1", vector)


@pytest.mark.asyncio
async def test_engine_settings_none_triggers_conversion(
    worker_harness: WorkerHarness,
) -> None:
    """engine_settings=None falls back to build_engine_settings(settings)."""
    templates = [_make_template("t1", "Alpha", "first")]
    graph_repo = MagicMock(name="graph_repository")
    graph_repo.list_templates.return_value = templates
    search_repo = MagicMock(name="search_repository")
    settings = _make_settings()

    create_patch, service_patch, _ = _patch_embedding_seams(embedding_return=[0.5, 0.6])
    converted = MagicMock(name="converted_engine_settings")
    build = MagicMock(name="build_engine_settings", return_value=converted)

    handler = _register_handler(
        worker_harness,
        graph_repository=graph_repo,
        search_repository=search_repo,
        settings=settings,
        engine_settings=None,
    )

    with (
        create_patch as create_provider,
        service_patch,
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            build,
        ),
    ):
        result = await handler({"database_name": "test_db"})

    build.assert_called_once_with(settings)
    # The converted settings flowed into the provider factory.
    create_provider.assert_called_once_with(converted)
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_falsy_embedding_skips_template(
    worker_harness: WorkerHarness,
) -> None:
    """A template whose embedding is falsy is skipped — not counted, not indexed."""
    templates = [
        _make_template("t1", "Alpha", "first"),
        _make_template("t2", "Beta", "second"),
    ]
    graph_repo = MagicMock(name="graph_repository")
    graph_repo.list_templates.return_value = templates
    search_repo = MagicMock(name="search_repository")

    # t1 -> empty vector (falsy, skipped); t2 -> a real vector.
    create_patch, service_patch, _ = _patch_embedding_seams(embedding_side_effect=[[], [0.9]])

    handler = _register_handler(
        worker_harness,
        graph_repository=graph_repo,
        search_repository=search_repo,
        settings=_make_settings(),
        engine_settings=MagicMock(name="engine_settings"),
    )

    with create_patch, service_patch:
        result = await handler({"database_name": "test_db"})

    assert result["count"] == 1
    assert result["total"] == 2
    # Only t2 was persisted.
    graph_repo.update_template.assert_called_once()
    assert graph_repo.update_template.call_args.args[0] == "t2"
    search_repo.index_template.assert_called_once_with("t2", [0.9])


@pytest.mark.asyncio
async def test_exception_inside_loop_logs_and_reraises(
    worker_harness: WorkerHarness,
    structlog_for_caplog: None,
) -> None:
    """A raise during embedding generation is logged and re-raised."""
    templates = [_make_template("t1", "Alpha", "first")]
    graph_repo = MagicMock(name="graph_repository")
    graph_repo.list_templates.return_value = templates
    search_repo = MagicMock(name="search_repository")

    boom = RuntimeError("embedding backend down")
    service_instance = MagicMock(name="template_service")
    service_instance.generate_embedding = AsyncMock(side_effect=boom)
    service_cls = MagicMock(name="TemplateEmbeddingService", return_value=service_instance)
    provider_factory = MagicMock(return_value=MagicMock(name="provider"))

    handler = _register_handler(
        worker_harness,
        graph_repository=graph_repo,
        search_repository=search_repo,
        settings=_make_settings(),
        engine_settings=MagicMock(name="engine_settings"),
    )

    with (
        patch(
            "chaoscypher_core.adapters.embedding.create_embedding_provider",
            provider_factory,
        ),
        patch(
            "chaoscypher_core.services.graph.management.embedding.TemplateEmbeddingService",
            service_cls,
        ),
        capture_logs() as captured,
        pytest.raises(RuntimeError, match="embedding backend down"),
    ):
        await handler({"database_name": "test_db"})

    events = [c.get("event") for c in captured]
    assert "regenerate_template_embeddings_failed" in events
    graph_repo.update_template.assert_not_called()


@pytest.mark.asyncio
async def test_batch_boundary_yields_to_event_loop(
    worker_harness: WorkerHarness,
    structlog_for_caplog: None,
) -> None:
    """Crossing the template_embedding_batch_size boundary emits a progress log."""
    templates = [_make_template(f"t{i}", f"N{i}", f"d{i}") for i in range(4)]
    graph_repo = MagicMock(name="graph_repository")
    graph_repo.list_templates.return_value = templates
    search_repo = MagicMock(name="search_repository")

    create_patch, service_patch, _ = _patch_embedding_seams(embedding_return=[0.1])

    # batch_size=2: index 2 (the third template) crosses the boundary.
    handler = _register_handler(
        worker_harness,
        graph_repository=graph_repo,
        search_repository=search_repo,
        settings=_make_settings(batch_size=2),
        engine_settings=MagicMock(name="engine_settings"),
    )

    with create_patch, service_patch, capture_logs() as captured:
        result = await handler({"database_name": "test_db"})

    events = [c.get("event") for c in captured]
    assert "template_embedding_progress" in events
    progress = next(c for c in captured if c.get("event") == "template_embedding_progress")
    assert progress["processed"] == 2
    assert progress["total"] == 4
    assert result["count"] == 4
