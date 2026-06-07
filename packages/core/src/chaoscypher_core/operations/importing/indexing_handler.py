# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Document indexing handler for RAG search integration.

Provides the ``handle_index_document`` async function that orchestrates the
load → vision → normalize → chunk → persist pipeline on the ops queue,
then hands off the LLM-bound embedding stage to ``OP_EMBED_CHUNKS`` on
the LLM queue. The embedding handler (``embedding_handler.py``) finalizes
the indexing stage (chunks count, metadata, ``task_completed`` event,
optional analysis queueing).

Called by ``ImportOperationsService`` with dependencies from the worker
context.

Vision processing (PR 2, 2026-05-13): the indexing handler no longer
issues vision LLM calls. ``_apply_vision_processing`` instead creates a
vision_job + N pending vision_page_descriptions rows, flips the source
to ``vision_pending``, and enqueues one ``OP_VISION_PAGE`` task per
image page on ``QUEUE_LLM``. The finalizer (``OP_VISION_FINALIZE``)
re-enters this handler with ``resume_after_vision=True`` once all
per-page tasks reach a terminal state — the resume path re-loads the
documents, splices descriptions back in via the shared
``vision_finalizer._splice_descriptions_into_documents`` helper, and
continues with chunking + embedding.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from chaoscypher_core.constants import OP_VISION_PAGE, QUEUE_LLM
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.importing.fanout_limits import (
    enforce_source_fanout_ceiling,
)
from chaoscypher_core.operations.queue_utils import (
    queue_embed_chunks,
)
from chaoscypher_core.queue import queue_client
from chaoscypher_core.services.events import event_bus
from chaoscypher_core.services.sources import source_heartbeat
from chaoscypher_core.vision.states import VisionPageKind, VisionPageStatus


if TYPE_CHECKING:
    from chaoscypher_core.ports.stage_progress import StageProgressStorageProtocol
    from chaoscypher_core.services.sources.normalizer import ContentType
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)

# Minimum extractable character count below which we treat the document as
# empty. ~one short sentence; short enough to accept minimal real content
# while rejecting whitespace-only artifacts and near-empty extractions.
# Below this threshold we raise ValidationError rather than producing a zero-chunk
# INDEXED source. Audit fix #H3.
_MIN_INDEXABLE_CHARS = 50


def _is_image_bearing_no_text(metadata: dict[str, Any] | None) -> bool:
    """True when loader metadata says this doc is image-bearing with too little text.

    Wizard §3.1 no-text short-circuit predicate. Evaluated at the post-load /
    pre-vision seam, where ``full_text`` has not been extracted yet — we read
    the loader's synchronous signals instead:

    * Primary: ``total_characters < _MIN_INDEXABLE_CHARS`` (reuses the indexable
      floor — no new literal). A doc that already carries enough raw text is not
      a no-text doc even if it also has images (mixed PDF).
    * Image-bearing hints (any one): ``needs_vision`` truthy (PDF loader sets it
      when every page produced empty text), ``image_page_count > 0``, or
      ``extraction_method == 'vision_pending'`` (standalone image loader).
      ``pypdfium2`` may be absent so ``image_page_count`` alone is unreliable —
      hence the OR of three independent signals.

    Args:
        metadata: First loader document's metadata dict (or ``None``).

    Returns:
        ``True`` only when both the low-text and image-bearing conditions hold.
    """
    if not isinstance(metadata, dict):
        return False
    total_characters = int(metadata.get("total_characters") or 0)
    if total_characters >= _MIN_INDEXABLE_CHARS:
        return False
    image_page_count = int(metadata.get("image_page_count") or 0)
    return bool(
        metadata.get("needs_vision")
        or image_page_count > 0
        or metadata.get("extraction_method") == "vision_pending"
    )


def _no_text_proposal() -> dict[str, Any]:
    """Build the ``no_text`` detection_proposal blob for an image-only doc.

    Wizard §3.1: the wizard reads ``no_text`` to render "not enough text to
    detect — pick a domain (defaults to generic)". The 4-key blob is produced
    by ``proposal_from_detection`` over a synthesized generic detection result
    (a well-formed generic ``ranking`` inline literal, low confidence), then
    the ``no_text`` flag is added on top. The ranking is hard-coded here as the
    trivial single-domain fallback ``[{"domain": "generic", "score": 0.0}]``
    rather than reaching into the orchestration module's private ranking
    builder for a one-element result.
    """
    from chaoscypher_core.operations.importing.confirmation_gate import (
        proposal_from_detection,
    )

    synthesized = {
        "ranking": [{"domain": "generic", "score": 0.0}],
        "confidence": None,
        "detected_domain": "generic",
        "low_confidence": True,
    }
    proposal = proposal_from_detection(synthesized)
    proposal["no_text"] = True
    return proposal


def vision_images_dir(data_dir: Any, database_name: str, source_id: str) -> Path:
    """Return the canonical PNG output directory for a source's vision pages.

    Single source of truth for the layout
    ``{data_dir}/databases/{database_name}/images/{source_id}/`` so the
    indexing handler (writer) and the source-delete cleanup path (reader)
    cannot drift apart on the path. Audit fix F32.
    """
    return Path(str(data_dir)) / "databases" / database_name / "images" / source_id


def cleanup_vision_images(
    data_dir: Any,
    database_name: str,
    source_id: str,
) -> None:
    """Best-effort removal of a source's rendered vision PNG directory.

    Used by the indexing failure path (so a crash mid-vision does not
    leave orphan PNGs forever) and by the source-delete cascade (so the
    images directory disappears with the source). Wrapped in
    ``contextlib.suppress(OSError)``; failure is logged at WARNING and
    never re-raised — vision PNGs are derived data and a leftover
    directory is harmless beyond disk usage. Audit fix F32.
    """
    target = vision_images_dir(data_dir, database_name, source_id)
    if not target.exists():
        return
    try:
        with contextlib.suppress(OSError):
            shutil.rmtree(target)
    except Exception as exc:
        # Truly unexpected errors (e.g. logging-config blowups) — surface
        # them as warnings so the underlying indexing failure stays the
        # one the operator investigates first.
        logger.warning(
            "vision_images_cleanup_failed",
            source_id=source_id,
            database_name=database_name,
            target=str(target),
            error_type=type(exc).__name__,
        )
        return
    if target.exists():
        # rmtree was suppressed by contextlib (OSError swallowed). Surface
        # the leftover so operators can investigate without burying the
        # underlying indexing failure.
        logger.warning(
            "vision_images_cleanup_incomplete",
            source_id=source_id,
            database_name=database_name,
            target=str(target),
        )


async def _rollup_phase6_loader_counters(
    documents: list[dict[str, Any]],
    adapter: Any,
    source_id: str,
    database_name: str,
) -> None:
    """Roll up Phase 6 loader counters from per-doc metadata.

    Scalar counters (DOCX paragraphs, XLSX rows, CSV rows) sum to a single int
    and increment via QualityCounter atomic-add. Dict counters (HTML tags,
    PPTX shapes) merge into a dict and write via update_source_columns since
    the column is a JSON dict, not an int.

    Phase 7 audit-remediation (2026-05-09): split into scalar and dict paths.
    Pairs with migration 0029 (column type INTEGER -> TEXT for HTML/PPTX) and
    loader updates 3de84fa32 (HTML) + ce0c6ad60 (PPTX).

    Args:
        documents: List of document dicts produced by the loader.
        adapter: Storage adapter exposing ``increment_source_counter`` and
            ``update_source_columns``.
        source_id: Source row ID.
        database_name: Target database name.
    """
    from chaoscypher_core.services.quality.counters import (
        QualityCounter,
        increment_quality_counter,
    )

    scalar_pairs: tuple[tuple[str, QualityCounter], ...] = (
        ("loader_docx_paragraphs_skipped", QualityCounter.LOADER_DOCX_PARAGRAPHS_SKIPPED),
        ("loader_xlsx_rows_skipped", QualityCounter.LOADER_XLSX_ROWS_SKIPPED),
        ("loader_csv_rows_truncated", QualityCounter.LOADER_CSV_ROWS_TRUNCATED),
    )
    dict_keys: tuple[str, ...] = (
        "loader_html_dropped_tags",
        "loader_pptx_shapes_skipped",
    )

    # 1. Scalar rollup: sum across documents and atomic-add to the counter column.
    for meta_key, counter in scalar_pairs:
        total = sum(
            int(doc.get("metadata", {}).get(meta_key, 0) or 0)
            for doc in documents
            if isinstance(doc.get("metadata"), dict)
        )
        if total > 0:
            await increment_quality_counter(
                adapter=adapter,
                source_id=source_id,
                database_name=database_name,
                counter=counter,
                n=total,
            )

    # 2. Dict rollup: merge per-doc dicts into one source-row write.
    # JSON columns can't atomic-add so we overwrite with the merged result.
    for meta_key in dict_keys:
        merged: dict[str, int] = {}
        for doc in documents:
            metadata = doc.get("metadata") or {}
            per_doc = metadata.get(meta_key)
            if isinstance(per_doc, dict):
                for k, v in per_doc.items():
                    try:
                        merged[k] = merged.get(k, 0) + int(v)
                    except TypeError, ValueError:
                        # Defensive: malformed loader output — skip the entry.
                        continue
        if merged:
            try:
                adapter.update_source_columns(
                    source_id=source_id,
                    database_name=database_name,
                    updates={meta_key: merged},
                )
            except Exception:
                logger.warning(
                    "rollup_dict_counter_update_failed",
                    source_id=source_id,
                    column=meta_key,
                    exc_info=True,
                )


async def handle_index_document(
    data: dict[str, Any],
    source_repository: Any,
    chunking_service: Any,
    metadata: dict[str, Any] | None = None,  # standard handler contract
    engine_settings: EngineSettings | None = None,
) -> dict[str, Any]:
    """Execute document indexing (chunking + enqueue embedding to LLM queue).

    Workflow:
        1. Update status to 'indexing'
        2. Load document and extract text
        3. Optionally run vision LLM over images (deferred split)
        4. Optionally normalize content (OCR cleanup, encoding, whitespace)
        5. Create hierarchical chunks (ChunkingService) and persist them
        6. Enqueue ``OP_EMBED_CHUNKS`` on ``QUEUE_LLM`` with an ID-only
           payload. The embedding handler finalizes the stage, emits the
           ``task_completed`` event, and queues analysis if requested.

    Embedding, completion, and the optional analysis-queue step moved
    to ``embedding_handler.py`` so the LLM-bound tail of the pipeline
    runs on the LLM queue while the cheap load/chunk/persist stage
    stays on the operations queue.

    Args:
        data: Task data with file ID and file info.
        source_repository: SqliteAdapter implementing storage protocols.
        chunking_service: ChunkingService cached at worker level.
        metadata: Task metadata.
        engine_settings: Cached EngineSettings from worker startup (optional,
            falls back to converting from settings if not provided).

    Returns:
        Result dictionary with chunks_persisted count, embedding task id,
        and the ``queued_for_embedding`` status.

    Raises:
        ValidationError: If ``file_info`` is missing from the task data, or if
            ``file_id`` is not a string.
    """
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.operations.pause_guard import check_paused

    file_id = data.get("file_id")
    file_info = data.get("file_info")
    if file_info is None:
        msg = "file_info is required"
        raise ValidationError(msg, field="file_info")
    filepath = file_info.get("filepath")

    # PR 2 (2026-05-13, Task 12): the vision finalizer re-enqueues this
    # handler with ``resume_after_vision=True`` once every per-page LLM
    # task is terminal. The resume path skips ``start_indexing`` (state
    # is already INDEXING after the finalizer's CAS), skips the loader
    # quality-counter rollups (already counted on the original pass —
    # double-incrementing would silently inflate the data-quality tab),
    # and feeds the loader output through
    # ``vision_finalizer._splice_descriptions_into_documents`` so the
    # vision text reaches the chunker.
    resume_after_vision = bool(data.get("resume_after_vision", False))

    settings = get_settings()
    database_name = settings.current_database

    # Workstream 1 (2026-05-07): user upload settings (extraction_depth /
    # enable_normalization / enable_vision) live authoritatively on the
    # source row so recovery / retry / re-extract preserve user choice.
    # We still fall back to the queue payload when the row hasn't been
    # written yet (legacy or test-only flows) to keep the handler
    # tolerant under recovery scenarios.
    row_settings: dict[str, Any] = {}
    if isinstance(file_id, str):
        try:
            row = source_repository.get_source(file_id, database_name)
        except (SQLAlchemyError, OperationalError) as exc:
            logger.warning(
                "indexing_row_settings_lookup_failed",
                source_id=file_id,
                database_name=database_name,
                error_type=type(exc).__name__,
            )
            row = None
        if row:
            row_settings = row

    def _row_or_payload(key: str, default: Any) -> Any:
        """Prefer the persisted source row's value; fall back to the queue payload."""
        if key in row_settings and row_settings.get(key) is not None:
            return row_settings[key]
        return file_info.get(key, default)

    analysis_depth = _row_or_payload("extraction_depth", "full")
    enable_vision = _row_or_payload("enable_vision", None)

    # Tri-state resolution for ``enable_normalization`` (W1 follow-up,
    # 2026-05-07). The row column allows NULL with the documented
    # semantic "use the per-file-type default" — structured formats
    # (CSV / TSV / JSON / JSONL / NDJSON / XML) skip normalization,
    # everything else normalizes. The route boundary used to resolve
    # this eagerly for multipart uploads but URL imports / CLI uploads
    # never went through that path, leaving the handler with two
    # semantics. Doing the resolution here puts every entry path on
    # the same footing.
    from chaoscypher_core.utils.normalization_default import (
        resolve_normalization_default,
    )

    raw_enable_normalization: Any = None
    if "enable_normalization" in row_settings:
        raw_enable_normalization = row_settings.get("enable_normalization")
    elif "enable_normalization" in file_info:
        raw_enable_normalization = file_info.get("enable_normalization")
    if raw_enable_normalization is None:
        enable_normalization = resolve_normalization_default(
            filename=str(file_info.get("filename") or ""),
        )
    else:
        enable_normalization = bool(raw_enable_normalization)

    logger.info(
        "import_document_indexing_processing",
        file_id=file_id,
        analysis_depth=analysis_depth,
        enable_normalization=enable_normalization,
        enable_vision=enable_vision,
        settings_source="row" if row_settings else "payload",
    )

    # Pause guard: if the source or the system is paused, return
    # {"skipped": "paused"} without touching any real work. Paused is
    # NOT an error — the worker frees up immediately and picks up the
    # next queued task.
    if isinstance(file_id, str):
        pause_check = check_paused(
            source_id=file_id,
            database_name=database_name,
            adapter=source_repository,
        )
        if pause_check.paused:
            logger.info(
                "handler_skipped_paused",
                handler="handle_index_document",
                source_id=file_id,
                scope=pause_check.scope,
                reason=pause_check.reason,
            )
            return {"skipped": "paused"}

    # Use cached engine_settings from worker context, fallback to building
    if engine_settings is None:
        from chaoscypher_core.app_config.engine_factory import (
            build_engine_settings,
        )

        engine_settings = build_engine_settings(settings)

    # Use shared adapter (passed from worker context)
    adapter = source_repository

    if not isinstance(file_id, str):
        msg = "file_id must be a string"
        raise ValidationError(msg, field="file_id")

    # Phase 4 Task 4 (2026-05-08): resolve per-domain normalizer overrides.
    # The forced_domain (user-selected) is the only domain available at
    # indexing time — extraction_domain (auto-detected) is written later.
    # Best-effort: a missing registry, unknown domain name, or config-parse
    # error all fall through to ``None`` so the normalizer uses global defaults
    # and indexing never stalls.
    domain_normalizer_overrides: Any = None
    forced_domain_name: str | None = row_settings.get("forced_domain") or file_info.get(
        "forced_domain"
    )
    if forced_domain_name and enable_normalization:
        try:
            from chaoscypher_core.services.sources.engine.extraction.domains.factory import (
                get_domain_registry,
            )

            domain_registry = get_domain_registry(engine_settings, database_name=database_name)
            domain_obj = domain_registry.get_domain(forced_domain_name)
            if domain_obj is not None:
                raw_overrides = getattr(domain_obj, "config", {}).get("normalizer_overrides")
                if raw_overrides is not None:
                    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
                        DomainNormalizerOverrides,
                    )

                    domain_normalizer_overrides = DomainNormalizerOverrides.model_validate(
                        raw_overrides
                    )
                    logger.info(
                        "indexing_domain_normalizer_overrides_applied",
                        source_id=file_id,
                        domain=forced_domain_name,
                        overrides=domain_normalizer_overrides.model_dump(exclude_none=True),
                    )
        except Exception as exc:  # best-effort; never block indexing
            logger.warning(
                "indexing_domain_normalizer_overrides_failed",
                source_id=file_id,
                domain=forced_domain_name,
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # Source liveness heartbeat — keeps last_activity_at fresh while
    # this handler is running so the source-recovery reconciler does
    # not treat a long load/chunk/embed step as a stall and dispatch
    # a duplicate handler. See chaoscypher_core.services.sources.heartbeat.
    async with source_heartbeat(
        adapter=adapter,
        source_id=file_id,
        database_name=database_name,
    ):
        return await _run_indexing(
            file_id=file_id,
            file_info=file_info,
            filepath=filepath,
            analysis_depth=analysis_depth,
            enable_normalization=enable_normalization,
            enable_vision=enable_vision,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=engine_settings,
            settings=settings,
            database_name=database_name,
            domain_normalizer_overrides=domain_normalizer_overrides,
            resume_after_vision=resume_after_vision,
        )


async def _run_indexing(
    *,
    file_id: str,
    file_info: dict[str, Any],
    filepath: Any,
    analysis_depth: str,
    enable_normalization: bool,
    enable_vision: bool | None,
    adapter: Any,
    chunking_service: Any,
    engine_settings: EngineSettings,
    settings: Any,
    database_name: str,
    domain_normalizer_overrides: Any | None = None,
    resume_after_vision: bool = False,
) -> dict[str, Any]:
    """Inner indexing pipeline body, wrapped by the heartbeat CM in the public handler.

    The body covers the load → vision → normalize → chunk → persist
    stages. Embedding + finalization moved to ``embedding_handler.py``
    (``OP_EMBED_CHUNKS`` on ``QUEUE_LLM``).

    PR 2 (2026-05-13, Task 12): ``resume_after_vision`` is set by the
    vision finalizer when re-enqueuing this handler after every per-page
    vision task is terminal. On the resume path:

    * ``start_indexing`` is skipped — state is already INDEXING via the
      finalizer's compare-and-swap, and re-running ``start_indexing``
      would reset ``indexing_started_at`` and clear ``error_message``,
      losing context.
    * Loader-stage quality counters are skipped — they were already
      written on the original pass; double-incrementing would silently
      inflate the data-quality tab.
    * Documents are re-loaded via the deterministic loader registry and
      then spliced with persisted ``vision_page_descriptions`` rows via
      ``vision_finalizer._splice_descriptions_into_documents`` — the
      same helper the finalizer uses, kept as the single source of
      truth for the splice.
    """
    from chaoscypher_core.services.sources.loaders import get_loader_registry

    try:
        if not resume_after_vision:
            adapter.start_indexing(file_id)

            _fname = file_info.get("filename", "unknown")
            event_bus.emit(
                "task_started",
                action=f"Indexing started: {_fname}",
                source="worker",
                details={"source_id": file_id, "filename": _fname},
                database_name=database_name,
            )

        # Step 1/2: Loading document
        adapter.update_step_progress(file_id, 1, 2, "Loading document")

        logger.info(
            "import_document_indexing_started",
            file_id=file_id,
            filepath=filepath,
            resume_after_vision=resume_after_vision,
        )

        # Load document and extract text (cached registry avoids loader rediscovery)
        loader_registry = get_loader_registry(engine_settings)
        documents = await asyncio.to_thread(loader_registry.load_document, filepath)

        if not resume_after_vision:
            # Workstream 6 (2026-05-07): record which encoding the loader
            # used so the data-quality tab can surface "this Latin-1 file
            # was detected and decoded correctly" instead of leaving the
            # operator guessing whether the import lost characters.
            # Best-effort — never block indexing on a counter UPDATE.
            if documents:
                first_meta = documents[0].get("metadata", {})
                encoding_used = (
                    first_meta.get("encoding_used") if isinstance(first_meta, dict) else None
                )
                if encoding_used:
                    from chaoscypher_core.services.quality.counters import (
                        set_loader_encoding,
                    )

                    set_loader_encoding(
                        adapter=adapter,
                        source_id=file_id,
                        database_name=database_name,
                        encoding=str(encoding_used),
                    )

            # Workstream 2 (2026-05-08): surface loader-emitted parse warnings
            # (e.g. JSONL lines that failed to deserialize, archive files that
            # couldn't be processed) on the source row. Loaders attach a
            # ``loader_warnings`` list to document metadata when a soft failure
            # happened — count the entries across every document so partial
            # successes still register every individual problem.
            warning_count = sum(
                len(doc.get("metadata", {}).get("loader_warnings", []) or [])
                for doc in documents
                if isinstance(doc.get("metadata"), dict)
            )
            if warning_count > 0:
                from chaoscypher_core.services.quality.counters import (
                    QualityCounter,
                    increment_quality_counter,
                )

                await increment_quality_counter(
                    adapter=adapter,
                    source_id=file_id,
                    database_name=database_name,
                    counter=QualityCounter.LOADER_WARNINGS,
                    n=warning_count,
                )

            # Workstream 2 (2026-05-08): surface archive files that the loader
            # skipped (unsupported extension, per-file processing error, hidden
            # by ``_should_skip``). The archive handler aggregates this onto the
            # first document's metadata under ``loader_files_skipped`` so the
            # indexing handler can roll it up onto the source row.
            files_skipped_total = 0
            for doc in documents:
                meta = doc.get("metadata", {})
                if not isinstance(meta, dict):
                    continue
                n = meta.get("loader_files_skipped")
                if isinstance(n, int) and n > 0:
                    files_skipped_total += n
                    # Drain so we don't double-count if the same metadata is
                    # walked again later (defensive; nothing today re-walks).
                    meta["loader_files_skipped"] = 0
            if files_skipped_total > 0:
                from chaoscypher_core.services.quality.counters import (
                    QualityCounter,
                    increment_quality_counter,
                )

                await increment_quality_counter(
                    adapter=adapter,
                    source_id=file_id,
                    database_name=database_name,
                    counter=QualityCounter.LOADER_FILES_SKIPPED,
                    n=files_skipped_total,
                )

            # P2T11 (2026-05-08): surface replacement-char insertions from
            # detect_encoding's last-resort utf-8-replace path. Each loader
            # stores the count on its document's metadata under
            # ``replacement_chars_count``; sum across all documents so the
            # source row records the full per-file impact. Best-effort — never
            # block indexing on a counter UPDATE.
            replacement_chars_total = sum(
                int(doc.get("metadata", {}).get("replacement_chars_count", 0) or 0)
                for doc in documents
                if isinstance(doc.get("metadata"), dict)
            )
            if replacement_chars_total > 0:
                from chaoscypher_core.services.quality.counters import (
                    QualityCounter,
                    increment_quality_counter,
                )

                await increment_quality_counter(
                    adapter=adapter,
                    source_id=file_id,
                    database_name=database_name,
                    counter=QualityCounter.LOADER_REPLACEMENT_CHARS_COUNT,
                    n=replacement_chars_total,
                )

            # Phase 5b (2026-05-08): roll up per-page PDF extraction failures from
            # document metadata onto the source-row counter.  The PDF loader stores
            # ``loader_pdf_pages_failed`` on each document's metadata; sum across
            # all documents (there is typically one for a plain PDF, but archive
            # loaders can produce many) so the counter reflects the full per-file
            # impact.  Best-effort — never block indexing on a counter UPDATE.
            pdf_pages_failed_total = sum(
                int(doc.get("metadata", {}).get("loader_pdf_pages_failed", 0) or 0)
                for doc in documents
                if isinstance(doc.get("metadata"), dict)
            )
            if pdf_pages_failed_total > 0:
                from chaoscypher_core.services.quality.counters import (
                    QualityCounter,
                    increment_quality_counter,
                )

                await increment_quality_counter(
                    adapter=adapter,
                    source_id=file_id,
                    database_name=database_name,
                    counter=QualityCounter.LOADER_PDF_PAGES_FAILED,
                    n=pdf_pages_failed_total,
                )

            # Phase 6 (2026-05-08): roll up per-doc loader observability counters.
            await _rollup_phase6_loader_counters(
                documents=documents,
                adapter=adapter,
                source_id=file_id,
                database_name=database_name,
            )

            # Vision processing: PR 2 (2026-05-13) rewire. The legacy gather +
            # merge body was replaced with a per-page enqueue. When vision work
            # is needed, ``_apply_vision_processing`` creates vision_jobs + N
            # pending page rows, flips the source to ``vision_pending``, and
            # enqueues OP_VISION_PAGE * N. We return immediately; the finalizer
            # (OP_VISION_FINALIZE) re-enters this handler with
            # ``resume_after_vision=True`` to continue the pipeline once every
            # per-page task is terminal.
            documents, vision_job_id = await _apply_vision_processing(
                documents=documents,
                file_id=file_id,
                filepath=filepath,
                enable_vision=enable_vision,
                engine_settings=engine_settings,
                database_name=database_name,
                data_dir=engine_settings.paths.data_dir,
                adapter=adapter,
                analysis_depth=analysis_depth,
            )
            if vision_job_id is not None:
                # Wizard §3.1 no-text short-circuit. An image-only / scanned doc
                # has no extractable text at load time, so domain detection
                # cannot run until *after* vision (minutes later). The wizard
                # must not wait on that: the instant we know the doc is
                # image-bearing with too little text AND it is routing into the
                # existing vision pipeline (a vision job was just enqueued), we
                # write a ``no_text`` detection_proposal for gate-eligible
                # sources so the wizard immediately shows "not enough text to
                # detect — pick a domain (defaults to generic)". The doc still
                # flows through vision → resume → chunk → the analysis-stage
                # gate (with the user's chosen forced_domain) — it is NEVER
                # routed down the empty-content ValidationError raise path,
                # because the early-return below takes it to VISION_PENDING.
                #
                # Gate-eligible = confirmation_required True AND no forced_domain
                # (same test the eager-detection step uses). Best-effort: a
                # failure here must never block the vision hand-off.
                first_meta = documents[0].get("metadata", {}) if documents else {}
                if _is_image_bearing_no_text(first_meta):
                    try:
                        _no_text_source = adapter.get_source(file_id, database_name)
                        if (
                            _no_text_source is not None
                            and _no_text_source.get("confirmation_required")
                            and not _no_text_source.get("forced_domain")
                        ):
                            from chaoscypher_core.operations.importing.confirmation_gate import (
                                write_detection_proposal,
                            )

                            write_detection_proposal(adapter, file_id, _no_text_proposal())
                            logger.info(
                                "indexing_no_text_proposal_written",
                                file_id=file_id,
                                vision_job_id=vision_job_id,
                            )
                    except Exception as _no_text_exc:
                        logger.warning(
                            "indexing_no_text_proposal_failed",
                            file_id=file_id,
                            error_type=type(_no_text_exc).__name__,
                            error_message=str(_no_text_exc),
                        )

                logger.info(
                    "indexing_paused_for_vision",
                    file_id=file_id,
                    vision_job_id=vision_job_id,
                )
                return {
                    "success": True,
                    "file_id": file_id,
                    "status": SourceStatus.VISION_PENDING,
                    "vision_job_id": vision_job_id,
                    "queued_for_vision": True,
                }
        else:
            # Resume path: the finalizer has already persisted every per-page
            # description. Splice them back into the freshly-loaded documents
            # using the same helper the finalizer ran in-memory; both call
            # sites use the deterministic loader output as their base so the
            # merge is reproducible across crashes.
            from chaoscypher_core.operations.importing.vision_finalizer import (
                _splice_descriptions_into_documents,
            )

            page_rows = adapter.list_vision_page_descriptions(file_id)
            documents = _splice_descriptions_into_documents(documents, page_rows)
            logger.info(
                "indexing_resume_after_vision_splice_applied",
                file_id=file_id,
                page_row_count=len(page_rows),
                document_count=len(documents),
            )

        # Optionally normalize content (clean OCR artifacts, encoding, whitespace)
        # Phase 5a (2026-05-08): capture raw loader text before normalization
        # so chunk offsets can later be recomputed against the original upload.
        # For archives / multi-document loaders we only preserve the first
        # document's text (most common case; full multi-doc support is a future
        # enhancement). Write is best-effort — never block indexing on an I/O
        # failure here. The toggle ``preserve_original_text_for_citations``
        # (default True) lets storage-conscious operators opt out.
        original_text_for_citations: str | None = None
        if (
            engine_settings is not None
            and engine_settings.chunking.preserve_original_text_for_citations
        ):
            original_text_for_citations = _persist_original_text(
                documents=documents,
                source_id=file_id,
                data_dir=engine_settings.paths.data_dir,
            )

        full_text, cleaner_counts = _extract_text(
            documents=documents,
            enable_normalization=enable_normalization,
            filepath=filepath,
            file_id=file_id,
            engine_settings=engine_settings,
            domain_normalizer_overrides=domain_normalizer_overrides,
        )

        # Workstream 11 (2026-05-08): surface the aggregated cleaner-stage
        # quality counts on the source row. ``_extract_text`` already
        # summed across every document the loader emitted, so a single
        # increment per counter captures the full per-source impact of
        # the normalizer pipeline. Best-effort — never block indexing on
        # a counter UPDATE (the helper itself swallows failures).
        if any(cleaner_counts.values()):
            from chaoscypher_core.services.quality.counters import (
                QualityCounter,
                increment_quality_counter,
            )

            counter_map = (
                ("lines_removed", QualityCounter.CLEANER_LINES_REMOVED),
                (
                    "paragraphs_deduplicated",
                    QualityCounter.CLEANER_PARAGRAPHS_DEDUPLICATED,
                ),
                ("chars_removed", QualityCounter.CLEANER_CHARS_REMOVED),
                ("ocr_predicate_skips", QualityCounter.OCR_CLEANER_SKIPPED_BY_PREDICATE),
                # Phase 6 (2026-05-08): user cleaner plugin load failures.
                (
                    "cleaner_plugin_load_failures",
                    QualityCounter.CLEANER_PLUGIN_LOAD_FAILURES,
                ),
            )
            for key, counter in counter_map:
                value = int(cleaner_counts.get(key, 0) or 0)
                if value > 0:
                    await increment_quality_counter(
                        adapter=adapter,
                        source_id=file_id,
                        database_name=database_name,
                        counter=counter,
                        n=value,
                    )

        # Guard: empty content after load + normalize means we have nothing
        # to chunk or embed. Raise rather than silently producing a zero-chunk
        # INDEXED source. Most-likely causes: scanned PDF with vision off; a
        # corrupt or empty file; an over-aggressive normalizer. Audit fix #H3.
        #
        # Workstream 6 (2026-05-07): inspect loader metadata + the row's
        # ``enable_vision`` setting and surface a specific actionable
        # message — "this PDF has 250 image-only pages, enable vision",
        # "image upload needs vision to extract text", etc. Generic
        # fallback mentions the normalization toggle so the operator
        # knows what to flip.
        if len(full_text.strip()) < _MIN_INDEXABLE_CHARS:
            first_meta = documents[0].get("metadata", {}) if documents else {}
            if not isinstance(first_meta, dict):
                first_meta = {}
            image_page_count = int(first_meta.get("image_page_count") or 0)
            ext = Path(str(filepath)).suffix.lower().lstrip(".")
            file_type = str(first_meta.get("file_type") or ext)

            logger.warning(
                "indexing_empty_content",
                file_id=file_id,
                filepath=filepath,
                character_count=len(full_text),
                file_type=file_type,
                image_page_count=image_page_count,
                enable_vision=enable_vision,
                extraction_method=first_meta.get("extraction_method"),
            )

            if file_type == "pdf" and image_page_count > 0:
                if enable_vision is False:
                    msg = (
                        f"This PDF has {image_page_count} image-only pages "
                        "and produced no extractable text. Enable vision in "
                        "upload settings to extract content from scanned "
                        "documents."
                    )
                else:
                    msg = (
                        f"This PDF has {image_page_count} image-only pages "
                        "and vision processing returned no text. The vision "
                        "model may have failed; check vision_page_descriptions "
                        "for this source to see per-page status."
                    )
                raise ValidationError(msg, field="file")

            if file_type in {"png", "jpg", "jpeg", "gif", "tiff", "tif", "webp", "bmp"}:
                if enable_vision is False:
                    msg = (
                        "Image content cannot be extracted without vision. "
                        "Enable vision in upload settings to describe the "
                        "image."
                    )
                else:
                    msg = (
                        "Image upload produced no text after vision "
                        "processing. The vision model may have failed; "
                        "check vision_page_descriptions for this source "
                        "to see per-page status."
                    )
                raise ValidationError(msg, field="file")

            msg = (
                f"Document has fewer than {_MIN_INDEXABLE_CHARS} characters "
                "of extractable content after loading and normalization. "
                "The file may be empty, structurally unusual, or already "
                "filtered out by normalization. Try uploading again with "
                "enable_normalization=False if you suspect cleanup is the "
                "cause."
            )
            raise ValidationError(msg, field="file")

        logger.info(
            "import_document_text_extracted",
            file_id=file_id,
            character_count=len(full_text),
            filepath=filepath,
        )

        # Step 2/2: Creating chunks
        adapter.update_step_progress(file_id, 2, 2, "Creating chunks")

        # Build a unified location_index from per-document loader metadata.
        # PDFs emit page_number entries; EPUB/DOCX emit section entries.
        # Loaders that don't emit one contribute nothing to the merge (but
        # their content still shifts cumulative offsets for following docs).
        #
        # First, REBUILD each PDF doc's location_index from its current
        # _page_texts. vision_finalizer mutates _page_texts in place
        # (appending visual-content descriptions), and the PDF loader's
        # original location_index goes stale whenever that happens. Loaders
        # without _page_texts (EPUB/DOCX) keep their loader-emitted index.
        from chaoscypher_core.utils.chunk import (
            build_pdf_location_index,
            merge_location_indexes,
        )

        for doc in documents:
            metadata = doc.get("metadata") or {}
            page_texts = metadata.get("_page_texts")
            if page_texts:
                metadata["location_index"] = build_pdf_location_index(page_texts)

        docs_with_indexes = [
            (doc.get("content", ""), doc.get("metadata", {}).get("location_index"))
            for doc in documents
        ]
        merged_location_index = merge_location_indexes(docs_with_indexes, separator="\n\n")

        # Create hierarchical chunks (small chunks + groups)
        # Use cached chunking_service from worker initialization.
        # Phase 5a: pass original_text so the chunker can recompute char offsets
        # against the upload rather than the post-cleaner text. None when the
        # toggle is disabled or the write failed (best-effort).
        chunking_result = await chunking_service.create_chunks(
            source_id=file_id,
            full_text=full_text,
            analysis_depth=analysis_depth,
            original_text=original_text_for_citations,
            location_index=merged_location_index or None,
        )

        chunking_service.store_chunks(chunking_result, database_name=database_name)

        # Wizard §3.1 (2026-05-29): eager domain detection for gate-eligible
        # sources. Runs immediately after chunks are durably persisted and
        # BEFORE queue_embed_chunks so the wizard's poll-until-proposal
        # predicate sees a populated detection_proposal within seconds of
        # upload, while embedding proceeds in the background.
        #
        # Gate-eligible = confirmation_required True AND no forced_domain.
        # Non-eligible paths (forced domain, no confirmation, vision-only)
        # skip this block entirely and get NO eager proposal.
        #
        # Best-effort: a failure here must never block the embedding step.
        # Status stays INDEXING — write_detection_proposal does NOT flip it.
        try:
            _gate_source = adapter.get_source(file_id, database_name)
            if (
                _gate_source is not None
                and _gate_source.get("confirmation_required")
                and not _gate_source.get("forced_domain")
            ):
                from chaoscypher_core.operations.importing.confirmation_gate import (
                    proposal_from_detection,
                    write_detection_proposal,
                )
                from chaoscypher_core.services.sources.engine.extraction.domains import (
                    create_domain_sample_text,
                    get_domain_registry,
                )
                from chaoscypher_core.services.sources.engine.extraction.orchestration import (
                    detect_extraction_domain,
                )

                _eager_chunks = adapter.get_chunks_for_extraction(
                    source_id=file_id,
                    database_name=database_name,
                )
                _eager_sample = create_domain_sample_text(_eager_chunks, content_key="content")
                _eager_registry = get_domain_registry(engine_settings, database_name=database_name)
                _eager_result = detect_extraction_domain(
                    registry=_eager_registry,
                    forced_domain=None,
                    sample_text=_eager_sample,
                    filename=file_info.get("filename", ""),
                    metadata=file_info.get("metadata", {}),
                )
                _eager_proposal = proposal_from_detection(_eager_result)
                write_detection_proposal(adapter, file_id, _eager_proposal)
                logger.info(
                    "indexing_eager_detection_proposal_written",
                    file_id=file_id,
                    detected_domain=_eager_proposal.get("detected_domain"),
                )
        except Exception as _eager_exc:
            logger.warning(
                "indexing_eager_detection_failed",
                file_id=file_id,
                error_type=type(_eager_exc).__name__,
                error_message=str(_eager_exc),
            )

        # Forward-progress milestone (2026-05-12): chunks are now durably
        # persisted, so any prior recovery attempts can be cleared — the
        # next reconcile pass will see a source that genuinely advanced.
        # The reset MUST stay anchored to a real progress event (not stage
        # entry) or the recovery exhaustion guard at
        # services/sources/recovery.py:331 is silently bypassed: every
        # mid-flight cancellation (e.g. operations_worker.timeout firing
        # during long vision processing) would reset the counter to 0,
        # producing an infinite recovery loop. Best-effort — counter
        # mismatch is recoverable; blocking indexing on it is not.
        try:
            adapter.reset_source_recovery_attempts(
                source_id=file_id,
                database_name=database_name,
            )
        except Exception as exc:
            logger.warning(
                "reset_recovery_attempts_failed",
                source_id=file_id,
                database_name=database_name,
                stage="indexing",
                error_type=type(exc).__name__,
            )

        chunks_persisted = chunking_result.total_small_chunks
        logger.info(
            "import_document_chunks_created",
            file_id=file_id,
            small_chunks=chunks_persisted,
            hierarchical_groups=chunking_result.total_groups,
            chunks_filtered=chunking_result.chunks_filtered,
        )

        # Workstream 5.3 (2026-05-07): record post-split min-size events on
        # the source row so the data-quality tab can flag aggressive
        # filtering. Best-effort; never block indexing on a counter UPDATE.
        # W5 follow-up (2026-05-08): the value now counts coalesce / merge
        # events rather than drops.
        # Phase 7 (2026-05-09): DB column renamed chunks_filtered_count ->
        # chunks_coalesced_count; enum renamed CHUNKS_FILTERED -> CHUNKS_COALESCED.
        if chunking_result.chunks_filtered > 0:
            from chaoscypher_core.services.quality.counters import (
                QualityCounter,
                increment_quality_counter,
            )

            await increment_quality_counter(
                adapter=adapter,
                source_id=file_id,
                database_name=database_name,
                counter=QualityCounter.CHUNKS_COALESCED,
                n=chunking_result.chunks_filtered,
            )

        # P2T10 (2026-05-08): record the three new chunker-stage quality counts
        # that were previously invisible. Best-effort; never block indexing on a
        # counter UPDATE (increment_quality_counter swallows on failure).
        _chunker_count_map = (
            ("normalize_drops", "CHUNKER_NORMALIZE_DROPS"),
            ("prestrip_lines_removed", "CHUNKER_PRESTRIP_LINES_REMOVED"),
            ("chunks_skipped_by_depth", "CHUNKS_SKIPPED_BY_DEPTH"),
        )
        if any(getattr(chunking_result, f, 0) for f, _ in _chunker_count_map):
            from chaoscypher_core.services.quality.counters import (
                QualityCounter,
                increment_quality_counter,
            )

            for field, counter_name in _chunker_count_map:
                n = int(getattr(chunking_result, field, 0) or 0)
                if n:
                    await increment_quality_counter(
                        adapter=adapter,
                        source_id=file_id,
                        database_name=database_name,
                        counter=QualityCounter[counter_name],
                        n=n,
                    )

        # Workstream 5.4 (2026-05-07): zero-chunk guard. If we made it past
        # the empty-content guard above (full_text was non-empty) but still
        # produced zero surviving chunks, the normalizer / cleaners ate
        # every line or every chunk fell below ``min_chunk_size``. Raise
        # with an actionable hint instead of letting the source land as
        # INDEXED with chunks_count=0 — that looks like success in the UI
        # but every search returns nothing.
        if chunks_persisted == 0:
            msg = (
                "Document produced no chunks after normalization and "
                "chunking. The cleaners or structural-noise filter consumed "
                "all content. Try re-uploading with "
                "``enable_normalization=False`` or disabling "
                "``chunking.normalize_remove_structural_noise``."
            )
            logger.warning(
                "indexing_zero_chunks",
                file_id=file_id,
                full_text_length=len(full_text),
                chunks_filtered=chunking_result.chunks_filtered,
            )
            raise ValidationError(msg, field="content")

        # Hand off the LLM-bound embedding stage to QUEUE_LLM. The payload
        # is ID-only — the embedding handler fetches unembedded chunks from
        # the database via ``adapter.list_unembedded_chunks``. See
        # ``embedding_handler.py`` for the rest of the finalize flow
        # (complete_indexing, task_completed event, auto_analyze queueing).
        #
        # Priority mirrors the downstream-analysis enqueue: background. An
        # indexing task that was itself kicked off interactively will
        # complete its chunks stage fast anyway and the embedding pass is
        # the bulk of the work — it's correct for it to run behind any
        # user-facing interactive chat task.
        adapter.update_step_progress(file_id, 2, 2, "Queuing embeddings")
        embed_task_id = await queue_embed_chunks(
            source_id=file_id,
            file_info=file_info,
            database_name=database_name,
            priority=settings.priorities.background,
        )

        logger.info(
            "import_document_embedding_queued",
            file_id=file_id,
            embed_task_id=embed_task_id,
            chunks_persisted=chunks_persisted,
        )

        # Note: ``complete_indexing``, ``task_completed``, the final
        # step-progress clear, and the ``auto_analyze`` enqueue all live
        # in ``handle_embed_chunks`` now. They represent pipeline
        # completion, which happens after embeddings land.

        return {
            "success": True,
            "file_id": file_id,
            "status": SourceStatus.INDEXING,
            "chunks_persisted": chunks_persisted,
            "embed_task_id": embed_task_id,
            "queued_for_embedding": True,
        }

    except Exception as exc:
        logger.exception(
            "import_document_indexing_failed",
            file_id=file_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

        # Mark indexing stage as failed
        if file_id is None:
            raise  # Re-raise without status update if file_id was never set

        # Rollback the session to clear any pending transaction errors
        # (e.g., IntegrityError leaves session in PendingRollbackError state)
        try:
            adapter.session.rollback()
        except Exception:
            logger.debug("session_rollback_failed_before_fail_indexing", file_id=file_id)

        # Best-effort cleanup of any partially-rendered vision PNGs so a
        # crash mid-vision (e.g. LLM error after page 3 of 10) does not
        # leave orphaned files in ``{data_dir}/databases/<db>/images/<src>/``
        # forever. Failure to clean is logged but never raised — the
        # underlying indexing exception must still propagate. Audit fix F32.
        cleanup_vision_images(
            data_dir=engine_settings.paths.data_dir,
            database_name=database_name,
            source_id=file_id,
        )

        adapter.fail_indexing(file_id, str(exc))

        raise

    # No finally/disconnect - singleton adapter is long-lived


def _resolve_content_type(
    metadata: dict[str, Any] | None,
    filepath: str,
) -> ContentType:
    """Resolve a document's ContentType from loader metadata.

    Falls back to the filename extension when the MIME-derived key doesn't
    map to a known ContentType.

    Args:
        metadata: Per-document metadata dict from the loader (may be ``None``).
        filepath: Source file path used to derive the extension-based fallback.

    Returns:
        The resolved ``ContentType`` for this document.

    Phase 7 audit-remediation (2026-05-09): a loader-supplied
    ``metadata["content_type"]`` like ``"application/vnd.ms-excel"`` was
    previously split on ``"/"`` yielding ``"vnd.ms-excel"``, then fed to
    ``ContentType.from_extension``.  Unknown post-split keys silently
    produced ``ContentType.TEXT`` (the map's default fallback), which could
    route documents to the wrong cleaner.  This helper validates the
    post-split key; if the result is identical to what an empty/invalid
    extension would produce *and* the ct_key isn't one of the explicitly
    mapped text-like extensions, it logs a structured warning
    (``content_type_unknown_mime_fallback``) and falls back to the
    extension-derived type from ``filepath`` instead.
    """
    from chaoscypher_core.services.sources.normalizer import ContentType
    from chaoscypher_core.services.sources.normalizer.models import (
        _EXTENSION_TO_CONTENT_TYPE,
    )

    file_ext = filepath.lower().rsplit(".", 1)[-1] if "." in filepath else ""
    extension_fallback = ContentType.from_extension(file_ext)

    per_doc_ct = metadata.get("content_type") if isinstance(metadata, dict) else None
    if per_doc_ct and isinstance(per_doc_ct, str):
        # "text/html" → "html"; "html" → "html"; "application/vnd.ms-excel" → "vnd.ms-excel"
        ct_key = per_doc_ct.split("/")[-1].lower()
        if ct_key in _EXTENSION_TO_CONTENT_TYPE:
            return _EXTENSION_TO_CONTENT_TYPE[ct_key]
        # ct_key is not a recognized extension — the MIME subtype is unknown.
        # Log a structured warning so operators can diagnose misrouting, then
        # fall back to the extension-derived type rather than silently returning
        # ContentType.TEXT (the from_extension fallback for unrecognized keys).
        logger.warning(
            "content_type_unknown_mime_fallback",
            mime=per_doc_ct,
            filepath=filepath,
        )
        return extension_fallback

    return extension_fallback


def _extract_text(
    documents: list[dict[str, Any]],
    enable_normalization: bool,
    filepath: str,
    file_id: str,
    engine_settings: EngineSettings | None = None,
    domain_normalizer_overrides: Any | None = None,
) -> tuple[str, dict[str, int]]:
    """Extract and optionally normalize text from loaded documents.

    Args:
        documents: Raw document dicts from the loader registry.
        enable_normalization: Whether to apply content normalization.
        filepath: Source file path (used for content type detection).
        file_id: Source file ID for logging.
        engine_settings: Engine settings to thread into the normalizer so
            operator flags (``enable_ocr_cleaning`` etc.) and the real
            ``data_dir`` for plugin discovery actually take effect.
            Workstream 5.2 (2026-05-07).
        domain_normalizer_overrides: Optional ``DomainNormalizerOverrides``
            from the source's resolved domain config.  When provided, each
            non-``None`` flag overrides the corresponding global
            ``NormalizerSettings`` flag for this source only.  Per-source
            granularity is intentionally limited to the existing single
            ``enable_normalization`` kill-switch — the per-cleaner
            granularity lives at the domain level (Phase 4 Task 4,
            2026-05-08).

    Returns:
        Tuple of ``(full_text, cleaner_counts)`` where ``cleaner_counts``
        is a ``{lines_removed, paragraphs_deduplicated, chars_removed,
        ocr_predicate_skips}`` dict summed across every document the
        normalizer touched. Counts are zero when normalization was skipped
        or when no cleaner reported a removal (Workstream 11, 2026-05-08).
        ``ocr_predicate_skips`` is non-zero when the OCR cleaner was
        globally enabled but skipped by its predicate due to an unknown
        extraction_method (Phase 2 observability, 2026-05-08).

    """
    from chaoscypher_core.services.sources.normalizer import (
        ContentNormalizerService,
    )
    from chaoscypher_core.services.sources.normalizer.service import _resolved_settings

    cleaner_counts: dict[str, int] = {
        "lines_removed": 0,
        "paragraphs_deduplicated": 0,
        "chars_removed": 0,
        "ocr_predicate_skips": 0,
        # Phase 6 (2026-05-08): user cleaner plugin failures from the registry.
        "cleaner_plugin_load_failures": 0,
    }

    if enable_normalization:
        # Phase 4 Task 4 (2026-05-08): apply per-domain cleaner overrides on
        # top of the global NormalizerSettings before handing settings to the
        # normalizer.  When ``domain_normalizer_overrides`` is ``None`` (the
        # common case) ``_resolved_settings`` is a no-op and returns the
        # global settings unchanged.
        if engine_settings is not None and domain_normalizer_overrides is not None:
            merged_normalizer = _resolved_settings(
                engine_settings.normalizer, domain_normalizer_overrides
            )
            # Build a lightweight EngineSettings copy with the merged normalizer
            # so plugin discovery (data_dir) is still driven by the real
            # engine_settings while the cleaner flags reflect domain overrides.
            effective_engine_settings: EngineSettings | None = engine_settings.model_copy(
                update={"normalizer": merged_normalizer}
            )
        else:
            effective_engine_settings = engine_settings

        normalizer = ContentNormalizerService(settings=effective_engine_settings)

        # Phase 6 (2026-05-08): read once after registry init so the count
        # is per-source (not multiplied by document count).
        cleaner_counts["cleaner_plugin_load_failures"] = normalizer.plugin_load_failures

        # Phase 7 (2026-05-09): content-type resolution is now handled by
        # _resolve_content_type, which validates the MIME-derived key, logs a
        # structured warning when unrecognized, and falls back to the
        # extension-derived type.  The old inline code (split on "/", call
        # from_extension, silently produce ContentType.TEXT for unknown MIME
        # subtypes like "vnd.ms-excel") is replaced by the helper call below.

        normalized_docs = []
        for doc in documents:
            doc_meta = doc.get("metadata", {}) or {}
            content_type = _resolve_content_type(doc_meta, filepath)
            normalized = normalizer.normalize(
                content=doc["content"],
                content_type=content_type,
                metadata=doc_meta,
            )
            normalized_docs.append(normalized)
            cleaner_counts["lines_removed"] += int(getattr(normalized, "lines_removed", 0) or 0)
            cleaner_counts["paragraphs_deduplicated"] += int(
                getattr(normalized, "paragraphs_deduplicated", 0) or 0
            )
            cleaner_counts["chars_removed"] += int(getattr(normalized, "chars_removed", 0) or 0)
            cleaner_counts["ocr_predicate_skips"] += int(
                getattr(normalized, "ocr_predicate_skips", 0) or 0
            )

        # Log quality metrics from normalization
        if normalized_docs:
            avg_quality = sum(d.quality_metrics.overall_score() for d in normalized_docs) / len(
                normalized_docs
            )
            logger.info(
                "import_document_normalized",
                file_id=file_id,
                docs_count=len(normalized_docs),
                avg_quality_score=round(avg_quality, 3),
                cleaner_lines_removed=cleaner_counts["lines_removed"],
                cleaner_paragraphs_deduplicated=cleaner_counts["paragraphs_deduplicated"],
                cleaner_chars_removed=cleaner_counts["chars_removed"],
            )

        full_text = "\n\n".join([doc.content for doc in normalized_docs]) if normalized_docs else ""
        return full_text, cleaner_counts

    # Skip normalization - use raw content
    logger.info(
        "import_document_normalization_skipped",
        file_id=file_id,
        reason="disabled_by_user",
    )
    raw_text = "\n\n".join([doc["content"] for doc in documents]) if documents else ""
    return raw_text, cleaner_counts


def _persist_original_text(
    documents: list[dict[str, Any]],
    source_id: str,
    data_dir: Any,
) -> str | None:
    """Write raw loader text to ``original.txt`` for citation recomputation.

    Called *before* normalization so the file captures the user's actual
    upload content. For archives / multi-document loaders only the first
    document's text is written (most common case; full multi-doc support is a
    future enhancement). Logs and returns ``None`` on any failure so the
    calling pipeline is never blocked by an I/O error here.

    Args:
        documents: Loader output documents.
        source_id: Source UUID (used as sub-directory name).
        data_dir: Application data root (``engine_settings.paths.data_dir``).

    Returns:
        The raw text that was written, or ``None`` if nothing was written
        (empty document list, first doc has no content, or I/O error).

    """
    from chaoscypher_core.services.sources.management.paths import get_original_text_path

    if not documents:
        return None

    first_doc = documents[0]
    raw_text: str | None = None

    if isinstance(first_doc, dict):
        raw_text = first_doc.get("content")
    if not raw_text:
        return None

    if len(documents) > 1:
        logger.info(
            "original_text_persist_first_doc_only",
            source_id=source_id,
            total_docs=len(documents),
        )

    try:
        dest = get_original_text_path(source_id=source_id, data_dir=Path(str(data_dir)))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(raw_text, encoding="utf-8")
        logger.debug(
            "original_text_persisted",
            source_id=source_id,
            path=str(dest),
            chars=len(raw_text),
        )
    except OSError as exc:
        logger.warning(
            "original_text_persist_failed",
            source_id=source_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return None

    return raw_text


def _get_active_vision_model(settings: EngineSettings) -> str | None:
    """Get the active vision model name from settings.

    Args:
        settings: Engine settings with LLM configuration.

    Returns:
        Vision model name, or None if not configured.
    """
    provider = getattr(settings.llm, "chat_provider", None)
    if not provider:
        return None
    return getattr(settings.llm, f"{provider}_vision_model", None)


def _select_quick_vision_pages(
    pages: list[dict[str, Any]],
    cap: int,
) -> list[dict[str, Any]]:
    """Sample image pages for ``extraction_depth='quick'`` vision processing.

    The selection policy targets a representative cross-section rather
    than a contiguous prefix so a 400-page book burns ~5% of the
    vision-LLM cost while still picking up the cover page, the last
    page (often references / index), and N evenly-spaced spreads
    through the body. Concretely:

    * Always include the first image page (cover).
    * Always include the last image page (when distinct from cover).
    * Evenly space the remainder across the interior so the picks are
      reproducible: for cap=20 + 400 pages the spacing is ~20 pages.
    * Cap the result at ``cap`` (sourced from
      ``LoaderSettings.vision_quick_sample_max_pages`` — never a
      literal here; CC046-style rule).

    Standalone images (``kind=STANDALONE_IMAGE``) are returned unchanged:
    they're always 1-page-per-source so sampling them down would lose the
    only image the user uploaded. The sampling policy only narrows
    multi-page PDF pages.

    The function is deterministic — the same ``pages`` list always
    returns the same selection — so a recovery / retry of a Quick
    import processes the same subset. It does not mutate the input
    list; ordering of the returned list follows the original page
    order so downstream consumers (per-page enqueue, finalizer splice)
    see ascending ``page_number`` and tests can assert on positions.

    TOC heuristic note: a heuristic for "Contents" / "Table of
    Contents" pages would require deep loader changes (the loader
    output today only exposes ``has_images`` per page, not extracted
    text). Skipping for v1 — cover + evenly-spaced + last is the
    representative sample. If we add per-page text to the loader
    output, this is the function to revisit.

    Args:
        pages: Image page dicts as built by ``_apply_vision_processing``.
            Each has ``page_number``, ``kind``, ``image_path``,
            ``doc_index``.
        cap: Maximum number of pages the work queue should include —
            sourced from ``LoaderSettings.vision_quick_sample_max_pages``.

    Returns:
        Subset of ``pages`` preserving original order. Returns the input
        unchanged when ``len(pages) <= cap``.
    """
    if cap <= 0 or not pages:
        # Defensive: caller validates cap >= 1, but guard the loop math
        # against accidental misconfiguration. An empty queue is a no-op.
        return []

    # Standalone images: never sample — there's only one image per source.
    standalone = [p for p in pages if p.get("kind") == VisionPageKind.STANDALONE_IMAGE]
    pdf_pages = [p for p in pages if p.get("kind") == VisionPageKind.PDF_PAGE]

    if len(pdf_pages) <= cap:
        # Nothing to sample — every PDF image page fits inside the cap.
        return pages

    # Reserve cover (first) + last; evenly space the middle picks.
    # ``cover`` and ``last`` always occupy two slots when distinct, so
    # the interior budget is cap - 2 picks across (len - 2) interior
    # candidates.
    cover = pdf_pages[0]
    last = pdf_pages[-1]
    interior = pdf_pages[1:-1]
    interior_budget = cap - 2

    selected_indices: list[int] = []
    if interior_budget > 0 and interior:
        # Evenly-spaced indices into ``interior`` — divmod-free so the
        # arithmetic is obviously reproducible. Using float step then
        # rounding keeps the picks symmetric (e.g. cap=20 on a 400-page
        # PDF lands ~every 22 pages with the spread centered).
        step = len(interior) / interior_budget
        for slot in range(interior_budget):
            # ``int(slot * step)`` is monotonic and bounded by
            # ``len(interior) - 1``; the set guard de-dupes the
            # pathological case where step < 1 (more budget than
            # interior pages).
            idx = int(slot * step)
            if idx >= len(interior):
                idx = len(interior) - 1
            if idx not in selected_indices:
                selected_indices.append(idx)

    sampled: list[dict[str, Any]] = [cover]
    sampled.extend(interior[idx] for idx in selected_indices)
    sampled.append(last)

    # Preserve original ordering — pages came in ascending page_number,
    # the selection above already follows that order, but a defensive
    # sort makes the contract explicit for future maintainers.
    sampled.sort(key=lambda p: int(p["page_number"]))

    # Re-attach standalone images. They go at the end so the PDF-page
    # ordering stays intact — the finalizer doesn't care, but
    # debug-log readers do.
    sampled.extend(standalone)
    return sampled


async def _apply_vision_processing(
    documents: list[dict[str, Any]],
    file_id: str,
    filepath: str,
    enable_vision: bool | None,
    engine_settings: EngineSettings,
    database_name: str,
    data_dir: Any,
    *,
    adapter: StageProgressStorageProtocol,
    analysis_depth: str = "full",
) -> tuple[list[dict[str, Any]], str | None]:
    """Loader-phase entry point: enqueue per-page vision tasks.

    PR 2 (2026-05-13, Task 12) rewire. The legacy gather-and-merge body
    is replaced with a queue-driven hand-off. Behaviour:

    1. Collect image pages (PDF pages with images, standalone images).
    2. If vision is disabled, no model is configured, or there are no
       image pages, return ``(documents, None)`` — the caller continues
       indexing in-line with the loader's documents unchanged.
    3. Otherwise, create a ``vision_jobs`` row + N pending
       ``vision_page_descriptions`` rows, flip the source state to
       ``vision_pending`` via an atomic compare-and-swap, and enqueue
       one ``OP_VISION_PAGE`` task per pending row on ``QUEUE_LLM``.

    The finalizer (``OP_VISION_FINALIZE`` handler) takes over once all
    per-page tasks reach a terminal state — it re-loads documents,
    splices descriptions back in, transitions the source from
    ``VISION_PENDING`` back to ``INDEXING``, and enqueues a fresh
    ``OP_INDEX_DOCUMENT`` with ``resume_after_vision=True`` so the
    indexing handler picks up the post-vision pipeline (normalize ->
    chunk -> embed).

    Args:
        documents: Loader output with per-page metadata.
        file_id: Source file ID.
        filepath: Path to source file. Used as the ``image_path`` for
            PDF page rows; the per-page handler opens the PDF and
            renders the specific page ephemerally.
        enable_vision: None=auto, True=force, False=skip.
        engine_settings: Engine settings (vision-model resolution).
        database_name: Current database name (scope for the CAS
            state transition).
        data_dir: Base data directory. Unused on the new path — the
            per-page handler renders ephemerally. Kept on the signature
            for caller-shape stability while tests migrate; the
            ``# noqa: ARG001`` above pins the deliberate ignore.
        adapter: Storage adapter exposing the vision-job /
            transition-source APIs. The unified SqliteAdapter satisfies
            this contract.
        analysis_depth: Extraction depth from the source row /
            queue payload — ``'quick'`` triggers
            ``_select_quick_vision_pages`` to narrow the work queue
            to a representative sample (cover + N evenly-spaced +
            last page) capped at
            ``LoaderSettings.vision_quick_sample_max_pages``. The
            skipped count increments
            ``QualityCounter.VISION_PAGES_SAMPLED_QUICK_MODE`` so the
            Processing tab surfaces "Quick mode: 12 of 400 pages"
            instead of looking like a partial vision failure.
            ``'full'`` (default) processes every image page.

    Returns:
        Tuple of ``(documents, vision_job_id)``. ``vision_job_id`` is
        ``None`` when no vision work was enqueued (vision disabled, no
        model, no images); the caller continues inline. When non-None,
        the caller must return immediately — the finalizer drives the
        rest.
    """
    if enable_vision is False:
        logger.info("vision_disabled_by_request", file_id=file_id)
        return documents, None

    vision_model = _get_active_vision_model(engine_settings)
    if vision_model is None:
        if enable_vision is True:
            logger.warning(
                "vision_requested_but_no_model_configured",
                file_id=file_id,
                hint="Configure llm.<provider>_vision_model to enable vision.",
            )
        else:
            logger.info(
                "vision_skipped_no_model_configured",
                file_id=file_id,
            )
        return documents, None

    # Collect image pages from PDF documents + standalone images.
    image_pages: list[dict[str, Any]] = []
    for doc_idx, doc in enumerate(documents):
        metadata = doc.get("metadata", {})

        for page_info in metadata.get("pages", []):
            if not page_info.get("has_images"):
                continue
            try:
                page_num = int(page_info["page_number"])
            except TypeError, ValueError, KeyError:
                logger.warning(
                    "vision_page_number_invalid",
                    file_id=file_id,
                    raw_value=repr(page_info.get("page_number")),
                )
                continue
            image_pages.append(
                {
                    "page_number": page_num,
                    "kind": VisionPageKind.PDF_PAGE,
                    "image_path": str(filepath),
                    "doc_index": doc_idx,
                }
            )

        # Standalone images (from ImageLoader).
        if metadata.get("extraction_method") == "vision_pending":
            image_path = metadata.get("image_path")
            if image_path:
                image_pages.append(
                    {
                        # Standalone images use page_number=1 by convention.
                        "page_number": 1,
                        "kind": VisionPageKind.STANDALONE_IMAGE,
                        "image_path": image_path,
                        "doc_index": doc_idx,
                    }
                )

    if not image_pages:
        return documents, None

    # Wave 4-5 (2026-05-23): honor ``extraction_depth='quick'`` at the
    # work-queue builder. The Quick/Full toggle in the upload dialog
    # reached the source row but was ignored here, so a 400-page Quick
    # import burned the full vision-LLM cost. ``_select_quick_vision_pages``
    # narrows the queue to a representative sample (cover + N evenly-
    # spaced body pages + last page) capped at LoaderSettings.
    # vision_quick_sample_max_pages. The skipped count increments the
    # VISION_PAGES_SAMPLED_QUICK_MODE QualityCounter so the Processing
    # tab can show "Quick mode: 12 of 400 pages processed (388 skipped
    # by Quick mode)" rather than looking like a partial failure.
    from chaoscypher_core.services.quality.counters import (
        QualityCounter,
        increment_quality_counter,
    )

    total_image_pages = len(image_pages)
    sampling_applied = False
    if analysis_depth == "quick":
        cap = engine_settings.loader.vision_quick_sample_max_pages
        sampled_pages = _select_quick_vision_pages(image_pages, cap)
        skipped = total_image_pages - len(sampled_pages)
        if skipped > 0:
            sampling_applied = True
            image_pages = sampled_pages
            await increment_quality_counter(
                adapter=adapter,
                source_id=file_id,
                database_name=database_name,
                counter=QualityCounter.VISION_PAGES_SAMPLED_QUICK_MODE,
                n=skipped,
            )
    else:
        # Full mode: cost / resource-exhaustion backstop. Hard-fail before
        # creating any vision job/page rows when the page count exceeds the
        # per-source ceiling. The raise propagates to the indexing handler's
        # ``except`` block (fail_indexing) and is classified permanent by the
        # queue (no retry). Quick mode is exempt — it is already sampled above.
        enforce_source_fanout_ceiling(
            item_count=total_image_pages,
            max_items=engine_settings.loader.vision_max_pages,
            item_noun="image pages",
            stage="vision",
            setting_path="loader.vision_max_pages",
        )

    logger.info(
        "vision_processing_enqueued",
        file_id=file_id,
        image_page_count=len(image_pages),
        total_image_pages=total_image_pages,
        analysis_depth=analysis_depth,
        sampling_applied=sampling_applied,
        vision_model=vision_model,
    )

    # Create vision_job + N pending vision_page_descriptions rows. The
    # mixin wraps both inserts in one transaction; the storage layer
    # owns atomicity.
    job_id: str = adapter.create_vision_job_with_pages(
        source_id=file_id,
        pages=[
            {
                "page_number": p["page_number"],
                "kind": p["kind"],
                "image_path": p["image_path"],
            }
            for p in image_pages
        ],
    )

    # Atomic CAS INDEXING -> VISION_PENDING so a concurrent finalize /
    # recovery dispatch cannot race the flip. The previous status is
    # INDEXING because ``_run_indexing`` called ``start_indexing`` right
    # before the loader phase.
    transitioned = adapter.transition_source_status(
        file_id,
        from_status=SourceStatus.INDEXING.value,
        to_status=SourceStatus.VISION_PENDING.value,
        database_name=database_name,
    )
    if not transitioned:
        logger.warning(
            "vision_pending_transition_skipped",
            file_id=file_id,
            hint=(
                "source status was not INDEXING - concurrent transition "
                "won the race, or the source was deleted mid-flight"
            ),
        )

    # Enqueue one OP_VISION_PAGE per pending row. The handler
    # (``vision_operations_service._handle_vision_page``) is registered
    # on QUEUE_LLM so per-page LLM calls are paced by the LLM worker
    # (concurrency = 1).
    page_rows = adapter.list_vision_page_descriptions(file_id, statuses=[VisionPageStatus.PENDING])
    for row in page_rows:
        await queue_client.enqueue_task(
            queue=QUEUE_LLM,
            operation=OP_VISION_PAGE,
            data={
                "page_id": row["id"],
                "job_id": job_id,
                "source_id": file_id,
            },
            metadata={
                "source_id": file_id,
                "page_id": row["id"],
                "page_number": row["page_number"],
                "operation_type": OP_VISION_PAGE,
            },
        )

    return documents, job_id
