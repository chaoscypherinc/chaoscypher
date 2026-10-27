# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP Server factory for Chaos Cypher.

Provides ``create_mcp_server()`` which wires an Engine instance to
an MCP Server with all tool handlers registered. Supports both
stdio and Streamable HTTP transports.

Besides the graph tools, two client-driven workflows are routed here: MCP
extraction (``get_extraction_tasks`` -> ``get_extraction_chunks`` ->
``submit_chunk_extraction`` -> ``finalize_extraction``, write mode) and the
MCP benchmark, in which the calling model answers a benchmark suite -
the extraction instruction probes or grounded chat (``start_benchmark`` -> ``get_benchmark_task`` ->
``submit_benchmark_output`` ... -> ``finish_benchmark``, read mode too,
since it never touches the graph).
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mcp.types
import structlog
from mcp.server import Server
from mcp.types import CallToolResult, TextContent, Tool

from chaoscypher_core.mcp.benchmark import BenchmarkBridge
from chaoscypher_core.mcp.bridge import MCPToolBridge
from chaoscypher_core.mcp.extraction import ExtractionOrchestrator
from chaoscypher_core.mcp.indexing import index_source_text
from chaoscypher_core.mcp.memory_link import MemoryLinker
from chaoscypher_core.mcp.processor import DocumentProcessor
from chaoscypher_core.mcp.sites import SITE_TOOLS, handle_site_tool
from chaoscypher_core.mcp.tools import ToolDefinition, get_tools
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.importing.confirmation_gate import confirm_extraction
from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    first_domain_name,
    packs_choice,
    proposal_packs,
    requested_packs,
)
from chaoscypher_core.services.sources.web import fetch_page, page_names
from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService
from chaoscypher_core.utils.retry import is_sqlite_lock_error
from chaoscypher_core.utils.tool_failure import discard_failed_work, log_tool_failure


if TYPE_CHECKING:
    from chaoscypher_core.benchmark.retrieval import NaiveRetrieverFactory
    from chaoscypher_core.bootstrap import Engine
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)

MEMORY_TOOLS = frozenset({"remember", "recall", "forget", "list_memories", "digest"})
"""The memory tools the server dispatches to ``mcp.memory``."""

TEXT_LINE_TOOLS = MEMORY_TOOLS | SITE_TOOLS
"""The tools that answer plain text lines, a failure included; every other tool answers JSON."""


def _register_relink_on_start(
    server: Server, settings: EngineSettings, linker: MemoryLinker
) -> None:
    """In write mode (unless ``mcp.memory.relink_on_start`` is off), sweep after the handshake.

    The client's ``initialized`` notification ends the MCP handshake; the sweep then links
    the memories not linked yet in the background, and the linker's ``close`` stops it.
    """
    if settings.mcp.mode != "write" or not settings.mcp.memory.relink_on_start:
        return

    async def _start_relink(_notification: mcp.types.InitializedNotification) -> None:
        """Start the linker's sweep (once per linker)."""
        linker.start_sweep()

    server.notification_handlers[mcp.types.InitializedNotification] = _start_relink


def create_mcp_server(
    engine: Engine,
    *,
    naive_retriever: NaiveRetrieverFactory | None = None,
    linker: MemoryLinker | None = None,
) -> Server:
    """Create a configured MCP Server from an Engine instance.

    Wires the ToolExecutorService with engine repositories, registers all
    tools (filtered by mcp.mode), and sets up the document processor.

    Args:
        engine: Initialized Engine instance with all repos connected.
        naive_retriever: The benchmark bridge's naive-rung index builder
            (the CLI's ``chaoscypher mcp`` passes it; Core owns no
            llama-index dependency).
        linker: The memory links ``remember`` leaves running when its wait runs
            out; the caller closes it on shutdown so each gives its claim back.
            None builds one nobody closes (a claim left open goes stale and the
            memory is linked again later).

    Returns:
        Configured MCP Server ready for transport binding.

    """
    settings = engine.settings
    mode = settings.mcp.mode
    memory_linker = linker or MemoryLinker(engine)

    # Build optional embedding callback. With vector search off the engine has no embedding
    # provider (embedding_provider_for, the one reader of search.enable_vector_search), and the
    # server runs on keywords alone: it never embeds a query or a written node.
    embedding_callback = None
    provider = engine.embedding_service
    if provider is not None:

        async def _embed(text: str) -> Any:
            """Forward to the engine's embedding service for MCP tool callbacks."""
            return await provider.embed(text)

        embedding_callback = _embed

    # Create ToolExecutorService
    tool_executor = ToolExecutorService(
        graph_repository=engine.graph_repository,
        search_repository=engine.search_repository,
        indexing_repository=engine.storage_adapter,
        embedding_callback=embedding_callback,
        # a node written through the tools gets a vector only when searches read vectors
        embedding_provider=provider,
        engine_settings=settings,
        source_storage=engine.storage_adapter,
        scope={"database_name": settings.current_database},
    )

    bridge = MCPToolBridge(tool_executor=tool_executor)

    # Pipeline callbacks, document processors and the extraction
    # orchestrator (only active in write mode)
    full_pipeline: Any = None
    index_only_pipeline: Any = None
    doc_processor: DocumentProcessor | None = None
    index_only_processor: DocumentProcessor | None = None
    extraction_orchestrator: ExtractionOrchestrator | None = None
    if mode == "write":
        extraction_orchestrator = ExtractionOrchestrator(engine=engine)
        full_pipeline = _create_pipeline_callback(engine, extract_entities=True)
        index_only_pipeline = _create_pipeline_callback(engine, extract_entities=False)
        doc_processor = DocumentProcessor(
            pipeline_callback=full_pipeline,
            completed_history_limit=settings.mcp.completed_history_limit,
        )
        index_only_processor = DocumentProcessor(
            pipeline_callback=index_only_pipeline,
            completed_history_limit=settings.mcp.completed_history_limit,
        )

    # Benchmark bridge: file-backed, never touches the graph, so it serves
    # read mode as well as write mode.
    benchmark_bridge = BenchmarkBridge(settings, naive_retriever=naive_retriever)

    # Build MCP Server
    server = Server("chaoscypher")
    tool_defs = get_tools(mode, settings.mcp.profile, settings.mcp.tools)
    _register_relink_on_start(server, settings, memory_linker)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """Return all registered tool definitions."""
        return [
            Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,
            )
            for t in tool_defs
        ]

    async def dispatch(name: str, args: dict[str, Any]) -> list[TextContent] | CallToolResult:
        """Route a tool call to the appropriate handler."""
        effective_mode = _extract_effective_mode(server, default=mode)
        rejected = _gate_tool_call(name, effective_mode, tool_defs)
        if rejected is not None:
            return rejected

        # Handle MCP-only tools
        if name == "get_document_status":
            return _handle_document_status(engine, effective_mode=effective_mode)
        if name == "add_document":
            extract_entities = args.pop("extract_entities", settings.mcp.auto_extract)
            extraction_depth = args.pop("extraction_depth", "full")
            packs = args.pop("packs", None)
            domain = args.pop("domain", None)
            # Tri-state: ``None`` defers to ``resolve_normalization_default``
            # at indexing time (prose → True, CSV/JSON/XML → False). Explicit
            # True/False is an MCP-client override.
            enable_normalization = args.pop("enable_normalization", None)
            skip_duplicates = args.pop("skip_duplicates", False)
            wait = args.pop("wait", True)
            wait_timeout = args.pop("wait_timeout", 300)
            content = args.pop("content", None)
            enable_vision = args.pop("enable_vision", None)
            # auto_confirm=True bypasses the domain-confirmation gate so the
            # source proceeds to extraction without parking. False/absent with
            # an auto domain sets confirmation_required=True on the source row.
            # The per-call flag defaults to the inverse of the server-wide
            # confirmation_required_default setting so operators can disable
            # the gate globally without every caller passing auto_confirm=true.
            auto_confirm = args.pop("auto_confirm", not settings.mcp.confirmation_required_default)
            processor = doc_processor if extract_entities else index_only_processor
            pipeline = full_pipeline if extract_entities else index_only_pipeline
            return await _handle_add_document(
                processor,
                args,
                extraction_depth=extraction_depth,
                forced_domain=domain,
                packs=packs,
                enable_normalization=enable_normalization,
                skip_duplicates=skip_duplicates,
                wait=wait,
                wait_timeout=wait_timeout,
                content=content,
                enable_vision=enable_vision,
                auto_confirm=auto_confirm,
                pipeline=pipeline,
                sandbox_dir=settings.paths.mcp_dir,
                engine=engine,
            )
        if name in MEMORY_TOOLS or name in SITE_TOOLS:
            return await _handle_module_tool(
                name, engine, args, settings, tool_executor, memory_linker
            )
        if name == "wait_for_document":
            return await _handle_wait_for_document(doc_processor, index_only_processor, args)
        if name == "remove_document":
            return await _handle_remove_document(engine, args)
        if name == "confirm_extraction":
            return await _handle_confirm_extraction(engine, args)

        # MCP extraction tools
        _extraction_methods = {
            "get_extraction_tasks": "get_tasks",
            "get_extraction_chunks": "get_chunks",
            "submit_chunk_extraction": "submit_chunk",
            "get_extraction_progress": "get_progress",
            "finalize_extraction": "finalize",
        }
        if name in _extraction_methods:
            method_name = _extraction_methods[name]
            method = (
                getattr(extraction_orchestrator, method_name, None)
                if extraction_orchestrator
                else None
            )
            return await _handle_extraction_tool(extraction_orchestrator, method, args)

        # MCP benchmark tools
        _benchmark_methods = {
            "start_benchmark": benchmark_bridge.start,
            "get_benchmark_task": benchmark_bridge.next_task,
            "submit_benchmark_output": benchmark_bridge.submit,
            "get_benchmark_progress": benchmark_bridge.progress,
            "finish_benchmark": benchmark_bridge.finish,
        }
        if name in _benchmark_methods:
            return await _handle_benchmark_tool(_benchmark_methods[name], args)

        # Delegate to ToolExecutorService via bridge. The bridge's verdict rides on the wire:
        # until 2026-10-20 a failed tool reached the client as a plain result (isError false).
        bridge_result = await bridge.execute(name, args)
        return CallToolResult(
            content=[TextContent(type="text", text=bridge_result.text)],
            isError=bridge_result.is_error,
        )

    server.call_tool()(_tool_call_handler(engine, dispatch))

    logger.info(
        "mcp_server_created",
        mode=mode,
        tool_count=len(tool_defs),
    )
    return server


Dispatch = Callable[[str, dict[str, Any]], Awaitable[list[TextContent] | CallToolResult]]
"""A server's tool router: the tool's name and arguments to its answer."""


def _tool_call_handler(
    engine: Engine, dispatch: Dispatch
) -> Callable[[str, dict | None], Awaitable[list[TextContent] | CallToolResult]]:
    """The server's ``call_tool`` handler: ``dispatch`` from fresh rows, a failure as a fixed answer.

    Another process may have written since the last call, so the engine's cached rows
    are expired first (spec 2026-10-24 § 3, G4). An error that escapes a tool would reach
    the client as the exception's text (the MCP SDK answers ``str(exc)``), which can carry
    the SQL and its parameters (a memory's words). It answers its class's fixed failure
    instead (``tool_failure``: a busy database, a missing node, ...; ``isError``) in the
    tool's own shape (:func:`_failure_result`). The log keeps the tool, the error's type
    and the code (``log_tool_failure``), never its message; a busy database is a warning.
    """

    async def call_tool(
        name: str, arguments: dict | None = None
    ) -> list[TextContent] | CallToolResult:
        """Route one tool call (see :func:`_tool_call_handler`)."""
        engine.expire_cached_rows()
        try:
            return await dispatch(name, arguments or {})
        except Exception as exc:
            level = "warning" if is_sqlite_lock_error(exc) else "error"
            answer = log_tool_failure(logger, "mcp_tool_failed", exc, level=level, tool=name)
            return _failure_result(name, answer)

    return call_tool


def _failure_result(name: str, answer: dict[str, Any]) -> CallToolResult:
    """A failed call's answer (``tool_failure``) in its tool's shape, flagged ``isError``.

    The memory and site tools (:data:`TEXT_LINE_TOOLS`) answer the failure's sentence as
    a line, the way they answer everything (Tsukumo reads those lines); every other tool
    answers the failure as JSON, as its own catches do.
    """
    text = answer["error"] if name in TEXT_LINE_TOOLS else json.dumps(answer)
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=True)


async def _handle_module_tool(
    name: str,
    engine: Engine,
    args: dict[str, Any],
    settings: EngineSettings,
    tool_executor: ToolExecutorService,
    linker: MemoryLinker,
) -> CallToolResult:
    """The tools with a handler module of their own: the sites (``mcp/sites.py``), the memory."""
    if name in SITE_TOOLS:
        answer, is_error = await handle_site_tool(engine, name, args)
        return CallToolResult(content=[TextContent(type="text", text=answer)], isError=is_error)
    return await _handle_memory_tool(name, engine, args, settings, tool_executor, linker)


async def _handle_memory_tool(
    name: str,
    engine: Engine,
    args: dict[str, Any],
    settings: EngineSettings,
    tool_executor: ToolExecutorService,
    linker: MemoryLinker,
) -> CallToolResult:
    """Dispatch one of :data:`MEMORY_TOOLS` to its handler in ``mcp.memory``."""
    from chaoscypher_core.mcp import memory

    if name == "remember":
        return await memory.handle_remember(engine, args, settings, linker)
    if name == "recall":
        return await memory.handle_recall(engine, args, settings, tool_executor)
    if name == "forget":
        return await memory.handle_forget(engine, args, settings)
    if name == "list_memories":
        return await memory.handle_list_memories(engine, args, settings)
    return await memory.handle_digest(engine, args, settings)


def _handle_document_status(
    engine: Engine,
    effective_mode: str = "write",
) -> list[TextContent]:
    """Handle get_document_status tool call.

    Lists all sources with their stage_progress, then merges the unbounded
    ``awaiting_confirmation`` query (``list_sources_by_statuses``) so parked
    sources are always discoverable — they would otherwise be invisible past
    the page_size=1000 cutoff. Awaiting docs carry the detection recommendation
    (``detected_domain`` = ranking[0].domain, ``confidence``, ``low_confidence``,
    ``confirmation_required``, ``file_id``). Read mode lists them but states it
    cannot confirm — confirmation requires a write-capable surface.
    """
    db_name = engine.settings.current_database
    sources, _total = engine.storage_adapter.list_sources(page=1, page_size=1000)
    documents: list[dict[str, Any]] = [
        {
            "id": s["id"],
            "filename": s.get("filename", ""),
            "status": s.get("status", ""),
            "stage_progress": s.get("stage_progress", {}),
        }
        for s in sources
    ]
    seen = {d["id"] for d in documents}

    awaiting = engine.storage_adapter.list_sources_by_statuses(
        statuses=[SourceStatus.AWAITING_CONFIRMATION],
        database_name=db_name,
    )
    for s in awaiting:
        proposal = s.get("detection_proposal") or {}
        ranking = proposal.get("ranking") or []
        detected_domain = ranking[0]["domain"] if ranking else proposal.get("detected_domain", "")
        doc: dict[str, Any] = {
            "id": s["id"],
            "file_id": s["id"],
            "filename": s.get("filename", ""),
            "status": s.get("status", ""),
            "stage_progress": s.get("stage_progress", {}),
            "packs": proposal_packs(proposal),
            "detected_domain": detected_domain,
            "confidence": proposal.get("confidence"),
            "low_confidence": proposal.get("low_confidence", False),
            "confirmation_required": s.get("confirmation_required", False),
        }
        if effective_mode == "read":
            doc["confirm_hint"] = (
                "This server is in read mode and cannot confirm extraction. "
                "Use a write-mode MCP surface and call confirm_extraction "
                f'with file_id="{s["id"]}".'
            )
        if s["id"] in seen:
            # The page scan may already include it (status visible there);
            # replace with the enriched record.
            documents = [d for d in documents if d["id"] != s["id"]]
        documents.append(doc)

    result: dict[str, Any] = {"success": True, "documents": documents}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _resolve_local_ingest_path(
    file_path: str, sandbox_dir: Path | None
) -> tuple[str, dict[str, Any] | None]:
    """Validate a client-supplied local file path before MCP ingest.

    Rejects hidden/dot files and confines the path to ``sandbox_dir``. Runs
    for BOTH the inline (wait=True) and queued (wait=False) ingest paths —
    each reads the file raw downstream (engine.add_document →
    Loaders.load_text) — so the guard must sit ahead of the wait split
    rather than inside a single branch.

    Args:
        file_path: Client-supplied local path. Callers must gate out URLs
            and inline content before calling.
        sandbox_dir: Directory the path must stay inside. ``None`` disables
            containment and falls back to a plain ``exists()`` check (used
            only by in-process unit tests; production always passes a dir).

    Returns:
        ``(resolved_path, None)`` on success, where ``resolved_path`` is the
        sandbox-confined absolute path to hand downstream. ``("", error)``
        when rejected; the caller serialises ``error`` back to the client.

    """
    from chaoscypher_core.utils.safe_paths import (
        PathOutsideSandboxError,
        resolve_within,
    )

    if Path(file_path).name.startswith("."):
        return "", {
            "success": False,
            "error_code": "DOTFILE_REJECTED",
            "error": f"Hidden files are not allowed in MCP ingest: {Path(file_path).name}",
        }

    if sandbox_dir is None:
        # No sandbox supplied — fall back to the legacy exists() check so
        # in-process unit tests without a sandbox keep working. Production
        # callers ALWAYS pass sandbox_dir.
        if not Path(file_path).exists():
            return "", {"success": False, "error": "File not found"}
        return file_path, None

    try:
        resolved = resolve_within(sandbox_dir, file_path)
    except PathOutsideSandboxError as e:
        return "", {
            "success": False,
            "error_code": "PATH_OUTSIDE_SANDBOX",
            "error": f"file_path is outside the sandbox: {e.message}",
        }
    return str(resolved), None


async def _handle_add_document(
    processor: DocumentProcessor | None,
    args: dict[str, Any],
    extraction_depth: str = "full",
    forced_domain: str | None = None,
    enable_normalization: bool | None = None,
    skip_duplicates: bool = False,
    wait: bool = True,
    wait_timeout: float = 300,
    content: str | None = None,
    enable_vision: bool | None = None,
    auto_confirm: bool = False,
    pipeline: Any = None,
    sandbox_dir: Path | None = None,
    engine: Any = None,
    packs: list[str] | None = None,
) -> list[TextContent]:
    """Handle add_document tool call.

    When wait=True, calls the pipeline callback directly to avoid
    asyncio/anyio deadlocks (asyncio.create_task and asyncio.Event
    do not schedule properly inside anyio's task group). The
    DocumentProcessor queue is only used for wait=False (fire-and-forget).

    Args:
        processor: DocumentProcessor instance (None in read mode).
        args: Tool call arguments (must include ``file_path`` or ``content``).
        extraction_depth: Entity extraction depth.
        forced_domain: One pack to force (``domain``, the single-name form of
            ``packs``; ``generic`` the floor alone, ``__auto__`` detects).
        enable_normalization: Whether to normalise text.
        skip_duplicates: Skip if content hash already exists.
        wait: Call pipeline inline and return when done.
        wait_timeout: Wait timeout in seconds (fire-and-forget branch).
        content: Inline content bytes (bypasses file path).
        enable_vision: Optional vision pipeline toggle.
        auto_confirm: When True the domain-confirmation gate is bypassed
            (``confirmation_required=False`` on the source row). When False
            (default) an auto-detected domain parks the source as
            ``awaiting_confirmation`` until ``confirm_extraction`` is called.
        pipeline: The pipeline callback to invoke.
        sandbox_dir: Directory that client-supplied ``file_path`` values
            must stay inside. ``None`` disables sandbox enforcement (used
            only from test contexts that don't care about this check).
        engine: Engine instance used to re-read the SourceRow after the
            inline pipeline completes. When provided and the source is
            parked (``status=awaiting_confirmation``), the handler returns
            the awaiting payload promptly instead of falling through to a
            misleading timeout. ``None`` skips the park-check (test paths
            that don't wire an engine).
        packs: The packs to force (``[]`` the floor alone); None with no
            ``forced_domain`` detects. Carried down the one-name pipeline as
            the ordered choice (``packs_choice``), which the row write splits.

    """
    try:
        forced_domain = packs_choice(requested_packs(packs, forced_domain))
    except ValueError as exc:
        invalid = {"success": False, "error_code": "INVALID_PACKS", "error": str(exc)}
        return [TextContent(type="text", text=json.dumps(invalid, default=str))]
    if processor is None or pipeline is None:
        result: dict[str, Any] = {
            "success": False,
            "error": "Document upload requires mcp.mode: write",
        }
    else:
        file_path = args.get("file_path", "")
        if not file_path and not content:
            result = {"success": False, "error": "file_path or content is required"}
        else:
            is_url = file_path.startswith(("http://", "https://")) if file_path else False

            # Confine local-file ingests to the sandbox BEFORE the path
            # reaches either the inline pipeline (wait=True) or the processor
            # queue (wait=False). Both read the file raw downstream
            # (engine.add_document → Loaders.load_text), so gating this guard
            # on ``wait`` alone left wait=False as an arbitrary-file-read
            # bypass. resolve_within is idempotent, so the index-only
            # pipeline's own resolve_within of the (now absolute) path is a
            # safe defense-in-depth no-op.
            #
            # The guard is deliberately NOT conditional on ``content``. The
            # tool schema claims that supplying ``content`` means the file is
            # never loaded, but nothing implements that: neither the inline
            # pipeline call below nor the processor's pipeline_callback
            # forwards ``content``, so ``file_path`` is read from disk on
            # every path. Skipping the guard when ``content`` was supplied
            # therefore handed a caller an unsandboxed arbitrary-file read —
            # and a caller supplying both is exactly the attack.
            if not is_url and file_path:
                file_path, guard_error = _resolve_local_ingest_path(file_path, sandbox_dir)
                if guard_error is not None:
                    return [TextContent(type="text", text=json.dumps(guard_error, default=str))]

            if wait:
                # Call pipeline directly. The pipeline contains sync SQLite
                # calls that temporarily block the event loop, but they
                # complete in milliseconds. The async embedding calls yield
                # properly.
                from chaoscypher_core.utils.id import generate_id

                file_id = generate_id()
                try:
                    result = await pipeline(
                        file_path=file_path or "",
                        file_id=file_id,
                        extraction_depth=extraction_depth,
                        forced_domain=forced_domain,
                        enable_normalization=enable_normalization,
                        skip_duplicates=skip_duplicates,
                        is_url=is_url,
                        auto_confirm=auto_confirm,
                    )
                    result.setdefault("success", True)
                    result.setdefault("source_id", file_id)

                    # The gate parks inside the full pipeline by writing
                    # status=awaiting_confirmation on the SourceRow; the
                    # ProcessingResult model can't carry that status, so
                    # re-read the row and surface the awaiting payload
                    # promptly instead of pretending the doc extracted.
                    if engine is not None:
                        row = engine.storage_adapter.get_source(
                            file_id, engine.settings.current_database
                        )
                        if row and row.get("status") == SourceStatus.AWAITING_CONFIRMATION:
                            proposal = row.get("detection_proposal") or {}
                            ranking = proposal.get("ranking") or []
                            detected_domain = (
                                ranking[0]["domain"]
                                if ranking
                                else proposal.get("detected_domain", "")
                            )
                            proposed = proposal_packs(proposal)
                            result = {
                                "success": True,
                                "source_id": file_id,
                                "file_id": file_id,
                                "status": SourceStatus.AWAITING_CONFIRMATION,
                                "packs": proposed,
                                "detected_domain": detected_domain,
                                "confidence": proposal.get("confidence"),
                                "low_confidence": proposal.get("low_confidence", False),
                                "next_steps": (
                                    f"Detection proposed the packs {proposed} "
                                    f"(the floor alone when empty). Call "
                                    f'confirm_extraction with file_id="{file_id}" '
                                    f"to start extraction, or re-add with "
                                    f"auto_confirm=true to skip the gate."
                                ),
                            }
                            return [
                                TextContent(
                                    type="text",
                                    text=json.dumps(result, default=str),
                                )
                            ]
                except Exception as e:
                    from chaoscypher_core.utils.id import generate_id

                    error_id = generate_id()
                    # Split intentionally: ERROR surfaces a correlation id to
                    # the operator; DEBUG carries the full traceback so
                    # production logs stay clean but the detail is retrievable.
                    logger.error(  # noqa: TRY400 - traceback logged separately at DEBUG
                        "add_document_pipeline_failed",
                        error_id=error_id,
                        error_type=type(e).__name__,
                    )
                    logger.debug(
                        "add_document_pipeline_failed_traceback",
                        error_id=error_id,
                        exc_info=True,
                    )
                    result = {
                        "success": False,
                        "error": "Document processing failed",
                        "error_id": error_id,
                    }
            else:
                # Fire-and-forget via processor queue
                result = await processor.add_document(
                    file_path or "",
                    extraction_depth=extraction_depth,
                    forced_domain=forced_domain,
                    enable_normalization=enable_normalization,
                    skip_duplicates=skip_duplicates,
                    wait=False,
                    content=content,
                    enable_vision=enable_vision,
                    auto_confirm=auto_confirm,
                )

        # Guide client to drive extraction when server didn't extract
        if result.get("status") == SourceStatus.INDEXED and result.get("success", True):
            source_id = result.get("source_id", "")
            result["next_steps"] = (
                f"Document indexed for search. To extract entities into the "
                f"knowledge graph, call get_extraction_tasks with "
                f'source_id="{source_id}" to get chunk count and extraction '
                f"instructions, then process each chunk group with "
                f"get_extraction_chunks and submit_chunk_extraction, "
                f"and finally call finalize_extraction to commit."
            )
    return [TextContent(type="text", text=json.dumps(result, default=str))]


async def _handle_wait_for_document(
    doc_processor: DocumentProcessor | None,
    index_only_processor: DocumentProcessor | None,
    args: dict[str, Any],
) -> list[TextContent]:
    """Handle wait_for_document tool call.

    Blocks until the specified document finishes processing.
    Checks both processors since the caller may not know which
    pipeline the document was submitted to.

    This sees only in-memory processor state — a source parked at
    ``awaiting_confirmation`` (persisted in the DB, not held by a processor)
    is reported as not found here. Clients waiting on a possibly-parked
    document should poll ``get_document_status`` instead.

    Args:
        doc_processor: Full pipeline processor (extract_entities=True).
        index_only_processor: Index-only processor (extract_entities=False).
        args: Tool arguments with file_id and optional timeout.

    Returns:
        List containing a single TextContent with JSON result.

    """
    file_id = args.get("file_id", "")
    timeout = args.get("timeout", 300)
    if not file_id:
        result: dict[str, Any] = {"success": False, "error": "file_id is required"}
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    # Check which processor has this file_id pending
    for processor in (index_only_processor, doc_processor):
        if processor is None:
            continue
        if processor.has_pending(file_id):
            wait_result = await processor.wait_for_completion(file_id, timeout)
            result = {"success": True, **wait_result}
            return [TextContent(type="text", text=json.dumps(result, default=str))]

    # Check completed results in both processors
    for processor in (index_only_processor, doc_processor):
        if processor is None:
            continue
        completed = processor.get_completed(file_id)
        if completed:
            result = {"success": True, **completed}
            return [TextContent(type="text", text=json.dumps(result, default=str))]

    result = {"success": False, "error": f"File ID {file_id} not found in any processor"}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _discard_failed_work(engine: Engine) -> None:
    """Discard what a failed call left in the engine's two sessions (graph and storage).

    A tool here that answers a failure and goes on must not leave the next call a session
    needing a rollback (``SafeSession.discard_failed_work``).
    """
    discard_failed_work(engine.graph_repository, engine.storage_adapter.session)


async def _handle_remove_document(
    engine: Engine,
    args: dict[str, Any],
) -> list[TextContent]:
    """Handle remove_document tool call."""
    source_id = args.get("source_id", "")
    if not source_id:
        result: dict[str, Any] = {
            "success": False,
            "error": "source_id is required",
        }
    else:
        try:
            from chaoscypher_core.mcp.memory import remove_source

            deleted = await remove_source(engine, source_id)
            result = {"success": deleted.deleted, "source_id": source_id}
            if not deleted:
                result["error"] = "Source not found"
        except Exception as e:
            _discard_failed_work(engine)
            result = log_tool_failure(
                logger, "mcp_source_tool_failed", e, level="warning", tool="remove_document"
            )
    return [TextContent(type="text", text=json.dumps(result, default=str))]


# Option keys forwarded verbatim to confirm_extraction's overrides dict.
# file_id and domain are pulled out separately; everything else here mirrors
# TriggerExtractionRequest's editable extraction options.
_CONFIRM_OVERRIDE_KEYS = (
    "analysis_depth",
    "filtering_mode",
    "content_filtering",
    "enable_direction_correction",
    "protect_orphans",
    "enable_inverse_relationships",
    "max_entity_degree_override",
)


async def _handle_confirm_extraction(
    engine: Engine,
    args: dict[str, Any],
) -> list[TextContent]:
    """Handle confirm_extraction tool call.

    Confirms (and optionally overrides) the packs for a source parked at
    ``awaiting_confirmation`` and starts extraction. Delegates to the core
    ``confirm_extraction`` primitive, which CAS-transitions
    ``awaiting_confirmation -> indexed``, persists the chosen domain + option
    overrides onto the SourceRow, sets ``extraction_confirmed_at`` write-once,
    and re-queues OP_IMPORT_ANALYSIS. A CAS loss (source no longer awaiting)
    is reported as a NOT_AWAITING_CONFIRMATION conflict.
    """
    file_id = args.get("file_id", "")
    if not file_id:
        result: dict[str, Any] = {"success": False, "error": "file_id is required"}
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    # ``packs`` (a list), or ``domain`` as one element; neither accepts the proposal.
    try:
        chosen = requested_packs(args.get("packs"), args.get("domain"))
    except ValueError as exc:
        result = {"success": False, "error_code": "INVALID_PACKS", "error": str(exc)}
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    overrides = {k: args[k] for k in _CONFIRM_OVERRIDE_KEYS if k in args}

    try:
        ok = await confirm_extraction(
            engine.storage_adapter,
            file_id,
            chosen,
            overrides,
        )
    except Exception as e:
        _discard_failed_work(engine)
        result = log_tool_failure(
            logger, "mcp_confirm_extraction_failed", e, level="warning", tool="confirm_extraction"
        )
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    if not ok:
        result = {
            "success": False,
            "error_code": "NOT_AWAITING_CONFIRMATION",
            "error": (
                f"Source {file_id} is not awaiting confirmation (already "
                f"confirmed, extracting, or not found)."
            ),
            "source_id": file_id,
        }
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    result = {
        "success": True,
        "source_id": file_id,
        "packs": list(chosen) if chosen is not None else None,
        "domain": first_domain_name(chosen) if chosen is not None else None,
        "status": SourceStatus.INDEXED,
    }
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _gate_tool_call(
    name: str, effective_mode: str, tool_defs: Sequence[ToolDefinition]
) -> list[TextContent] | None:
    """Reject a call to a tool the server does not advertise in this mode.

    Allowlist, not denylist, in both modes: the MCP SDK only warns about a
    call to a tool that was never listed, so bridge handlers absent from
    ``TOOL_DEFINITIONS`` (summarize, research_topic, ...) reached
    ``bridge.execute`` — in read mode because they carry no ``write_only``
    flag (fixed first), and in write mode because nothing checked the list
    at all. Read mode answers ``NOT_AUTHORIZED`` (the tool may exist in write
    mode); an unlisted name answers ``UNKNOWN_TOOL``. Both sets are read from the
    served tuple (the profile and allowlist applied), never the global registry.
    """
    if effective_mode == "read" and name not in {t.name for t in tool_defs if not t.write_only}:
        result = {
            "success": False,
            "error_code": "NOT_AUTHORIZED",
            "error": f"Tool '{name}' requires write access",
        }
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    if name not in {t.name for t in tool_defs}:
        result = {
            "success": False,
            "error_code": "UNKNOWN_TOOL",
            "error": f"Unknown tool '{name}'",
        }
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    return None


async def _handle_extraction_tool(
    orchestrator: ExtractionOrchestrator | None,
    method: Any,
    args: dict[str, Any],
) -> list[TextContent]:
    """Handle an extraction orchestrator tool call.

    Routes to the appropriate ExtractionOrchestrator method,
    returning a JSON-encoded TextContent response.

    Args:
        orchestrator: ExtractionOrchestrator instance (None in read mode).
        method: Bound async method to call on the orchestrator.
        args: Tool call arguments to pass to the method.

    Returns:
        List containing a single TextContent with JSON result.

    """
    if orchestrator is None:
        result: dict[str, Any] = {
            "success": False,
            "error": "Extraction tools require mcp.mode: write",
        }
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    try:
        result = await method(**args)
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    except (ValueError, KeyError) as exc:  # fmt: skip
        # The orchestrator's ValueError text is written for the client
        # ("Incomplete: N/M chunks submitted …") but can quote what was
        # submitted; it is replaced by the class's fixed line here, and the
        # log keeps the tool, the error's type and the code (``log_tool_failure``).
        result = log_tool_failure(
            logger,
            "mcp_extraction_tool_failed",
            exc,
            level="warning",
            tool=getattr(method, "__name__", str(method)),
        )
        return [TextContent(type="text", text=json.dumps(result, default=str))]


async def _handle_benchmark_tool(method: Any, args: dict[str, Any]) -> list[TextContent]:
    """Call a :class:`BenchmarkBridge` method and JSON-encode its result.

    The bridge maps its own failures to ``success: false`` payloads; this
    wrapper only turns argument mismatches (a missing or unknown argument)
    into ``INVALID_ARGUMENT`` instead of an exception.
    """
    try:
        result = await method(**args)
    except TypeError as exc:
        result = {"success": False, "error_code": "INVALID_ARGUMENT", "error": str(exc)}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


async def _fetch_url_source(engine: Engine, url: str) -> dict[str, Any]:
    """Fetch and describe a URL source for the MCP index-only pipeline.

    Extracted from the pipeline closure so that callback stays under the
    branch-complexity ceiling. Returns the source-metadata fields the pipeline
    needs, or ``{"error": ...}`` when the URL returns binary content.

    Args:
        engine: Engine (provides settings + the web allowlist/max_bytes).
        url: HTTP(S) URL to scrape.

    Returns:
        Dict with ``text``, ``title``, ``filename``, ``file_type``,
        ``file_size``, ``resolved_path``, ``source_type`` — or ``{"error": ...}``
        when the response was binary.
    """
    # W9 (2026-05-08): URL imports use the unified allowlist + max_bytes, which
    # the shared ``fetch_page`` reads from the engine settings. The MCP path
    # ignores binary responses today (the loader registry would reject them via
    # the ``.md`` extension below). Surface them as an explicit error so future
    # work can decide between staging the bytes or refusing the URL.
    page = await fetch_page(url, engine.settings)
    if page.is_binary:
        return {
            "error": (
                f"URL returned binary content ({page.content_type}); "
                "MCP URL imports require a textual response."
            ),
        }
    text = page.content if page else ""
    # The stored title and filename: the rule every URL import shares.
    names = page_names(page.title, engine.settings, fallback_title=url)
    return {
        "text": text,
        "title": names.title,
        "filename": f"{names.stem}.md",
        "file_type": "md",
        "file_size": len(page.payload),
        "resolved_path": url,  # Store URL as filepath
        "source_type": "webpage",
    }


def _create_pipeline_callback(engine: Engine, *, extract_entities: bool = True) -> Any:
    """Create the document processing pipeline callback.

    This wraps the Engine's add_document method into a single
    async callback for the DocumentProcessor.

    When ``extract_entities`` is True, the full pipeline runs
    (load, chunk, index, extract, commit). When False, only
    load, chunk, and index are performed, leaving the source
    in 'indexed' status ready for MCP-driven extraction.

    Args:
        engine: Engine instance.
        extract_entities: Whether to run entity extraction after indexing.

    Returns:
        Async callback function for processing documents.

    """

    async def pipeline(
        file_path: str,
        file_id: str,
        progress_callback: Any = None,
        extraction_depth: str = "full",
        forced_domain: str | None = None,
        enable_normalization: bool | None = None,
        skip_duplicates: bool = False,
        is_url: bool = False,
        auto_confirm: bool = False,
    ) -> dict[str, Any]:
        """Process a single document through the configured pipeline.

        All file/DB operations are synchronous (fast, millisecond-level).
        Embedding generation is async via httpx. ``asyncio.to_thread`` is safe
        here (the URL fetch's extraction and the duplicate-hash read use it)
        as long as no module is imported for the first time inside the thread:
        that import-lock deadlock was the April 2026 hang (``mcp/sites.py``).
        """
        if extract_entities:
            # Full pipeline: load, chunk, index, extract, commit. Forward
            # ``auto_confirm`` / ``forced_domain`` so the engine's confirmation
            # gate engages: with auto_confirm=False and an auto-detected domain
            # the source PARKS at awaiting_confirmation between index and
            # extraction instead of extracting immediately (the gate bypass
            # this path previously had). The wait=True handler re-reads the
            # SourceRow and surfaces the awaiting payload.
            # ``extraction_depth`` maps onto the engine's ``analysis_depth``;
            # clamp to the schema's enum so an unexpected value degrades to
            # the default rather than reaching the engine.
            result = await engine.add_document(
                filepath=file_path,
                source_id=file_id,
                analysis_depth="quick" if extraction_depth == "quick" else "full",
                auto_confirm=auto_confirm,
                forced_domain=forced_domain,
            )
            return result.model_dump()

        # Index-only: create source, load, chunk, index (no extraction).
        # Delegated to a module-level helper to keep this closure under the
        # branch-complexity ceiling.
        return await _run_index_only_pipeline(
            engine,
            file_path=file_path,
            file_id=file_id,
            extraction_depth=extraction_depth,
            forced_domain=forced_domain,
            enable_normalization=enable_normalization,
            skip_duplicates=skip_duplicates,
            is_url=is_url,
            auto_confirm=auto_confirm,
        )

    return pipeline


async def _run_index_only_pipeline(
    engine: Engine,
    *,
    file_path: str,
    file_id: str,
    extraction_depth: str,
    forced_domain: str | None,
    enable_normalization: bool | None,
    skip_duplicates: bool,
    is_url: bool,
    auto_confirm: bool,
) -> dict[str, Any]:
    """Create-source → load → chunk → index a document WITHOUT extracting.

    Leaves the source at ``indexed`` ready for MCP-driven (client) extraction.
    The confirmation-gate flag (``confirmation_required``) is persisted on the
    source row so the later client-driven ``get_extraction_tasks`` gate sees
    the upload-time ``auto_confirm`` choice; this path itself never parks.

    File metadata and DB operations are synchronous (millisecond-level); the
    content read for the duplicate hash is offloaded with ``asyncio.to_thread``
    because a file can be hundreds of MB, and embedding generation is async.
    """
    import hashlib

    from chaoscypher_core.utils import generate_id

    db_name = engine.settings.current_database

    # URL import: fetch content via web scraper.
    title = ""
    if is_url:
        url_source = await _fetch_url_source(engine, file_path)
        if "error" in url_source:
            return url_source
        text = url_source["text"]
        title = url_source["title"]
        filename = url_source["filename"]
        file_type = url_source["file_type"]
        file_size = url_source["file_size"]
        resolved_path = url_source["resolved_path"]
        source_type = url_source["source_type"]
    else:
        # Sync pathlib operations are intentional: stat/name/suffix are
        # millisecond-level (see docstring).
        from chaoscypher_core.utils.safe_paths import resolve_within

        sandbox = engine.settings.paths.mcp_dir
        filepath = resolve_within(sandbox, file_path)
        resolved_path = str(filepath)
        file_size = filepath.stat().st_size
        filename = filepath.name
        file_type = filepath.suffix.lstrip(".") or "txt"
        source_type = "file"
        text = ""  # Loaded below

    # Skip duplicates via content hash
    content_hash: str | None
    if skip_duplicates:
        if is_url:
            content_for_hash = text.encode("utf-8")
        else:
            # Offload the blocking read — files can be hundreds of MB; a sync
            # read would block the MCP event loop for every concurrent call.
            content_for_hash = await asyncio.to_thread(filepath.read_bytes)
        content_hash = hashlib.sha256(content_for_hash).hexdigest()
        existing = engine.storage_adapter.find_by_content_hash(db_name, content_hash)
        if existing:
            return {
                "source_id": existing["id"],
                "status": existing.get("status", SourceStatus.COMMITTED),
                "skipped_duplicate": True,
                "nodes": [],
                "edges": [],
                "templates": [],
            }
    else:
        content_hash = None

    # Create source record BEFORE chunking (FK constraint on document_chunks)
    source_data: dict[str, Any] = {
        "id": file_id or generate_id(),
        "database_name": db_name,
        "filename": filename,
        "filepath": str(resolved_path),
        "file_type": file_type,
        "file_size": file_size,
        "status": SourceStatus.PENDING,
        "source_type": source_type,
        "extraction_depth": extraction_depth,
    }
    if forced_domain:
        source_data["forced_domain"] = forced_domain
    if content_hash:
        source_data["content_hash"] = content_hash
    if is_url:
        source_data["origin_url"] = file_path
        source_data["title"] = title
    # ``auto_confirm=False`` (default) means an auto-detected domain will park
    # the source at awaiting_confirmation for human review. Setting ``True``
    # bypasses the gate (mirrors cortex auto_confirm logic).
    if not forced_domain:
        source_data["confirmation_required"] = not auto_confirm
    engine.storage_adapter.create_source(source_data)

    # Use lifecycle methods (same as internal pipeline). From here on a
    # failure must mark the row failed — without ``fail_indexing`` a loader
    # or embedding error left the source stuck at 'indexing' forever.
    engine.storage_adapter.start_indexing(file_id)
    try:
        return await index_source_text(
            engine,
            file_id=file_id,
            file_path=file_path,
            is_url=is_url,
            text=text,
            file_type=file_type,
            enable_normalization=enable_normalization,
        )
    except Exception as exc:
        engine.storage_adapter.fail_indexing(file_id, f"{type(exc).__name__}: {exc}")
        raise


def _extract_effective_mode(server: Server, default: str) -> str:
    """Read ``effective_mcp_mode`` from the current ASGI scope.

    Returns *default* (the server's static mode) when no scope is
    attached, which is the stdio transport case.

    Args:
        server: The MCP ``Server`` instance (used to reach the current
            request context via the MCP SDK).
        default: Fallback mode when no ASGI scope is attached.

    Returns:
        The effective MCP mode for the current request.

    """
    try:
        ctx = server.request_context
    except Exception:
        return default
    try:
        request = ctx.request
        if request is None:
            return default
        scope = request.scope
    except Exception:
        return default
    mode_val = scope.get("state", {}).get("effective_mcp_mode", default)
    return str(mode_val) if mode_val is not None else default
