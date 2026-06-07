---
title: "Storage Adapters API"
---

# Storage Adapters API

Concrete storage implementations that fulfill the protocol contracts defined in the ports layer.

## `chaoscypher_core.adapters.sqlite.adapter`

SQLite Storage Adapter for ChaosCypher Knowledge Engine.

Provides complete SQLite database storage implementation using SQLModel.
Implements the per-domain storage Protocols defined under `chaoscypher_core.ports`
(`storage_workflows`, `storage_chats`, `storage_sources`, ...).

This adapter requires SQLModel:
    pip install sqlmodel>=0.0.14

Usage:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

    adapter = SqliteAdapter(db_path="data/databases/default/app.db")
    adapter.connect()

    # Workflow operations
    workflow = adapter.create_workflow(\{
        "id": "workflow_001",
        "database_name": "default",
        "name": "Research Pipeline",
        ...
    \})

    adapter.disconnect()

    # Or use as context manager
    with SqliteAdapter(db_path="data/app.db") as adapter:
        workflows = adapter.list_workflows("default")

### `class SqliteAdapter`

SQLite storage adapter implementing all storage protocols.

Provides complete CRUD operations for all ChaosCypher entities using SQLite + SQLModel.

Architecture:
    - Entity conversion via SqliteMixinBase (delegates to utils module)
    - Implements all storage protocols via mixins (ISP-compliant)
    - Uses SQLModel for ORM
    - Manages SQLite sessions and transactions

Source file operations are split across 4 focused mixins:
    - SourceLifecycleMixin: File CRUD (upload, get, list, delete, update)
    - SourceIndexingMixin: Status lifecycle, embeddings, extraction gating
    - SourceExtractionJobsMixin: Extraction job CRUD and status
    - SourceChunkTasksMixin: Chunk task CRUD, analytics, recovery

Source operations are split across 5 focused mixins:
    - SourcesMixin: Core source CRUD (get, create, update, list)
    - SourceTagsMixin: Tag CRUD and tag-to-source assignments
    - SourceChunksMixin: Document chunk CRUD, batch ops, hierarchical grouping
    - SourceCitationsMixin: Citations, stats, orphan detection, bulk clear
    - SourceDeletionMixin: Cross-mixin delete-source cascade orchestrator

Example:
    adapter = SqliteAdapter(db_path="data/app.db")
    adapter.connect()
    workflow = adapter.create_workflow(\{...\})
    adapter.disconnect()

**Bases:** `WorkflowsMixin, WorkflowExecutionsMixin, ToolsMixin, SourceLifecycleMixin, SourceIndexingMixin, SourceExtractionJobsMixin, SourceChunkTasksMixin, SourceDeletionMixin, SourcesMixin, StageProgressMixin, SourceTagsMixin, SourceChunksMixin, SourceCitationsMixin, SourceRecoveryEventsMixin, VisionPagesMixin, ChatsMixin, TriggersMixin, LLMMetricsMixin, ExtractionSubmissionsMixin, SearchRetryQueueMixin, SystemStateMixin`

**Methods:**

#### `connect() -> None`

Initialize connection to SQLite database.

NOTE: Tables should already be created via initialize_database() at startup.
This method just creates the session for database operations.

#### `disconnect() -> None`

Close connection to SQLite database.

#### `session_scope() -> AsyncIterator[SafeSession]`

Run the wrapped block with a fresh per-task `SafeSession`.

Creates a brand-new `SafeSession` bound to the cached engine,
installs it in the module-level ContextVar so `self.session`
(and `GraphRepository.session`) resolve to it for the duration
of the block, and closes the session on exit.

This is what eliminates the 2026-05-20 silent-data-loss race:
every queue handler dispatch enters a fresh scope, so two
interleaved handlers can never share session state.

Example:

```
async with adapter.session_scope():
    adapter.create_extraction_job(...)
    # adapter.session here is a fresh SafeSession unique
    # to this task; sibling tasks see a different session.
```

Raises:
    RuntimeError: if the adapter is not connected.

#### `transaction() -> Iterator[None]`

Group multiple repository writes into a single database transaction.

Inside this context, repository methods call `_maybe_commit()`
which flushes to the database buffer but does not commit. The
outer transaction commits at context exit, or rolls back on any
exception. Supports nesting — only the outermost context
actually commits / rolls back.

Example:
    with adapter.transaction():
        adapter.source_repository.start_commit(source_id)
        adapter.graph_repository.delete_graph_data_by_source(source_id)
        # ... more writes ...
    # All committed atomically here

**Attributes:**

- `database_name`
- `db_path`
- `session`: `SafeSession | None` — Active session: per-task scope if entered, else fallback.

Inside `session_scope()` returns the ContextVar-bound session
unique to the current async task. Outside any scope (tests,
startup, Cortex per-request adapters) returns the connection-time
`_fallback_session`.
