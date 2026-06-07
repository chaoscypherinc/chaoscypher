# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""recovery_attempts resets to 0 on successful stage transitions.

The counter must not monotonically climb across the source's lifetime.
A source that legitimately progresses through indexing → extracting →
extracted → committing → committed must not carry recovery attempts
accumulated during indexing into its extraction phase, because each
stage's worth of false positives compounds toward the 10-attempt
exhaustion cap even when no single stage triggered 10 recoveries.

Reset hooks:
- finalize_extraction_handler runs on entry to the 'extracted' stage
- import_service.commit (via _run_commit) runs on entry to 'committing'
"""

from __future__ import annotations


def _seed_extracting_source(adapter, source_id: str, recovery_attempts: int) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "extracting",
            "auto_analyze": True,
        }
    )
    # Simulate accumulated false-positive recoveries during indexing.
    for _ in range(recovery_attempts):
        adapter.increment_source_recovery_attempts(
            source_id=source_id, database_name=adapter.database_name
        )


def test_reset_source_recovery_attempts_writes_zero(in_memory_adapter) -> None:
    """The new adapter method zeros the counter atomically."""
    _seed_extracting_source(in_memory_adapter, "src-1", recovery_attempts=4)

    before = in_memory_adapter.get_source("src-1", database_name="default")["recovery_attempts"]
    assert before == 4

    in_memory_adapter.reset_source_recovery_attempts(source_id="src-1", database_name="default")

    after = in_memory_adapter.get_source("src-1", database_name="default")["recovery_attempts"]
    assert after == 0


def test_reset_is_idempotent_when_counter_is_already_zero(in_memory_adapter) -> None:
    """Calling reset on a source that was never incremented stays at 0."""
    _seed_extracting_source(in_memory_adapter, "src-2", recovery_attempts=0)

    in_memory_adapter.reset_source_recovery_attempts(source_id="src-2", database_name="default")

    after = in_memory_adapter.get_source("src-2", database_name="default")["recovery_attempts"]
    assert after == 0


def test_reset_scoped_to_database_name(in_memory_adapter) -> None:
    """Reset only touches the row in the specified database scope.

    Pins the multi-DB isolation that mirrors update_source_last_activity.
    A source ID could exist in two databases (cross-DB rare but legal);
    a reset must not bleed across.
    """
    _seed_extracting_source(in_memory_adapter, "src-3", recovery_attempts=7)

    # Reset a non-existent ID in the same DB → no error, no effect.
    in_memory_adapter.reset_source_recovery_attempts(
        source_id="does-not-exist", database_name="default"
    )

    counter = in_memory_adapter.get_source("src-3", database_name="default")["recovery_attempts"]
    assert counter == 7, "reset on a different ID must not zero this row"


def test_source_recovery_ports_protocol_includes_reset() -> None:
    """The composite port surfaces reset_source_recovery_attempts.

    Without this, recovery handlers that take ``adapter:
    SourceRecoveryPorts`` would not be able to call the reset, and
    structural type-checking on substituted adapters would not enforce
    the contract.
    """
    from chaoscypher_core.ports.source_recovery import SourceRecoveryPorts

    assert hasattr(SourceRecoveryPorts, "reset_source_recovery_attempts")
