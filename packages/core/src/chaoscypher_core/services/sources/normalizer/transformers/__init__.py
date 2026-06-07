# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""Content Transformers for the Normalization Pipeline.

Provides transformer implementations for converting cleaned content
to a uniform output format (typically Markdown).

All transformers implement the TransformerProtocol interface.

Example:
    from chaoscypher_core.services.sources.normalizer.transformers import (
        MarkdownNormalizer,
        TransformerProtocol,
    )
    from chaoscypher_core.services.sources.normalizer.models import (
        ContentType,
        NormalizerSettings,
    )

    settings = NormalizerSettings()
    transformer: TransformerProtocol = MarkdownNormalizer(settings)

    content = "#Messy Header\n* mixed\n+ list markers"
    normalized = transformer.transform(content, ContentType.PDF)
    # Output: "# Messy Header\n- mixed\n- list markers"
"""

# Infrastructure
from chaoscypher_core.services.sources.normalizer.transformers.base import (
    TransformerProtocol,
)

# Built-in transformers
from chaoscypher_core.services.sources.normalizer.transformers.markdown_transformer import (
    MarkdownNormalizer,
)


__all__ = [
    # Built-in transformers
    "MarkdownNormalizer",
    # Infrastructure
    "TransformerProtocol",
]
