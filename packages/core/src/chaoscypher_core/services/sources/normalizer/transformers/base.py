# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""Protocol definition for content transformers.

Defines the TransformerProtocol interface that all transformer implementations
must follow. Transformers convert cleaned content into a uniform output format.

Example:
    from chaoscypher_core.services.sources.normalizer.transformers import (
        TransformerProtocol,
    )
    from chaoscypher_core.services.sources.normalizer.models import ContentType

    class CustomTransformer:
        '''Custom transformer implementation.'''

        @property
        def name(self) -> str:
            return "custom_transformer"

        def transform(self, content: str, source_type: ContentType) -> str:
            # Custom transformation logic
            return f"# Transformed\\n\\n{content}"

"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.normalizer.models import ContentType


@runtime_checkable
class TransformerProtocol(Protocol):
    """Protocol for content transformers.

    Transformers convert cleaned content into a standardized output format
    (typically Markdown). They ensure uniform structure regardless of the
    original content source.

    The protocol is runtime_checkable, allowing isinstance() checks.

    Attributes:
        name: Unique identifier for the transformer.

    Methods:
        transform: Convert content to the target format.

    Example:
        class MyMarkdownTransformer:
            @property
            def name(self) -> str:
                return "my_markdown_transformer"

            def transform(self, content: str, source_type: ContentType) -> str:
                # Normalize to markdown
                return content

        # Verify implementation
        transformer = MyMarkdownTransformer()
        assert isinstance(transformer, TransformerProtocol)

    """

    @property
    def name(self) -> str:
        """Return the unique name of this transformer.

        Returns:
            Transformer name used for logging and tracking.

        """
        ...

    def transform(self, content: str, source_type: ContentType) -> str:
        """Transform content to the target format.

        Args:
            content: The cleaned content to transform.
            source_type: The original content type (helps determine transformation rules).

        Returns:
            Transformed content in the target format.

        Example:
            transformed = transformer.transform(
                "Some text content",
                ContentType.PDF
            )

        """
        ...


__all__ = ["TransformerProtocol"]
