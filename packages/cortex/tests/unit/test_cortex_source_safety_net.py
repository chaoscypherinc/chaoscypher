# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cortex no longer owns source recovery — Neuron is the sole owner.

Audit ID #H1 / Decision 4. Before, the Cortex lifespan ran a
periodic loop that called ``SourceRecovery.reconcile_database``
for every database. Neuron's worker ran the same loop on its
own timer; the two reconcilers raced on the queue-check window
and double-incremented recovery_attempts.

These tests pin the new contract: the Cortex lifespan must not
expose a source-reconciliation loop or a starter for one.
"""

from __future__ import annotations

import chaoscypher_cortex.lifespan as lifespan_module


def test_cortex_does_not_export_source_reconcile_loop() -> None:
    """The deprecated _cortex_source_reconcile_loop symbol is gone."""
    assert not hasattr(lifespan_module, "_cortex_source_reconcile_loop"), (
        "Cortex must not own source recovery — Neuron is the sole owner. "
        "If you re-introduce this loop, document why in the lifespan "
        "module docstring + update the queue routing constants and tests."
    )


def test_cortex_does_not_export_source_recovery_starter() -> None:
    """The deprecated _start_source_recovery_loop starter is gone."""
    assert not hasattr(lifespan_module, "_start_source_recovery_loop")


def test_lifespan_does_not_reference_source_recovery_starter() -> None:
    """Source-string check: the lifespan module itself doesn't call the starter."""
    import inspect

    src = inspect.getsource(lifespan_module)
    assert "_start_source_recovery_loop(" not in src
    assert "_cortex_source_reconcile_loop(" not in src
