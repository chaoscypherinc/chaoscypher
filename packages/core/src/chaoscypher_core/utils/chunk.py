# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
# ruff: noqa: D301  -- docstrings use literal "\r" / "\n" / "\b" sequences
# to describe text-normalization behaviour; raw-prefixing would change meaning.

"""Chunking Service for chaoscypher-engine.

Hierarchical document chunking:
1. Small chunks (~900 chars) for RAG retrieval
2. Grouped chunks (4x small, ~900 tokens) for entity extraction

Research-based defaults (GraphRAG paper, RAG best practices 2025):
- 900-char small chunks (~225 tokens) for balanced RAG retrieval and extraction
- 4 chunks per group (~900 tokens after overlap) optimal for entity extraction
- 150-char overlap (~16%) within optimal 10-20% range

Single source of truth for all document chunking.
"""

import re
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict

import structlog

from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.models import ChunksResult, ExtractionResult
    from chaoscypher_core.ports.chunk import ChunkingProtocol
    from chaoscypher_core.settings import EngineSettings

# Phase 5a: citation offset recompute
# Minimum rapidfuzz partial_ratio score to accept a fuzzy match as a valid
# citation anchor.  Below this threshold, offsets are set to NULL and the
# method tagged 'none'.  80 is a pragmatic balance: low enough to handle
# aggressive whitespace normalisation, high enough to avoid false anchors.
_FUZZY_SCORE_THRESHOLD = 80

logger = structlog.get_logger(__name__)


class LocationBoundary(TypedDict):
    """One char-range entry in a LocationIndex.

    Either ``page_number`` or ``section`` (or both) is set per entry; the
    unset field is ``None``. Each entry's ``[start_char, end_char)`` range
    is half-open — ``end_char`` is exclusive.

    Coordinates match the loader's ``content`` field (pre-normalization),
    which approximates raw-upload coordinates. The chunker's lookup runs
    after Phase 5a (``_recompute_chunk_offsets``) so the coordinate
    systems align when ``original_text`` is provided.
    """

    start_char: int
    end_char: int
    page_number: int | None
    section: str | None


# Ordered list of LocationBoundary entries covering the joined loader content.
# Built by individual loaders (PDF, EPUB, DOCX); consumed by the chunker
# (and by merge_location_indexes when an orchestrator joins multiple docs).
LocationIndex = list[LocationBoundary]


def _lookup_location(
    location_index: LocationIndex | None,
    char_start: int,
) -> tuple[int | None, str | None]:
    """Return ``(page_number, section)`` for the boundary containing ``char_start``.

    Returns ``(None, None)`` when the index is missing/empty or when
    ``char_start`` falls outside every boundary range. The latter case
    is a defensive default for the rare event of Phase-5a recompute
    producing a char_start past the index's coverage.
    """
    if not location_index:
        return None, None
    for boundary in location_index:
        if boundary["start_char"] <= char_start < boundary["end_char"]:
            return boundary["page_number"], boundary["section"]
    return None, None


def build_pdf_location_index(
    page_texts: list[str],
    separator: str = "\n\n",
) -> LocationIndex:
    """Build a LocationIndex covering joined per-page text.

    Each entry maps the char range of one page (in the joined text) to
    its 1-based page_number. ``section`` is always None for PDF pages.

    This is the single source of truth for "how many chars does each page
    occupy in the joined content". Used by:
    - PDF loader, to emit the initial location_index alongside _page_texts
    - Orchestrators (indexing_handler, CLI), to REBUILD the index from
      the current _page_texts before chunking — vision_finalizer mutates
      _page_texts in place after the loader runs (appending visual-content
      descriptions), so the loader's original location_index goes stale
      whenever vision processing fires. Rebuilding closes that gap.
    """
    index: LocationIndex = []
    offset = 0
    sep_len = len(separator)
    for page_idx, page_text in enumerate(page_texts):
        page_len = len(page_text)
        index.append(
            {
                "start_char": offset,
                "end_char": offset + page_len,
                "page_number": page_idx + 1,  # 1-based
                "section": None,
            }
        )
        offset += page_len
        # Separator only appears BETWEEN pages, not after the last.
        if page_idx < len(page_texts) - 1:
            offset += sep_len
    return index


def merge_location_indexes(
    docs_with_indexes: list[tuple[str, LocationIndex | None]],
    separator: str = "\n\n",
) -> LocationIndex:
    r"""Merge per-document location indexes into one covering the joined text.

    The orchestrator joins multiple loader documents into a single
    ``full_text`` using ``separator`` (default ``"\n\n"`` — matches
    indexing_handler._extract_text). Each document's location_index is
    in its own local coordinates; this helper shifts them to align with
    the joined text.

    Documents whose loader didn't emit a location_index contribute
    nothing to the merge but still advance the cumulative offset
    (their content still occupies space in the joined text).
    """
    merged: LocationIndex = []
    offset = 0
    sep_len = len(separator)
    for i, (content, index) in enumerate(docs_with_indexes):
        if index:
            for boundary in index:
                merged.append(
                    {
                        "start_char": boundary["start_char"] + offset,
                        "end_char": boundary["end_char"] + offset,
                        "page_number": boundary["page_number"],
                        "section": boundary["section"],
                    }
                )
        offset += len(content)
        # Separator only appears BETWEEN documents — not after the last one.
        if i < len(docs_with_indexes) - 1:
            offset += sep_len
    return merged


# Patterns and placeholders for protecting quoted text from sentence splitting.
# Each placeholder is a single character replacing a two-char sequence ("x "),
# so character positions stay accurate through the protect/restore cycle.
_QUOTE_PATTERN = re.compile(
    r'"(?:[^"\\]|\\.)*"'
    r"|\u201c[^\u201d]*\u201d"
    r"|\u00ab[^\u00bb]*\u00bb",
    re.DOTALL,
)
# Placeholders for ``_protect_quoted_text`` / ``_restore_quoted_text``.
# The BMP Private Use Area (U+E000\u2013U+F8FF) is reserved by Unicode for
# application-specific use; legitimate source documents must not contain
# these code points. Earlier versions used U+2024 (ONE DOT LEADER),
# U+2757 (HEAVY EXCLAMATION MARK) and U+2753 (BLACK QUESTION MARK ORN.) \u2014
# real characters that do appear in user content (typography leaders,
# emoji). The restore step's blind ``.replace()`` then converted any
# original instance to plain ASCII punctuation, silently corrupting text.
# Hypothesis property test (2026-05-19) surfaced this with input ``'\u2024'``.
_PERIOD_PLACEHOLDER = "\ue000"
_EXCLAMATION_PLACEHOLDER = "\ue001"
_QUESTION_PLACEHOLDER = "\ue002"


class ChunkingService:
    """Hierarchical document chunking service.

    Creates two levels of chunks:
    - Small chunks: Optimal for RAG retrieval (~900 chars, sentence boundaries)
    - Grouped chunks: Optimal for extraction (4 small chunks combined, ~900 tokens)

    Uses fixed group_size based on GraphRAG research showing 600-900 token chunks
    are optimal for entity detection.
    """

    def __init__(
        self,
        settings: EngineSettings | None = None,
        repository: ChunkingProtocol | None = None,
    ):
        """Initialize chunking service.

        Args:
            settings: Engine settings with chunking configuration. When None,
                creates a default EngineSettings instance with sensible defaults.
            repository: Optional ChunkingProtocol implementation. Required only
                for store_chunks(), get_small_chunks(), and
                get_hierarchical_groups().

        """
        if settings is None:
            from chaoscypher_core.settings import EngineSettings

            settings = EngineSettings()

        self.repository = repository
        self.settings = settings

        # Get chunking settings from EngineSettings
        chunk_settings = settings.chunking

        # Extract all chunking parameters
        self.small_chunk_size = chunk_settings.small_chunk_size
        self.small_chunk_overlap = chunk_settings.small_chunk_overlap
        self.min_chunk_size = chunk_settings.min_chunk_size
        self.max_chunk_size = chunk_settings.max_chunk_size
        self.respect_boundaries = chunk_settings.respect_boundaries
        self.group_size = chunk_settings.group_size
        self.group_overlap = chunk_settings.group_overlap

        # Text normalization settings
        self.normalize_newlines = chunk_settings.normalize_newlines
        self.normalize_remove_structural_noise = chunk_settings.normalize_remove_structural_noise

        # Quick-mode group cap (Phase 7 audit P1 #5, 2026-05-09)
        self.quick_mode_max_groups = chunk_settings.quick_mode_max_groups

        logger.info(
            "chunking_service_initialized",
            small_chunk_size=self.small_chunk_size,
            group_size=self.group_size,
            normalize_newlines=self.normalize_newlines,
        )

    async def process(
        self,
        text: str,
        *,
        analysis_depth: str = "full",
        file_info: dict[str, Any] | None = None,
        embedding_service: Any = None,
    ) -> ExtractionResult:
        """Chunk text and extract entities in one call.

        Convenience method that combines :meth:`create_chunks` and
        entity extraction into a single call.  Useful for standalone
        extraction without a database.

        Args:
            text: Document text to process.
            analysis_depth: Extraction depth (``"full"`` or ``"quick"``).
            file_info: Optional file metadata for domain detection.
            embedding_service: Embedding provider for semantic deduplication.
                When ``None`` (default), one is lazily constructed from
                ``self.settings`` so semantic dedup runs by default. Pass an
                explicit instance to inject (e.g., for tests) or pass an
                already-constructed provider to share state across calls.

        Returns:
            ExtractionResult with ``entities``, ``relationships``, ``domain``,
            ``domain_confidence``, and other extraction results. Call
            ``model_dump_json()`` for JSON output.

        Example:
            >>> service = ChunkingService(settings=EngineSettings())
            >>> result = await service.process("Your document text here...")
            >>> print(result.model_dump_json(indent=2))

        """
        from chaoscypher_core.models import ExtractionResult
        from chaoscypher_core.services.sources.engine.extraction.extractor import (
            extract_entities_from_groups,
        )

        # Lazy-init semantic dedup provider so the standalone helper "just
        # works" without callers having to wire embeddings explicitly. The
        # underlying extract_entities_from_groups requires the kwarg; we
        # supply it here rather than forcing every caller to think about it.
        if embedding_service is None:
            from chaoscypher_core.adapters.embedding import create_embedding_provider

            embedding_service = create_embedding_provider(self.settings)

        chunks = await self.create_chunks(
            full_text=text,
            source_id="standalone",
            analysis_depth=analysis_depth,
        )
        raw = await extract_entities_from_groups(
            hierarchical_groups=chunks.hierarchical_groups,
            settings=self.settings,
            embedding_service=embedding_service,
            file_info=file_info,
        )
        return ExtractionResult(**raw)

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Apply universal text sanitization before chunking.

        Always runs regardless of normalization settings. Removes artifacts
        that waste chunk space and never constitute meaningful content:

        1. **BOM characters** (UTF-8/UTF-16 byte order marks left by editors).
        2. **CR / CRLF line endings**. Windows files and a non-trivial
           fraction of pasted-from-Word content arrives as CRLF or lone CR.
           The downstream ``_normalize_text`` regex at step 3 below only
           matches ``\\n`` patterns, so a CRLF passed through unchanged
           becomes ``\\r `` (CR + space) in the final chunk text \u2014 that
           leaks into the LLM prompt as visible garbage and inflates token
           counts. Normalize every variant to ``\\n`` here so the
           downstream regexes (and the LLM) only ever see one line-ending
           shape. This is defense-in-depth: ``ContentNormalizerService``
           does the same via ftfy when ``enable_normalization=True``, but
           sources with normalization explicitly off, or paths that bypass
           the normalizer entirely, still benefit from this scrub.
        3. **Excessive blank lines** (3+ consecutive newlines collapsed to 2).

        Args:
            text: Raw document text.

        Returns:
            Sanitized text.

        """
        # Remove BOM characters (UTF-8/UTF-16)
        text = text.lstrip("\ufeff\ufffe")

        # Normalize CRLF / lone CR to LF. Order matters: CRLF first so we
        # don't double-replace and emit blank lines.
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Collapse 3+ consecutive newlines to 2 (preserve paragraph breaks)
        return re.sub(r"\n{3,}", "\n\n", text)

    def _normalize_text(self, text: str) -> tuple[str, int]:
        """Normalize text for better chunking.

        Performs several normalizations for books/PDFs:
        1. Removes PDF page headers (e.g., "8 Introduction" as standalone paragraphs)
        2. Joins paragraph breaks that appear mid-sentence (broken sentences)
        3. Converts single newlines (line wraps) to spaces
        4. Collapses multiple spaces to a single space

        Args:
            text: Raw document text

        Returns:
            Tuple of ``(normalized_text, normalize_drops)`` where
            ``normalize_drops`` is the total number of regex substitutions
            performed across **all four passes** (page-header removals,
            broken-sentence joins, single-newline-to-space conversions, and
            multi-space collapses). Surfaced on ``ChunksResult.normalize_drops``
            so the indexing handler can increment
            ``QualityCounter.CHUNKER_NORMALIZE_DROPS``.

        """
        total_subs = 0

        # Step 1: Remove PDF page headers that appear as standalone paragraphs
        # Pattern: \n\n + digit(s) + space + capitalized word(s) + \n\n
        # Examples: "8 Introduction", "10 Chapter I", "42 The Kybalion"
        text, n = re.subn(r"\n\n\d+\s+[A-Z][A-Za-z\s]+\n\n", "\n\n", text)
        total_subs += n

        # Step 2: Join paragraph breaks that appear mid-sentence
        # If text before \n\n ends with lowercase letter AND next char is lowercase,
        # this indicates a sentence was broken across paragraphs (common in PDFs)
        # "discourage\n\nand disgust" → "discourage and disgust"
        text, n = re.subn(r"([a-z,])\n\n([a-z])", r"\1 \2", text)
        total_subs += n

        # Phase 7 audit-remediation (2026-05-09): use re.subn for steps 3-4 too
        # so CHUNKER_NORMALIZE_DROPS measures all four transformation passes.

        # Step 3: Replace single \n with space (not preceded or followed by \n)
        # This handles line wraps: "word\nword" → "word word"
        text, n = re.subn(r"(?<!\n)\n(?!\n)", " ", text)
        total_subs += n

        # Step 4: Clean up multiple spaces that may result from normalization
        text, n = re.subn(r" +", " ", text)
        total_subs += n

        return text, total_subs

    @staticmethod
    def _protect_quoted_text(text: str) -> str:
        """Replace sentence-ending punctuation inside quotes with placeholders.

        Prevents the text splitter from breaking chunks at sentence boundaries
        that fall inside quoted dialogue (e.g. ``"Hello! How are you?"``).

        Each placeholder is a single character replacing the punctuation character
        (the trailing space is preserved), keeping string length identical so
        character positions remain accurate after splitting.

        Args:
            text: Input text, possibly containing quoted passages.

        Returns:
            Text with in-quote sentence separators replaced by placeholders.

        """

        def _replace_in_quote(match: re.Match[str]) -> str:
            q = match.group(0)
            q = q.replace(". ", f"{_PERIOD_PLACEHOLDER} ")
            q = q.replace("! ", f"{_EXCLAMATION_PLACEHOLDER} ")
            return q.replace("? ", f"{_QUESTION_PLACEHOLDER} ")

        return _QUOTE_PATTERN.sub(_replace_in_quote, text)

    @staticmethod
    def _restore_quoted_text(text: str) -> str:
        """Restore placeholders back to original punctuation.

        Reverses the substitution performed by :meth:`_protect_quoted_text`.

        Args:
            text: Text that may contain placeholder characters.

        Returns:
            Text with original punctuation restored.

        """
        text = text.replace(_PERIOD_PLACEHOLDER, ".")
        text = text.replace(_EXCLAMATION_PLACEHOLDER, "!")
        return text.replace(_QUESTION_PLACEHOLDER, "?")

    async def create_chunks(
        self,
        full_text: str,
        source_id: str | None = None,
        analysis_depth: str = "full",
        *,
        store: bool | None = None,
        original_text: str | None = None,
        location_index: LocationIndex | None = None,
    ) -> ChunksResult:
        """Create hierarchical chunks from document text with filtering.

        Optionally persists chunks to storage after creation.  When *store*
        is ``None`` (default), chunks are stored automatically if a repository
        is available.  Pass ``store=False`` to inspect or transform chunks
        before writing.

        Process:
        1. Split text into ALL small chunks (~900 chars, sentence boundaries)
        2. Create ALL hierarchical groups (4 small chunks per group)
        3. Filter based on analysis_depth (quick=5 groups / full=all)
        4. (Phase 5a) Recompute char offsets against ``original_text`` when
           provided so citation anchors reference the raw upload, not the
           post-cleaner text.

        Args:
            full_text: Full document text (post-normalization).
            source_id: ID of the source. Auto-generated UUID if not provided.
            analysis_depth: 'quick' | 'full'.
            store: Persist chunks to storage after creation. Defaults to True
                when a repository is available, False otherwise. Set to False
                to inspect chunks before storing.
            original_text: Raw loader output *before* normalization.  When
                supplied, each chunk's ``char_start`` / ``char_end`` are
                recomputed against this text via substring search (method
                ``'exact'``) or rapidfuzz fuzzy match (method ``'fuzzy'``).
                Chunks that cannot be located receive NULL offsets and method
                ``'none'``.  When ``None`` the existing offset values computed
                against the cleaned text are kept and tagged ``'exact'`` (the
                pre-Phase-5a behaviour — slightly inaccurate but consistent
                with what was shipped before this phase).
            location_index: Optional per-document location index built by
                the loader. Each boundary maps a char range to a
                ``page_number`` and/or ``section``. Coordinates are in
                the loader-content coordinate system (≈ raw-upload). The
                lookup runs after Phase 5a so chunk char_start aligns
                with the index. When None, page_number and section
                stay None on every chunk.

        Returns:
            ChunksResult with small_chunks, hierarchical_groups, and counts.

        """
        from chaoscypher_core.utils import generate_id

        source_id = source_id or generate_id()

        try:
            logger.info(
                "creating_hierarchical_chunks",
                source_id=source_id,
                text_length=len(full_text),
                analysis_depth=analysis_depth,
            )

            # Step 1: Create ALL small chunks (no embeddings)
            # Workstream 5.3 (2026-05-07): the helper now returns a tuple
            # ``(chunks, chunks_coalesced)`` so the caller can record the
            # ``chunks_filtered`` field on ``ChunksResult``. The counter
            # is recorded via ``QualityCounter.CHUNKS_COALESCED`` (DB column
            # ``chunks_coalesced_count``, renamed in Alembic 0029); it counts
            # merge events, not drops (W5 follow-up, 2026-05-08).
            # P2T10 (2026-05-08): two additional counts — normalize_drops and
            # prestrip_lines_removed — are now threaded through for the
            # CHUNKER_NORMALIZE_DROPS / CHUNKER_PRESTRIP_LINES_REMOVED counters.
            (
                all_small_chunks,
                chunks_coalesced,
                normalize_drops,
                prestrip_lines_removed,
            ) = await self._create_small_chunks(source_id, full_text, original_text=original_text)

            logger.info("small_chunks_created", chunk_count=len(all_small_chunks))

            # Step 2: Create ALL hierarchical groups
            all_groups = self._create_hierarchical_groups(all_small_chunks)

            logger.info("hierarchical_groups_created", group_count=len(all_groups))

            # Step 3: Filter based on analysis_depth
            filtered_groups, filtered_chunks, chunks_skipped_by_depth = self._filter_by_depth(
                all_small_chunks, all_groups, analysis_depth
            )

            logger.info(
                "chunks_filtered_by_depth",
                filtered_chunks=len(filtered_chunks),
                filtered_groups=len(filtered_groups),
                analysis_depth=analysis_depth,
            )

            # Step 4 (Phase 5a): recompute char offsets against original text
            # when the caller supplies it.  The recompute operates on the
            # post-filter small_chunks list so only persisted chunks are
            # annotated; groups inherit the outer chunks' bounds and are not
            # individually rescanned.
            if original_text is not None:
                _recompute_chunk_offsets(filtered_chunks, original_text)

                # Phase 1 raw_content (Task 1.2, 2026-05-16): attach the
                # pre-cleanup slice from ``original_text`` using the offsets
                # we just (re)computed against it. Chunks that the recompute
                # could not locate ('none' method) keep raw_content=None so
                # the UI's "raw view unavailable" fallback fires for them.
                for chunk in filtered_chunks:
                    start = chunk.get("char_start")
                    end = chunk.get("char_end")
                    if (
                        start is not None
                        and end is not None
                        and chunk.get("citation_offset_method") in ("exact", "fuzzy")
                    ):
                        chunk["raw_content"] = original_text[start:end]

            # Per-chunk location assignment from the loader-provided index
            # (PDF page_number; EPUB/DOCX section). Runs AFTER Phase 5a so
            # char_start is in the loader-content coordinate system that
            # matches the index. Replaces the deleted _assign_page_numbers
            # post-pass in indexing_handler / CLI service.
            if location_index is not None:
                for chunk in filtered_chunks:
                    char_start = chunk.get("char_start") or 0
                    page, section = _lookup_location(location_index, char_start)
                    chunk["page_number"] = page
                    chunk["section"] = section

            from chaoscypher_core.models import ChunksResult

            result = ChunksResult(
                small_chunks=filtered_chunks,
                hierarchical_groups=filtered_groups,
                total_small_chunks=len(filtered_chunks),
                total_groups=len(filtered_groups),
                total_original_chunks=len(all_small_chunks),
                total_original_groups=len(all_groups),
                chunks_filtered=chunks_coalesced,
                normalize_drops=normalize_drops,
                prestrip_lines_removed=prestrip_lines_removed,
                chunks_skipped_by_depth=chunks_skipped_by_depth,
            )

            # Auto-store if repository available and store not explicitly False
            if store is None:
                store = self.repository is not None
            if store and self.repository is not None:
                self.store_chunks(result)

            return result

        except Exception as e:
            logger.exception(
                "chunk_creation_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    def store_chunks(
        self,
        chunks_result: Any,
        database_name: str | None = None,
    ) -> None:
        """Persist chunks to storage with database metadata.

        Accepts the ChunksResult from :meth:`create_chunks` directly.
        Stamps storage fields (database_name, embedding placeholders, status,
        created_at) onto chunks, then calls repository.store_chunks_and_groups().

        Args:
            chunks_result: ChunksResult from create_chunks().
            database_name: Database name for storage metadata. If None,
                uses ``settings.current_database``.

        Raises:
            RuntimeError: If no repository was provided to the constructor.

        """
        repo = self._require_repository()

        small_chunks = chunks_result.small_chunks
        hierarchical_groups = chunks_result.hierarchical_groups
        db_name = database_name or self.settings.current_database

        for chunk in small_chunks:
            chunk["database_name"] = db_name
            chunk.setdefault("embedding", None)
            chunk.setdefault("embedding_model", None)
            chunk.setdefault("embedding_dimensions", None)
            chunk.setdefault("status", "staged")
            chunk.setdefault("created_at", datetime.now(UTC))

        repo.store_chunks_and_groups(
            small_chunks,
            hierarchical_groups,
            batch_size=self.settings.batching.export_page_size,
        )

    @staticmethod
    def _prestrip_structural_noise(text: str) -> tuple[str, int]:
        """Remove structural artifacts before extraction.

        Deterministic regex heuristics to strip:
        1. Table-of-contents blocks (dotted leaders, Roman numeral lists)
        2. Repeated headers/footers across page-like segments
        3. Standalone structural markers (CHAPTER, BOOK, PART labels)
        4. Standalone page number lines

        Args:
            text: Raw document text.

        Returns:
            Tuple of ``(cleaned_text, lines_removed)`` where ``lines_removed``
            is the total count of lines stripped across all passes (page
            numbers, structural markers, TOC blocks, repeated headers/footers).
            Surfaced on ``ChunksResult.prestrip_lines_removed`` so the
            indexing handler can increment
            ``QualityCounter.CHUNKER_PRESTRIP_LINES_REMOVED``.

        """
        lines = text.split("\n")
        total_lines_before = len(lines)

        # --- Pass 1: Remove standalone page number lines ---
        page_num_re = re.compile(r"^\s*-?\s*\d{1,5}\s*-?\s*$")
        lines = [ln for ln in lines if not page_num_re.match(ln)]

        # --- Pass 2: Remove standalone structural markers ---
        # Lines like "CHAPTER IV", "BOOK 2", "PART III:" with no narrative
        structural_marker_re = re.compile(
            r"^\s*(CHAPTER|BOOK|PART)\s+[IVXLCDM\d]+\s*[:.]?\s*$",
            re.IGNORECASE,
        )
        lines = [ln for ln in lines if not structural_marker_re.match(ln)]

        # --- Pass 3: Detect and remove TOC-like blocks ---
        toc_header_re = re.compile(
            r"^\s*(CHAPTER|BOOK|PART|SECTION|APPENDIX|INDEX|CONTENTS|TABLE\s+OF\s+CONTENTS)\b",
            re.IGNORECASE,
        )
        dotted_leader_re = re.compile(r"\.{3,}\s*\d+\s*$")
        roman_list_re = re.compile(r"^\s*[IVXLCDM]+[\.\s]", re.IGNORECASE)

        # Scan for contiguous TOC-like runs (3+ lines)
        toc_ranges: list[tuple[int, int]] = []
        run_start: int | None = None
        run_count = 0
        for i, ln in enumerate(lines):
            stripped = ln.strip()
            is_toc_line = bool(
                toc_header_re.match(stripped)
                or dotted_leader_re.search(stripped)
                or roman_list_re.match(stripped)
            )
            if is_toc_line:
                if run_start is None:
                    run_start = i
                run_count += 1
            else:
                if run_start is not None and run_count >= 3:
                    toc_ranges.append((run_start, run_start + run_count))
                run_start = None
                run_count = 0
        # Handle trailing run
        if run_start is not None and run_count >= 3:
            toc_ranges.append((run_start, run_start + run_count))

        if toc_ranges:
            toc_indices: set[int] = set()
            for start, end in toc_ranges:
                toc_indices.update(range(start, end))
            lines = [ln for i, ln in enumerate(lines) if i not in toc_indices]

        # --- Pass 4: Detect repeated headers/footers ---
        # Split into page-like segments by form-feed or double-newline blocks.
        # Find lines that repeat identically in 3+ segments.
        text_rebuilt = "\n".join(lines)
        if "\f" in text_rebuilt:
            segments = text_rebuilt.split("\f")
        else:
            segments = re.split(r"\n{3,}", text_rebuilt)

        if len(segments) >= 3:
            # Count occurrences of each non-empty stripped line across segments
            line_segment_counts: Counter[str] = Counter()
            for segment in segments:
                # Unique lines per segment (avoid counting duplicates within same segment)
                unique_lines = {ln.strip() for ln in segment.split("\n") if ln.strip()}
                for uln in unique_lines:
                    line_segment_counts[uln] += 1

            # Lines appearing in 3+ segments are likely headers/footers
            repeated = {
                ln for ln, count in line_segment_counts.items() if count >= 3 and len(ln) < 200
            }
            if repeated:
                lines = text_rebuilt.split("\n")
                lines = [ln for ln in lines if ln.strip() not in repeated]
                text_rebuilt = "\n".join(lines)
        else:
            lines = text_rebuilt.split("\n")

        lines_removed = total_lines_before - len(lines)
        # Collapse gaps left by removed lines (3+ newlines → 2)
        return re.sub(r"\n{3,}", "\n\n", text_rebuilt), max(0, lines_removed)

    async def _create_small_chunks(
        self,
        source_id: str,
        full_text: str,
        *,
        original_text: str | None = None,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        """Create small chunks with sentence/paragraph boundaries.

        Uses RecursiveCharacterTextSplitter with configurable size (~900 chars default)
        for optimal RAG retrieval.

        NO EMBEDDINGS - those are generated at index time!

        Workstream 5.3 (2026-05-07): three previously-inert chunking settings
        are now honored:

        - ``min_chunk_size`` — sub-threshold chunks are **coalesced** into a
          neighbor (merged with the next chunk that brings the combination
          over the threshold) rather than dropped. A trailing sub-threshold
          fragment is emitted as its own row so we never lose content. Set
          to 0 to disable coalescing entirely. (Pre-2026-05-08 behaviour
          was to drop, which silently lost real prose — dialogue,
          transitions, short paragraphs — on natural-language imports.)
        - ``max_chunk_size`` — chunks longer than this are hard-truncated at
          the last whitespace before the cap (or at the cap when no
          whitespace is in the back half). Coalesce never produces a chunk
          larger than this cap; if merging would exceed it, the pending
          fragment is flushed as its own row instead.
        - ``respect_boundaries`` — when ``False``, the splitter falls back to
          whitespace-only separators (no sentence-aware ``". "`` / ``"! "`` /
          ``"? "`` priority).

        Returns:
            Tuple of ``(small_chunks, chunks_coalesced, normalize_drops,
            prestrip_lines_removed)``.  ``chunks_coalesced`` counts merge
            events.  ``normalize_drops`` is the total regex-substitution count
            from :meth:`_normalize_text` (0 when normalization is disabled).
            ``prestrip_lines_removed`` is the line-removal count from
            :meth:`_prestrip_structural_noise` (0 when prestrip is disabled).

        """
        # Always sanitize: strip BOMs and collapse excessive blank lines
        full_text = self._sanitize_text(full_text)

        normalize_drops = 0
        prestrip_lines_removed = 0

        # Pre-strip structural noise if enabled
        if self.normalize_remove_structural_noise:
            full_text, prestrip_lines_removed = self._prestrip_structural_noise(full_text)

        # Normalize text if enabled (converts single newlines to spaces)
        if self.normalize_newlines:
            full_text, normalize_drops = self._normalize_text(full_text)
            logger.debug(
                "text_normalized",
                original_length=len(full_text),
                normalize_newlines=self.normalize_newlines,
            )

        # Protect quoted text so the splitter won't break mid-dialogue
        full_text = self._protect_quoted_text(full_text)

        # Create text splitter with sentence-aware boundaries
        # keep_separator="end" ensures periods stay at END of chunks, not start of next
        # Separator priority: sentences FIRST, then paragraphs - this ensures chunks
        # end at complete sentences rather than mid-sentence at paragraph breaks
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        if self.respect_boundaries:
            # Sentence-aware separators (default): break at "." / "!" / "?"
            # before falling back to paragraphs / lines / whitespace.
            separators = [
                ". ",  # Sentence ends (highest priority)
                "! ",
                "? ",
                "\n\n",  # Paragraphs (secondary)
                "\n",  # Lines
                "; ",
                ", ",
                " ",  # Words
                "",  # Characters (fallback)
            ]
        else:
            # Whitespace-only fallback: callers who want token-style splits
            # (e.g. log files, code) skip sentence-aware boundary detection.
            separators = ["\n\n", "\n", " ", ""]

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.small_chunk_size,
            chunk_overlap=self.small_chunk_overlap,
            length_function=len,
            is_separator_regex=False,
            keep_separator="end",
            separators=separators,
        )

        # Split text
        chunk_texts = text_splitter.split_text(full_text)

        logger.info(
            "text_split_into_chunks",
            chunk_count=len(chunk_texts),
            chunk_size=self.small_chunk_size,
            chunk_overlap=self.small_chunk_overlap,
            respect_boundaries=self.respect_boundaries,
        )

        # 2026-05-08 (W5 follow-up): coalesce — not drop — short chunks.
        #
        # First pass: walk the splitter output, restore quoted text, cap
        # oversize chunks, and merge sub-threshold fragments into the next
        # chunk that lifts the combination over ``min_chunk_size``. The
        # operator's mental model is "the chunker re-organizes content to
        # fill chunks near the target size", not "the chunker silently
        # discards small chunks" — pre-2026-05-08 the latter behaviour was
        # losing real prose (dialogue, transitions, short paragraphs) on
        # natural-language imports.
        coalesced_texts: list[str] = []
        chunks_coalesced = 0
        pending = ""

        for raw_text in chunk_texts:
            # Restore original punctuation inside quoted text
            text = self._restore_quoted_text(raw_text)

            # Workstream 5.3: hard-cap at max_chunk_size. Prefer to break
            # at the last whitespace inside the back half of the chunk so
            # words aren't sliced; fall back to a flat truncation when the
            # chunk has no whitespace in the back half (rare — e.g. URL
            # lists).
            if len(text) > self.max_chunk_size:
                cap = self.max_chunk_size
                last_space = text.rfind(" ", 0, cap)
                text = text[:last_space] if last_space > cap // 2 else text[:cap]

            if pending:
                # Try to fold the pending fragment into this chunk. The
                # ``\n\n`` separator preserves the paragraph break the
                # splitter saw between them so sentence-aware downstream
                # processing (sentence offsets, citation highlighting)
                # still works.
                combined = pending + "\n\n" + text
                if len(combined) <= self.max_chunk_size:
                    text = combined
                    chunks_coalesced += 1
                    pending = ""
                else:
                    # Merging would exceed the hard cap. Flush the pending
                    # fragment as its own row — better than losing it. This
                    # is rare because ``pending`` is by definition shorter
                    # than ``min_chunk_size``.
                    coalesced_texts.append(pending)
                    pending = ""

            if len(text) < self.min_chunk_size:
                # Hold this chunk to merge with the next one.
                pending = text
                continue

            coalesced_texts.append(text)

        # Final tail: emit pending content even if under min so we never
        # lose the trailing fragment. We count this as a coalesce event
        # too — it was held with intent to merge but ran out of neighbors.
        if pending:
            coalesced_texts.append(pending)
            chunks_coalesced += 1

        # Build chunk objects WITHOUT embeddings (generated at index time)
        small_chunks: list[dict[str, Any]] = []
        char_position = 0

        for kept_idx, text in enumerate(coalesced_texts):
            chunk_id = generate_id()

            # Track character positions in original document
            char_start = char_position
            char_end = char_start + len(text)
            char_position = char_end - self.small_chunk_overlap  # Account for overlap

            # Approximate token count (4 chars per token)
            token_count = len(text) // 4

            # Compute sentence character offsets for citation highlighting
            from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
                split_into_sentences_with_offsets,
            )

            sentence_offsets = split_into_sentences_with_offsets(text)

            small_chunks.append(
                {
                    "id": chunk_id,
                    "source_id": source_id,
                    "chunk_index": kept_idx,
                    "content": text,
                    # Phase 1 raw_content (Task 1.2, 2026-05-16): default to
                    # None here. When ``original_text`` is supplied to
                    # ``create_chunks``, the slice is attached after
                    # ``_recompute_chunk_offsets`` runs so we can use the
                    # Phase-5a-corrected ``char_start`` / ``char_end`` which
                    # are guaranteed to index into ``original_text`` rather
                    # than the cleaned text. ``original_text`` is accepted
                    # here on the signature for forward-compatibility, but
                    # the slice itself lives in ``create_chunks`` so the
                    # exact/fuzzy/none cascade decides which chunks get a
                    # raw view and which fall back to NULL (UI: "raw view
                    # unavailable").
                    "raw_content": None,
                    "char_start": char_start,
                    "char_end": char_end,
                    # Phase 5a: default 'exact' until _recompute_chunk_offsets
                    # runs. Callers that don't supply original_text keep this
                    # value — offsets are relative to cleaned text (pre-5a
                    # behaviour, slightly inaccurate but consistent).
                    "citation_offset_method": "exact",
                    "token_count": token_count,
                    "page_number": None,
                    "section": None,
                    "chunk_metadata": {
                        "chunk_type": "small",  # Mark as small chunk
                        "group_ids": [],  # Will be populated when creating groups
                        "sentence_offsets": sentence_offsets,
                    },
                }
            )

        if chunks_coalesced:
            logger.info(
                "chunks_coalesced_below_min_size",
                source_id=source_id,
                chunks_coalesced=chunks_coalesced,
                min_chunk_size=self.min_chunk_size,
            )

        return small_chunks, chunks_coalesced, normalize_drops, prestrip_lines_removed

    def _create_hierarchical_groups(
        self, small_chunks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Create hierarchical groups from small chunks.

        Groups multiple small chunks together using sliding window:
        - Group 0: chunks [0, 1, 2, 3]
        - Group 1: chunks [3, 4, 5, 6]  (overlap=1)
        - Group 2: chunks [6, 7, 8, 9]

        Args:
            small_chunks: List of small chunk dictionaries

        Returns:
            List of hierarchical group dictionaries

        """
        hierarchical_groups: list[dict[str, Any]] = []
        step = self.group_size - self.group_overlap

        for group_idx in range(0, len(small_chunks), step):
            # Get chunks for this group
            group_chunks = small_chunks[group_idx : group_idx + self.group_size]

            if not group_chunks:
                break

            # Create group
            group_id = generate_id()
            small_chunk_ids = [chunk["id"] for chunk in group_chunks]
            combined_content = "\n\n".join([chunk["content"] for chunk in group_chunks])

            # Track positions
            char_start = group_chunks[0]["char_start"]
            char_end = group_chunks[-1]["char_end"]
            token_count = sum(chunk["token_count"] for chunk in group_chunks)

            hierarchical_groups.append(
                {
                    "id": group_id,
                    "group_index": len(hierarchical_groups),
                    "small_chunk_ids": small_chunk_ids,
                    "combined_content": combined_content,
                    "char_start": char_start,
                    "char_end": char_end,
                    "token_count": token_count,
                }
            )

            # Update small chunks to reference this group
            for chunk in group_chunks:
                chunk["chunk_metadata"]["group_ids"].append(group_id)

        logger.info(
            "hierarchical_groups_formed",
            group_count=len(hierarchical_groups),
            group_size=self.group_size,
            group_overlap=self.group_overlap,
        )

        return hierarchical_groups

    def _filter_by_depth(
        self,
        all_chunks: list[dict[str, Any]],
        all_groups: list[dict[str, Any]],
        analysis_depth: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        """Filter chunks and groups based on analysis depth.

        Args:
            all_chunks: All small chunks
            all_groups: All hierarchical groups
            analysis_depth: 'quick' | 'full'

        Returns:
            Tuple of ``(filtered_groups, filtered_chunks, groups_skipped)``
            where ``groups_skipped`` is the number of groups silently dropped
            by the quick-mode cap (``max(0, len(all_groups) - 5)``). Surfaced
            on ``ChunksResult.chunks_skipped_by_depth`` so the indexing
            handler can increment
            ``QualityCounter.CHUNKS_SKIPPED_BY_DEPTH``.

        """
        groups_skipped = 0
        if analysis_depth == "quick":
            # Quick: evenly-distributed sample (cap from settings, default 5)
            sample_size = min(self.quick_mode_max_groups, len(all_groups))
            groups_skipped = max(0, len(all_groups) - sample_size)
            if sample_size >= len(all_groups):
                selected_groups = all_groups
            else:
                step = max(1, len(all_groups) // sample_size)
                indices = list(range(0, len(all_groups), step))[:sample_size]
                selected_groups = [all_groups[i] for i in indices]

        else:  # 'full'
            # Full: all groups
            selected_groups = all_groups

        # Get all chunk IDs from selected groups
        selected_chunk_ids = set()
        for group in selected_groups:
            selected_chunk_ids.update(group["small_chunk_ids"])

        # Filter chunks to only those in selected groups
        selected_chunks = [chunk for chunk in all_chunks if chunk["id"] in selected_chunk_ids]

        # Re-index chunks to be sequential
        for idx, chunk in enumerate(selected_chunks):
            chunk["chunk_index"] = idx

        # Re-index groups
        for idx, group in enumerate(selected_groups):
            group["group_index"] = idx

        logger.info(
            "chunks_filtered",
            analysis_depth=analysis_depth,
            selected_groups=len(selected_groups),
            total_groups=len(all_groups),
            selected_chunks=len(selected_chunks),
            total_chunks=len(all_chunks),
            groups_skipped_by_depth=groups_skipped,
        )

        return selected_groups, selected_chunks, groups_skipped

    def _require_repository(self) -> ChunkingProtocol:
        """Return the repository or raise if none was provided.

        Raises:
            RuntimeError: If no repository was provided to the constructor.

        """
        if self.repository is None:
            msg = "Cannot access storage: no repository provided to ChunkingService"
            raise RuntimeError(msg)
        return self.repository

    def get_small_chunks(self, source_id: str) -> list[dict[str, Any]]:
        """Get all small chunks for a source (for RAG indexing)."""
        return self._require_repository().get_small_chunks(source_id)

    def get_hierarchical_groups(self, source_id: str, analysis_depth: str) -> list[dict[str, Any]]:
        """Get hierarchical groups for entity extraction.

        Args:
            source_id: Source ID
            analysis_depth: 'quick' | 'full'

        Returns:
            Subset of groups based on analysis depth

        """
        all_groups = self._require_repository().get_hierarchical_groups(source_id)

        if analysis_depth == "quick":
            # Quick: evenly-distributed sample (cap from settings, default 5)
            sample_size = min(self.quick_mode_max_groups, len(all_groups))
            if sample_size >= len(all_groups):
                return all_groups
            step = max(1, len(all_groups) // sample_size)
            indices = list(range(0, len(all_groups), step))[:sample_size]
            return [all_groups[i] for i in indices]

        # 'full' — all groups
        return all_groups


# ---------------------------------------------------------------------------
# Phase 5a: citation offset recompute
# ---------------------------------------------------------------------------


# Collapsed-space anchor width for the prefix/suffix span recovery level.
# 32 visible-ish chars is distinctive enough to avoid false anchors while
# staying well inside the unmodified head/tail of a ~900-char chunk.
_ANCHOR_LEN = 32
# Reject anchored spans wildly larger than the cleaned content — a runaway
# suffix match on repeated boilerplate would otherwise swallow pages.
_MAX_SPAN_FACTOR = 4
_MAX_SPAN_SLACK = 8192


def _collapse_whitespace_with_map(text: str) -> tuple[str, list[int], list[int]]:
    """Collapse every whitespace run in *text* to a single space, with maps.

    Returns ``(collapsed, starts, ends)`` where ``starts[i]`` / ``ends[i]``
    are the original-text indices of the first / last character that
    produced ``collapsed[i]``. A collapsed ``" "`` standing in for a run of
    ``\\r\\n``s, indentation, or blank lines maps to the run's full extent,
    so spans recovered in collapsed space translate back to original-text
    offsets that include the whitespace the cleaner rewrote.
    """
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i].isspace():
            j = i
            while j + 1 < n and text[j + 1].isspace():
                j += 1
            out.append(" ")
            starts.append(i)
            ends.append(j)
            i = j + 1
        else:
            out.append(text[i])
            starts.append(i)
            ends.append(i)
            i += 1
    return "".join(out), starts, ends


def _find_anchored_span(
    collapsed_text: str,
    needle: str,
    search_from: int,
) -> tuple[int, int] | None:
    """Locate *needle*'s span in *collapsed_text* via prefix/suffix anchors.

    Used when the cleaner removed lines from *inside* the chunk: the full
    needle no longer matches anywhere, but its first and last
    :data:`_ANCHOR_LEN` characters usually survive verbatim. The recovered
    span — first char of the prefix anchor through last char of the suffix
    anchor — is allowed to be LONGER than the needle, which is exactly what
    a window-style fuzzy match cannot express.

    Returns ``(first, last)`` collapsed-space indices (inclusive), or
    ``None`` when either anchor is missing or the span size is implausible
    (premature suffix repeat, runaway match).
    """
    prefix = needle[:_ANCHOR_LEN]
    suffix = needle[-_ANCHOR_LEN:]

    start = collapsed_text.find(prefix, search_from)
    if start == -1:
        start = collapsed_text.find(prefix)
    if start == -1:
        return None

    suffix_pos = collapsed_text.find(suffix, start + _ANCHOR_LEN)
    if suffix_pos == -1:
        return None

    last = suffix_pos + len(suffix) - 1
    span_len = last - start + 1
    # The raw span can only grow relative to the cleaned content (the
    # cleaner removes text); allow a little shrink for character-level
    # transforms, and cap growth to stop boilerplate-repeat runaways.
    if span_len < int(len(needle) * 0.9):
        return None
    if span_len > len(needle) * _MAX_SPAN_FACTOR + _MAX_SPAN_SLACK:
        return None
    return start, last


def _recompute_chunk_offsets(
    chunks: list[dict[str, Any]],
    original_text: str,
) -> None:
    """Recompute ``char_start`` / ``char_end`` on *chunks* against *original_text*.

    Mutates each chunk dict in-place to set:
    - ``char_start`` / ``char_end``: character offsets into *original_text*.
    - ``citation_offset_method``: one of ``'exact'``, ``'fuzzy'``, ``'none'``.
    - ``chunk_metadata["sentence_offsets"]``: if present, each sentence's
      ``start`` / ``end`` are also shifted to match the original-text anchor.

    Four-level cascade per chunk:

    1. **Exact** — ``chunk.content`` is a verbatim substring of
       *original_text*.  ``str.find`` is O(n·m) but fast in practice because
       chunks are short (~900 chars) and the search string is unique enough to
       resolve quickly.
    2. **Whitespace-collapsed exact** (method ``'fuzzy'``) — both sides are
       collapsed to single-space whitespace runs and searched again. This
       resolves every chunk whose only divergence from the raw upload is
       line endings, indentation, or blank-line collapse (e.g. ALL chunks
       of a CRLF file), and — unlike a fuzzy window — maps back to the FULL
       raw span including the rewritten whitespace.
    3. **Anchored span** (method ``'fuzzy'``) — when the cleaner removed
       lines from inside the chunk, the prefix and suffix anchors are
       located independently (:func:`_find_anchored_span`) so the span can
       exceed the cleaned length and keep the removed text inside it.
    4. **Window fuzzy** — ``rapidfuzz.fuzz.partial_ratio`` pre-filter at the
       :data:`_FUZZY_SCORE_THRESHOLD` boundary, then
       ``rapidfuzz.fuzz.partial_ratio_alignment`` for the span. Last resort
       for heavily transformed chunks; the matched window never exceeds
       ``len(content)``, so it can clip text that level 2/3 would keep.
    5. **None** — everything failed; ``char_start`` / ``char_end`` set to
       ``None``, method ``'none'``.

    Levels 2 and 3 search forward from the previous chunk's match first
    (chunks are sequential over the document) and fall back to a
    whole-document search, which keeps repeated boilerplate from re-anchoring
    a later chunk to an earlier occurrence.

    Sentence offsets are adjusted by the delta between the old and new
    ``char_start`` when a match is found, so they remain relative to
    *original_text* rather than the cleaned text.

    Args:
        chunks: Small-chunk dicts from :meth:`ChunkingService._create_small_chunks`.
        original_text: Raw loader output before normalization.

    """
    from rapidfuzz import fuzz as _fuzz

    exact_count = 0
    fuzzy_count = 0
    none_count = 0

    collapsed_text, collapsed_starts, collapsed_ends = _collapse_whitespace_with_map(original_text)
    # Collapsed-space floor for the sequential search. Overlapping chunks
    # share their head with the previous chunk's tail, so the floor is the
    # previous chunk's START, not its end.
    cursor = 0

    def _apply_collapsed_match(
        chunk: dict[str, Any], old_start: int | None, first: int, last: int
    ) -> None:
        new_start = collapsed_starts[first]
        chunk["char_start"] = new_start
        chunk["char_end"] = collapsed_ends[last] + 1
        chunk["citation_offset_method"] = "fuzzy"
        _shift_sentence_offsets(chunk, old_start, new_start)

    for chunk in chunks:
        content: str = chunk.get("content", "")
        old_start: int | None = chunk.get("char_start")

        # --- Level 1: exact substring match ---
        idx = original_text.find(content)
        if idx != -1:
            chunk["char_start"] = idx
            chunk["char_end"] = idx + len(content)
            chunk["citation_offset_method"] = "exact"
            _shift_sentence_offsets(chunk, old_start, idx)
            exact_count += 1
            continue

        # Whitespace-collapsed needle shared by levels 2 and 3.
        needle = " ".join(content.split())
        if needle:
            # --- Level 2: whitespace-collapsed exact match ---
            pos = collapsed_text.find(needle, cursor)
            if pos == -1:
                pos = collapsed_text.find(needle)
            if pos != -1:
                _apply_collapsed_match(chunk, old_start, pos, pos + len(needle) - 1)
                fuzzy_count += 1
                cursor = max(cursor, pos)
                continue

            # --- Level 3: prefix/suffix anchored span ---
            if len(needle) >= 2 * _ANCHOR_LEN:
                span = _find_anchored_span(collapsed_text, needle, cursor)
                if span is not None:
                    first, last = span
                    _apply_collapsed_match(chunk, old_start, first, last)
                    fuzzy_count += 1
                    cursor = max(cursor, first)
                    continue

        # --- Level 4: rapidfuzz window fuzzy match ---
        # partial_ratio gives a cheap score on the whole content vs. original;
        # we only pay for alignment when it crosses the threshold.
        if len(content) > 0:
            score = _fuzz.partial_ratio(content, original_text)
            if score >= _FUZZY_SCORE_THRESHOLD:
                alignment = _fuzz.partial_ratio_alignment(content, original_text)
                if alignment is not None and alignment.score >= _FUZZY_SCORE_THRESHOLD:
                    # alignment.dest_start / dest_end are indices into the
                    # *longer* string (original_text when content is shorter).
                    chunk["char_start"] = alignment.dest_start
                    chunk["char_end"] = alignment.dest_end
                    chunk["citation_offset_method"] = "fuzzy"
                    _shift_sentence_offsets(chunk, old_start, alignment.dest_start)
                    fuzzy_count += 1
                    continue

        # --- Level 5: no match ---
        chunk["char_start"] = None
        chunk["char_end"] = None
        chunk["citation_offset_method"] = "none"
        none_count += 1

    logger.info(
        "citation_offsets_recomputed",
        total=len(chunks),
        exact=exact_count,
        fuzzy=fuzzy_count,
        none=none_count,
    )


def _shift_sentence_offsets(
    chunk: dict[str, Any],
    old_start: int | None,
    new_start: int,
) -> None:
    """Shift sentence offsets by (new_start - old_start).

    No-op when:
    - ``chunk_metadata`` is absent.
    - ``sentence_offsets`` is absent or empty.
    - ``old_start`` is ``None`` (no previous anchor to delta from).

    Args:
        chunk: Chunk dict with optional ``chunk_metadata.sentence_offsets``.
        old_start: Previous ``char_start`` value (pre-recompute).
        new_start: New ``char_start`` anchored into original_text.

    """
    if old_start is None:
        return
    meta = chunk.get("chunk_metadata")
    if not isinstance(meta, dict):
        return
    sentence_offsets = meta.get("sentence_offsets")
    if not isinstance(sentence_offsets, list):
        return

    delta = new_start - old_start
    if delta == 0:
        return

    shifted: list[dict[str, Any]] = []
    for so in sentence_offsets:
        if isinstance(so, dict):
            shifted.append(
                {
                    **so,
                    "start": so.get("start", 0) + delta,
                    "end": so.get("end", 0) + delta,
                }
            )
        else:
            shifted.append(so)
    meta["sentence_offsets"] = shifted
