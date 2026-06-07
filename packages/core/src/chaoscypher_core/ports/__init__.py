# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Port Protocols - Hexagonal Architecture Interface Definitions.

This module provides protocol definitions (ports) for the engine's hexagonal architecture.
All ports use typing.Protocol for structural typing, enabling dependency injection
and adapter-based implementations.

Ports define contracts that adapters must implement (SQLite, File, etc).

Example:
    from chaoscypher_core.ports import GraphRepository, SearchRepository

"""

from chaoscypher_core.plugins.base import PluginMetadata
from chaoscypher_core.ports.chunk import ChunkingProtocol
from chaoscypher_core.ports.db import DatabaseProtocol
from chaoscypher_core.ports.embedding import EmbeddingHealthStatus, EmbeddingProviderProtocol
from chaoscypher_core.ports.graph import GraphRepositoryProtocol
from chaoscypher_core.ports.index import IndexingProtocol
from chaoscypher_core.ports.llm import LLMProviderPort, TaskType
from chaoscypher_core.ports.retry import RetryPolicyPort
from chaoscypher_core.ports.search import SearchRepositoryProtocol
from chaoscypher_core.ports.stage_progress import StageProgressStorageProtocol
from chaoscypher_core.ports.storage_chats import ChatStorageProtocol
from chaoscypher_core.ports.storage_chunks import ChunkStorageProtocol
from chaoscypher_core.ports.storage_citations import CitationStorageProtocol
from chaoscypher_core.ports.storage_embeddings import EntityEmbeddingStorageProtocol
from chaoscypher_core.ports.storage_extraction_queue import ExtractionQueueStorageProtocol
from chaoscypher_core.ports.storage_extraction_submissions import (
    ExtractionSubmissionStorageProtocol,
)
from chaoscypher_core.ports.storage_graph_snapshot import (
    GraphBreakdownQueryProtocol,
    GraphSnapshotStorageProtocol,
    SnapshotStalenessInfo,
)
from chaoscypher_core.ports.storage_llm_metrics import LLMMetricsStorageProtocol
from chaoscypher_core.ports.storage_source_tags import SourceTagStorageProtocol
from chaoscypher_core.ports.storage_sources import SourceStorageProtocol
from chaoscypher_core.ports.storage_tools import ToolStorageProtocol
from chaoscypher_core.ports.storage_triggers import TriggerStorageProtocol
from chaoscypher_core.ports.storage_vision import (
    VisionJob,
    VisionPageDescription,
    VisionStorageProtocol,
)
from chaoscypher_core.ports.storage_workflow_executions import WorkflowExecutionStorageProtocol
from chaoscypher_core.ports.storage_workflows import WorkflowStorageProtocol
from chaoscypher_core.ports.transactional import TransactionalAdapterProtocol, TransactionalSession
from chaoscypher_core.ports.types import (
    ChatDict,
    MessageDict,
    SourceDict,
    StageProgressDict,
    SystemToolDict,
    ToolDict,
    TriggerDict,
    UserToolDict,
    WorkflowDict,
    WorkflowStepDict,
)


__all__ = [
    "ChatDict",
    "ChatStorageProtocol",
    "ChunkStorageProtocol",
    "ChunkingProtocol",
    "CitationStorageProtocol",
    "DatabaseProtocol",
    "EmbeddingHealthStatus",
    "EmbeddingProviderProtocol",
    "EntityEmbeddingStorageProtocol",
    "ExtractionQueueStorageProtocol",
    "ExtractionSubmissionStorageProtocol",
    "GraphBreakdownQueryProtocol",
    "GraphRepositoryProtocol",
    "GraphSnapshotStorageProtocol",
    "IndexingProtocol",
    "LLMMetricsStorageProtocol",
    "LLMProviderPort",
    "MessageDict",
    "PluginMetadata",
    "RetryPolicyPort",
    "SearchRepositoryProtocol",
    "SnapshotStalenessInfo",
    "SourceDict",
    "SourceStorageProtocol",
    "SourceTagStorageProtocol",
    "StageProgressDict",
    "StageProgressStorageProtocol",
    "SystemToolDict",
    "TaskType",
    "ToolDict",
    "ToolStorageProtocol",
    "TransactionalAdapterProtocol",
    "TransactionalSession",
    "TriggerDict",
    "TriggerStorageProtocol",
    "UserToolDict",
    "VisionJob",
    "VisionPageDescription",
    "VisionStorageProtocol",
    "WorkflowDict",
    "WorkflowExecutionStorageProtocol",
    "WorkflowStepDict",
    "WorkflowStorageProtocol",
]
