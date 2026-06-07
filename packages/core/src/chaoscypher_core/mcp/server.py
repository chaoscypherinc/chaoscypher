# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP Server factory for Chaos Cypher.

Provides ``create_mcp_server()`` which wires an Engine instance to
an MCP Server with all tool handlers registered. Supports both
stdio and Streamable HTTP transports.
"""

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from mcp.server import Server
from mcp.types import TextContent, Tool

from chaoscypher_core.mcp.bridge import MCPToolBridge
from chaoscypher_core.mcp.extraction import ExtractionOrchestrator
from chaoscypher_core.mcp.processor import DocumentProcessor
from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS, get_tools_for_mode
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.importing.confirmation_gate import confirm_extraction
from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService


if TYPE_CHECKING:
    from chaoscypher_core.bootstrap import Engine

logger = structlog.get_logger(__name__)


def create_mcp_server(engine: Engine) -> Server:
    """Create a configured MCP Server from an Engine instance.

    Wires the ToolExecutorService with engine repositories, registers all
    tools (filtered by mcp.mode), and sets up the document processor.

    Args:
        engine: Initialized Engine instance with all repos connected.

    Returns:
        Configured MCP Server ready for transport binding.

    """
    settings = engine.settings
    mode = settings.mcp.mode

    # Build optional embedding callback
    embedding_callback = None
    if engine.embedding_service:

        async def _embed(text: str) -> Any:
            """Forward to the engine's embedding service for MCP tool callbacks."""
            return await engine.embedding_service.embed(text)

        embedding_callback = _embed

    # Create ToolExecutorService
    tool_executor = ToolExecutorService(
        graph_repository=engine.graph_repository,
        search_repository=engine.search_repository,
        indexing_repository=engine.storage_adapter,
        embedding_callback=embedding_callback,
        engine_settings=settings,
        source_storage=engine.storage_adapter,
        scope={"database_name": settings.current_database},
    )

    bridge = MCPToolBridge(tool_executor=tool_executor)

    # Pipeline callbacks and document processors (only active in write mode)
    full_pipeline: Any = None
    index_only_pipeline: Any = None
    doc_processor: DocumentProcessor | None = None
    index_only_processor: DocumentProcessor | None = None
    if mode == "write":
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

    # Extraction orchestrator (only active in write mode)
    extraction_orchestrator: ExtractionOrchestrator | None = None
    if mode == "write":
        extraction_orchestrator = ExtractionOrchestrator(engine=engine)

    # Build MCP Server
    server = Server("chaoscypher")
    tool_defs = get_tools_for_mode(mode)

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

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None = None) -> list[TextContent]:
        """Route a tool call to the appropriate handler."""
        args = arguments or {}

        # Effective-mode gating (per-key downgrade).
        effective_mode = _extract_effective_mode(server, default=mode)
        if effective_mode == "read":
            write_only_tools = {t.name for t in TOOL_DEFINITIONS if t.write_only}
            if name in write_only_tools:
                result = {
                    "success": False,
                    "error_code": "NOT_AUTHORIZED",
                    "error": f"Tool '{name}' requires write access",
                }
                return [TextContent(type="text", text=json.dumps(result, default=str))]

        # Handle MCP-only tools
        if name == "get_document_status":
            return _handle_document_status(engine, effective_mode=effective_mode)
        if name == "add_document":
            extract_entities = args.pop("extract_entities", settings.mcp.auto_extract)
            extraction_depth = args.pop("extraction_depth", "full")
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

        # Delegate to ToolExecutorService via bridge
        bridge_result = await bridge.execute(name, args)
        return [TextContent(type="text", text=bridge_result.text)]

    logger.info(
        "mcp_server_created",
        mode=mode,
        tool_count=len(tool_defs),
    )
    return server


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
        forced_domain: Optional domain override.
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

    """
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
            if not content and not is_url and file_path:
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
                            result = {
                                "success": True,
                                "source_id": file_id,
                                "file_id": file_id,
                                "status": SourceStatus.AWAITING_CONFIRMATION,
                                "detected_domain": detected_domain,
                                "confidence": proposal.get("confidence"),
                                "low_confidence": proposal.get("low_confidence", False),
                                "next_steps": (
                                    f"Detection proposed domain "
                                    f'"{detected_domain}". Call confirm_extraction '
                                    f'with file_id="{file_id}" to start '
                                    f"extraction, or re-add with "
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
            from chaoscypher_core.services.graph.management.source import (
                SourceService,
            )

            sms = SourceService(
                repository=engine.storage_adapter,
                database_name=engine.settings.current_database,
            )
            deleted = sms.delete_source(
                source_id,
                graph_repo=engine.graph_repository,
                search_repo=engine.search_repository,
            )
            result = {"success": deleted, "source_id": source_id}
            if not deleted:
                result["error"] = "Source not found"
        except Exception as e:
            logger.warning("mcp_source_tool_failed", error=str(e))
            result = {"success": False, "error": "Operation failed"}
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

    Confirms (and optionally overrides) the domain for a source parked at
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

    chosen_domain = args.get("domain")
    overrides = {k: args[k] for k in _CONFIRM_OVERRIDE_KEYS if k in args}

    try:
        ok = await confirm_extraction(
            engine.storage_adapter,
            file_id,
            chosen_domain,
            overrides,
        )
    except Exception as e:
        logger.warning("mcp_confirm_extraction_failed", error=str(e))
        result = {"success": False, "error": "Confirm failed"}
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
        "domain": chosen_domain,
        "status": SourceStatus.INDEXED,
    }
    return [TextContent(type="text", text=json.dumps(result, default=str))]


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
    except (ValueError, KeyError):  # fmt: skip
        result = {"success": False, "error": "Tool execution failed"}
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
    from chaoscypher_core.adapters.web.search import WebScraper

    # W9 (2026-05-08): URL imports use the unified allowlist + max_bytes. The
    # MCP path ignores binary responses today (the loader registry would reject
    # them via the ``.md`` extension below). Surface them as an explicit error
    # so future work can decide between staging the bytes or refusing the URL.
    mcp_batching = getattr(engine.settings, "batching", None)
    mcp_allowlist = list(getattr(mcp_batching, "upload_content_type_allowlist", []) or [])
    mcp_max_bytes_raw = getattr(mcp_batching, "max_upload_bytes", None)
    mcp_max_bytes = int(mcp_max_bytes_raw) if isinstance(mcp_max_bytes_raw, int) else None
    scraper = WebScraper(
        allowlist=mcp_allowlist,
        max_bytes=mcp_max_bytes,
        web_settings=getattr(engine.settings, "web", None),
    )
    page = await scraper.extract_full_content(url, max_bytes=mcp_max_bytes)
    if page.is_binary:
        return {
            "error": (
                f"URL returned binary content ({page.content_type}); "
                "MCP URL imports require a textual response."
            ),
        }
    text = page.content if page else ""
    title = page.title or url
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)[
        : engine.settings.paths.max_filename_length
    ]
    return {
        "text": text,
        "title": title,
        "filename": f"{safe_title}.md",
        "file_type": "md",
        "file_size": len(text.encode("utf-8")),
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
        Embedding generation is async via httpx. No ``asyncio.to_thread``
        calls — they deadlock under the MCP server's anyio event loop.
        """
        if extract_entities:
            # Full pipeline: load, chunk, index, extract, commit. Forward
            # ``auto_confirm`` / ``forced_domain`` so the engine's confirmation
            # gate engages: with auto_confirm=False and an auto-detected domain
            # the source PARKS at awaiting_confirmation between index and
            # extraction instead of extracting immediately (the gate bypass
            # this path previously had). The wait=True handler re-reads the
            # SourceRow and surfaces the awaiting payload.
            result = await engine.add_document(
                filepath=file_path,
                source_id=file_id,
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

    All file/DB operations are synchronous (millisecond-level); only embedding
    generation is async. No ``asyncio.to_thread`` on the DB path — it deadlocks
    under the MCP server's anyio event loop.
    """
    import hashlib
    from pathlib import Path

    from chaoscypher_core.services.sources.loaders.facade import Loaders
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
        # Sync pathlib operations are intentional: asyncio.to_thread
        # deadlocks under MCP server's anyio event loop (see docstring).
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
            content_for_hash = await asyncio.to_thread(Path(file_path).read_bytes)
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

    # Use lifecycle methods (same as internal pipeline)
    engine.storage_adapter.start_indexing(file_id)

    # Load text (files only — URL text already fetched above)
    if not is_url:
        text = Loaders.load_text(str(file_path), settings=engine.settings)

    # Resolve the tri-state ``enable_normalization`` the same way the CLI
    # (sources/service.py) and Cortex (indexing_handler.py) do — ``None``
    # defers to ``resolve_normalization_default(filename)``, which returns True
    # for prose and False for structured formats (CSV / TSV / JSON / JSONL /
    # NDJSON / XML). Pre-fix the MCP default was hardcoded True, which
    # corrupted structured uploads by stripping their whitespace as OCR noise.
    if enable_normalization is None:
        from chaoscypher_core.utils.normalization_default import (
            resolve_normalization_default,
        )

        enable_normalization = resolve_normalization_default(
            filename=Path(file_path).name if file_path else ""
        )

    # Normalize content if enabled
    if enable_normalization and text:
        from chaoscypher_core.services.sources.normalizer.service import (
            ContentNormalizerService,
        )

        normalizer = ContentNormalizerService(settings=engine.settings)
        normalized = normalizer.normalize(content=text, content_type=file_type)
        text = normalized.content

    # Chunk
    await engine.chunking_service.create_chunks(full_text=text, source_id=file_id)

    # Index (generate embeddings)
    index_result = await engine.indexing_service.create_index(source_id=file_id)

    # Complete indexing with proper metadata
    engine.storage_adapter.complete_indexing(
        file_id,
        chunks_count=index_result.get("chunks_count", 0),
        embedding_model=index_result.get("embedding_model", ""),
        embedding_dimensions=index_result.get("embedding_dimensions", 0),
    )

    return {
        "source_id": file_id,
        "status": SourceStatus.INDEXED,
        "nodes": [],
        "edges": [],
        "templates": [],
    }


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
