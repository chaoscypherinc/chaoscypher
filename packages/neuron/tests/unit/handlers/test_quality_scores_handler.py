# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the recalculate_quality_scores handler closure.

Exercises ``register_quality_score_handler``'s inner
``recalculate_quality_scores_handler`` across every branch:

- batch larger than ``max_quality_score_batch`` is truncated + warned;
- source not found yields an error entry;
- source with no entities is silently skipped;
- happy path updates the file and counts the success;
- domain present drives the registry lookup; the lookup-fails branch is
  swallowed and logged;
- a per-source exception is tracked (capped at ``max_tracked_errors``);
- returned errors are capped at ``max_returned_errors``;
- crossing the ``batch_size`` boundary yields to the event loop.

The handler is obtained by calling ``register_quality_score_handler`` with a
``MagicMock`` storage adapter and pulling the recorded handler off the
``worker_harness`` recording queue. Lazily-imported deps
(``QualityScorer`` / ``SCORING_VERSION`` / ``get_domain_registry``) are
patched at their source module path.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs


# worker_harness.py lives in packages/neuron/tests/fixtures/ (added to sys.path
# by conftest.py); the same insert is mirrored here for the type-only import.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "fixtures"))
from worker_harness import WorkerHarness  # type: ignore[import-not-found]

# Import for side-effect (configure_logging at import time)
import chaoscypher_neuron.worker  # noqa: F401


_QS_OP = "recalculate_quality_scores"


def _make_settings(
    *,
    batch_size: int = 50,
    max_tracked_errors: int = 100,
    max_returned_errors: int = 50,
) -> MagicMock:
    """Build a settings double whose batching knobs drive the handler closure."""
    settings = MagicMock(name="settings")
    settings.batching.quality_score_batch_size = batch_size
    settings.batching.quality_score_max_tracked_errors = max_tracked_errors
    settings.batching.quality_score_max_returned_errors = max_returned_errors
    return settings


def _register_handler(
    harness: WorkerHarness,
    storage_adapter: MagicMock,
    settings: MagicMock,
    *,
    max_quality_score_batch: int = 500,
) -> Any:
    """Register the handler and return the recorded closure.

    ``max_quality_score_batch`` is read from ``get_neuron_settings()`` at
    registration time, so it is patched at the handler module's source path.
    """
    from chaoscypher_neuron.config import NeuronSettings
    from chaoscypher_neuron.handlers.quality_scores import register_quality_score_handler

    neuron_settings = NeuronSettings(max_quality_score_batch=max_quality_score_batch)
    with patch(
        "chaoscypher_neuron.handlers.quality_scores.get_neuron_settings",
        return_value=neuron_settings,
    ):
        register_quality_score_handler(storage_adapter, "test_db", settings)

    handler = harness.queue.registered_on("operations")[_QS_OP]
    return getattr(handler, "handler", handler)


def _patch_scorer(grade: str = "A", *, cached_scores: dict[str, Any] | None = None) -> Any:
    """Patch QualityScorer + SCORING_VERSION at their source module.

    Returns the ``patch`` context manager for ``QualityScorer``; the scorer
    instance's ``get_cacheable_scores`` returns a dict including
    ``cached_quality_grade``.
    """
    scores = cached_scores or {
        "cached_quality_grade": grade,
        "cached_quality_score": 0.9,
    }
    scorer_instance = MagicMock(name="scorer_instance")
    scorer_instance.get_cacheable_scores.return_value = scores
    return MagicMock(name="QualityScorer", return_value=scorer_instance)


@pytest.mark.asyncio
async def test_batch_too_large_truncated_and_warned(
    worker_harness: WorkerHarness,
    structlog_for_caplog: None,
) -> None:
    """source_ids over max_quality_score_batch are truncated with a warning."""
    storage = MagicMock(name="storage")
    # Truncated list keeps only the first 2; both will be "not found", so the
    # processing is cheap and we never touch a real scorer.
    storage.get_source_extraction_metadata.return_value = None

    handler = _register_handler(
        worker_harness, storage, _make_settings(), max_quality_score_batch=2
    )

    with capture_logs() as captured:
        result = await handler(
            {"source_ids": ["s1", "s2", "s3", "s4", "s5"], "database_name": "test_db"}
        )

    events = [c.get("event") for c in captured]
    assert "quality_score_batch_too_large" in events
    warn = next(c for c in captured if c.get("event") == "quality_score_batch_too_large")
    assert warn["requested"] == 5
    assert warn["max_allowed"] == 2
    # Only the first 2 survived truncation, each "not found".
    assert storage.get_source_extraction_metadata.call_count == 2
    assert result["recalculated_count"] == 0
    assert result["error_count"] == 2


@pytest.mark.asyncio
async def test_source_not_found_records_error(
    worker_harness: WorkerHarness,
) -> None:
    """A missing source yields an explicit 'Source not found' error entry."""
    storage = MagicMock(name="storage")
    storage.get_source_extraction_metadata.return_value = None

    handler = _register_handler(worker_harness, storage, _make_settings())

    result = await handler({"source_ids": ["missing"], "database_name": "test_db"})

    assert result["recalculated_count"] == 0
    assert result["errors"] == [{"source_id": "missing", "error": "Source not found"}]
    # Never reached the entity lookup.
    storage.list_source_entities.assert_not_called()


@pytest.mark.asyncio
async def test_source_with_no_entities_skipped(
    worker_harness: WorkerHarness,
) -> None:
    """A source with no entities is skipped — no error, no update."""
    storage = MagicMock(name="storage")
    storage.get_source_extraction_metadata.return_value = {"chunk_count": 3}
    storage.list_source_entities.return_value = []
    storage.list_source_relationships.return_value = []

    handler = _register_handler(worker_harness, storage, _make_settings())

    result = await handler({"source_ids": ["s1"], "database_name": "test_db"})

    assert result["recalculated_count"] == 0
    assert result["errors"] == []
    storage.update_file.assert_not_called()


@pytest.mark.asyncio
async def test_happy_path_updates_file_and_counts(
    worker_harness: WorkerHarness,
) -> None:
    """A scoreable source updates the file and increments recalculated_count."""
    storage = MagicMock(name="storage")
    storage.get_source_extraction_metadata.return_value = {"chunk_count": 4}
    storage.list_source_entities.return_value = [
        {"source_chunks": ["c1", "c2"]},
        {"chunks": ["c3"]},
        {},  # falls back to a mention count of 1
    ]
    storage.list_source_relationships.return_value = [{"id": 1}]

    cached = {"cached_quality_grade": "A", "cached_quality_score": 0.95}
    scorer_cls = _patch_scorer(cached_scores=cached)

    handler = _register_handler(worker_harness, storage, _make_settings())

    with (
        patch("chaoscypher_core.services.quality.QualityScorer", scorer_cls),
        patch("chaoscypher_core.services.quality.SCORING_VERSION", "v-test"),
    ):
        result = await handler({"source_ids": ["s1"], "database_name": "test_db"})

    assert result["recalculated_count"] == 1
    assert result["error_count"] == 0
    storage.update_file.assert_called_once_with("s1", database_name="test_db", updates=cached)
    # Scorer was constructed with an (empty) quality_config and asked for scores.
    scorer_cls.assert_called_once()
    instance = scorer_cls.return_value
    instance.get_cacheable_scores.assert_called_once()
    call_kwargs = instance.get_cacheable_scores.call_args.kwargs
    assert call_kwargs["source_id"] == "s1"
    assert call_kwargs["chunk_count"] == 4


@pytest.mark.asyncio
async def test_domain_present_drives_registry_lookup(
    worker_harness: WorkerHarness,
) -> None:
    """A source with an extraction_domain pulls quality config from the registry."""
    storage = MagicMock(name="storage")
    storage.get_source_extraction_metadata.return_value = {
        "chunk_count": 2,
        "extraction_domain": "medical",
    }
    storage.list_source_entities.return_value = [{"source_chunks": ["c1"]}]
    storage.list_source_relationships.return_value = []

    domain_config = {"weight": 0.7}
    analyzer = MagicMock(name="analyzer")
    analyzer.get_quality_scoring.return_value = domain_config
    registry = MagicMock(name="registry")
    registry.get_domain.return_value = analyzer
    get_registry = MagicMock(name="get_domain_registry", return_value=registry)

    scorer_cls = _patch_scorer()

    handler = _register_handler(worker_harness, storage, _make_settings())

    with (
        patch("chaoscypher_core.services.quality.QualityScorer", scorer_cls),
        patch("chaoscypher_core.services.quality.SCORING_VERSION", "v-test"),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            get_registry,
        ),
    ):
        result = await handler({"source_ids": ["s1"], "database_name": "test_db"})

    assert result["recalculated_count"] == 1
    get_registry.assert_called_once_with(database_name="test_db")
    registry.get_domain.assert_called_once_with("medical")
    # The domain config flowed into the scorer constructor.
    scorer_cls.assert_called_once_with(domain_config)


@pytest.mark.asyncio
async def test_domain_lookup_failure_is_swallowed_and_logged(
    worker_harness: WorkerHarness,
    structlog_for_caplog: None,
) -> None:
    """A raising domain-registry lookup is caught; scoring continues with {} config."""
    storage = MagicMock(name="storage")
    storage.get_source_extraction_metadata.return_value = {
        "chunk_count": 1,
        "extraction_domain": "broken",
    }
    storage.list_source_entities.return_value = [{"chunks": ["c1"]}]
    storage.list_source_relationships.return_value = []

    boom = MagicMock(name="get_domain_registry", side_effect=RuntimeError("registry exploded"))
    scorer_cls = _patch_scorer()

    handler = _register_handler(worker_harness, storage, _make_settings())

    with (
        patch("chaoscypher_core.services.quality.QualityScorer", scorer_cls),
        patch("chaoscypher_core.services.quality.SCORING_VERSION", "v-test"),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            boom,
        ),
        capture_logs() as captured,
    ):
        result = await handler({"source_ids": ["s1"], "database_name": "test_db"})

    events = [c.get("event") for c in captured]
    assert "domain_quality_scoring_lookup_failed" in events
    # Scoring still happened — the source was not dropped.
    assert result["recalculated_count"] == 1
    # The failed lookup falls through to the default empty config.
    scorer_cls.assert_called_once_with({})


@pytest.mark.asyncio
async def test_per_source_exception_tracked_and_capped(
    worker_harness: WorkerHarness,
    structlog_for_caplog: None,
) -> None:
    """A scorer that raises is logged; tracked errors honour max_tracked_errors."""
    storage = MagicMock(name="storage")
    storage.get_source_extraction_metadata.return_value = {"chunk_count": 1}
    storage.list_source_entities.return_value = [{"chunks": ["c1"]}]
    storage.list_source_relationships.return_value = []

    scorer_cls = MagicMock(name="QualityScorer", side_effect=ValueError("scorer blew up"))

    # Two sources both raise, but max_tracked_errors=1 caps the tracked list at 1.
    handler = _register_handler(worker_harness, storage, _make_settings(max_tracked_errors=1))

    with (
        patch("chaoscypher_core.services.quality.QualityScorer", scorer_cls),
        patch("chaoscypher_core.services.quality.SCORING_VERSION", "v-test"),
        capture_logs() as captured,
    ):
        result = await handler({"source_ids": ["s1", "s2"], "database_name": "test_db"})

    events = [c.get("event") for c in captured]
    assert events.count("source_quality_recalculation_failed") == 2
    failure = next(c for c in captured if c.get("event") == "source_quality_recalculation_failed")
    assert failure["error_type"] == "ValueError"
    assert "scorer blew up" in failure["error_message"]
    assert result["recalculated_count"] == 0
    # Both sources failed but the tracked list is capped at 1.
    assert result["error_count"] == 1
    assert result["errors"] == [{"source_id": "s1", "error": "Score calculation failed"}]


@pytest.mark.asyncio
async def test_returned_errors_capped_at_max_returned(
    worker_harness: WorkerHarness,
) -> None:
    """The returned 'errors' slice honours max_returned_errors even when more are tracked."""
    storage = MagicMock(name="storage")
    storage.get_source_extraction_metadata.return_value = None  # all "not found"

    # Track up to 10, but only return 2.
    handler = _register_handler(
        worker_harness,
        storage,
        _make_settings(max_tracked_errors=10, max_returned_errors=2),
    )

    result = await handler({"source_ids": ["s1", "s2", "s3", "s4"], "database_name": "test_db"})

    # All four tracked (count reflects the full tracked list)...
    assert result["error_count"] == 4
    # ...but only two returned.
    assert len(result["errors"]) == 2


@pytest.mark.asyncio
async def test_batch_boundary_yields_to_event_loop(
    worker_harness: WorkerHarness,
    structlog_for_caplog: None,
) -> None:
    """Crossing the batch_size boundary emits a progress log and yields."""
    storage = MagicMock(name="storage")
    storage.get_source_extraction_metadata.return_value = None  # cheap "not found" path

    # batch_size=2: index 2 (the third source) crosses the boundary.
    handler = _register_handler(
        worker_harness, storage, _make_settings(batch_size=2, max_returned_errors=10)
    )

    with capture_logs() as captured:
        result = await handler({"source_ids": ["s1", "s2", "s3", "s4"], "database_name": "test_db"})

    events = [c.get("event") for c in captured]
    assert "recalculate_quality_scores_progress" in events
    progress = next(c for c in captured if c.get("event") == "recalculate_quality_scores_progress")
    assert progress["processed"] == 2
    assert progress["total"] == 4
    assert result["error_count"] == 4
