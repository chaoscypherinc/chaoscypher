# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Pipeline - Orchestrates full source processing with progress UI.

Provides a simple terminal UI for the source processing pipeline with:
- Spinner per stage (via Rich console.status)
- Completion status lines with stats
- Summary panel at completion
- Error handling with helpful messages
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from chaoscypher_cli.utils.display import get_quality_color
from chaoscypher_core.models import SourceStatus


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_cli.sources.service import CLISourceProcessingService
    from chaoscypher_core.ports.types import FilteringMode


@dataclass
class PipelineResult:
    """Result of a pipeline run."""

    file_id: str
    filename: str
    success: bool
    status: str
    stages_completed: list[str] = field(default_factory=list)
    stages_skipped: list[str] = field(default_factory=list)
    chunks_count: int = 0
    tokens_count: int = 0
    failed_embeddings: int = 0
    entities_count: int = 0
    relationships_count: int = 0
    nodes_created: int = 0
    edges_created: int = 0
    duration_seconds: float = 0.0
    error: str | None = None

    # LLM Metrics (populated during extraction)
    llm_total_calls: int = 0
    llm_successful_calls: int = 0
    llm_failed_calls: int = 0
    llm_retry_calls: int = 0
    llm_total_input_tokens: int = 0
    llm_total_output_tokens: int = 0
    llm_wasted_tokens: int = 0
    llm_estimated_cost_usd: float = 0.0
    llm_model: str = ""
    extraction_mode: str = ""
    llm_retry_rate: float = 0.0
    llm_success_rate: float = 0.0

    # Quality scores (populated after extraction)
    quality_grade: float | None = None
    quality_label: str | None = None

    # Domain (populated during extraction)
    detected_domain: str | None = None

    # Confirmation gate (populated when the gate runs)
    parked_for_confirmation: bool = False
    detection_confidence: float | None = None
    detection_ranking: list[dict[str, Any]] = field(default_factory=list)
    detection_low_confidence: bool = False

    # Warnings captured during pipeline execution
    warnings: list[str] = field(default_factory=list)


class _WarningCapture(logging.Handler):
    """Logging handler that captures warning messages to a list."""

    # Map raw structlog event names to user-friendly messages.
    # Only events that are user-actionable or informative are mapped here;
    # internal parsing/retry noise is intentionally omitted.
    _FRIENDLY_MESSAGES: ClassVar[dict[str, str]] = {
        # -- Extraction warnings --
        "chunk_has_entities_but_no_relationships": (
            "Some chunks had entities but no relationships extracted"
        ),
        "harvest_relationships_skipped_no_entities": (
            "Relationship extraction skipped for chunks with no entities"
        ),
        "group_extraction_failed": "Entity extraction failed for a chunk group",
        "extraction_failed": "Entity extraction failed for a chunk",
        "llm_extraction_stream_error": "Error during LLM extraction streaming",
        "stream_loop_detected_aborting": "Repetition loop detected in extraction output",
        "domain_analysis_failed": "Domain auto-detection failed, using generic domain",
        "no_domains_available": "No extraction domains available",
        # -- Template warnings --
        "domain_node_templates_empty_using_fallback": (
            "No domain node templates found, using fallback"
        ),
        "domain_edge_templates_empty_using_fallback": (
            "No domain edge templates found, using fallback"
        ),
        "domain_node_templates_lookup_failed": "Failed to look up domain node templates",
        "domain_edge_templates_lookup_failed": "Failed to look up domain edge templates",
        "edge_template_creation_failed": "Failed to create an edge template during commit",
        "template_invalid_name_skipped": "A template with an invalid name was skipped",
        # -- Embedding warnings --
        "embedding_generation_failed": "Some entity embeddings failed to generate",
        "embedding_failed_in_batch": "Some embeddings failed in batch processing",
        "embeddings_not_supported": "Current LLM provider doesn't support embeddings",
        "embeddings_load_failed": "Failed to load existing embeddings",
        "batch_embed_not_supported_fallback_sequential": (
            "Batch embedding not supported, falling back to sequential"
        ),
        "chunks_missing_embeddings_skipped": "Some chunks skipped due to missing embeddings",
        # -- LLM / provider warnings --
        "ollama_embedding_failed": "Ollama embedding request failed",
        "ollama_health_check_failed": "Ollama health check failed — is Ollama running?",
        "anthropic_embeddings_fallback": (
            "Anthropic embeddings not available, using fallback provider"
        ),
        "llm_response_empty": "LLM returned an empty response",
        "provider_unhealthy_backing_off": "LLM provider unhealthy, backing off",
        "unknown_provider_fallback": "Unknown LLM provider, using fallback",
        "structured_extraction_quality_issues_detected": (
            "Quality issues detected in extraction results"
        ),
        "structured_extraction_significant_quality_issues": (
            "Significant quality issues in extraction results"
        ),
        # -- Loader warnings --
        "document_no_content_loaded": "No content could be extracted from the document",
        "no_loader_available": "No loader available for this file type",
        "file_not_found": "Source file not found",
        "archive_loading_failed": "Failed to load archive file",
        "unstructured_package_not_available": (
            "Unstructured package not installed (some file formats may not load)"
        ),
        # -- Normalizer warnings --
        "ftfy_not_installed": "Text normalization library (ftfy) not installed",
        # -- Deduplication warnings --
        "semantic_deduplication_failed_fallback": (
            "Semantic deduplication failed, using exact matching"
        ),
        # -- Commit warnings --
        "no_chunks_found_for_file": "No chunks found for file during commit",
        "no_source_citations_created": "No source citations were created",
    }

    def __init__(self) -> None:
        """Initialize warning capture handler."""
        super().__init__(level=logging.WARNING)
        self.captured: list[str] = []
        self._seen: set[str] = set()

    def emit(self, record: logging.LogRecord) -> None:
        """Capture warning/error log records, deduplicating by event name."""
        msg = record.getMessage()
        # Extract structlog event name (first bracket-delimited token or raw message)
        event = msg.split("]")[-1].strip().split()[0] if "]" in msg else msg.split()[0]

        if event in self._seen:
            return
        self._seen.add(event)

        friendly = self._FRIENDLY_MESSAGES.get(event)
        if friendly:
            self.captured.append(friendly)


class SourcePipeline:
    """Orchestrates the full source processing workflow with progress UI.

    Uses simple console.status() spinners for each stage. No Live panel,
    no stdout redirection, no handler swapping — just straightforward
    output that works reliably with structlog and SQLite.

    Example:
        ctx = get_context()
        service = CLISourceProcessingService(ctx)
        pipeline = SourcePipeline(service, console)

        result = pipeline.run(
            file_path=Path("document.pdf"),
            skip_extract=False,
            skip_commit=False,
        )
    """

    def __init__(
        self,
        service: CLISourceProcessingService,
        console: Console | None = None,
    ):
        """Initialize pipeline.

        Args:
            service: CLI source processing service
            console: Rich console for output
        """
        self.service = service
        self.console = console or Console()

    def run(
        self,
        file_path: Path | None = None,
        file_id: str | None = None,
        url: str | None = None,
        skip_index: bool = False,
        skip_extract: bool = False,
        skip_commit: bool = False,
        skip_embeddings: bool = False,
        enable_normalization: bool | None = None,
        enable_vision: bool = True,
        content_filtering: bool = True,
        index_only: bool = False,
        extract_only: bool = False,
        extraction_depth: str = "full",
        domain: str | None = None,
        filtering_mode: FilteringMode | None = None,
        quiet: bool = False,
        verbose: bool = False,
        skip_duplicates: bool = False,
        no_confirm: bool = False,
    ) -> PipelineResult:
        """Run the source processing pipeline.

        Args:
            file_path: Path to new file (mutually exclusive with file_id and url)
            file_id: Existing file ID to resume
            url: URL to fetch and import (mutually exclusive with file_path)
            skip_index: Skip indexing stage
            skip_extract: Skip extraction stage
            skip_commit: Skip commit stage
            skip_embeddings: Skip embedding generation during indexing (faster)
            enable_normalization: Enable content normalization. ``None``
                (default) defers to the file-type default; ``True`` /
                ``False`` is an explicit user override and is persisted
                on the source row (Workstream 1, 2026-05-07).
            enable_vision: Enable vision LLM processing for image-heavy documents
            content_filtering: Apply domain content-exclusion rules
                during extraction (default ``True``).
            index_only: Stop after indexing
            extract_only: Stop after extraction
            extraction_depth: Extraction depth (quick, full)
            domain: Domain for extraction (None or 'auto' = auto-detect)
            filtering_mode: Filtering mode preset override (overrides domain default)
            quiet: Minimal output
            verbose: Show real-time structlog output during execution
            skip_duplicates: Skip upload if identical content (by SHA-256) already exists.
                When a duplicate is detected the pipeline prints a message and returns
                a successful result without running indexing / extraction / commit.
            no_confirm: Bypass the domain-confirmation gate: run extraction with the
                auto-detected domain without prompting or parking.

        Returns:
            PipelineResult with stats and status
        """
        start_time = time.time()

        # Determine what stages to run
        # URL imports also need the upload stage (url acts like file_path for staging)
        stages = self._determine_stages(
            file_id=file_id if not url else None,
            skip_index=skip_index,
            skip_extract=skip_extract,
            skip_commit=skip_commit,
            index_only=index_only,
            extract_only=extract_only,
        )

        # Initialize result
        result = PipelineResult(
            file_id=file_id or "",
            filename=file_path.name if file_path else (url or ""),
            success=False,
            status=SourceStatus.PENDING,
        )

        # Capture warnings during pipeline execution to show in summary
        capture = _WarningCapture()
        root_logger = logging.getLogger()
        root_logger.addHandler(capture)
        # Suppress warnings from printing to stderr during UI stages
        original_level = root_logger.level
        if not quiet and not verbose:
            root_logger.setLevel(logging.ERROR)

        try:
            if quiet:
                result = self._run_quiet(
                    file_path=file_path,
                    file_id=file_id,
                    url=url,
                    stages=stages,
                    extraction_depth=extraction_depth,
                    domain=domain,
                    skip_embeddings=skip_embeddings,
                    enable_normalization=enable_normalization,
                    enable_vision=enable_vision,
                    content_filtering=content_filtering,
                    result=result,
                    filtering_mode=filtering_mode,
                    skip_duplicates=skip_duplicates,
                    no_confirm=no_confirm,
                )
            else:
                result = self._run_with_ui(
                    file_path=file_path,
                    file_id=file_id,
                    url=url,
                    stages=stages,
                    extraction_depth=extraction_depth,
                    domain=domain,
                    skip_embeddings=skip_embeddings,
                    enable_normalization=enable_normalization,
                    enable_vision=enable_vision,
                    content_filtering=content_filtering,
                    result=result,
                    filtering_mode=filtering_mode,
                    skip_duplicates=skip_duplicates,
                    no_confirm=no_confirm,
                )

            # Preserve gate/duplicate statuses set by the stage helpers.
            if result.status not in ("skipped_duplicate", "awaiting_confirmation", "cancelled"):
                result.success = result.error is None
                result.status = "completed" if result.success else "failed"

        except Exception as e:
            result.error = str(e)
            result.status = "failed"

        finally:
            root_logger.removeHandler(capture)
            root_logger.setLevel(original_level)

        result.warnings = capture.captured
        result.duration_seconds = time.time() - start_time

        if not quiet and result.status != "skipped_duplicate":
            self._show_summary(result)

        return result

    def _determine_stages(
        self,
        file_id: str | None,
        skip_index: bool,
        skip_extract: bool,
        skip_commit: bool,
        index_only: bool,
        extract_only: bool,
    ) -> list[str]:
        """Determine which stages to run based on flags and file status."""
        stages = []

        # If no file_id, we need upload
        if not file_id:
            stages.append("upload")

        # Index stage
        if not skip_index:
            stages.append("index")

        # Extract stage
        if not skip_extract and not index_only:
            stages.append("extract")

        # Commit stage
        if not skip_commit and not index_only and not extract_only:
            stages.append("commit")

        return stages

    @staticmethod
    def _render_duplicate_skip(
        skip_info: dict[str, Any],
        console: Any,
    ) -> None:
        """Render a human-readable message when an upload is skipped as a duplicate.

        Prints a dim informational line for non-error duplicates.
        When the existing source is in error state, suggests
        ``chaoscypher source delete <id>`` to clear it before retrying (no
        dedicated ``retry`` subcommand exists today).

        Args:
            skip_info: Dict with ``skipped_duplicate=True``, ``id``, ``existing_status``.
            console: Rich console instance.
        """
        source_id = skip_info.get("id", "unknown")
        existing_status = skip_info.get("existing_status", "unknown")
        filename = skip_info.get("filename", "file")

        if existing_status == "error":
            console.print(
                f"[yellow]Skipped {filename}: identical content already exists as "
                f"[bold]{source_id}[/bold] (status: error).[/yellow]\n"
                f"[yellow]To retry it, delete and re-add: "
                f"[cyan]chaoscypher source delete {source_id}[/cyan][/yellow]"
            )
        else:
            console.print(
                f"[dim]Skipped {filename}: identical content already exists as "
                f"[bold]{source_id}[/bold] (status: {existing_status}).[/dim]"
            )

    def _handle_duplicate_skip(
        self,
        skip_info: dict[str, Any],
        result: PipelineResult,
    ) -> PipelineResult:
        """Handle a duplicate-skip upload result: print message, mark result, return early.

        Args:
            skip_info: Dict with ``skipped_duplicate=True``, ``id``, ``existing_status``.
            result: PipelineResult to populate (mutated in place).

        Returns:
            The mutated result (success=True, status='skipped_duplicate').
        """
        self._render_duplicate_skip(skip_info, self.console)
        result.file_id = skip_info.get("id", "")
        result.filename = skip_info.get("filename", result.filename)
        result.stages_skipped.append("upload")
        result.status = "skipped_duplicate"
        # Treat as success so the process exits 0 — the file is already present.
        result.success = True
        return result

    def _run_quiet(
        self,
        file_path: Path | None,
        file_id: str | None,
        stages: list[str],
        extraction_depth: str,
        domain: str | None,
        skip_embeddings: bool,
        enable_normalization: bool | None,
        enable_vision: bool,
        content_filtering: bool,
        result: PipelineResult,
        url: str | None = None,
        filtering_mode: FilteringMode | None = None,
        skip_duplicates: bool = False,
        no_confirm: bool = False,
    ) -> PipelineResult:
        """Run pipeline with minimal output."""
        # W1 (2026-05-07): collect upload-row settings once and forward
        # to upload_file / upload_url so the row carries the user's
        # exact choices. ``dict[str, Any]`` because the values have
        # heterogeneous types (str | bool | None) and mypy can't infer
        # per-key types through ``**kwargs`` spread.
        upload_kwargs: dict[str, Any] = {
            "extraction_depth": extraction_depth,
            "domain": domain,
            "skip_duplicates": skip_duplicates,
            "enable_normalization": enable_normalization,
            "enable_vision": enable_vision,
            "content_filtering": content_filtering,
            "filtering_mode": filtering_mode or "balanced",
        }

        # Upload
        if "upload" in stages and (file_path or url):
            if url:
                upload_result, page_title = self.service.upload_url(
                    url,
                    **upload_kwargs,
                )
                if isinstance(upload_result, dict) and upload_result.get("skipped_duplicate"):
                    return self._handle_duplicate_skip(upload_result, result)
                assert isinstance(upload_result, str)  # narrowed: skip dict handled above
                file_id = upload_result
                result.file_id = file_id
                result.filename = page_title
            elif file_path:
                upload_result = self.service.upload_file(
                    file_path,
                    **upload_kwargs,
                )
                if isinstance(upload_result, dict) and upload_result.get("skipped_duplicate"):
                    return self._handle_duplicate_skip(upload_result, result)
                assert isinstance(upload_result, str)  # narrowed: skip dict handled above
                file_id = upload_result
                result.file_id = file_id
                result.filename = file_path.name
            result.stages_completed.append("upload")

        if not file_id:
            result.error = "No file ID - upload failed or not provided"
            return result

        # Index
        if "index" in stages:
            index_result = self.service.index_file(
                file_id,
                skip_embeddings=skip_embeddings,
                enable_normalization=enable_normalization,
                enable_vision=enable_vision,
            )
            result.chunks_count = index_result.get("chunks_count", 0)
            result.tokens_count = index_result.get("tokens_count", 0)
            result.failed_embeddings = index_result.get("failed_embeddings", 0)
            result.stages_completed.append("index")

        # Extract
        if "extract" in stages:
            if not self._gate_before_extract(file_id, result, no_confirm=no_confirm, quiet=True):
                return result
            if not self.service.has_llm:
                result.stages_skipped.append("extract")
            else:
                extract_result, llm_summary = self.service.extract_entities(
                    file_id,
                    filtering_mode=filtering_mode,
                )
                stats = extract_result.get("stats", {})
                result.entities_count = stats.get("entities_count", 0)
                result.relationships_count = stats.get("relationships_count", 0)
                result.detected_domain = stats.get("detected_domain")
                result.stages_completed.append("extract")
                # Populate LLM metrics
                self._populate_llm_metrics(result, llm_summary)
                # Read cached quality scores (already computed by service.extract_entities)
                source_file = self.service.ctx.storage_adapter.get_file(
                    file_id, self.service.ctx.database_name
                )
                if source_file:
                    result.quality_grade = source_file.get("cached_quality_grade")
                    result.quality_label = source_file.get("cached_quality_label")

        # Commit
        if "commit" in stages:
            commit_result = self.service.commit_to_graph(file_id)
            result.nodes_created = commit_result.get("nodes_created", 0)
            result.edges_created = commit_result.get("edges_created", 0)
            result.stages_completed.append("commit")

        return result

    def _format_stage_line(
        self,
        step: int,
        total_steps: int,
        icon: str,
        color: str,
        label: str,
        detail: str = "",
        elapsed: float | None = None,
    ) -> str:
        """Format a single stage output line.

        Args:
            step: Current step number (1-based)
            total_steps: Total number of steps
            icon: Status icon (✓, ✗, -)
            color: Rich color for the icon
            label: Stage label (Upload, Index, etc.)
            detail: Optional detail text (e.g. "703 chunks, 60k tokens")
            elapsed: Optional elapsed time in seconds

        Returns:
            Formatted Rich markup string
        """
        prefix = f"  [dim]\\[{step}/{total_steps}][/dim]"
        icon_str = f"[{color}]{icon}[/{color}]"
        detail_str = f" [dim]·[/dim] {detail}" if detail else ""
        time_str = f"  [dim]{elapsed:.1f}s[/dim]" if elapsed is not None else ""
        return f"{prefix} {icon_str} {label}{detail_str}{time_str}"

    def _gate_before_extract(  # noqa: PLR0911 - one return per confirmation-gate early-exit branch
        self,
        file_id: str,
        result: PipelineResult,
        *,
        no_confirm: bool,
        quiet: bool = False,
    ) -> bool:
        """Run the confirmation gate between index and extract.

        Returns True to proceed to extraction, False to stop (parked or
        cancelled). On park, sets ``result.parked_for_confirmation`` and an
        actionable error so the caller exits non-zero.

        Honours the unified rule: a forced domain (already on the row) skips
        the gate entirely; auto/unforced + no bypass either prompts (TTY) or
        parks (non-TTY / quiet).
        """
        import sys

        # LLM not configured → extraction will be skipped anyway; gate is a no-op.
        if not self.service.has_llm:
            return True

        file_record = self.service.ctx.storage_adapter.get_file(
            file_id, self.service.ctx.database_name
        )
        # Forced domain (set by --domain or a prior confirm) bypasses the gate.
        if file_record and file_record.get("forced_domain"):
            return True
        if no_confirm:
            # Bypass: detect for the recommendation, proceed with the auto pick.
            rec = self.service.detect_domain_for_source(file_id)
            if rec:
                result.detected_domain = rec["detected_domain"]
                result.detection_confidence = rec["confidence"]
                result.detection_ranking = rec["ranking"]
                result.detection_low_confidence = rec["low_confidence"]
            return True

        rec = self.service.detect_domain_for_source(file_id)
        if rec is None:
            # No chunks to detect against — let extract handle the no-op.
            return True

        result.detected_domain = rec["detected_domain"]
        result.detection_confidence = rec["confidence"]
        result.detection_ranking = rec["ranking"]
        result.detection_low_confidence = rec["low_confidence"]

        # Non-interactive guard (mirror __main__.py:380). The console is the
        # quiet/UI console; quiet mode and non-TTY both forbid prompting.
        is_tty = sys.stdin.isatty() and sys.stderr.isatty()
        if not is_tty:
            self._park(file_id, rec, result, quiet=quiet)
            return False

        chosen = self._prompt_for_domain(rec)
        if chosen is None:
            result.error = "Cancelled at domain confirmation"
            result.status = "cancelled"
            return False
        # Persist the human-chosen domain as the forced domain, then proceed.
        self.service.ctx.storage_adapter.update_file(
            file_id,
            database_name=self.service.ctx.database_name,
            updates={"forced_domain": chosen, "confirmation_required": False},
        )
        result.detected_domain = chosen
        return True

    def _park(
        self, file_id: str, rec: dict[str, Any], result: PipelineResult, *, quiet: bool = False
    ) -> None:
        """Park the source for later confirmation and mark the result."""
        from chaoscypher_core.operations.importing.confirmation_gate import (
            park_for_confirmation,
        )

        proposal = {
            "ranking": rec["ranking"],
            "confidence": rec["confidence"],
            "detected_domain": rec["detected_domain"],
            "low_confidence": rec["low_confidence"],
        }
        park_for_confirmation(self.service.ctx.storage_adapter, file_id, proposal)
        result.parked_for_confirmation = True
        result.status = "awaiting_confirmation"
        result.error = (
            f"Domain not confirmed (detected: {rec['detected_domain']}). "
            f"Run: cc source confirm {file_id}  (or re-run with --no-confirm to accept)"
        )
        # In quiet mode, add.py's quiet output block owns the AWAITING line.
        # Only print here on the UI (non-quiet) path so it appears exactly once
        # in both modes.
        if not quiet:
            self.console.print(
                f"[yellow]AWAITING[/yellow] {file_id} "
                f"(detected: {rec['detected_domain']}) — "
                f"cc source confirm {file_id}"
            )

    def _prompt_for_domain(self, rec: dict[str, Any]) -> str | None:
        """Interactive TTY prompt; default is ranking[0]. None => cancelled."""
        from rich.prompt import Prompt

        from chaoscypher_cli.sources.domains import DOMAIN_NAMES

        recommended = rec["detected_domain"]
        if rec["low_confidence"]:
            self.console.print(
                "[yellow]Detection wasn't confident — please pick a domain.[/yellow]"
            )
        else:
            ranked = ", ".join(f"{r['domain']} ({r['score']:.1f})" for r in rec["ranking"][:3])
            self.console.print(
                f"[dim]Recommended domain:[/dim] [magenta]{recommended}[/magenta]"
                + (f"  [dim]({ranked})[/dim]" if ranked else "")
            )
        choices = [*DOMAIN_NAMES, "cancel"]
        answer = Prompt.ask(
            "Confirm extraction domain",
            choices=choices,
            default=recommended if recommended in DOMAIN_NAMES else "generic",
        )
        if answer == "cancel":
            return None
        return answer

    def _run_with_ui(  # noqa: PLR0911 - one return per pipeline-stage early-exit
        self,
        file_path: Path | None,
        file_id: str | None,
        stages: list[str],
        extraction_depth: str,
        domain: str | None,
        skip_embeddings: bool,
        enable_normalization: bool | None,
        enable_vision: bool,
        content_filtering: bool,
        result: PipelineResult,
        url: str | None = None,
        filtering_mode: FilteringMode | None = None,
        skip_duplicates: bool = False,
        no_confirm: bool = False,
    ) -> PipelineResult:
        """Run pipeline with spinner-per-stage progress UI.

        Uses console.status() for each stage — a simple spinner that doesn't
        capture stdout, swap logging handlers, or interfere with structlog.
        """
        total = len(stages)
        step = 0

        # Header panel
        if url:
            self._print_header(None, file_id, extraction_depth, url=url)
        else:
            self._print_header(file_path, file_id, extraction_depth)

        # Upload stage. W1 (2026-05-07): forward upload-row settings.
        # ``dict[str, Any]`` for the same reason as ``_run_quiet`` above.
        upload_kwargs: dict[str, Any] = {
            "extraction_depth": extraction_depth,
            "domain": domain,
            "skip_duplicates": skip_duplicates,
            "enable_normalization": enable_normalization,
            "enable_vision": enable_vision,
            "content_filtering": content_filtering,
            "filtering_mode": filtering_mode or "balanced",
        }
        if "upload" in stages and (file_path or url):
            step += 1
            if url:
                file_id = self._ui_upload_url(
                    step,
                    total,
                    url,
                    result,
                    **upload_kwargs,
                )
            elif file_path:
                file_id = self._ui_upload(
                    step,
                    total,
                    file_path,
                    result,
                    **upload_kwargs,
                )
            if result.error or result.status == "skipped_duplicate":
                return result

        if not file_id:
            result.error = "No file ID"
            return result

        # Index stage
        if "index" in stages:
            step += 1
            self._ui_index(
                step, total, file_id, skip_embeddings, enable_normalization, enable_vision, result
            )
            if result.error:
                return result

        # Extract stage
        if "extract" in stages:
            if not self._gate_before_extract(file_id, result, no_confirm=no_confirm):
                return result
            step += 1
            self._ui_extract(step, total, file_id, result, filtering_mode=filtering_mode)
            if result.error:
                return result

        # Commit stage
        if "commit" in stages:
            step += 1
            self._ui_commit(step, total, file_id, result)
            if result.error:
                return result

        return result

    def _print_header(
        self,
        file_path: Path | None,
        file_id: str | None,
        extraction_depth: str,
        url: str | None = None,
    ) -> None:
        """Print the pipeline header panel."""
        if url:
            filename = url
        elif file_path:
            filename = file_path.name
        else:
            filename = f"file {file_id}"

        # Truncate for narrow terminals (panel border + padding = ~10 chars)
        max_width = self.console.width - 10
        if len(filename) > max_width > 20:
            filename = filename[: max_width - 3] + "..."

        config_parts = []
        if extraction_depth != "full":
            config_parts.append(f"Depth: {extraction_depth}")
        config_line = f"  [dim]{' · '.join(config_parts)}[/dim]" if config_parts else ""

        body = (
            f"  [cyan]{filename}[/cyan]\n{config_line}"
            if config_line
            else f"  [cyan]{filename}[/cyan]"
        )
        self.console.print()
        self.console.print(
            Panel(
                body,
                title="[bold]Source Pipeline[/bold]",
                border_style="cyan",
                padding=(0, 1),
            )
        )
        self.console.print()

    def _ui_upload(
        self,
        step: int,
        total: int,
        file_path: Path,
        result: PipelineResult,
        *,
        extraction_depth: str,
        domain: str | None,
        skip_duplicates: bool = False,
        enable_normalization: bool | None = None,
        enable_vision: bool = True,
        content_filtering: bool = True,
        filtering_mode: FilteringMode = "balanced",
    ) -> str | None:
        """Run upload stage with UI. Returns file_id or None on failure/skip."""
        stage_start = time.time()
        with self.console.status(
            f"[dim]\\[{step}/{total}][/dim] [cyan]Uploading...[/cyan]",
        ):
            try:
                upload_result = self.service.upload_file(
                    file_path,
                    extraction_depth=extraction_depth,
                    domain=domain,
                    skip_duplicates=skip_duplicates,
                    enable_normalization=enable_normalization,
                    enable_vision=enable_vision,
                    content_filtering=content_filtering,
                    filtering_mode=filtering_mode,
                )
                if isinstance(upload_result, dict) and upload_result.get("skipped_duplicate"):
                    self._handle_duplicate_skip(upload_result, result)
                    return None
                assert isinstance(upload_result, str)  # narrowed: skip dict handled above
                file_id = upload_result
                result.file_id = file_id
                result.filename = file_path.name
                result.stages_completed.append("upload")
            except Exception as e:
                elapsed = time.time() - stage_start
                self.console.print(
                    self._format_stage_line(
                        step,
                        total,
                        "✗",
                        "red",
                        "Upload",
                        str(e),
                        elapsed,
                    )
                )
                result.error = str(e)
                return None
        elapsed = time.time() - stage_start
        self.console.print(
            self._format_stage_line(
                step,
                total,
                "✓",
                "green",
                "Upload",
                elapsed=elapsed,
            )
        )
        return file_id

    def _ui_upload_url(
        self,
        step: int,
        total: int,
        url: str,
        result: PipelineResult,
        *,
        extraction_depth: str,
        domain: str | None,
        skip_duplicates: bool = False,
        enable_normalization: bool | None = None,
        enable_vision: bool = True,
        content_filtering: bool = True,
        filtering_mode: FilteringMode = "balanced",
    ) -> str | None:
        """Run URL fetch + upload stage with UI. Returns file_id or None on failure/skip."""
        stage_start = time.time()
        with self.console.status(
            f"[dim]\\[{step}/{total}][/dim] [cyan]Fetching URL...[/cyan]",
        ):
            try:
                upload_result, page_title = self.service.upload_url(
                    url,
                    extraction_depth=extraction_depth,
                    domain=domain,
                    skip_duplicates=skip_duplicates,
                    enable_normalization=enable_normalization,
                    enable_vision=enable_vision,
                    content_filtering=content_filtering,
                    filtering_mode=filtering_mode,
                )
                if isinstance(upload_result, dict) and upload_result.get("skipped_duplicate"):
                    self._handle_duplicate_skip(upload_result, result)
                    return None
                assert isinstance(upload_result, str)  # narrowed: skip dict handled above
                file_id = upload_result
                result.file_id = file_id
                result.filename = page_title
                result.stages_completed.append("upload")
            except Exception as e:
                elapsed = time.time() - stage_start
                self.console.print(
                    self._format_stage_line(
                        step,
                        total,
                        "✗",
                        "red",
                        "Fetch URL",
                        str(e),
                        elapsed,
                    )
                )
                result.error = str(e)
                return None
        elapsed = time.time() - stage_start
        self.console.print(
            self._format_stage_line(
                step,
                total,
                "✓",
                "green",
                "Fetch URL",
                f'"{page_title}"',
                elapsed,
            )
        )
        return file_id

    def _ui_index(
        self,
        step: int,
        total: int,
        file_id: str,
        skip_embeddings: bool,
        enable_normalization: bool | None,
        enable_vision: bool,
        result: PipelineResult,
    ) -> None:
        """Run index stage with UI."""
        file_status = self.service.get_file_status(file_id)
        if file_status and file_status.get("status") in (
            SourceStatus.INDEXED,
            SourceStatus.EXTRACTED,
            SourceStatus.COMMITTED,
        ):
            self.console.print(
                self._format_stage_line(
                    step,
                    total,
                    "✓",
                    "dim",
                    "Index",
                    "cached",
                )
            )
            result.stages_completed.append("index")
            return

        stage_start = time.time()
        with self.console.status(
            f"[dim]\\[{step}/{total}][/dim] [cyan]Indexing...[/cyan]",
        ):
            try:
                index_result = self.service.index_file(
                    file_id,
                    skip_embeddings=skip_embeddings,
                    enable_normalization=enable_normalization,
                    enable_vision=enable_vision,
                )
                result.chunks_count = index_result.get("chunks_count", 0)
                result.tokens_count = index_result.get("tokens_count", 0)
                result.failed_embeddings = index_result.get("failed_embeddings", 0)
                result.stages_completed.append("index")
            except Exception as e:
                elapsed = time.time() - stage_start
                self.console.print(
                    self._format_stage_line(
                        step,
                        total,
                        "✗",
                        "red",
                        "Index",
                        str(e),
                        elapsed,
                    )
                )
                result.error = str(e)
                return
        elapsed = time.time() - stage_start
        detail = f"{result.chunks_count} chunks"
        if result.tokens_count:
            detail += f", {result.tokens_count:,} tokens"
        if result.failed_embeddings:
            detail += f" [yellow]({result.failed_embeddings} embeddings failed)[/yellow]"
        self.console.print(
            self._format_stage_line(
                step,
                total,
                "✓",
                "green",
                "Index",
                detail,
                elapsed,
            )
        )

    def _ui_extract(
        self,
        step: int,
        total: int,
        file_id: str,
        result: PipelineResult,
        filtering_mode: FilteringMode | None = None,
    ) -> None:
        """Run extract stage with UI."""
        if not self.service.has_llm:
            self.console.print(
                self._format_stage_line(
                    step,
                    total,
                    "-",
                    "yellow",
                    "Extract",
                    "no LLM configured",
                )
            )
            result.stages_skipped.append("extract")
            return

        stage_start = time.time()
        progress = Progress(
            SpinnerColumn(),
            TextColumn(f"[dim]\\[{step}/{total}][/dim] [cyan]Extract[/cyan]"),
            BarColumn(bar_width=30),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.completed}/{task.total} groups"),
            TimeRemainingColumn(),
            console=self.console,
            transient=True,
        )
        task_id = progress.add_task("extract", total=1)

        def on_progress(current: int, total_groups: int) -> None:
            """Update progress bar from extraction callback."""
            progress.update(task_id, total=total_groups, completed=current)

        def on_domain(domain_name: str) -> None:
            """Show detected domain before progress bar starts."""
            result.detected_domain = domain_name
            self.console.print(
                f"  [dim]\\[{step}/{total}][/dim] [dim]Domain:[/dim] [magenta]{domain_name}[/magenta]"
            )

        try:
            with progress:
                extract_result, llm_summary = self.service.extract_entities(
                    file_id,
                    progress_callback=on_progress,
                    domain_callback=on_domain,
                    filtering_mode=filtering_mode,
                )
                stats = extract_result.get("stats", {})
                result.entities_count = stats.get("entities_count", 0)
                result.relationships_count = stats.get("relationships_count", 0)
                if not result.detected_domain:
                    result.detected_domain = stats.get("detected_domain")
                result.stages_completed.append("extract")
                self._populate_llm_metrics(result, llm_summary)
                # Read cached quality scores (already computed by service.extract_entities)
                source_file = self.service.ctx.storage_adapter.get_file(
                    file_id, self.service.ctx.database_name
                )
                if source_file:
                    result.quality_grade = source_file.get("cached_quality_grade")
                    result.quality_label = source_file.get("cached_quality_label")
        except Exception as e:
            elapsed = time.time() - stage_start
            self.console.print(
                self._format_stage_line(
                    step,
                    total,
                    "✗",
                    "red",
                    "Extract",
                    str(e),
                    elapsed,
                )
            )
            result.error = str(e)
            return
        elapsed = time.time() - stage_start
        detail_parts = [f"{result.entities_count} entities"]
        if result.llm_retry_calls > 0:
            detail_parts.append(f"{result.llm_retry_calls} retries")
        if stats.get("groups_processed"):
            detail_parts.append(f"{stats['groups_processed']} groups")
        self.console.print(
            self._format_stage_line(
                step,
                total,
                "✓",
                "green",
                "Extract",
                ", ".join(detail_parts),
                elapsed,
            )
        )

    def _ui_commit(
        self,
        step: int,
        total: int,
        file_id: str,
        result: PipelineResult,
    ) -> None:
        """Run commit stage with UI."""
        stage_start = time.time()
        with self.console.status(
            f"[dim]\\[{step}/{total}][/dim] [cyan]Committing...[/cyan]",
        ):
            try:
                commit_result = self.service.commit_to_graph(file_id)
                result.nodes_created = commit_result.get("nodes_created", 0)
                result.edges_created = commit_result.get("edges_created", 0)
                result.stages_completed.append("commit")
            except Exception as e:
                elapsed = time.time() - stage_start
                self.console.print(
                    self._format_stage_line(
                        step,
                        total,
                        "✗",
                        "red",
                        "Commit",
                        str(e),
                        elapsed,
                    )
                )
                result.error = str(e)
                return
        elapsed = time.time() - stage_start
        detail_parts = [f"{result.nodes_created} nodes"]
        if result.edges_created:
            detail_parts.append(f"{result.edges_created} edges")
        self.console.print(
            self._format_stage_line(
                step,
                total,
                "✓",
                "green",
                "Commit",
                ", ".join(detail_parts),
                elapsed,
            )
        )

    def _populate_llm_metrics(self, result: PipelineResult, llm_summary: dict[str, Any]) -> None:
        """Populate LLM metrics in result from summary dict."""
        result.llm_total_calls = llm_summary.get("total_calls", 0)
        result.llm_successful_calls = llm_summary.get("successful_calls", 0)
        result.llm_failed_calls = llm_summary.get("failed_calls", 0)
        result.llm_retry_calls = llm_summary.get("retry_calls", 0)
        result.llm_total_input_tokens = llm_summary.get("total_input_tokens", 0)
        result.llm_total_output_tokens = llm_summary.get("total_output_tokens", 0)
        result.llm_wasted_tokens = llm_summary.get("wasted_tokens", 0)
        result.llm_estimated_cost_usd = llm_summary.get("estimated_cost_usd", 0.0)
        result.llm_model = llm_summary.get("model", "")
        result.extraction_mode = "internal"
        result.llm_retry_rate = llm_summary.get("retry_rate", 0.0)
        result.llm_success_rate = llm_summary.get("success_rate", 0.0)

    @staticmethod
    def _add_llm_metrics_rows(table: Table, result: PipelineResult) -> None:
        """Add LLM metrics rows to the summary table.

        Args:
            table: Rich Table to add rows to
            result: PipelineResult with LLM metrics
        """
        table.add_row("", "")  # Spacer
        table.add_row("[dim]LLM[/dim]", "")

        # Call stats
        calls_info = f"{result.llm_successful_calls}/{result.llm_total_calls}"
        if result.llm_retry_calls > 0:
            calls_info += f" ({result.llm_retry_calls} retries)"
        table.add_row("Calls", calls_info)

        # Token stats
        total_tokens = result.llm_total_input_tokens + result.llm_total_output_tokens
        tokens_info = f"{total_tokens:,}"
        if result.llm_wasted_tokens > 0:
            waste_pct = (result.llm_wasted_tokens / total_tokens * 100) if total_tokens > 0 else 0
            tokens_info += (
                f" ([yellow]{result.llm_wasted_tokens:,} wasted, {waste_pct:.0f}%[/yellow])"
            )
        table.add_row("Tokens", tokens_info)

        # Cost estimate
        if result.llm_estimated_cost_usd > 0:
            cost_str = f"${result.llm_estimated_cost_usd:.4f}"
            if result.llm_estimated_cost_usd < 0.01:
                cost_str = "<$0.01"
            table.add_row("Cost", cost_str)
        elif result.llm_model and "ollama" in result.llm_model.lower():
            table.add_row("Cost", "$0.00 (local)")

        # Model and extraction mode
        if result.llm_model:
            table.add_row("Model", result.llm_model)
        if result.extraction_mode:
            table.add_row("Extraction Mode", result.extraction_mode.upper())

    @staticmethod
    def _format_quality(grade: float, label: str | None) -> str:
        """Format quality grade with color markup.

        Args:
            grade: Quality grade (0-100)
            label: Quality label (Excellent/Good/Fair/Low)

        Returns:
            Rich-formatted quality string
        """
        display_label = label or "Unknown"
        color = get_quality_color(grade)
        return f"[{color}]{grade:.0f}/100 ({display_label})[/{color}]"

    def _show_summary(self, result: PipelineResult) -> None:
        """Show final summary panel."""
        # Build summary table
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Key", style="dim", min_width=14)
        table.add_column("Value")

        table.add_row("File ID", result.file_id)
        if result.detected_domain:
            table.add_row("Domain", f"[magenta]{result.detected_domain}[/magenta]")
        if result.chunks_count:
            table.add_row("Chunks", str(result.chunks_count))
        if result.entities_count:
            table.add_row("Entities", str(result.entities_count))
        if result.relationships_count:
            table.add_row("Relationships", str(result.relationships_count))
        if result.quality_grade is not None:
            table.add_row(
                "Quality", self._format_quality(result.quality_grade, result.quality_label)
            )
        if result.nodes_created:
            table.add_row("Nodes", str(result.nodes_created))
        if result.edges_created:
            table.add_row("Edges", str(result.edges_created))
        table.add_row("Duration", f"{result.duration_seconds:.1f}s")

        # LLM Metrics section
        if result.llm_total_calls > 0:
            self._add_llm_metrics_rows(table, result)

        if result.stages_skipped:
            table.add_row("", "")  # Spacer
            table.add_row("Skipped", ", ".join(result.stages_skipped))

        if result.warnings:
            table.add_row("", "")  # Spacer
            for i, warning in enumerate(result.warnings):
                label = "Warnings" if i == 0 else ""
                table.add_row(label, f"[yellow]⚠ {warning}[/yellow]")

        if result.error:
            table.add_row("Error", f"[red]{result.error}[/red]")

        # Show panel
        status_icon = "[green]✓[/green]" if result.success else "[red]✗[/red]"
        title_text = "Complete" if result.success else "Failed"
        self.console.print()
        self.console.print(
            Panel(
                table,
                title=f"{status_icon} [bold]{title_text}[/bold]",
                border_style="green" if result.success else "red",
                padding=(1, 2),
            )
        )
