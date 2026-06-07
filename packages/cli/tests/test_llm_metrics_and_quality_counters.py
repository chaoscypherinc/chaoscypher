# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for LLM-metrics + quality-counter persistence.

LLM metrics: ``compute_llm_summary`` returns an ``llm_*``-prefixed
dict; callers (CLI extract step, extraction_finalizer) pass it to
``update_source_columns`` which writes the columns directly with
loud-failure on typos. Tests below pin both the summary shape and
the round-trip into SourceRow.

Bug 8 (still under investigation): All 40 quality counters (loader /
cleaner / chunking / LLM / post-extraction / commit / search) stayed
at 0 on a run that exercised extraction end-to-end. Possibly the CLI
extract step is not calling ``increment_quality_counter`` at the
relevant drop sites (orphan-filter, structural-filter, etc.). This
file pins the helper's contract; a follow-up needs to audit each
drop site.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel


# ---------------------------------------------------------------------------
# Fixtures — real SqliteAdapter against tmp_path. Mirrors the canonical
# pattern at packages/core/tests/unit/adapters/sqlite/conftest.py so any
# future refactor of the core fixture is the single point of update.
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator:  # type: ignore[type-arg]
    """Per-test file-backed ``SqliteAdapter`` with all tables created.

    Same setup as packages/core/tests/unit/adapters/sqlite/conftest.py's
    ``sqlite_adapter`` fixture — duplicated here so this CLI suite stays
    self-contained.
    """
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.engine import get_engine

    db_dir = tmp_path / "metrics-test-db"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        yield adapter
    finally:
        adapter.disconnect()


def _seed_source(adapter, source_id: str = "src_metrics_test") -> str:
    """Insert a minimal SourceRow so update_source_columns has a target."""
    adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename=f"{source_id}.txt",
        file_content=b"placeholder",
        staging_dir="/tmp/staging-metrics-test",
    )
    return source_id


# ---------------------------------------------------------------------------
# Bug 7: collector summary key mismatch silently drops every metric
# ---------------------------------------------------------------------------


def test_compute_llm_summary_returns_llm_prefixed_keys(sqlite_adapter) -> None:  # type: ignore[no-untyped-def]
    """``compute_llm_summary`` must emit ``llm_*`` prefixed keys that
    match SourceRow columns. This is the helper the CLI SHOULD be using
    (Bug 7 fix); the test fails if the prefix ever drops away.
    """
    source_id = _seed_source(sqlite_adapter)

    # No metrics persisted yet — summary should be all-zero but the
    # SHAPE (keys) is what we're pinning.
    summary = sqlite_adapter.compute_llm_summary(source_id=source_id, database_name="default")

    # The 15 ``llm_*``-prefixed keys that map to SourceRow columns. The
    # ``extraction_mode`` key is also emitted by the non-empty code path
    # (line 297 of mixins/llm_metrics.py) but is conditional, so we
    # don't include it in the must-have set.
    expected_keys = {
        "llm_total_calls",
        "llm_successful_calls",
        "llm_failed_calls",
        "llm_retry_calls",
        "llm_first_try_successes",
        "llm_retry_successes",
        "llm_permanent_failures",
        "llm_total_input_tokens",
        "llm_total_output_tokens",
        "llm_wasted_tokens",
        "llm_avg_call_duration_ms",
        "llm_total_duration_ms",
        "llm_estimated_cost_usd",
        "llm_error_counts",
        "llm_model",
    }
    assert expected_keys.issubset(set(summary.keys())), (
        f"compute_llm_summary is missing llm_*-prefixed keys. Got: "
        f"{sorted(summary.keys())}. Missing: {sorted(expected_keys - set(summary.keys()))}"
    )


def test_collector_summary_uses_unprefixed_keys() -> None:
    """``LLMMetricsCollector.get_summary()`` emits keys WITHOUT ``llm_``
    prefix. Callers must route through ``compute_llm_summary`` (which
    DOES emit the prefixed keys) before passing to update_source_columns
    — otherwise the unprefixed keys would raise ValueError. Pinning
    the collector's output shape so the contract stays explicit.
    """
    from chaoscypher_core.analytics.llm_metrics import LLMMetricsCollector

    collector = LLMMetricsCollector(provider="ollama", model="qwen3:30b")
    collector.record_attempt(
        success=True,
        was_retry=False,
        input_tokens=100,
        output_tokens=50,
        duration_ms=1500,
    )

    summary = collector.get_summary()

    # The shape that's wrong-for-source-row but right-for-other-callers.
    assert "total_calls" in summary
    assert "total_input_tokens" in summary
    assert "model" in summary
    # And critically: no llm_ prefix on these.
    assert "llm_total_calls" not in summary
    assert "llm_total_input_tokens" not in summary
    assert "llm_model" not in summary


def test_update_source_columns_raises_on_typoed_keys(
    sqlite_adapter,
) -> None:  # type: ignore[no-untyped-def]
    """Typo'd column names raise ValueError instead of being silently dropped.

    The previous ``update_source_llm_summary`` was a setattr loop guarded
    by ``hasattr(source, key)`` — it silently filtered unknown keys.
    The replacement (``update_source_columns``) raises ValueError so
    typos fail loudly.
    """
    source_id = _seed_source(sqlite_adapter)

    # Pre-fix this dict would have been silently dropped (no llm_ prefix
    # on the keys). After the fix, the unknown column names raise.
    unprefixed_summary = {
        "total_calls": 8,
        "successful_calls": 8,
    }

    with pytest.raises(ValueError, match="unknown field"):
        sqlite_adapter.update_source_columns(
            source_id=source_id,
            database_name="default",
            updates=unprefixed_summary,
        )


def test_update_source_columns_persists_llm_prefixed_keys(
    sqlite_adapter,
) -> None:  # type: ignore[no-untyped-def]
    """``llm_``-prefixed keys persist correctly via update_source_columns.

    This is the canonical CLI / extraction-finalizer call shape after
    the rip-and-replace of update_source_llm_summary.
    """
    source_id = _seed_source(sqlite_adapter)

    prefixed_summary = {
        "llm_total_calls": 8,
        "llm_successful_calls": 8,
        "llm_failed_calls": 0,
        "llm_total_input_tokens": 12_000,
        "llm_total_output_tokens": 17_921,
        "llm_model": "qwen3:30b-instruct",
    }

    sqlite_adapter.update_source_columns(
        source_id=source_id,
        database_name="default",
        updates=prefixed_summary,
    )

    import sqlite3

    db_path = sqlite_adapter.db_path
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT llm_total_calls, llm_total_input_tokens, llm_total_output_tokens, "
        "llm_model FROM sources WHERE id=?",
        (source_id,),
    ).fetchone()
    con.close()

    llm_total_calls, llm_total_input, llm_total_output, llm_model = row
    assert llm_total_calls == 8
    assert llm_total_input == 12_000
    assert llm_total_output == 17_921
    assert llm_model == "qwen3:30b-instruct"


def test_extract_finalizer_pipeline_persists_llm_metrics_to_source_row(
    sqlite_adapter,
) -> None:  # type: ignore[no-untyped-def]
    """The fixed extract→finalize sequence: persist call metrics,
    aggregate via compute_llm_summary, write the llm_-prefixed dict
    back to the source row. After Bug 7's fix, the CLI's extract step
    does exactly this — and the source row's llm_* columns reflect the
    real LLM activity.

    The test mirrors what the fixed extract code does without standing
    up a fake LLM provider: persist canned ``LLMCallMetric`` rows,
    invoke the same two adapter calls the CLI now makes, then
    round-trip the source row via raw SQL to check every column the
    UI displays.
    """
    source_id = _seed_source(sqlite_adapter, "src_e2e_metrics")

    # Mirror what extract_entities does — write per-call metric rows
    # (would normally come from the LLM provider's metrics_collector).
    # create_llm_call_metrics_batch takes dicts shaped like
    # ``LLMMetricsCollector.get_all_attempts()`` output, which the
    # adapter coerces into LLMCallMetric SQLModel rows internally.
    metrics = [
        {
            "id": f"call_{i}",
            "source_id": source_id,
            "database_name": "default",
            "operation_type": "entity_extraction",
            "call_sequence": 1,
            "provider": "ollama",
            "model": "qwen3:30b-instruct",
            "success": True,
            "was_retry": False,
            "input_tokens": 1_200,
            "output_tokens": 2_500,
            "duration_ms": 18_000,
        }
        for i in range(8)
    ]
    sqlite_adapter.create_llm_call_metrics_batch(metrics)

    # Fixed CLI flow: compute the llm_-prefixed summary, then persist it
    # via update_source_columns (matches extraction_finalizer + CLI extract).
    llm_summary = sqlite_adapter.compute_llm_summary(source_id=source_id, database_name="default")
    sqlite_adapter.update_source_columns(
        source_id=source_id,
        database_name="default",
        updates=llm_summary,
    )

    # Check the row reflects the LLM activity. Pre-fix this would have
    # all stayed at 0.
    import sqlite3

    con = sqlite3.connect(sqlite_adapter.db_path)
    row = con.execute(
        "SELECT llm_total_calls, llm_successful_calls, llm_total_input_tokens, "
        "llm_total_output_tokens, llm_total_duration_ms, llm_model "
        "FROM sources WHERE id=?",
        (source_id,),
    ).fetchone()
    con.close()

    total_calls, successful, in_tokens, out_tokens, duration, model = row
    assert total_calls == 8, (
        f"llm_total_calls={total_calls} after 8 LLM calls. If 0, Bug 7 "
        "regressed — extract is again writing unprefixed keys."
    )
    assert successful == 8
    assert in_tokens == 8 * 1_200
    assert out_tokens == 8 * 2_500
    assert duration == 8 * 18_000
    assert model == "qwen3:30b-instruct"


# ---------------------------------------------------------------------------
# Bug 8: quality counters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_increment_quality_counter_persists_to_source_row(
    sqlite_adapter,
) -> None:  # type: ignore[no-untyped-def]
    """The ``increment_quality_counter`` helper must actually increment
    the named column on the source row.

    Bug 8 root cause is unknown today — the symptom is all 40 counters
    stay at 0. This unit test pins the helper's contract so we can rule
    OUT a broken helper. If this passes but counters stay 0 in real
    runs, the bug is at the call sites (CLI doesn't call the helper).
    """
    from chaoscypher_core.services.quality.counters import (
        QualityCounter,
        increment_quality_counter,
    )

    source_id = _seed_source(sqlite_adapter, "src_quality_test")

    await increment_quality_counter(
        adapter=sqlite_adapter,
        source_id=source_id,
        database_name="default",
        counter=QualityCounter.ORPHAN_ENTITIES_FILTERED,
        n=5,
    )

    # Round-trip via raw SQL because get_file projects extraction_results
    # away but should still include all counter columns.
    import sqlite3

    db_path = sqlite_adapter.db_path
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT orphan_entities_filtered FROM sources WHERE id=?",
        (source_id,),
    ).fetchone()
    con.close()

    assert row[0] == 5, (
        f"increment_quality_counter wrote {row[0]} into "
        f"orphan_entities_filtered (expected 5). If 0, the helper is "
        "broken — Bug 8 lives in the helper, not the call sites."
    )


@pytest.mark.asyncio
async def test_increment_quality_counter_is_additive(
    sqlite_adapter,
) -> None:  # type: ignore[no-untyped-def]
    """Calling the helper twice should add to the existing counter
    (e.g., a chunk-level dedup increments 3 then 2 — final value 5).
    """
    from chaoscypher_core.services.quality.counters import (
        QualityCounter,
        increment_quality_counter,
    )

    source_id = _seed_source(sqlite_adapter, "src_quality_additive")

    await increment_quality_counter(
        adapter=sqlite_adapter,
        source_id=source_id,
        database_name="default",
        counter=QualityCounter.DEDUP_ENTITIES_MERGED,
        n=3,
    )
    await increment_quality_counter(
        adapter=sqlite_adapter,
        source_id=source_id,
        database_name="default",
        counter=QualityCounter.DEDUP_ENTITIES_MERGED,
        n=2,
    )

    import sqlite3

    db_path = sqlite_adapter.db_path
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT dedup_entities_merged FROM sources WHERE id=?",
        (source_id,),
    ).fetchone()
    con.close()

    assert row[0] == 5, (
        f"increment_quality_counter is not additive: got {row[0]} after two increments of 3 + 2."
    )


@pytest.mark.asyncio
async def test_increment_quality_counter_swallows_unknown_source(
    sqlite_adapter,
) -> None:  # type: ignore[no-untyped-def]
    """Per docstring: failures are logged and swallowed — the helper
    is best-effort and must not block the pipeline. Pin that
    contract so a future "throw on unknown source" refactor surfaces.
    """
    from chaoscypher_core.services.quality.counters import (
        QualityCounter,
        increment_quality_counter,
    )

    # No exception — just a logged warning.
    await increment_quality_counter(
        adapter=sqlite_adapter,
        source_id="does-not-exist",
        database_name="default",
        counter=QualityCounter.ORPHAN_ENTITIES_FILTERED,
        n=1,
    )


# ---------------------------------------------------------------------------
# Bug 8 audit: which counter sites are CLI-reachable and which are queue-only.
#
# Findings (May 2026): the 40 quality counters are wired in two halves:
#
#   * ~11 increment sites live under ``chaoscypher_core/services/`` —
#     these are reachable from BOTH the CLI's direct path and Cortex's
#     queue path.
#   * ~19 increment sites live under ``chaoscypher_core/operations/``
#     (indexing_handler, import_service, chunk_extraction_service,
#     extraction_finalizer) — these are invoked ONLY from the queue
#     worker. The CLI's index_file / extract_entities call shallower
#     Core helpers that bypass these paths entirely.
#
# Net effect: every CLI run has ~19 quality counters permanently at 0,
# regardless of input. The Cortex/Neuron flow gets the full set. A
# proper fix would migrate the operations/ increments down into the
# shared services/ helpers so both paths emit the same metrics.
#
# The test below pins this asymmetry as a known architectural gap so
# (a) anyone investigating "CLI counters are 0" finds this trail of
# breadcrumbs, and (b) when someone migrates the increments, the count
# baselines become canaries that surface the change.
# ---------------------------------------------------------------------------


def test_quality_counter_sites_audit() -> None:
    """Pin the current counter-increment site layout.

    Three things the test catches:

    1. A new increment site appears under ``services/`` — the CLI gets
       a free upgrade. Test should be updated to reflect the new total.
    2. An increment site moves from ``operations/`` to ``services/`` —
       Bug 8 progress. Counts shift; update the constants.
    3. A site is removed entirely — counts shrink. Verify that's
       intentional (counter retired? site no longer reachable?).
    """
    import re
    from pathlib import Path

    # __file__ = packages/cli/tests/test_*.py
    # parents[2] = packages/
    core_root = Path(__file__).parents[2] / "core" / "src" / "chaoscypher_core"
    assert core_root.exists(), f"core source root not found at {core_root}"

    def _count_increments(subdir: str) -> int:
        root = core_root / subdir
        total = 0
        for p in root.rglob("*.py"):
            text = p.read_text(encoding="utf-8")
            total += len(re.findall(r"counter=QualityCounter\.\w+", text))
        return total

    services_count = _count_increments("services")
    operations_count = _count_increments("operations")

    # CLI-reachable sites. Treat as a baseline — a real Bug 8 fix raises this.
    assert services_count >= 11, (
        f"services/ counter sites dropped to {services_count} (expected >=11). "
        "If sites were retired, update the baseline. Otherwise something "
        "regressed."
    )

    # Queue-only sites. The "Bug 8 gap" — every one of these is a CLI
    # blind spot. Test will fail if the gap GROWS (more sites added to
    # operations/ without a mirror in services/), prompting a design
    # conversation.
    # PR 2 Task 10 (2026-05-13): baseline raised from 19 to 20 for the
    # VISION_PAGES_TRUNCATED counter in vision_operations_service. The
    # per-page vision handler is queue-only by design (LLM-bound on
    # QUEUE_LLM); the CLI vision path remains separate and emits its
    # own warnings rather than incrementing this counter.
    # 2026-05-21: baseline raised from 20 to 22 for the extraction-honesty
    # finalizer counters added in the model-gates campaign — both fire
    # only on the queue-bound failure path, so the CLI deliberately does
    # not see them.
    # Wave 4-5 (2026-05-23): baseline raised from 22 to 23 for the
    # VISION_PAGES_SAMPLED_QUICK_MODE counter in indexing_handler.
    # The Quick/Full toggle work-queue sampling lives on the indexing
    # handler which is queue-only by design (same vision pipeline as
    # the truncation counter); the CLI extraction path doesn't share
    # the vision sampling code, so deliberately not mirrored.
    assert operations_count <= 23, (
        f"operations/ counter sites grew to {operations_count} (expected <=23). "
        "New queue-only counters extend the CLI's blind-spot gap. Either "
        "wire them in services/ instead (preferred), or update this baseline "
        "with a comment explaining why the CLI deliberately doesn't see them."
    )
