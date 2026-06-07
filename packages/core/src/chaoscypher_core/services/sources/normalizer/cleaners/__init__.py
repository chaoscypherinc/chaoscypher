# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Content Cleaners for the Normalization Pipeline.

Provides cleaner implementations for different content cleaning needs:
- TextCleaner: Encoding fixes, unicode normalization, whitespace
- OCRCleaner: OCR artifact removal, gibberish filtering, deduplication
- WebCleaner: HTML extraction using trafilatura

All cleaners implement the CleanerProtocol interface for consistent usage.

Example:
    from chaoscypher_core.services.sources.normalizer.cleaners import (
        CleanerProtocol,
        TextCleaner,
        OCRCleaner,
        WebCleaner,
    )
    from chaoscypher_core.services.sources.normalizer.models import NormalizerSettings

    settings = NormalizerSettings()

    # Create cleaners
    cleaners: list[CleanerProtocol] = [
        TextCleaner(settings),
        OCRCleaner(settings),
        WebCleaner(settings),
    ]

    # Apply in sequence
    content = "dirty content"
    all_operations = []
    for cleaner in cleaners:
        result = cleaner.clean(content)
        content = result.content
        all_operations.extend(result.ops)
"""

# Infrastructure
from chaoscypher_core.services.sources.normalizer.cleaners.base import (
    CleanerProtocol,
    CleanerResult,
)

# Built-in cleaners
from chaoscypher_core.services.sources.normalizer.cleaners.ocr_cleaner import (
    OCRCleaner,
)
from chaoscypher_core.services.sources.normalizer.cleaners.text_cleaner import (
    TextCleaner,
)
from chaoscypher_core.services.sources.normalizer.cleaners.web_cleaner import (
    WebCleaner,
)


__all__ = [
    # Infrastructure
    "CleanerProtocol",
    "CleanerResult",
    # Built-in cleaners
    "OCRCleaner",
    "TextCleaner",
    "WebCleaner",
]
