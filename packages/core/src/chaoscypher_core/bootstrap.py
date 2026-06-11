# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Bootstrap - Unified dependency injection for Chaos Cypher.

Provides a single entry point for initializing the service layer,
regardless of whether running in web, CLI, or worker context.

This module uses lazy imports to keep startup fast.

Usage:
    from chaoscypher_core import Engine

    with Engine("./data/databases/default", initialize_db=True) as engine:
        # Graph services
        nodes = engine.node_service.list_nodes()
        templates = engine.template_service.list_templates()

        # Pipeline services
        chunks = await engine.chunking_service.create_chunks(
            source_id="src_001", full_text="..."
        )
        await engine.indexing_service.create_index(source_id="src_001")
        results = engine.search_service.keyword_search("query")

        # LLM and extraction (lazy - initialized on first access)
        response = await engine.llm_provider.chat(messages=[...])
        extraction = await engine.extraction_service.finalize_distributed_extraction(...)
        commit = await engine.commit_service.commit(...)
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import structlog


_T = TypeVar("_T")


if TYPE_CHECKING:
    from chaoscypher_core.adapters.llm.provider import LLMProvider
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.models import (
        AnalysisDepth,
        BatchEmbedResult,
        ChunkingResult,
        DatabaseStats,
        Edge,
        EdgeCreate,
        EdgeUpdate,
        EmbedResult,
        EngineSearchResult,
        HealthReport,
        IndexingResult,
        LLMChatResponse,
        Node,
        NodeCreate,
        NodeUpdate,
        PaginatedResult,
        ProcessingResult,
        ProgressCallback,
        RebuildResult,
        SearchMode,
        Template,
        TemplateCreate,
        TemplateUpdate,
    )
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol
    from chaoscypher_core.ports.retry import RetryPolicyPort
    from chaoscypher_core.services import EdgeService, NodeService, TemplateService
    from chaoscypher_core.services.search.engine.index import IndexingService
    from chaoscypher_core.services.search.engine.search import SearchService
    from chaoscypher_core.services.sources.engine.commit.service import (
        SourceCommitService,
    )
    from chaoscypher_core.services.sources.engine.extraction.service import (
        ExtractionService,
    )
    from chaoscypher_core.services.workflows.management import WorkflowService
    from chaoscypher_core.settings import EngineSettings
    from chaoscypher_core.utils.chunk import ChunkingService


logger = structlog.get_logger(__name__)


class Engine:
    """Chaos Cypher Engine - Single entry point for all services.

    Wires up storage adapters, repositories, and services with proper
    dependency injection. Use as a context manager or call close() when done.

    Args:
        data_dir: Path to database directory (e.g., "./data/databases/default").
        database: Database name shorthand (inferred from data_dir if not provided).
        settings: Optional pre-configured EngineSettings. When provided, used
            instead of creating a default instance. ``current_database`` and
            ``paths.data_dir`` are still set from the other arguments.
        initialize_db: When True, create tables before connecting. Useful for
            CLI and first-run scenarios where the database may not exist yet.

    Attributes:
        database_name: Name of the current database.
        data_dir: Path to the database directory.
        settings: Engine settings instance.
        storage_adapter: SQLite storage adapter.
        graph_repository: Knowledge graph repository.
        search_repository: Search/vector repository.
        node_service: Node CRUD service.
        edge_service: Edge CRUD service.
        template_service: Template CRUD service.
        workflow_service: Workflow CRUD service.
        chunking_service: Document chunking service.
        indexing_service: Embedding generation service.
        search_service: Keyword, semantic, and hybrid search service.
        llm_provider: Queue-free LLM provider (lazy, initialized on first access).
        extraction_service: Entity extraction service (lazy, initialized on first access).
        commit_service: Source commit service (lazy, initialized on first access).

    Convenience methods return domain models with attribute access:
        add_node / add_edge: Quick graph building with get-or-create templates.
        create_template / get_template / list_templates / update_template / delete_template
        create_node / get_node / list_nodes / update_node / delete_node
        create_edge / get_edge / list_edges / update_edge / delete_edge
        chunk_document: Chunk text and store for RAG search.
        commit: Extract entities from chunks and write to graph.
        process_document / add_document / add_documents: Full extraction pipeline.
        search: Hybrid, semantic, or keyword search.
        index_source: Generate embeddings for a source's chunks.
        rebuild_indexes: Rebuild all search indexes.

    Synchronous wrappers (for scripts, notebooks, non-async contexts):
        search_sync, chat_sync, embed_sync, batch_embed_sync,
        add_document_sync, add_documents_sync, process_document_sync

    Example:
        # Minimal — database name + optional inline configuration
        with Engine(database="demo", provider="openai", api_key="sk-...") as engine:
            alice = engine.add_node("Person", "Alice")
            bob = engine.add_node("Person", "Bob")
            engine.add_edge("knows", alice, bob)
            results = engine.search_sync("people")
    """

    database_name: str
    data_dir: Path
    settings: EngineSettings
    storage_adapter: SqliteAdapter
    graph_repository: GraphRepository
    search_repository: SearchRepository
    node_service: NodeService
    edge_service: EdgeService
    template_service: TemplateService
    workflow_service: WorkflowService
    chunking_service: ChunkingService
    indexing_service: IndexingService
    search_service: SearchService

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        database: str | None = None,
        settings: EngineSettings | None = None,
        initialize_db: bool = True,
        provider: str | None = None,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Create fully wired engine with all services.

        Args:
            data_dir: Path to database directory. If omitted, auto-resolved
                from ``database`` name + XDG data directory. Mutually
                exclusive with ``database``.
            database: Database name shorthand. Resolves to
                ``{xdg_data_dir}/databases/{database}``. Mutually
                exclusive with ``data_dir``.
            settings: Optional pre-configured EngineSettings. Mutually
                exclusive with ``provider``/``api_key``/kwargs.
            initialize_db: Create tables automatically. Set to False to skip.
            provider: LLM provider name ("openai", "anthropic", "gemini",
                "ollama"). Same as :meth:`ChaosCypher.configure`.
            api_key: API key for the selected provider. Provider is
                auto-detected from key prefix if not given explicitly.
            **kwargs: LLMSettings fields or top-level aliases
                (``embedding_model``, ``chunk_size``, etc.). Same as
                :meth:`ChaosCypher.configure`.

        Raises:
            ValueError: If both ``data_dir`` and ``database`` are provided,
                or both ``settings`` and configure-style kwargs are provided.

        Example:
            # Minimal — inherits ChaosCypher.configure() settings
            engine = Engine(database="mydb")

            # With inline configuration (no EngineSettings needed)
            engine = Engine(database="mydb", provider="openai", api_key="sk-...")
        """
        # Import heavy dependencies only when creating engine
        # This keeps module load time fast
        from chaoscypher_core.adapters.sqlite import SqliteAdapter
        from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
        from chaoscypher_core.services import EdgeService, NodeService, TemplateService
        from chaoscypher_core.services.search.engine.index import IndexingService
        from chaoscypher_core.services.search.engine.search import SearchService
        from chaoscypher_core.services.workflows.management import WorkflowService
        from chaoscypher_core.utils.chunk import ChunkingService

        self._closed = False
        self._llm_provider: LLMProvider | None = None
        self._extraction_service: ExtractionService | None = None
        self._commit_service: SourceCommitService | None = None
        self._retry_policy: RetryPolicyPort | None = None
        self._template_cache: dict[tuple[str, str], str] = {}  # (name, type) -> id

        if data_dir is not None and database is not None:
            raise ValueError(
                "Pass either 'data_dir' (explicit path) or 'database' (name), not both."
            )

        # Build settings from configure-style kwargs, explicit settings, or global defaults
        has_configure_kwargs = provider is not None or api_key is not None or kwargs
        if has_configure_kwargs and settings is not None:
            raise ValueError(
                "Pass either 'settings' or configure-style kwargs "
                "(provider, api_key, ...), not both."
            )

        if has_configure_kwargs:
            from chaoscypher_core.facade import _build_settings_from_kwargs

            settings = _build_settings_from_kwargs(provider=provider, api_key=api_key, **kwargs)
        elif settings is None:
            # Inherit from ChaosCypher.configure() if set, else fresh defaults
            from chaoscypher_core.facade import _get_default_settings

            settings = _get_default_settings()

        # Resolve data_dir: explicit path > database kwarg > default
        if data_dir is not None:
            data_dir = Path(data_dir)
        else:
            database = database or settings.current_database or "default"
            data_dir = Path(settings.paths.data_dir) / "databases" / database

        self.database_name = database or data_dir.name
        self.data_dir = data_dir

        logger.debug(
            "engine_creating",
            data_dir=str(data_dir),
            database_name=self.database_name,
        )

        # Deep copy to avoid mutating the caller's shared settings object
        self.settings = settings.model_copy(deep=True)
        self.settings.current_database = self.database_name
        self.settings.paths.data_dir = str(data_dir.parent.parent)  # Go up from databases/name

        # Ensure directories exist
        data_dir.mkdir(parents=True, exist_ok=True)

        # Optionally initialize the database schema.
        #
        # Build it through the Alembic runner — never a bare create_all. The
        # tier-aware runner stamps alembic_version (HEAD on a fresh install),
        # so a CLI-first-created DB later opened by Cortex/Neuron is already
        # at HEAD instead of being mis-stamped at the baseline and replaying
        # 0002→HEAD against present schema (migration crash / cross-tool
        # drift). This matches the Cortex/Neuron init_database path.
        if initialize_db:
            from chaoscypher_core.database.migrations.startup import (
                run_startup_migrations,
            )

            run_startup_migrations(
                data_dir / "app.db",
                auto_apply_destructive=self.settings.migrations.auto_apply_destructive,
            )

        # Create storage adapter
        db_path = str(data_dir / "app.db")
        self.storage_adapter = SqliteAdapter(db_path=db_path, database_name=self.database_name)
        self.storage_adapter.connect()

        # Initialize repositories
        from chaoscypher_core.adapters.sqlite.session import get_session

        app_db_path = str(data_dir / "app.db")
        self._graph_session = get_session(app_db_path)
        self.graph_repository = GraphRepository(self._graph_session, self.database_name)
        from chaoscypher_core.adapters.sqlite.engine import get_engine as get_db_engine

        app_db_engine = get_db_engine(Path(data_dir) / "app.db")
        self.search_repository = SearchRepository(
            engine=app_db_engine,
            vector_dim=self.settings.search.vector_dimensions,
            embedding_model=self.settings.embedding.model,
        )

        # Create embedding provider via factory
        from chaoscypher_core.adapters.embedding import create_embedding_provider

        self.embedding_service: EmbeddingProviderProtocol = create_embedding_provider(self.settings)

        # Graph services
        self.node_service = NodeService(
            graph_repository=self.graph_repository,
            search_repository=self.search_repository,
            settings=self.settings,
        )
        self.edge_service = EdgeService(graph_repository=self.graph_repository)
        self.template_service = TemplateService(graph_repository=self.graph_repository)
        self.workflow_service = WorkflowService(
            storage=self.storage_adapter,
            database_name=self.database_name,
        )

        # Pipeline services
        self.chunking_service = ChunkingService(
            settings=self.settings,
            repository=self.storage_adapter,
        )
        self.indexing_service = IndexingService(
            repository=self.storage_adapter,
            settings=self.settings,
            embedding_service=self.embedding_service,
        )
        self.search_service = SearchService(
            search_repository=self.search_repository,
            graph_repository=self.graph_repository,
            indexing_repository=self.storage_adapter,
            source_repository=self.storage_adapter,
            sources_repository=self.storage_adapter,
            settings=self.settings,
            default_embedding_callback=self._default_embed_callback,
        )

        logger.info(
            "engine_created",
            database_name=self.database_name,
            data_dir=str(data_dir),
            adapter_type=type(self.storage_adapter).__name__,
        )

    def close(self) -> None:
        """Disconnect adapters and cleanup resources.

        Safe to call multiple times - subsequent calls are no-ops.
        """
        if self._closed:
            return

        logger.debug("engine_closing", database_name=self.database_name)

        if self._graph_session:
            try:
                self._graph_session.close()
            except Exception as e:
                logger.warning(
                    "graph_session_close_failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

        if self.storage_adapter:
            try:
                self.storage_adapter.disconnect()
            except Exception as e:
                logger.warning(
                    "storage_adapter_disconnect_failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

        self._closed = True
        logger.info("engine_closed", database_name=self.database_name)

    def __enter__(self) -> Engine:
        """Context manager entry - returns self."""
        return self

    def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        """Context manager exit - closes the engine."""
        self.close()

    async def __aenter__(self) -> Engine:
        """Async context manager entry - returns self."""
        return self

    async def __aexit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        """Async context manager exit - closes the engine."""
        self.close()

    def get_stats(self) -> DatabaseStats:
        """Get database statistics.

        Returns:
            DatabaseStats model with node, edge, template counts.

        """
        from chaoscypher_core.models import DatabaseStats

        return DatabaseStats(
            database_name=self.database_name,
            data_dir=str(self.data_dir),
            nodes=self.graph_repository.count_nodes(),
            edges=self.graph_repository.count_edges(),
            templates=self.graph_repository.count_templates(database_name=self.database_name),
        )

    async def check_health(self) -> HealthReport:
        """Check health of configured LLM providers.

        Verifies that chat and embedding providers are reachable
        and functioning correctly.

        Returns:
            HealthReport with chat and embedding health results.

        Example:
            health = await engine.check_health()
            if health.chat.status == "healthy":
                print(f"Chat OK ({health.chat.response_time_ms}ms)")

        """
        return await self.llm_provider.check_health()

    @property
    def retry_policy(self) -> RetryPolicyPort:
        """Shared ``RetryPolicyPort`` instance for SQLite-lock-sensitive work.

        Lazily constructs a :class:`DbLockRetryPolicy` on first access and
        caches it. Services that accept a ``RetryPolicyPort`` via DI
        receive this instance when constructed through the Engine.

        Returns:
            The shared retry policy.
        """
        if self._retry_policy is None:
            from chaoscypher_core.utils.retry import DbLockRetryPolicy

            self._retry_policy = DbLockRetryPolicy()
        return self._retry_policy

    @property
    def embedding_provider(self) -> EmbeddingProviderProtocol:
        """Convenience alias for :attr:`embedding_service`.

        Named to match the ``EmbeddingProviderProtocol`` port terminology
        used by services that consume the port. Returns the same instance
        as :attr:`embedding_service`.
        """
        return self.embedding_service

    @property
    def llm_provider(self) -> LLMProvider:
        """Queue-free LLM provider for chat, embeddings, and tool execution.

        Lazily initialized on first access to avoid startup cost for
        graph-only usage. Uses empty managers dict (no tool execution
        support). For tool execution, create an LLMProvider manually
        with appropriate managers.

        Returns:
            LLMProvider instance wired with engine settings.
        """
        if self._llm_provider is None:
            from chaoscypher_core.adapters.llm.provider import LLMProvider

            self._llm_provider = LLMProvider(settings=self.settings)
        return self._llm_provider

    @property
    def extraction_service(self) -> ExtractionService:
        """Entity extraction service for deduplication and template matching.

        Lazily initialized on first access. Requires LLM provider for
        AI-powered operations (embedding generation, deduplication).

        Returns:
            ExtractionService instance wired with engine dependencies.
        """
        if self._extraction_service is None:
            from chaoscypher_core.services.sources.engine.extraction.service import (
                ExtractionService,
            )

            self._extraction_service = ExtractionService(
                graph_repository=self.graph_repository,
                llm_provider=self.llm_provider,
                settings=self.settings,
                embedding_service=self.embedding_service,
            )
        return self._extraction_service

    @property
    def commit_service(self) -> SourceCommitService:
        """Source commit service for writing extraction results to the graph.

        Lazily initialized on first access. Orchestrates template creation,
        node/edge creation, citation tracking, and search indexing.

        Returns:
            SourceCommitService instance wired with engine dependencies.
        """
        if self._commit_service is None:
            from chaoscypher_core.adapters.sqlite.repos import GraphRepository
            from chaoscypher_core.services.sources.engine.commit.service import (
                SourceCommitService,
            )

            # Build a transient ``GraphRepository`` bound to the storage
            # adapter's SafeSession so commit's storage-side writes (start_commit,
            # complete_commit, source row updates) participate in the SAME
            # transaction as commit's graph-side writes (INSERT INTO graph_nodes,
            # graph_edges, graph_templates). The Engine's default
            # ``self.graph_repository`` lives on ``self._graph_session`` — a
            # separate SafeSession on the same SQLite engine — and inside
            # ``adapter.transaction()`` the two sessions race the SQLite writer
            # lock: storage_adapter's flush acquires the lock first, then
            # graph_repository's first INSERT hits SQLITE_BUSY and the whole
            # commit cascades with PendingRollbackError.
            #
            # The CLI's CLISourceProcessingService.commit_to_graph applied this
            # same fix as a local override; pinning it here at construction
            # makes MCP's finalize_extraction (and any future consumer of
            # engine.commit_service) safe by default. Cortex's queue worker
            # doesn't trip this because each task builds a fresh Engine per
            # dispatch — but in-process consumers keep the Engine alive across
            # stages, and that's where the dual-session race lives.
            commit_graph_repository = GraphRepository(
                self.storage_adapter.session,  # type: ignore[arg-type]
                self.database_name,
            )

            self._commit_service = SourceCommitService(
                graph_repository=commit_graph_repository,
                source_repository=self.storage_adapter,
                sources_repository=self.storage_adapter,
                indexing_repository=self.storage_adapter,
                search_repository=self.search_repository,
                settings=self.settings,
                retry_policy=self.retry_policy,
                embedding_provider=self.embedding_service,
            )
        return self._commit_service

    # ========================================================================
    # Convenience Methods (return domain models, not dicts)
    # ========================================================================

    async def _default_embed_callback(self, text: str) -> list[float]:
        """Default embedding callback for SearchService.

        Uses the configured embedding provider for search.
        """
        result = await self.embedding_service.embed(text)
        embedding: list[float] = result.embedding
        return embedding

    @staticmethod
    def _to_model(model_class: type[_T], data: dict[str, Any]) -> _T:
        """Convert a service dict to a domain model, filtering extra keys.

        Args:
            model_class: Pydantic model class to construct.
            data: Dict from a service method.

        Returns:
            Instance of model_class.

        """
        known = model_class.model_fields  # type: ignore[attr-defined]
        return model_class(**{k: data[k] for k in known if k in data})

    def _to_paginated(self, model_class: type, result: dict[str, Any]) -> PaginatedResult:
        """Convert a paginated service dict to a PaginatedResult.

        Args:
            model_class: Pydantic model class for items in data.
            result: Dict with 'data' and 'pagination' keys.

        Returns:
            PaginatedResult with model instances.

        """
        from chaoscypher_core.models import PaginatedResult

        pagination = result["pagination"]
        return PaginatedResult(
            data=[self._to_model(model_class, item) for item in result["data"]],
            **pagination,
        )

    # -- Templates -----------------------------------------------------------

    def create_template(self, template_create: TemplateCreate) -> Template:
        """Create a template and return a Template model.

        Args:
            template_create: Template creation data.

        Returns:
            Created Template with attribute access (e.g., ``template.id``).

        """
        from chaoscypher_core.models import Template

        result = self.template_service.create_template(template_create)
        return self._to_model(Template, result)

    def get_template(self, template_id: str) -> Template:
        """Get a template by ID.

        Args:
            template_id: Template identifier.

        Returns:
            Template model.

        Raises:
            NotFoundError: If template not found.

        """
        from chaoscypher_core.models import Template

        result = self.template_service.get_template(template_id)
        return self._to_model(Template, result)

    def list_templates(
        self,
        template_type: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedResult:
        """List templates with pagination.

        Args:
            template_type: Filter by 'node' or 'edge' (optional).
            page: Page number (1-based).
            page_size: Items per page.

        Returns:
            PaginatedResult containing Template models.

        """
        from chaoscypher_core.models import Template

        result = self.template_service.list_templates(
            template_type=template_type, page=page, page_size=page_size
        )
        return self._to_paginated(Template, result)

    def update_template(self, template_id: str, template_update: TemplateUpdate) -> Template:
        """Update a template.

        Args:
            template_id: Template identifier.
            template_update: Fields to update.

        Returns:
            Updated Template model.

        """
        from chaoscypher_core.models import Template

        result = self.template_service.update_template(template_id, template_update)
        return self._to_model(Template, result)

    def delete_template(self, template_id: str) -> None:
        """Delete a template.

        Args:
            template_id: Template identifier.

        """
        self.template_service.delete_template(template_id)

    # -- Nodes ---------------------------------------------------------------

    def create_node(self, node_create: NodeCreate) -> Node:
        """Create a node with template validation and search indexing.

        Args:
            node_create: Node creation data.

        Returns:
            Created Node with attribute access (e.g., ``node.id``).

        Raises:
            NotFoundError: If template not found.

        """
        from chaoscypher_core.models import Node

        result = self.node_service.create_node(node_create)
        return self._to_model(Node, result)

    def get_node(self, node_id: str) -> Node:
        """Get a node by ID.

        Args:
            node_id: Node identifier.

        Returns:
            Node model.

        Raises:
            NotFoundError: If node not found.

        """
        from chaoscypher_core.models import Node

        result = self.node_service.get_node(node_id)
        return self._to_model(Node, result)

    def list_nodes(
        self,
        template_id: str | None = None,
        source_ids: list[str] | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedResult:
        """List nodes with pagination.

        Args:
            template_id: Filter by template (optional).
            source_ids: Filter by source document IDs (optional).
            page: Page number (1-based).
            page_size: Items per page.

        Returns:
            PaginatedResult containing Node models.

        """
        from chaoscypher_core.models import Node

        result = self.node_service.list_nodes(
            template_id=template_id,
            source_ids=source_ids,
            page=page,
            page_size=page_size,
        )
        return self._to_paginated(Node, result)

    def update_node(self, node_id: str, node_update: NodeUpdate) -> Node:
        """Update a node.

        Args:
            node_id: Node identifier.
            node_update: Fields to update.

        Returns:
            Updated Node model.

        """
        from chaoscypher_core.models import Node

        result = self.node_service.update_node(node_id, node_update)
        return self._to_model(Node, result)

    def delete_node(self, node_id: str) -> None:
        """Delete a node and remove from search index.

        Args:
            node_id: Node identifier.

        """
        self.node_service.delete_node(node_id)

    # -- Edges ---------------------------------------------------------------

    def create_edge(self, edge_create: EdgeCreate) -> Edge:
        """Create an edge between two nodes.

        Args:
            edge_create: Edge creation data.

        Returns:
            Created Edge with attribute access (e.g., ``edge.id``).

        Raises:
            NotFoundError: If source/target node not found.
            ValidationError: If source equals target.

        """
        from chaoscypher_core.models import Edge

        result = self.edge_service.create_edge(edge_create)
        return self._to_model(Edge, result)

    def get_edge(self, edge_id: str) -> Edge:
        """Get an edge by ID.

        Args:
            edge_id: Edge identifier.

        Returns:
            Edge model.

        Raises:
            NotFoundError: If edge not found.

        """
        from chaoscypher_core.models import Edge

        result = self.edge_service.get_edge(edge_id)
        return self._to_model(Edge, result)

    def list_edges(
        self,
        source_node_id: str | None = None,
        target_node_id: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedResult:
        """List edges with pagination.

        Args:
            source_node_id: Filter by source node (optional).
            target_node_id: Filter by target node (optional).
            page: Page number (1-based).
            page_size: Items per page.

        Returns:
            PaginatedResult containing Edge models.

        """
        from chaoscypher_core.models import Edge

        result = self.edge_service.list_edges(
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            page=page,
            page_size=page_size,
        )
        return self._to_paginated(Edge, result)

    def update_edge(self, edge_id: str, edge_update: EdgeUpdate) -> Edge:
        """Update an edge.

        Args:
            edge_id: Edge identifier.
            edge_update: Fields to update.

        Returns:
            Updated Edge model.

        """
        from chaoscypher_core.models import Edge

        result = self.edge_service.update_edge(edge_id, edge_update)
        return self._to_model(Edge, result)

    def delete_edge(self, edge_id: str) -> None:
        """Delete an edge.

        Args:
            edge_id: Edge identifier.

        """
        self.edge_service.delete_edge(edge_id)

    # -- Quick Graph Building ------------------------------------------------

    def _get_or_create_template(self, name: str, template_type: str) -> str:
        """Get or create a template by name, returning its ID.

        Uses an instance-level cache to avoid repeated DB lookups.

        Args:
            name: Template name.
            template_type: 'node' or 'edge'.

        Returns:
            Template ID.

        """
        cache_key = (name, template_type)
        if cache_key in self._template_cache:
            return self._template_cache[cache_key]

        from chaoscypher_core.models import TemplateCreate

        # Search existing templates (bulk-populate cache to reduce future DB lookups)
        result = self.template_service.list_templates(
            template_type=template_type,
            page=1,
            page_size=self.settings.pagination.graph_list_page_size,
        )
        for t in result["data"]:
            self._template_cache[(t["name"], template_type)] = t["id"]
        if cache_key in self._template_cache:
            return self._template_cache[cache_key]

        # Create new template
        created = self.template_service.create_template(
            TemplateCreate(name=name, template_type=template_type)
        )
        self._template_cache[cache_key] = created["id"]
        return str(created["id"])

    def add_node(
        self,
        template_name: str,
        label: str,
        *,
        properties: dict[str, Any] | None = None,
        source_id: str | None = None,
    ) -> Node:
        """Create a node with get-or-create template semantics.

        If a node template with ``template_name`` doesn't exist, it is
        created automatically. Subsequent calls with the same name reuse
        the existing template (cached per Engine instance).

        Args:
            template_name: Name of the node template (e.g., 'Person').
            label: Node label.
            properties: Optional node properties dict.
            source_id: Optional source document ID.

        Returns:
            Created Node model with attribute access.

        Example:
            alice = engine.add_node("Person", "Alice", properties={"role": "Engineer"})
            print(alice.id, alice.label)

        """
        from chaoscypher_core.models import Node, NodeCreate

        template_id = self._get_or_create_template(template_name, "node")
        result = self.node_service.create_node(
            NodeCreate(
                template_id=template_id,
                label=label,
                properties=properties or {},
                source_id=source_id,
            )
        )
        return self._to_model(Node, result)

    def add_edge(
        self,
        template_name: str,
        source: Node | str,
        target: Node | str,
        *,
        label: str | None = None,
        properties: dict[str, Any] | None = None,
        source_id: str | None = None,
    ) -> Edge:
        """Create an edge with get-or-create template semantics.

        If an edge template with ``template_name`` doesn't exist, it is
        created automatically. Accepts Node models or string IDs for
        source and target.

        Args:
            template_name: Name of the edge template (e.g., 'knows').
            source: Source node (Node model or node ID string).
            target: Target node (Node model or node ID string).
            label: Edge label. Defaults to ``template_name`` if omitted.
            properties: Optional edge properties dict.
            source_id: Optional source document ID.

        Returns:
            Created Edge model with attribute access.

        Example:
            engine.add_edge("knows", alice, bob)
            engine.add_edge("works_at", alice, "node_id_123", label="employed by")

        """
        from chaoscypher_core.models import Edge, EdgeCreate

        template_id = self._get_or_create_template(template_name, "edge")
        source_node_id = source.id if hasattr(source, "id") else str(source)
        target_node_id = target.id if hasattr(target, "id") else str(target)

        result = self.edge_service.create_edge(
            EdgeCreate(
                template_id=template_id,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                label=label or template_name,
                properties=properties or {},
                source_id=source_id,
            )
        )
        return self._to_model(Edge, result)

    # ========================================================================
    # Indexing & Search
    # ========================================================================

    async def index_source(self, source_id: str) -> IndexingResult:
        """Generate embeddings for all chunks of a source document.

        Wraps ``indexing_service.create_index()`` and returns a typed
        result model.

        Args:
            source_id: Source document identifier.

        Returns:
            IndexingResult with chunks_count, embedding_model, and
            embedding_dimensions.

        Raises:
            NotFoundError: If no chunks exist for the given source_id.
                Call ``chunk_document()`` first, or use ``add_document()``
                for the full pipeline.

        """
        from chaoscypher_core.models import IndexingResult

        small_chunks = self.chunking_service.get_small_chunks(source_id)
        if not small_chunks:
            from chaoscypher_core.exceptions import NotFoundError

            raise NotFoundError(
                "source chunks",
                source_id,
            )

        raw = await self.indexing_service.create_index(source_id=source_id)
        return self._to_model(IndexingResult, raw)

    def rebuild_indexes(self) -> RebuildResult:
        """Rebuild all keyword, vector, and chunk search indexes.

        Rebuilds graph node indexes (FTS + vector) and re-indexes all
        committed document chunk embeddings into the vector search index.

        Returns:
            RebuildResult with total_nodes, nodes_with_embeddings,
            and chunks_indexed counts.

        """
        from chaoscypher_core.models import RebuildResult

        raw = self.search_service.rebuild_indexes()
        return self._to_model(RebuildResult, raw)

    # ========================================================================
    # LLM Convenience Methods
    # ========================================================================

    async def chat(
        self,
        messages: str | list[dict[str, Any]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMChatResponse:
        """Send a chat message to the configured LLM provider.

        Accepts a plain string (auto-wrapped as a user message) or a full
        message list for multi-turn conversations.

        Args:
            messages: A string prompt or list of message dicts
                (``[{"role": "user", "content": "..."}]``).
            stream: Whether to stream the response.
            **kwargs: Forwarded to LLMProvider.chat() (temperature,
                max_tokens, enable_thinking, etc.).

        Returns:
            LLMChatResponse with content, tool_calls, usage, and provider info.

        Example:
            response = await engine.chat("What is a knowledge graph?")
            print(response.content)

        """
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        return await self.llm_provider.chat(messages, stream=stream, **kwargs)

    async def embed(self, text: str, **kwargs: Any) -> EmbedResult:
        """Generate a vector embedding for text.

        Args:
            text: Text to embed.
            **kwargs: Forwarded to embedding provider's embed().

        Returns:
            EmbedResult with embedding vector, provider, and usage.

        Example:
            result = await engine.embed("quantum entanglement")
            print(f"Dimensions: {len(result.embedding)}")

        """
        return await self.embedding_service.embed(text, **kwargs)

    async def batch_embed(self, texts: list[str], **kwargs: Any) -> BatchEmbedResult:
        """Generate vector embeddings for multiple texts.

        Args:
            texts: List of texts to embed.
            **kwargs: Forwarded to embedding provider's batch_embed().

        Returns:
            BatchEmbedResult with embeddings list, counts, and provider.

        """
        return await self.embedding_service.batch_embed(texts, **kwargs)

    # ========================================================================
    # Document Processing
    # ========================================================================

    async def chunk_document(
        self,
        text: str,
        *,
        source_id: str | None = None,
        analysis_depth: AnalysisDepth = "full",
    ) -> ChunkingResult:
        """Chunk document text and store for RAG search.

        Splits text into small RAG chunks and hierarchical groups, then
        persists them to storage. Use the returned ``source_id`` for
        subsequent ``index_source()`` or ``commit()`` calls.

        Args:
            text: Document text to chunk.
            source_id: Identifier for this source. Auto-generated if omitted.
            analysis_depth: 'full' (all chunks) or 'quick' (sampled subset).

        Returns:
            ChunkingResult with source_id, chunk counts, and analysis depth.

        Example:
            chunks = await engine.chunk_document(text)
            index = await engine.index_source(chunks.source_id)

        """
        from chaoscypher_core.models import ChunkingResult
        from chaoscypher_core.utils import generate_id

        source_id = source_id or generate_id()

        raw = await self.chunking_service.create_chunks(
            full_text=text, source_id=source_id, analysis_depth=analysis_depth
        )

        return ChunkingResult(
            source_id=source_id,
            total_small_chunks=raw.total_small_chunks,
            total_groups=raw.total_groups,
            analysis_depth=analysis_depth,
        )

    async def commit(
        self,
        source_id: str,
        *,
        filename: str = "document.txt",
        analysis_depth: AnalysisDepth = "full",
    ) -> ProcessingResult:
        """Extract entities from stored chunks and commit to the knowledge graph.

        Orchestrates entity extraction, deduplication, template matching,
        and graph write for a source that has already been chunked via
        ``chunk_document()``.

        Internally reconstructs document text from stored chunks, runs
        the extraction pipeline, and commits the results.

        Args:
            source_id: Source identifier (from ``chunk_document().source_id``).
            filename: Original filename for domain detection and metadata.
            analysis_depth: Extraction depth — 'full' (default) or 'quick'.

        Returns:
            ProcessingResult with lists of created node, edge, and template IDs.

        Example:
            chunks = await engine.chunk_document(text)
            await engine.index_source(chunks.source_id)
            result = await engine.commit(chunks.source_id)

        """
        # Reconstruct text from stored small chunks (ordered by index)
        small_chunks = self.chunking_service.get_small_chunks(source_id)
        if not small_chunks:
            from chaoscypher_core.exceptions import NotFoundError

            raise NotFoundError("source chunks", source_id)

        reconstructed_text = "\n\n".join(
            chunk["content"] for chunk in small_chunks if chunk.get("content")
        )

        # Extract entities via chunking + LLM
        raw_result = await self.chunking_service.process(
            reconstructed_text,
            analysis_depth=analysis_depth,
            file_info={"filename": filename},
        )

        # Finalize: deduplicate, normalize, suggest templates
        results = await self.extraction_service.finalize_distributed_extraction(
            raw_entities=raw_result.entities,
            raw_relationships=raw_result.relationships,
            generate_embeddings=True,
            detected_domain=raw_result.domain,
        )

        # Commit to graph
        commit_result = await self.commit_service.commit(
            file_id=source_id,
            commit_data=results,
            file_info={"filename": filename},
        )

        logger.info(
            "commit_completed",
            source_id=source_id,
            nodes=len(commit_result.get("created_nodes", [])),
            edges=len(commit_result.get("created_edges", [])),
        )

        from chaoscypher_core.models import ProcessingResult

        return ProcessingResult(
            source_id=source_id,
            nodes=commit_result.get("created_nodes", []),
            edges=commit_result.get("created_edges", []),
            templates=commit_result.get("created_templates", []),
        )

    def _ensure_source_row(
        self,
        *,
        source_id: str,
        filename: str,
        analysis_depth: AnalysisDepth,
        confirmation_required: bool,
        forced_domain: str | None,
    ) -> None:
        """Persist a SourceRow so the confirmation gate has state to read/write.

        The direct-extraction engine path (``process_document``) does not
        otherwise create a SourceRow — it only stores chunks. The gate parks a
        source by writing ``status=awaiting_confirmation`` onto a persisted
        row, so a row must exist before the gate evaluates. Idempotent: if the
        row already exists (e.g. a caller pre-created it) only the gate-
        relevant fields are stamped, leaving file metadata untouched.

        Args:
            source_id: Source identifier.
            filename: Original filename (also used for domain detection).
            analysis_depth: Extraction depth persisted as ``extraction_depth``.
            confirmation_required: Persisted gate flag (True parks an
                auto-detected source; gate_decision short-circuits otherwise).
            forced_domain: Explicit domain choice (suppresses parking).
        """
        from chaoscypher_core.models import SourceStatus

        db_name = self.settings.current_database
        existing = self.storage_adapter.get_source(source_id, db_name)
        if existing is not None:
            updates: dict[str, Any] = {"confirmation_required": confirmation_required}
            if forced_domain:
                updates["forced_domain"] = forced_domain
            self.storage_adapter.update_source(source_id, updates)
            return

        source_data: dict[str, Any] = {
            "id": source_id,
            "database_name": db_name,
            "filename": filename,
            "filepath": filename,
            "file_type": (filename.rsplit(".", 1)[-1] if "." in filename else "txt"),
            "status": SourceStatus.PENDING,
            "source_type": "file",
            "extraction_depth": analysis_depth,
            "confirmation_required": confirmation_required,
        }
        if forced_domain:
            source_data["forced_domain"] = forced_domain
        self.storage_adapter.create_source(source_data)

    async def _maybe_park_for_confirmation(
        self,
        *,
        source_id: str,
        filename: str,
    ) -> ProcessingResult | None:
        """Park the source for human domain confirmation if the gate says so.

        Reads the persisted SourceRow via ``gate_decision`` (the shared gate
        brain). Returns ``None`` when extraction may proceed — including the
        common case where no SourceRow exists (direct-SDK path), so behaviour
        for non-gated callers is unchanged. When the gate decides ``"park"``,
        runs fast domain detection to build the proposal, persists
        ``status=awaiting_confirmation`` via ``park_for_confirmation``, and
        returns a terminal ``ProcessingResult`` (no extraction/commit ran).

        Args:
            source_id: Source identifier (already INDEXED, chunks present).
            filename: Original filename (a domain-detection signal).

        Returns:
            A parked ``ProcessingResult`` (status=awaiting_confirmation) when
            the source was parked; ``None`` to proceed with extraction.
        """
        from chaoscypher_core.models import ProcessingResult, SourceStatus
        from chaoscypher_core.operations.importing.confirmation_gate import (
            gate_decision,
            park_for_confirmation,
            proposal_from_detection,
        )

        db_name = self.settings.current_database
        source = self.storage_adapter.get_source(source_id, db_name)
        if source is None or gate_decision(source) != "park":
            return None

        # Fast heuristic detection so the parked source carries a recommended
        # domain for the human to confirm. Mirrors the MCP get_tasks proposal.
        from chaoscypher_core.services.sources.engine.extraction.domains.factory import (
            get_domain_registry,
        )
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            detect_extraction_domain,
        )

        registry = get_domain_registry(self.settings, database_name=db_name)
        extraction_settings = self.settings.extraction
        chunks = self.storage_adapter.list_chunks(
            database_name=db_name,
            source_id=source_id,
            limit=extraction_settings.domain_detection_sample_count,
            include_content=True,
        )
        sample_text = " ".join(c.get("content", "") for c in chunks)[
            : extraction_settings.domain_detection_sample_chars
        ]
        detection = detect_extraction_domain(
            registry=registry,
            forced_domain=None,
            sample_text=sample_text,
            filename=source.get("filename", filename),
        )
        proposal = proposal_from_detection(detection)
        park_for_confirmation(self.storage_adapter, source_id, proposal)
        logger.info(
            "process_document_parked_for_confirmation",
            source_id=source_id,
            detected_domain=detection["detected_domain"],
            confidence=detection["confidence"],
            low_confidence=detection.get("low_confidence", False),
        )
        return ProcessingResult(
            source_id=source_id,
            status=SourceStatus.AWAITING_CONFIRMATION,
        )

    async def process_document(
        self,
        text: str,
        *,
        source_id: str | None = None,
        filename: str = "document.txt",
        analysis_depth: AnalysisDepth = "full",
        on_progress: ProgressCallback | None = None,
        auto_confirm: bool = True,
        forced_domain: str | None = None,
    ) -> ProcessingResult:
        """Process a document through the full extraction pipeline.

        Chunks the text, stores and indexes chunks for RAG search,
        extracts entities and relationships using AI, and commits
        them to the knowledge graph.

        Args:
            text: Document text to process. To load from a file first,
                use ``Loaders.load_text(filepath)`` then pass the result.
            source_id: Identifier for this source document. Auto-generated
                if not provided.
            filename: Original filename (used for domain detection and
                source metadata).
            analysis_depth: Extraction depth — 'full' (default, all chunks)
                or 'quick' (samples ~5 chunk groups, ~5x faster).
            on_progress: Unified callback invoked after each pipeline stage.
                Receives ``(stage, result)`` where stage is ``"chunking"``,
                ``"indexing"``, or ``"extraction"``.
            auto_confirm: Whether to bypass the domain-confirmation gate.
                Defaults to ``True`` so the direct-SDK path extracts
                immediately as before (no source row is parked). The MCP
                server-extraction path forwards ``False`` to opt INTO the
                gate, which persists ``confirmation_required=True`` and parks
                an auto-detected source at ``awaiting_confirmation`` between
                the index and extraction stages.
            forced_domain: Explicit human domain choice. When set, the gate
                always proceeds (a forced domain is never parked).

        Returns:
            ProcessingResult model with ``source_id``, ``nodes``, ``edges``,
            ``templates`` listing the IDs of created graph entities. When the
            gate parks the source, the result's ``status`` is
            ``awaiting_confirmation`` and ``nodes``/``edges``/``templates``
            are empty (extraction did not run).

        Example:
            with Engine("./data/databases/demo", initialize_db=True) as engine:
                result = await engine.process_document(text, filename="paper.pdf")
                print(f"Created {len(result.nodes)} nodes")

                # With progress tracking:
                def on_progress(stage, result):
                    print(f"[{stage}] done")

                result = await engine.process_document(
                    text, filename="paper.pdf", on_progress=on_progress
                )

        """
        from chaoscypher_core.models import ChunkingResult, IndexingResult
        from chaoscypher_core.utils import generate_id

        source_id = source_id or generate_id()

        # The confirmation gate only engages when extraction was NOT pre-
        # authorised (``auto_confirm=False``) and no domain was forced. Mirrors
        # the cortex/CLI/index-only rule. The direct-SDK default
        # (``auto_confirm=True``) leaves this False, so no SourceRow is created
        # here and the gate read below short-circuits to 'proceed' —
        # byte-for-byte the pre-gate behaviour for SDK callers.
        confirmation_required = (not auto_confirm) and (forced_domain is None)
        if confirmation_required:
            # The gate writes status=awaiting_confirmation onto a persisted
            # SourceRow, so the row must exist before extraction. Create it
            # ahead of chunking (also satisfies the document_chunks FK), the
            # same ordering the MCP index-only pipeline uses.
            self._ensure_source_row(
                source_id=source_id,
                filename=filename,
                analysis_depth=analysis_depth,
                confirmation_required=True,
                forced_domain=forced_domain,
            )

        logger.info(
            "process_document_started",
            source_id=source_id,
            filename=filename,
            text_length=len(text),
            confirmation_required=confirmation_required,
        )

        # Stage 1: Chunk and store (enables RAG search)
        chunks_result = await self.chunking_service.create_chunks(
            full_text=text, source_id=source_id
        )
        chunking_model = ChunkingResult(
            source_id=source_id,
            total_small_chunks=chunks_result.total_small_chunks,
            total_groups=chunks_result.total_groups,
            analysis_depth=analysis_depth,
        )
        if on_progress:
            on_progress("chunking", chunking_model)

        # Stage 2: Index chunks (generate embeddings)
        index_result = await self.indexing_service.create_index(source_id=source_id)
        indexing_model = self._to_model(IndexingResult, index_result)
        if on_progress:
            on_progress("indexing", indexing_model)

        # --- Confirmation gate ------------------------------------------- #
        # Evaluate from PERSISTED SourceRow state (the same brain the worker,
        # recovery, and MCP get_tasks use). The row exists only when this call
        # opted into the gate above; otherwise get_source returns None and the
        # source-less SDK path proceeds untouched. A parked source returns
        # early WITHOUT running extraction/finalize/commit, so the MCP
        # server-extraction wait=True re-read sees awaiting_confirmation.
        parked = await self._maybe_park_for_confirmation(
            source_id=source_id,
            filename=filename,
        )
        if parked is not None:
            return parked
        # ----------------------------------------------------------------- #

        # Stage 3: Extract entities (chunk + LLM extraction)
        raw_result = await self.chunking_service.process(
            text, analysis_depth=analysis_depth, file_info={"filename": filename}
        )

        # Stage 4: Finalize (deduplicate, normalize, suggest templates)
        results = await self.extraction_service.finalize_distributed_extraction(
            raw_entities=raw_result.entities,
            raw_relationships=raw_result.relationships,
            generate_embeddings=True,
            detected_domain=raw_result.domain,
        )
        from chaoscypher_core.models import ExtractionResult as ExtResult

        # Pick only model-defined fields: ``finalize_distributed_extraction``
        # also returns extras (e.g. ``suggested_templates``) that ExtractionResult
        # forbids.
        extraction_model = ExtResult(
            entities=results.get("entities", []),
            relationships=results.get("relationships", []),
            cached_embeddings=results.get("cached_embeddings", []),
            chunk_ids=results.get("chunk_ids", []),
            domain=results.get("domain", "generic"),
            domain_confidence=results.get("domain_confidence", 0.0),
            filtering_log=results.get("filtering_log"),
        )
        if on_progress:
            on_progress("extraction", extraction_model)

        # Stage 5: Commit to graph
        commit_result = await self.commit_service.commit(
            file_id=source_id,
            commit_data=results,
            file_info={"filename": filename},
        )

        logger.info(
            "process_document_completed",
            source_id=source_id,
            nodes=len(commit_result.get("created_nodes", [])),
            edges=len(commit_result.get("created_edges", [])),
        )

        from chaoscypher_core.models import ProcessingResult

        return ProcessingResult(
            source_id=source_id,
            nodes=commit_result.get("created_nodes", []),
            edges=commit_result.get("created_edges", []),
            templates=commit_result.get("created_templates", []),
        )

    async def add_document(
        self,
        filepath: str | Path,
        *,
        source_id: str | None = None,
        analysis_depth: AnalysisDepth = "full",
        on_progress: ProgressCallback | None = None,
        auto_confirm: bool = True,
        forced_domain: str | None = None,
    ) -> ProcessingResult:
        """Load a file and process it through the full extraction pipeline.

        Convenience method that combines Loaders.load_text() with
        process_document(). Loads any supported file type, then chunks,
        indexes, extracts entities, and commits to the graph.

        Args:
            filepath: Path to the document file. Supports PDF, text, CSV,
                JSON, audio, video, image, and archive formats.
            source_id: Identifier for this source. Auto-generated if omitted.
            analysis_depth: Extraction depth — 'full' (default) or 'quick'.
            on_progress: Unified callback invoked after each pipeline stage.
                Receives ``(stage, result)`` where stage is ``"chunking"``,
                ``"indexing"``, or ``"extraction"``.
            auto_confirm: Bypass the domain-confirmation gate (default
                ``True``). Forwarded to :meth:`process_document`; the MCP
                server-extraction path passes ``False`` to opt into the gate.
            forced_domain: Explicit domain choice (suppresses parking).

        Returns:
            ProcessingResult with source_id and lists of created node,
            edge, and template IDs. When the gate parks the source, ``status``
            is ``awaiting_confirmation`` and no entities were extracted.

        Example:
            with Engine("./data/databases/demo") as engine:
                result = await engine.add_document("paper.pdf")
                print(f"Created {len(result.nodes)} nodes")

        """
        from chaoscypher_core.services.sources.loaders.facade import Loaders

        filepath = Path(filepath)
        text = Loaders.load_text(str(filepath), settings=self.settings)
        return await self.process_document(
            text,
            source_id=source_id,
            filename=filepath.name,
            analysis_depth=analysis_depth,
            on_progress=on_progress,
            auto_confirm=auto_confirm,
            forced_domain=forced_domain,
        )

    async def add_documents(
        self,
        paths: str | list[str | Path],
        *,
        on_document_complete: Callable[[str, ProcessingResult], None] | None = None,
    ) -> list[ProcessingResult]:
        """Load and process multiple documents.

        Accepts a glob pattern (e.g., ``"docs/*.pdf"``) or a list of file
        paths. Documents are processed sequentially.

        Args:
            paths: Glob pattern string or list of file paths.
            on_document_complete: Optional callback invoked after each
                document (receives filename and ProcessingResult).

        Returns:
            List of ProcessingResult models, one per document.

        Example:
            results = await engine.add_documents("papers/*.pdf")
            print(f"Processed {len(results)} documents")

        """
        if isinstance(paths, str):
            parent = Path(paths).parent
            pattern = Path(paths).name
            resolved = sorted(str(p) for p in parent.glob(pattern))
        else:
            resolved = [str(p) for p in paths]

        results: list[ProcessingResult] = []
        for filepath in resolved:
            result = await self.add_document(filepath)
            if on_document_complete:
                on_document_complete(Path(filepath).name, result)
            results.append(result)

        return results

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        mode: SearchMode = "hybrid",
    ) -> list[EngineSearchResult]:
        """Search the knowledge graph and document chunks.

        Convenience method that runs hybrid search (semantic + keyword
        fallback) by default. Returns flat EngineSearchResult models with
        consistent fields regardless of result type.

        Args:
            query: Search query string.
            limit: Maximum number of results.
            mode: Search mode — 'hybrid' (default), 'semantic', or 'keyword'.

        Returns:
            List of EngineSearchResult models sorted by relevance score.

        Example:
            results = await engine.search("quantum entanglement")
            for r in results:
                print(f"{r.label} ({r.score:.2f})")

        """
        from chaoscypher_core.models import EngineSearchResult

        async def _embed_callback(text: str) -> list[float]:
            """Embed a query and return the bare vector for search services."""
            result = await self.embedding_service.embed(text)
            embedding: list[float] = result.embedding
            return embedding

        if mode == "keyword":
            raw = self.search_service.keyword_search(query, limit=limit)
        elif mode == "semantic":
            raw = await self.search_service.semantic_search(
                query,
                limit=limit,
                embedding_provider_callback=_embed_callback,
            )
        else:
            raw = await self.search_service.hybrid_search(
                query,
                limit=limit,
                embedding_provider_callback=_embed_callback,
            )

        results: list[EngineSearchResult] = []
        for item in raw.get("data", []):
            result_type = item.get("result_type", "node")
            score = item.get("score", 0.0)

            if result_type == "node" and "node" in item:
                node = item["node"]
                results.append(
                    EngineSearchResult(
                        label=node.get("label", ""),
                        score=score,
                        result_type="node",
                        id=node.get("id", ""),
                        template_id=node.get("template_id"),
                    )
                )
            elif result_type == "chunk" and "chunk" in item:
                preview_chars = self.settings.search.result_preview_chars
                chunk = item["chunk"]
                content = chunk.get("content", "")
                results.append(
                    EngineSearchResult(
                        label=content[:preview_chars] if content else "",
                        score=score,
                        result_type="chunk",
                        id=chunk.get("id", ""),
                        source=chunk.get("filename"),
                        content=content,
                    )
                )

        return results

    # ========================================================================
    # Synchronous Convenience Wrappers
    # ========================================================================

    def search_sync(
        self,
        query: str,
        *,
        limit: int = 10,
        mode: SearchMode = "hybrid",
    ) -> list[EngineSearchResult]:
        """Synchronous wrapper for :meth:`search`.

        Runs the async search in a new event loop. For use in scripts,
        notebooks, and non-async contexts.

        Args:
            query: Search query string.
            limit: Maximum number of results.
            mode: Search mode — 'hybrid' (default), 'semantic', or 'keyword'.

        Returns:
            List of EngineSearchResult models sorted by relevance score.

        Example:
            with Engine(database="demo") as engine:
                results = engine.search_sync("quantum entanglement")
                for r in results:
                    print(f"{r.label} ({r.score:.2f})")

        """
        import asyncio

        return asyncio.run(self.search(query, limit=limit, mode=mode))

    def chat_sync(
        self,
        messages: str | list[dict[str, Any]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMChatResponse:
        """Synchronous wrapper for :meth:`chat`.

        Args:
            messages: A string prompt or list of message dicts.
            stream: Whether to stream the response.
            **kwargs: Forwarded to LLMProvider.chat().

        Returns:
            LLMChatResponse with content, tool_calls, usage, and provider info.

        Example:
            with Engine(database="demo") as engine:
                response = engine.chat_sync("What is a knowledge graph?")
                print(response.content)

        """
        import asyncio

        return asyncio.run(self.chat(messages, stream=stream, **kwargs))

    def embed_sync(self, text: str, **kwargs: Any) -> EmbedResult:
        """Synchronous wrapper for :meth:`embed`.

        Args:
            text: Text to embed.
            **kwargs: Forwarded to embedding provider.

        Returns:
            EmbedResult with embedding vector, provider, and usage.

        """
        import asyncio

        return asyncio.run(self.embed(text, **kwargs))

    def batch_embed_sync(self, texts: list[str], **kwargs: Any) -> BatchEmbedResult:
        """Synchronous wrapper for :meth:`batch_embed`.

        Args:
            texts: List of texts to embed.
            **kwargs: Forwarded to embedding provider.

        Returns:
            BatchEmbedResult with embeddings list, counts, and provider.

        """
        import asyncio

        return asyncio.run(self.batch_embed(texts, **kwargs))

    def add_document_sync(
        self,
        filepath: str | Path,
        *,
        source_id: str | None = None,
        analysis_depth: AnalysisDepth = "full",
        on_progress: ProgressCallback | None = None,
    ) -> ProcessingResult:
        """Synchronous wrapper for :meth:`add_document`.

        Args:
            filepath: Path to the document file.
            source_id: Identifier for this source. Auto-generated if omitted.
            analysis_depth: Extraction depth — 'full' or 'quick'.
            on_progress: Callback invoked after each pipeline stage.

        Returns:
            ProcessingResult with source_id and created entity IDs.

        Example:
            with Engine(database="demo") as engine:
                result = engine.add_document_sync("paper.pdf")
                print(f"Created {len(result.nodes)} nodes")

        """
        import asyncio

        return asyncio.run(
            self.add_document(
                filepath,
                source_id=source_id,
                analysis_depth=analysis_depth,
                on_progress=on_progress,
            )
        )

    def add_documents_sync(
        self,
        paths: str | list[str | Path],
        *,
        on_document_complete: Callable[[str, ProcessingResult], None] | None = None,
    ) -> list[ProcessingResult]:
        """Synchronous wrapper for :meth:`add_documents`.

        Args:
            paths: Glob pattern string or list of file paths.
            on_document_complete: Callback invoked after each document.

        Returns:
            List of ProcessingResult models, one per document.

        Example:
            with Engine(database="demo") as engine:
                results = engine.add_documents_sync(["doc1.pdf", "doc2.pdf"])
                print(f"Processed {len(results)} documents")

        """
        import asyncio

        return asyncio.run(self.add_documents(paths, on_document_complete=on_document_complete))

    def process_document_sync(
        self,
        text: str,
        *,
        source_id: str | None = None,
        filename: str = "document.txt",
        analysis_depth: AnalysisDepth = "full",
        on_progress: ProgressCallback | None = None,
    ) -> ProcessingResult:
        """Synchronous wrapper for :meth:`process_document`.

        Args:
            text: Document text to process.
            source_id: Identifier for this source. Auto-generated if omitted.
            filename: Original filename for domain detection.
            analysis_depth: Extraction depth — 'full' or 'quick'.
            on_progress: Callback invoked after each pipeline stage.

        Returns:
            ProcessingResult with source_id and created entity IDs.

        """
        import asyncio

        return asyncio.run(
            self.process_document(
                text,
                source_id=source_id,
                filename=filename,
                analysis_depth=analysis_depth,
                on_progress=on_progress,
            )
        )


__all__ = [
    "Engine",
]
