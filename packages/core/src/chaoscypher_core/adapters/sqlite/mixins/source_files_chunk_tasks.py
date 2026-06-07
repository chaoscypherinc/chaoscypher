# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Chunk Extraction Tasks Mixin for SqliteAdapter.

Composes chunk extraction task operations from focused sub-mixins:
- CRUD: Create, read, update, delete, list, batch create
- Lifecycle: Status transitions, progress tracking, LLM I/O tracking
- Recovery: Orphaned task detection, stuck source identification
- Analytics: Metrics, statistics, chart data, detail views

Part of the unified SourceStorageProtocol implementation.
"""

from chaoscypher_core.adapters.sqlite.mixins._chunk_tasks_analytics import (
    ChunkTasksAnalyticsMixin,
)
from chaoscypher_core.adapters.sqlite.mixins._chunk_tasks_crud import ChunkTasksCRUDMixin
from chaoscypher_core.adapters.sqlite.mixins._chunk_tasks_lifecycle import (
    ChunkTasksLifecycleMixin,
)
from chaoscypher_core.adapters.sqlite.mixins._chunk_tasks_recovery import (
    ChunkTasksRecoveryMixin,
)


class SourceChunkTasksMixin(
    ChunkTasksCRUDMixin,
    ChunkTasksLifecycleMixin,
    ChunkTasksRecoveryMixin,
    ChunkTasksAnalyticsMixin,
):
    """Mixin providing chunk extraction task operations for SQLite storage.

    Composes all chunk task functionality from focused sub-mixins:

    - ``ChunkTasksCRUDMixin``: Create, read, update, delete, list, batch create
    - ``ChunkTasksLifecycleMixin``: Status transitions (queue, start, complete, fail),
      progress summaries, timing stats, LLM I/O tracking
    - ``ChunkTasksRecoveryMixin``: Orphaned task detection, stuck source recovery
    - ``ChunkTasksAnalyticsMixin``: Paginated task listing, chart data, detail views,
      aggregate statistics via SQL aggregates

    Note: This mixin contributes to the unified SourceStorageProtocol.
    """
