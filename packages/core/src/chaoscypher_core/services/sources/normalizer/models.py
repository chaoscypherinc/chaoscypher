# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Data models for content normalization.

Defines the core data structures used throughout the normalization pipeline:
- ContentType: Enum for classifying input content
- QualityMetrics: Metrics for assessing content quality
- NormalizedContent: Output container with content, metrics, and metadata
- NormalizerSettings: Re-exported from settings (single source of truth)

Example:
    from chaoscypher_core.services.sources.normalizer.models import (
        ContentType,
        NormalizedContent,
        QualityMetrics,
    )

    metrics = QualityMetrics(
        text_ratio=0.95,
        language_confidence=0.98,
        duplicate_ratio=0.02,
    )

    result = NormalizedContent(
        content="# Clean Content",
        original_content="  # Clean Content  ",
        content_type=ContentType.MARKDOWN,
        quality_metrics=metrics,
    )

"""

from dataclasses import dataclass, field
from enum import StrEnum

# Import NormalizerSettings from settings (single source of truth)
from chaoscypher_core.settings import NormalizerSettings


class ContentType(StrEnum):
    """Classification of content source types.

    Used to determine which cleaners and transformers to apply
    during the normalization pipeline.

    Attributes:
        TEXT: Plain text content (.txt, .log files)
        MARKDOWN: Markdown-formatted content (.md files, PDF extracts)
        HTML: HTML content (web pages, .html files)
        CSV: Comma-separated values (.csv files)
        JSON: JSON data (.json, .jsonl files)
        PDF: PDF document content (extracted text)
        WEB: Web-scraped content (URLs)
        CODE: Source code content
        UNKNOWN: Unclassified content type

    """

    TEXT = "text"
    MARKDOWN = "markdown"
    HTML = "html"
    CSV = "csv"
    JSON = "json"
    PDF = "pdf"
    WEB = "web"
    CODE = "code"
    UNKNOWN = "unknown"

    @classmethod
    def from_extension(cls, extension: str) -> ContentType:
        """Get ContentType from file extension (without dot).

        Args:
            extension: File extension without dot (e.g., 'pdf', 'html').

        Returns:
            Matching ContentType, or TEXT as fallback.

        """
        return _EXTENSION_TO_CONTENT_TYPE.get(extension.lower(), cls.TEXT)


_EXTENSION_TO_CONTENT_TYPE: dict[str, ContentType] = {
    "pdf": ContentType.PDF,
    "html": ContentType.HTML,
    "htm": ContentType.HTML,
    "md": ContentType.MARKDOWN,
    "csv": ContentType.CSV,
    "json": ContentType.JSON,
    "jsonl": ContentType.JSON,
    "txt": ContentType.TEXT,
    "log": ContentType.TEXT,
    "py": ContentType.CODE,
    "js": ContentType.CODE,
    "ts": ContentType.CODE,
}


@dataclass
class QualityMetrics:
    """Metrics for assessing normalized content quality.

    Provides quantitative measures of content quality after cleaning,
    useful for filtering low-quality content or flagging for review.

    Attributes:
        text_ratio: Ratio of alphabetic characters to total characters (0.0-1.0).
            Higher values indicate cleaner text with less noise.
        language_confidence: Confidence score for detected language (0.0-1.0).
            Lower values may indicate mixed languages or gibberish.
        duplicate_ratio: Ratio of duplicate content detected (0.0-1.0).
            Higher values indicate more repetition was removed.
        structure_score: Score indicating preserved document structure (0.0-1.0).
            Higher values mean better header/list/table preservation.
        cleaning_operations: List of cleaning operations that were applied.

    Example:
        metrics = QualityMetrics(
            text_ratio=0.92,
            language_confidence=0.95,
            duplicate_ratio=0.05,
            structure_score=0.88,
            cleaning_operations=["encoding_fix", "whitespace_normalize"],
        )

        if metrics.text_ratio < 0.5:
            logger.warning("Low quality content detected")

    """

    text_ratio: float = 1.0
    language_confidence: float = 1.0
    duplicate_ratio: float = 0.0
    structure_score: float = 1.0
    cleaning_operations: list[str] = field(default_factory=list)

    def overall_score(self) -> float:
        """Calculate overall quality score.

        Returns:
            Weighted average of quality metrics (0.0-1.0).

        """
        return (
            self.text_ratio * 0.3
            + self.language_confidence * 0.3
            + (1.0 - self.duplicate_ratio) * 0.2
            + self.structure_score * 0.2
        )


@dataclass
class NormalizedContent:
    r"""Container for normalized content with metadata and quality metrics.

    The primary output of the ContentNormalizerService, containing the cleaned
    and transformed content along with quality assessment and processing metadata.

    Attributes:
        content: The cleaned and normalized content (typically Markdown).
        original_content: The original content before normalization.
        content_type: The detected or specified content type.
        quality_metrics: Quality assessment metrics for the normalized content.
        metadata: Additional metadata from the source document.
        lines_removed: Aggregate count of gibberish / artifact lines dropped
            across every cleaner that ran. Surfaced to the source row as
            ``cleaner_lines_removed``.
        paragraphs_deduplicated: Aggregate count of duplicate paragraphs
            collapsed across every cleaner that ran. Surfaced to the
            source row as ``cleaner_paragraphs_deduplicated``.
        chars_removed: Aggregate net character delta across every cleaner
            that ran. Surfaced to the source row as
            ``cleaner_chars_removed``. Captures whitespace / control-char /
            encoding fixes that don't show up as a line or paragraph drop.
        char_count: Character count of normalized content.
        word_count: Estimated word count of normalized content.
        ocr_predicate_skips: Count of documents where the OCR cleaner's
            ``applies_to`` predicate returned ``False`` despite OCR cleaning
            being globally enabled. Non-zero values signal an unknown
            ``extraction_method`` that bypassed OCR cleanup silently.
            Surfaced to the source row as
            ``QualityCounter.OCR_CLEANER_SKIPPED_BY_PREDICATE``.

    Example:
        result = NormalizedContent(
            content="# Document Title\\n\\nClean paragraph text.",
            original_content="  # Document Title \\n\\n  Clean paragraph text.  ",
            content_type=ContentType.PDF,
            quality_metrics=QualityMetrics(text_ratio=0.95),
            metadata={"source": "document.pdf", "pages": 5},
        )

        print(f"Quality: {result.quality_metrics.overall_score():.2f}")

    """

    content: str
    original_content: str
    content_type: ContentType
    quality_metrics: QualityMetrics = field(default_factory=QualityMetrics)
    metadata: dict = field(default_factory=dict)
    lines_removed: int = 0
    paragraphs_deduplicated: int = 0
    chars_removed: int = 0
    ocr_predicate_skips: int = 0

    @property
    def char_count(self) -> int:
        """Get character count of normalized content."""
        return len(self.content)

    @property
    def word_count(self) -> int:
        """Get estimated word count of normalized content."""
        return len(self.content.split())


# NormalizerSettings is imported from chaoscypher_core.settings
# and re-exported here for convenience


__all__ = [
    "ContentType",
    "NormalizedContent",
    "NormalizerSettings",
    "QualityMetrics",
]
