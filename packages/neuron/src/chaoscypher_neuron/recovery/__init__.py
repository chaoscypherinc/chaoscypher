# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Recovery modules for the Neuron worker.

Provides startup recovery routines that detect and repair orphaned
extraction tasks and sources stuck in transient states after an
unexpected worker shutdown.
"""

from .extraction import recover_orphaned_extraction_tasks
from .sources import recover_stuck_sources


__all__ = [
    "recover_orphaned_extraction_tasks",
    "recover_stuck_sources",
]
