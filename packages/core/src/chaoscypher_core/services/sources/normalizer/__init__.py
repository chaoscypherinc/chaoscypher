# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Content normalization service for uniform document processing.

The normalizer provides a "swiss army knife" solution for cleaning and
normalizing content from any source (PDFs, HTML, CSV, JSON, web URLs, etc.)
into clean, uniform Markdown output ready for chunking and embedding.

Components:
- ContentNormalizerService: Main orchestrator for the normalization pipeline
- Cleaners: TextCleaner, OCRCleaner, WebCleaner for content-specific cleaning
- Transformers: MarkdownNormalizer for uniform output formatting
- Models: ContentType, QualityMetrics, NormalizedContent, NormalizerSettings

Pipeline Flow:
1. Content type detection (auto or specified)
2. Web extraction (for HTML/web content)
3. Text cleaning (encoding, unicode, whitespace)
4. OCR cleaning (artifact removal, deduplication)
5. Markdown transformation (uniform output)
6. Quality metrics calculation

Example:
    from chaoscypher_core.services.sources.normalizer import (
        ContentNormalizerService,
        ContentType,
        NormalizerSettings,
    )

    # Create service with default settings
    normalizer = ContentNormalizerService()

    # Normalize content
    result = normalizer.normalize(
        content="Dirty PDF content with OCR artifacts...",
        content_type=ContentType.PDF,
    )

    print(f"Quality: {result.quality_metrics.overall_score():.2f}")
    print(result.content)

"""

# Models
# Cleaners (for custom pipeline construction)
from chaoscypher_core.services.sources.normalizer.cleaners import (
    CleanerProtocol,
    CleanerResult,
    OCRCleaner,
    TextCleaner,
    WebCleaner,
)
from chaoscypher_core.services.sources.normalizer.models import (
    ContentType,
    NormalizedContent,
    NormalizerSettings,
    QualityMetrics,
)

# Service
from chaoscypher_core.services.sources.normalizer.service import (
    ContentNormalizerService,
)

# Transformers (for custom pipeline construction)
from chaoscypher_core.services.sources.normalizer.transformers import (
    MarkdownNormalizer,
    TransformerProtocol,
)


__all__ = [
    # Cleaners
    "CleanerProtocol",
    "CleanerResult",
    # Service
    "ContentNormalizerService",
    # Models
    "ContentType",
    # Transformers
    "MarkdownNormalizer",
    "NormalizedContent",
    "NormalizerSettings",
    "OCRCleaner",
    "QualityMetrics",
    "TextCleaner",
    "TransformerProtocol",
    "WebCleaner",
]
