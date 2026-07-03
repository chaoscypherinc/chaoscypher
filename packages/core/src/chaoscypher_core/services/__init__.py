# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Engine Services - Business Logic Layer.

Clean public API with flat imports from nested structure.
"""

# Backup
from chaoscypher_core.services.backup import BackupService

# Chat
from chaoscypher_core.services.chat.engine.executor import ChatExecutor
from chaoscypher_core.services.chat.engine.research import ResearchAgent

# Chat & research
from chaoscypher_core.services.chat.management.service import ChatService

# Chat recovery
from chaoscypher_core.services.chat.recovery import reconcile_stuck_chats

# Compose
from chaoscypher_core.services.compose import (
    ComposeConfig,
    ComposeError,
    ComposeService,
    CompositionResult,
    MergerError,
    MergeStrategy,
    NamespaceMerger,
    PackageResolver,
    PackageSpec,
    ResolvedPackage,
    ResolverError,
)

# Diagnostics
from chaoscypher_core.services.diagnostics import DiagnosticCollector

# Events
from chaoscypher_core.services.events import event_bus
from chaoscypher_core.services.events.health.pause_evaluator import HealthPauseEvaluator
from chaoscypher_core.services.events.health.registry import HealthRegistry

# Export
from chaoscypher_core.services.export.management.service import CcxExporter
from chaoscypher_core.services.graph.engine.analytics import GraphAnalyticsService
from chaoscypher_core.services.graph.engine.stats import CountsService
from chaoscypher_core.services.graph.management.edge import EdgeService

# Graph operations
from chaoscypher_core.services.graph.management.node import NodeService
from chaoscypher_core.services.graph.management.source import SourceService
from chaoscypher_core.services.graph.management.template import TemplateService

# Lexicon service
from chaoscypher_core.services.lexicon import (
    AuthConfig,
    LexiconClient,
    LexiconClientError,
    PackageInfo,
)

# Package management
from chaoscypher_core.services.package import (
    ArchiveInfo,
    ArchiveSecurityError,
    extract_archive,
    format_size,
    get_archive_info,
)

# Quality scoring
from chaoscypher_core.services.quality import (
    EntityQualityScore,
    QualityScorer,
    RelationshipQualityScore,
    SourceQualityScore,
    calculate_entity_score,
    calculate_relationship_score,
    calculate_source_score,
)
from chaoscypher_core.services.search.engine.index import IndexingService

# Search & indexing
from chaoscypher_core.services.search.engine.search import SearchService
from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor
from chaoscypher_core.services.sources.engine.extraction.service import ExtractionService

# Source processing pipeline
from chaoscypher_core.services.sources.management.service import SourceProcessingService
from chaoscypher_core.services.sources.recovery import SourceRecovery
from chaoscypher_core.services.workflows.engine.executor import WorkflowExecutor

# Workflow ecosystem
from chaoscypher_core.services.workflows.management import (
    WorkflowExecutionService,
    WorkflowPortabilityService,
    WorkflowService,
    WorkflowStepsService,
)
from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService
from chaoscypher_core.services.workflows.tools.management.service import ToolService
from chaoscypher_core.services.workflows.triggers.engine.executor import TriggerExecutor
from chaoscypher_core.services.workflows.triggers.management.service import TriggerService


__all__ = [
    "ArchiveInfo",
    "ArchiveSecurityError",
    "AuthConfig",
    "BackupService",
    "CcxExporter",
    "ChatExecutor",
    "ChatService",
    "ComposeConfig",
    "ComposeError",
    "ComposeService",
    "CompositionResult",
    "CountsService",
    "DiagnosticCollector",
    "EdgeService",
    "EntityProcessor",
    "EntityQualityScore",
    "ExtractionService",
    "GraphAnalyticsService",
    "HealthPauseEvaluator",
    "HealthRegistry",
    "IndexingService",
    "LexiconClient",
    "LexiconClientError",
    "MergeStrategy",
    "MergerError",
    "NamespaceMerger",
    "NodeService",
    "PackageInfo",
    "PackageResolver",
    "PackageSpec",
    "QualityScorer",
    "RelationshipQualityScore",
    "ResearchAgent",
    "ResolvedPackage",
    "ResolverError",
    "SearchService",
    "SourceCommitService",
    "SourceProcessingService",
    "SourceQualityScore",
    "SourceRecovery",
    "SourceService",
    "TemplateService",
    "ToolExecutorService",
    "ToolService",
    "TriggerExecutor",
    "TriggerService",
    "WorkflowExecutionService",
    "WorkflowExecutor",
    "WorkflowPortabilityService",
    "WorkflowService",
    "WorkflowStepsService",
    "calculate_entity_score",
    "calculate_relationship_score",
    "calculate_source_score",
    "event_bus",
    "extract_archive",
    "format_size",
    "get_archive_info",
    "reconcile_stuck_chats",
]
