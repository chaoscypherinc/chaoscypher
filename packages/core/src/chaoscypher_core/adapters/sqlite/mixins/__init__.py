# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SqliteAdapter Mixins.

Protocol-specific mixins for SQLite storage adapter:
- WorkflowsMixin: Workflow definition storage operations
- WorkflowExecutionsMixin: Workflow execution tracking operations (ISP-separated)
- ToolsMixin: Tool storage operations
- SourceLifecycleMixin: Source file CRUD operations (upload, get, list, delete)
- SourceIndexingMixin: Source status lifecycle, embeddings, extraction gating
- SourceExtractionJobsMixin: Extraction job CRUD and status transitions
- SourceChunkTasksMixin: Chunk task CRUD, analytics, and recovery
- SourceDeletionMixin: Cross-mixin source-delete cascade orchestrator
- SourcesMixin: Core source CRUD operations
- SourceTagsMixin: Tag CRUD and tag-to-source assignments
- SourceChunksMixin: Document chunk CRUD, batch operations, hierarchical grouping
- SourceCitationsMixin: Citations, stats, orphan detection, bulk clear
- StageProgressMixin: LLM stage progress CRUD (start/tick/complete/extras)
- ChatsMixin: Chat storage operations
- TriggersMixin: Trigger storage operations
- LLMMetricsMixin: LLM call metrics storage operations
- ExtractionSubmissionsMixin: MCP extraction partial result storage operations
- VisionPagesMixin: Per-page vision processing operations
"""

from chaoscypher_core.adapters.sqlite.mixins.chats import ChatsMixin
from chaoscypher_core.adapters.sqlite.mixins.extraction_submissions import (
    ExtractionSubmissionsMixin,
)
from chaoscypher_core.adapters.sqlite.mixins.llm_metrics import LLMMetricsMixin
from chaoscypher_core.adapters.sqlite.mixins.search_retry_queue import (
    SearchRetryQueueMixin,
)
from chaoscypher_core.adapters.sqlite.mixins.source_deletion import SourceDeletionMixin
from chaoscypher_core.adapters.sqlite.mixins.source_files import SourceLifecycleMixin
from chaoscypher_core.adapters.sqlite.mixins.source_files_chunk_tasks import SourceChunkTasksMixin
from chaoscypher_core.adapters.sqlite.mixins.source_files_extraction_jobs import (
    SourceExtractionJobsMixin,
)
from chaoscypher_core.adapters.sqlite.mixins.source_files_indexing import SourceIndexingMixin
from chaoscypher_core.adapters.sqlite.mixins.source_recovery_events import (
    SourceRecoveryEventsMixin,
)
from chaoscypher_core.adapters.sqlite.mixins.sources import SourcesMixin
from chaoscypher_core.adapters.sqlite.mixins.sources_chunks import SourceChunksMixin
from chaoscypher_core.adapters.sqlite.mixins.sources_citations import SourceCitationsMixin
from chaoscypher_core.adapters.sqlite.mixins.sources_tags import SourceTagsMixin
from chaoscypher_core.adapters.sqlite.mixins.stage_progress import StageProgressMixin
from chaoscypher_core.adapters.sqlite.mixins.system_state import SystemStateMixin
from chaoscypher_core.adapters.sqlite.mixins.tools import ToolsMixin
from chaoscypher_core.adapters.sqlite.mixins.triggers import TriggersMixin
from chaoscypher_core.adapters.sqlite.mixins.vision_pages import VisionPagesMixin
from chaoscypher_core.adapters.sqlite.mixins.workflow_executions import WorkflowExecutionsMixin
from chaoscypher_core.adapters.sqlite.mixins.workflows import WorkflowsMixin


__all__ = [
    "ChatsMixin",
    "ExtractionSubmissionsMixin",
    "LLMMetricsMixin",
    "SearchRetryQueueMixin",
    "SourceChunkTasksMixin",
    "SourceChunksMixin",
    "SourceCitationsMixin",
    "SourceDeletionMixin",
    "SourceExtractionJobsMixin",
    "SourceIndexingMixin",
    "SourceLifecycleMixin",
    "SourceRecoveryEventsMixin",
    "SourceTagsMixin",
    "SourcesMixin",
    "StageProgressMixin",
    "SystemStateMixin",
    "ToolsMixin",
    "TriggersMixin",
    "VisionPagesMixin",
    "WorkflowExecutionsMixin",
    "WorkflowsMixin",
]
