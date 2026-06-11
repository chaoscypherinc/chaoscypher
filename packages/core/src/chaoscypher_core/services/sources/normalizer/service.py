# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Content normalizer service - the main orchestrator.

Provides a unified interface for normalizing content from any source into
clean, consistent output. Orchestrates cleaners and transformers in a
configurable pipeline.

The service is the primary entry point for content normalization:
1. Detects content type (if not provided)
2. Applies appropriate cleaners in sequence
3. Transforms to uniform output format
4. Calculates quality metrics

Cleaners are discovered via :class:`CleanerRegistry` at init time, which
scans both the built-in cleaners directory and the user plugin directory
at ``{engine_settings.paths.data_dir}/plugins/cleaners/``.

Example:
    from chaoscypher_core.services.sources.normalizer import (
        ContentNormalizerService,
        ContentType,
        NormalizerSettings,
    )

    # Create service with settings
    settings = NormalizerSettings(enable_ocr_cleaning=True)
    normalizer = ContentNormalizerService(settings)

    # Normalize content
    result = normalizer.normalize(
        content="  Dirty content with OCR artifacts  ",
        content_type=ContentType.PDF,
        metadata={"source": "document.pdf"},
    )

    print(f"Quality score: {result.quality_metrics.overall_score():.2f}")
    print(f"Cleaned content: {result.content}")

"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, ClassVar

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.services.sources.normalizer.cleaners.registry import (
    CleanerRegistry,
)
from chaoscypher_core.services.sources.normalizer.models import (
    ContentType,
    NormalizedContent,
    NormalizerSettings,
    QualityMetrics,
)
from chaoscypher_core.services.sources.normalizer.transformers import (
    MarkdownNormalizer,
    TransformerProtocol,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        DomainNormalizerOverrides,
    )
    from chaoscypher_core.services.sources.normalizer.cleaners import (
        CleanerProtocol,
    )
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


def _resolved_settings(
    global_settings: NormalizerSettings,
    domain_overrides: DomainNormalizerOverrides | None,
) -> NormalizerSettings:
    """Merge domain-level cleaner overrides onto the global ``NormalizerSettings``.

    Each field on ``DomainNormalizerOverrides`` is a nullable boolean.  A
    ``None`` value means "no domain-level preference — use the global
    default".  Non-``None`` values replace the corresponding flag on a copy
    of ``global_settings`` so the caller always receives a standalone
    ``NormalizerSettings`` that the normalizer can use directly.

    Args:
        global_settings: Operator's global ``NormalizerSettings`` (from
            ``EngineSettings.normalizer``).
        domain_overrides: Optional per-domain flag overrides from
            ``DomainConfigModel.normalizer_overrides``.  When ``None``,
            ``global_settings`` is returned unchanged (no copy needed).

    Returns:
        A ``NormalizerSettings`` instance with domain flags applied.  When
        ``domain_overrides`` is ``None`` or every field is ``None``, the
        original ``global_settings`` object is returned as-is — no
        unnecessary copies.

    Example::

        global_s = NormalizerSettings(enable_ocr_cleaning=True)
        overrides = DomainNormalizerOverrides(enable_ocr_cleaning=False)
        resolved = _resolved_settings(global_s, overrides)
        assert resolved.enable_ocr_cleaning is False
        assert resolved.enable_encoding_fix is True   # fell through to global

    """
    if domain_overrides is None:
        return global_settings

    # model_dump(exclude_none=True) yields only the fields the domain
    # explicitly set — unset (None) fields are absent and therefore fall
    # through to the global default.
    overrides_dict = domain_overrides.model_dump(exclude_none=True)
    if not overrides_dict:
        return global_settings

    return global_settings.model_copy(update=overrides_dict)


class ContentNormalizerService:
    """Service for normalizing content from any source.

    Orchestrates a pipeline of cleaners and transformers to produce
    clean, uniform content ready for chunking and embedding.

    Pipeline Order:
    1. Content type detection (if not specified)
    2. Web cleaner (for HTML/web content)
    3. Text cleaner (encoding, unicode, whitespace)
    4. OCR cleaner (artifact removal, deduplication)
    5. Markdown transformer (uniform output format)
    6. Quality metrics calculation

    Attributes:
        settings: Configuration for the normalization pipeline.

    Example:
        service = ContentNormalizerService(NormalizerSettings())

        # Normalize PDF content
        result = service.normalize(
            "PDF extracted text...",
            ContentType.PDF
        )

    """

    # Pre-compiled patterns for content type detection and quality metrics
    _HTML_RE: ClassVar[re.Pattern[str]] = re.compile(r"<!doctype\s+html|<html|<head|<body")
    _MARKDOWN_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(r"^#{1,6}\s+\S", re.MULTILINE),
        re.compile(r"^```", re.MULTILINE),
        re.compile(r"^\|.*\|.*\|", re.MULTILINE),
        re.compile(r"^[-*+]\s+\S", re.MULTILINE),
    ]
    _CODE_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(r"^(def|class|function|const|let|var|import|from)\s+", re.MULTILINE),
        re.compile(r"^\s*(if|for|while|return)\s*[\(:]", re.MULTILINE),
        re.compile(r"[{};]\s*$", re.MULTILINE),
    ]
    _HEADER_RE: ClassVar[re.Pattern[str]] = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
    _LIST_RE: ClassVar[re.Pattern[str]] = re.compile(r"^[-*+]\s+\S|^\d+[.)]\s+\S", re.MULTILINE)
    _TABLE_RE: ClassVar[re.Pattern[str]] = re.compile(r"^\|.*\|", re.MULTILINE)
    _WORD_RE: ClassVar[re.Pattern[str]] = re.compile(r"\b[a-zA-Z]+\b")

    def __init__(
        self,
        settings: NormalizerSettings | EngineSettings | None = None,
        cleaners: list[CleanerProtocol] | None = None,
        transformer: TransformerProtocol | None = None,
        engine_settings: EngineSettings | None = None,
    ) -> None:
        """Initialize the normalizer service.

        Args:
            settings: Either a ``NormalizerSettings`` (legacy) or an
                ``EngineSettings``. When an ``EngineSettings`` is supplied
                here, it is treated as if it had been passed via
                ``engine_settings``: registry discovery uses its
                ``paths.data_dir`` and built-in cleaners receive its
                ``.normalizer``. When a ``NormalizerSettings`` is
                supplied (or ``None``), behaviour matches the historical
                contract.
            cleaners: Custom cleaner list. When provided, overrides registry
                discovery entirely — used for dependency injection in tests
                and specialized pipelines.
            transformer: Custom transformer. Uses MarkdownNormalizer if not provided.
            engine_settings: Full engine settings for registry-driven cleaner
                discovery. When provided, user plugins in
                ``{engine_settings.paths.data_dir}/plugins/cleaners/`` are
                discovered alongside built-ins. When omitted, a default
                ``EngineSettings`` is synthesized with ``settings`` as its
                ``.normalizer`` slot so that user plugins are still discovered
                from the default data directory.

        """
        # Workstream 5 (2026-05-07): callers (indexing handler / CLI / MCP)
        # now pass the full EngineSettings via the first positional arg so
        # operator-tuned normalizer flags actually take effect. Detect that
        # shape and route it through the existing engine_settings slot,
        # keeping the legacy NormalizerSettings path working untouched.
        from chaoscypher_core.settings import EngineSettings as _EngineSettings

        if isinstance(settings, _EngineSettings):
            if engine_settings is None:
                engine_settings = settings
            self.settings = settings.normalizer
        else:
            self.settings = settings or NormalizerSettings()
        self._cleaners_override = cleaners
        self._cleaners: list[CleanerProtocol] | None = None
        self._transformer = transformer
        self._engine_settings = engine_settings
        self._cleaner_registry: CleanerRegistry | None = None

    def _build_engine_settings(self) -> EngineSettings:
        """Return the engine settings to use for registry discovery.

        When the caller supplied ``engine_settings``, use it verbatim.
        Otherwise synthesize a fresh :class:`EngineSettings` whose
        ``.normalizer`` field is the normalizer settings the caller passed
        (or the default). Imported lazily to avoid a circular import at
        module load time.

        If ``self.settings`` is not a real :class:`NormalizerSettings`
        instance (callers using ``MagicMock`` in unit tests, legacy code
        paths that passed a malformed object), fall back to defaults rather
        than letting Pydantic v2 raise ``ValidationError`` deep inside
        cleaner-registry initialization.
        """
        if self._engine_settings is not None:
            return self._engine_settings

        from chaoscypher_core.settings import EngineSettings as _EngineSettings

        normalizer = (
            self.settings if isinstance(self.settings, NormalizerSettings) else NormalizerSettings()
        )
        return _EngineSettings(normalizer=normalizer)

    @property
    def cleaner_registry(self) -> CleanerRegistry:
        """Return the lazily-built cleaner registry.

        The registry is constructed on first access so services that never
        normalize (e.g. instantiated for metadata only) pay no discovery
        cost.
        """
        if self._cleaner_registry is None:
            self._cleaner_registry = CleanerRegistry(settings=self._build_engine_settings())
        return self._cleaner_registry

    @property
    def cleaners(self) -> list[CleanerProtocol]:
        """Get the cleaner pipeline.

        Cleaners are applied in priority-descending order. Built-in order
        by default priority:
        1. WebCleaner (priority 30 — extracts from HTML)
        2. TextCleaner (priority 20 — encoding/whitespace fixes)
        3. OCRCleaner (priority 10 — artifact removal)

        User plugins in ``data/plugins/cleaners/`` slot into the list based
        on their own ``metadata.priority``.

        Returns:
            List of cleaner instances sorted by priority descending.

        """
        if self._cleaners_override is not None:
            return self._cleaners_override
        if self._cleaners is None:
            # Pipeline-style cleaners apply to everything by default, so we
            # pass an empty metadata dict and let each cleaner's applies_to
            # predicate (or lack thereof) decide.
            self._cleaners = self.cleaner_registry.list_applicable({})
        return self._cleaners

    @property
    def plugin_load_failures(self) -> int:
        """Number of user cleaner plugins that failed to load or instantiate.

        Exposes the ``CleanerRegistry.plugin_load_failures`` counter so
        callers (the indexing handler) can roll the count up to the
        source-row quality counter ``CLEANER_PLUGIN_LOAD_FAILURES`` without
        coupling directly to the registry implementation.

        Returns 0 when no user plugin directory exists or all plugins loaded
        successfully.
        """
        return self.cleaner_registry.plugin_load_failures

    @property
    def transformer(self) -> TransformerProtocol:
        """Get the output transformer.

        Returns:
            Transformer instance (default: MarkdownNormalizer).

        """
        if self._transformer is None:
            self._transformer = MarkdownNormalizer(self.settings)
        return self._transformer

    def normalize(
        self,
        content: str,
        content_type: ContentType | str | None = None,
        metadata: dict | None = None,
    ) -> NormalizedContent:
        """Normalize content through the cleaning and transformation pipeline.

        This is the primary method for normalizing content. It:
        1. Detects content type if not provided
        2. Applies all cleaners in sequence
        3. Transforms to the target format
        4. Calculates quality metrics

        Args:
            content: Raw content to normalize.
            content_type: Content type (auto-detected if not provided).
            metadata: Additional metadata (passed to cleaners, preserved in output).

        Returns:
            NormalizedContent with cleaned content, metrics, and metadata.

        Example:
            result = service.normalize(
                "  Content with issues  ",
                ContentType.TEXT,
                {"source": "input.txt"}
            )
            print(result.content)  # Cleaned content
            print(result.quality_metrics.overall_score())

        """
        if not content:
            return NormalizedContent(
                content="",
                original_content="",
                content_type=ContentType.UNKNOWN,
                quality_metrics=QualityMetrics(),
                metadata=metadata or {},
            )

        # Resolve content type
        if isinstance(content_type, str):
            try:
                resolved_type = ContentType(content_type)
            except ValueError:
                resolved_type = self._detect_content_type(content)
        else:
            resolved_type = (
                content_type if content_type is not None else self._detect_content_type(content)
            )

        # Prepare metadata with content type
        working_metadata = dict(metadata or {})
        working_metadata["content_type"] = resolved_type

        logger.info(
            "normalization_started",
            content_type=resolved_type.value,
            content_length=len(content),
        )

        # Track original for comparison
        original_content = content
        all_operations: list[str] = []
        # Workstream 11 (2026-05-08): aggregate per-removal counts across
        # every cleaner that runs so the indexing handler can surface them
        # as source-row quality counters (cleaner_lines_removed,
        # cleaner_paragraphs_deduplicated, cleaner_chars_removed).
        total_lines_removed = 0
        total_paragraphs_deduplicated = 0
        total_chars_removed = 0
        # Phase 2 observability (2026-05-08): count predicate-gated OCR
        # cleaner skips so unknown extraction_methods are visible in the
        # Data Quality UI (OCR_CLEANER_SKIPPED_BY_PREDICATE counter).
        ocr_predicate_skips = 0
        normalization_start = time.perf_counter()

        # Apply cleaners in sequence with timing.
        #
        # Workstream 5.5 (2026-05-07): cleaners may opt out per-document via
        # an instance-level ``applies_to(metadata)`` predicate. The OCR
        # cleaner uses this so plain ``.txt`` / ``.md`` content keeps short
        # identifiers like ``git`` / ``npm`` / ``K8s`` instead of having
        # them stripped as "gibberish lines". A cleaner without an
        # ``applies_to`` method is treated as always-applicable (back-compat
        # with user plugins that pre-date the predicate).
        result = content
        for cleaner in self.cleaners:
            applies = getattr(cleaner, "applies_to", None)
            if callable(applies) and not applies(working_metadata):
                logger.debug(
                    "cleaner_skipped_by_predicate",
                    cleaner=cleaner.__class__.__name__,
                    extraction_method=working_metadata.get("extraction_method"),
                )
                # Phase 2 observability (2026-05-08): when the OCR cleaner is
                # the one being skipped and OCR cleaning is globally enabled,
                # the skip means an unknown extraction_method bypassed cleanup
                # silently. Surface this so operators see the gap.
                from chaoscypher_core.services.sources.normalizer.cleaners.ocr_cleaner import (
                    OCRCleaner as _OCRCleaner,
                )

                if isinstance(cleaner, _OCRCleaner) and self.settings.enable_ocr_cleaning:
                    ocr_predicate_skips += 1
                    logger.info(
                        "ocr_cleaner_skipped_by_predicate",
                        extraction_method=working_metadata.get("extraction_method"),
                    )
                continue

            cleaner_start = time.perf_counter()
            cleaner_output = cleaner.clean(result, working_metadata)
            cleaner_elapsed = time.perf_counter() - cleaner_start

            # Workstream 11 (2026-05-08): cleaners now return a
            # ``CleanerResult`` dataclass. The dataclass is iterable over
            # ``(content, ops)`` for back-compat, but for new code we read
            # the named fields. User plugins that still return the legacy
            # ``(content, list[str])`` tuple keep working because tuple
            # unpacking handles both shapes; the per-removal counts simply
            # default to 0 for those plugins.
            cleaned_content = getattr(cleaner_output, "content", None)
            if cleaned_content is None:
                # Legacy tuple shape from a third-party plugin.
                cleaned_content, operations = cleaner_output
                lines_removed = 0
                paragraphs_deduplicated = 0
                chars_removed = 0
            else:
                operations = list(getattr(cleaner_output, "ops", []) or [])
                lines_removed = int(getattr(cleaner_output, "lines_removed", 0) or 0)
                paragraphs_deduplicated = int(
                    getattr(cleaner_output, "paragraphs_deduplicated", 0) or 0
                )
                chars_removed = int(getattr(cleaner_output, "chars_removed", 0) or 0)

            result = cleaned_content
            all_operations.extend(operations)
            total_lines_removed += lines_removed
            total_paragraphs_deduplicated += paragraphs_deduplicated
            total_chars_removed += chars_removed

            logger.debug(
                "cleaner_completed",
                cleaner=cleaner.__class__.__name__,
                elapsed_seconds=round(cleaner_elapsed, 3),
                operations_count=len(operations),
                content_length_after=len(result),
                lines_removed=lines_removed,
                paragraphs_deduplicated=paragraphs_deduplicated,
                chars_removed=chars_removed,
            )

        # Apply transformer with timing
        transformer_start = time.perf_counter()
        result = self.transformer.transform(result, resolved_type)
        transformer_elapsed = time.perf_counter() - transformer_start
        all_operations.append(f"transform:{self.transformer.name}")

        logger.debug(
            "transformer_completed",
            transformer=self.transformer.name,
            elapsed_seconds=round(transformer_elapsed, 3),
        )

        total_elapsed = time.perf_counter() - normalization_start

        # Calculate quality metrics
        quality_metrics = self._calculate_quality_metrics(original_content, result, all_operations)

        logger.info(
            "normalization_complete",
            content_type=resolved_type.value,
            original_length=len(original_content),
            normalized_length=len(result),
            operations_count=len(all_operations),
            quality_score=round(quality_metrics.overall_score(), 3),
            elapsed_seconds=round(total_elapsed, 3),
            lines_removed=total_lines_removed,
            paragraphs_deduplicated=total_paragraphs_deduplicated,
            chars_removed=total_chars_removed,
        )

        return NormalizedContent(
            content=result,
            original_content=original_content,
            content_type=resolved_type,
            quality_metrics=quality_metrics,
            metadata=working_metadata,
            lines_removed=total_lines_removed,
            paragraphs_deduplicated=total_paragraphs_deduplicated,
            chars_removed=total_chars_removed,
            ocr_predicate_skips=ocr_predicate_skips,
        )

    def _detect_content_type(self, content: str) -> ContentType:
        """Detect content type from content analysis.

        Examines content to determine its type based on patterns and structure.

        Args:
            content: Content to analyze.

        Returns:
            Detected ContentType.

        """
        content_start = content[: get_settings().web.content_type_detection_window_bytes].lower()

        # HTML detection
        if self._HTML_RE.search(content_start):
            return ContentType.HTML

        # JSON detection
        stripped = content.strip()
        if (stripped.startswith("{") and stripped.endswith("}")) or (
            stripped.startswith("[") and stripped.endswith("]")
        ):
            return ContentType.JSON

        # CSV detection (comma-separated with consistent columns)
        lines = content_start.split("\n")[:5]
        if len(lines) >= 2:
            comma_counts = [line.count(",") for line in lines if line.strip()]
            if comma_counts and all(c == comma_counts[0] and c > 0 for c in comma_counts):
                return ContentType.CSV

        # Markdown detection (headers, lists, code blocks)
        if any(p.search(content) for p in self._MARKDOWN_PATTERNS):
            return ContentType.MARKDOWN

        # Code detection
        if any(p.search(content) for p in self._CODE_PATTERNS):
            return ContentType.CODE

        return ContentType.TEXT

    def _infer_type_from_metadata(self, metadata: dict) -> ContentType | None:
        """Infer content type from document metadata.

        Args:
            metadata: Document metadata from loader.

        Returns:
            Inferred ContentType or None.

        """
        # Check explicit format field
        if "format" in metadata:
            format_val = metadata["format"].lower()
            if format_val == "markdown":
                return ContentType.MARKDOWN
            if format_val == "html":
                return ContentType.HTML

        # Check extraction method
        if metadata.get("extraction_method") == "pypdf":
            return ContentType.PDF

        # Check filename extension
        source = metadata.get("source", "") or metadata.get("filename", "")
        if source:
            ext = source.rsplit(".", 1)[-1] if "." in source else ""
            if ext:
                result = ContentType.from_extension(ext)
                if result != ContentType.TEXT:
                    return result

        return None

    def _calculate_quality_metrics(
        self,
        original: str,
        cleaned: str,
        operations: list[str],
    ) -> QualityMetrics:
        """Calculate quality metrics for normalized content.

        Args:
            original: Original content before normalization.
            cleaned: Cleaned content after normalization.
            operations: List of operations applied.

        Returns:
            QualityMetrics with calculated scores.

        """
        # Calculate text ratio (alphabetic chars / total)
        if cleaned:
            alpha_count = sum(1 for c in cleaned if c.isalpha())
            total_count = len(cleaned)
            text_ratio = alpha_count / total_count if total_count > 0 else 0.0
        else:
            text_ratio = 0.0

        # Calculate duplicate ratio (how much was removed)
        if original:
            length_reduction = 1 - (len(cleaned) / len(original))
            # Cap at 1.0, and interpret reduction as potential duplicate removal
            duplicate_ratio = max(0.0, min(1.0, length_reduction))
        else:
            duplicate_ratio = 0.0

        # Structure score based on markdown structure presence
        structure_score = self._calculate_structure_score(cleaned)

        # Language confidence (simplified - based on word patterns)
        language_confidence = self._calculate_language_confidence(cleaned)

        return QualityMetrics(
            text_ratio=round(text_ratio, 3),
            language_confidence=round(language_confidence, 3),
            duplicate_ratio=round(duplicate_ratio, 3),
            structure_score=round(structure_score, 3),
            cleaning_operations=operations,
        )

    def _calculate_structure_score(self, content: str) -> float:
        """Calculate structure preservation score.

        Higher scores indicate more preserved/detected structure.

        Args:
            content: Content to analyze.

        Returns:
            Structure score (0.0-1.0).

        """
        if not content:
            return 0.0

        score = 0.5  # Base score

        # Check for headers
        if self._HEADER_RE.search(content):
            score += 0.15

        # Check for lists
        if self._LIST_RE.search(content):
            score += 0.1

        # Check for paragraphs (double newlines)
        if "\n\n" in content:
            score += 0.1

        # Check for code blocks
        if "```" in content:
            score += 0.1

        # Check for tables
        if self._TABLE_RE.search(content):
            score += 0.05

        return min(1.0, score)

    def _calculate_language_confidence(self, content: str) -> float:
        """Calculate language confidence score.

        Simple heuristic based on word patterns and character distribution.

        Args:
            content: Content to analyze.

        Returns:
            Language confidence score (0.0-1.0).

        """
        if not content:
            return 0.0

        # Extract words
        words = self._WORD_RE.findall(content)
        if not words:
            return 0.3  # No words found, low confidence

        # Check average word length (typical: 4-8 chars)
        avg_length = sum(len(w) for w in words) / len(words)
        length_score = 1.0 if 3 <= avg_length <= 10 else 0.5

        # Check for common English words as proxy for valid language
        common_words = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "must",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "with",
            "by",
            "from",
        }
        words_lower = {w.lower() for w in words}
        common_found = len(words_lower & common_words)
        common_ratio = common_found / len(common_words) if words else 0

        # Combined score
        return min(1.0, (length_score * 0.5) + (common_ratio * 0.5) + 0.3)


__all__ = ["ContentNormalizerService", "_resolved_settings"]
