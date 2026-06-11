# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Protocol definition for content cleaners.

Defines the :class:`CleanerProtocol` interface and the :class:`CleanerResult`
dataclass every cleaner returns from :meth:`CleanerProtocol.clean`.

The protocol used to return a ``(content, ops_list[str])`` tuple. To wire
the per-source quality counters ``CLEANER_LINES_REMOVED`` /
``CLEANER_PARAGRAPHS_DEDUPLICATED`` / ``CLEANER_CHARS_REMOVED``, cleaners
now return a structured result that carries those counts alongside the
content and ops list. Counts default to 0 so a cleaner that doesn't
naturally track a particular signal (e.g. the text cleaner has no concept
of "paragraphs deduplicated") can stay honest without contorting itself.

Example:
    from chaoscypher_core.services.sources.normalizer.cleaners import (
        CleanerProtocol,
        CleanerResult,
    )

    class CustomCleaner:
        '''Custom cleaner implementation.'''

        metadata = PluginMetadata(
            name="custom_cleaner",
            version="1.0.0",
            description="Custom cleaner implementation.",
            priority=0,
        )

        @property
        def name(self) -> str:
            return "custom_cleaner"

        def clean(self, content: str, metadata: dict | None = None) -> CleanerResult:
            cleaned = content.strip()
            return CleanerResult(
                content=cleaned,
                ops=["strip_whitespace"],
                chars_removed=len(content) - len(cleaned),
            )

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable


if TYPE_CHECKING:
    from chaoscypher_core.plugins.base import PluginMetadata


@dataclass
class CleanerResult:
    """Structured return value for :meth:`CleanerProtocol.clean`.

    Carries the cleaned content plus the per-removal counts that feed the
    source-row quality counters. Cleaners that don't naturally track a
    given signal leave its count at 0 — there is no obligation to fabricate
    a number.

    Attributes:
        content: The cleaned content. May be the original string when the
            cleaner was a no-op for this input.
        ops: String identifiers for every cleaning operation that fired.
            Empty when the cleaner was a no-op.
        lines_removed: Lines dropped as gibberish / artifacts / page noise.
            Currently populated by :class:`OCRCleaner`. The text and web
            cleaners leave this at 0.
        paragraphs_deduplicated: Paragraphs removed as exact / fuzzy
            duplicates. Currently populated by :class:`OCRCleaner`'s
            duplicate-paragraph pass.
        chars_removed: Net character delta between input and output. The
            text cleaner records this from its before/after lengths so
            whitespace / control-char / encoding fixes show up on the
            data-quality tab even when no lines or paragraphs were
            dropped.
    """

    content: str
    ops: list[str] = field(default_factory=list)
    lines_removed: int = 0
    paragraphs_deduplicated: int = 0
    chars_removed: int = 0

    def __iter__(self):  # type: ignore[no-untyped-def]
        """Tuple-unpacking compatibility for legacy callers.

        Legacy callers wrote ``content, ops = cleaner.clean(...)``.
        Yielding ``(content, ops)`` keeps that pattern working for any
        external user plugins or third-party code that still expects the
        old tuple shape. New code should index the dataclass fields by
        name instead.
        """
        yield self.content
        yield self.ops


@runtime_checkable
class CleanerProtocol(Protocol):
    """Protocol for content cleaners.

    All cleaner implementations must provide a name property, a clean method,
    and a plugin metadata descriptor. Cleaners are applied in sequence by the
    normalizer service, each receiving the output of the previous cleaner.

    The protocol is runtime_checkable, allowing isinstance() checks.

    Attributes:
        metadata: Plugin descriptor for registry discovery (priority, version, etc.).
        name: Unique identifier for the cleaner (used in logging and metrics).

    Methods:
        clean: Process content and return a :class:`CleanerResult` carrying
            the cleaned content, ops list, and per-removal counts.

    Example:
        class MyTextCleaner:
            metadata = PluginMetadata(
                name="my_text_cleaner",
                version="1.0.0",
                description="Lowercases text.",
                priority=0,
            )

            @property
            def name(self) -> str:
                return "my_text_cleaner"

            def clean(
                self, content: str, metadata: dict | None = None
            ) -> CleanerResult:
                cleaned = content.lower()
                return CleanerResult(content=cleaned, ops=["lowercase"])

        # Verify implementation
        cleaner = MyTextCleaner()
        assert isinstance(cleaner, CleanerProtocol)

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Plugin descriptor for registry discovery.

        Returns:
            Metadata describing this cleaner (name, version, priority, etc.).

        """
        ...

    @property
    def name(self) -> str:
        """Return the unique name of this cleaner.

        Returns:
            Cleaner name used for logging and tracking applied operations.

        """
        ...

    def clean(self, content: str, metadata: dict | None = None) -> CleanerResult:
        """Clean the provided content.

        Args:
            content: The text content to clean.
            metadata: Optional metadata about the content source (e.g., filename,
                content_type). Cleaners may use this to adjust behavior.

        Returns:
            A :class:`CleanerResult` carrying the cleaned content, the ops
            list (string identifiers for operations performed), and the
            per-removal counts that feed source-row quality counters
            (``lines_removed``, ``paragraphs_deduplicated``,
            ``chars_removed``). A cleaner that doesn't naturally track a
            particular count leaves it at 0.

        Example:
            result = cleaner.clean("  Hello World  ", {"type": "text"})
            # result.content = "Hello World"
            # result.ops = ["strip_whitespace"]
            # result.chars_removed = 4

        """
        ...


__all__ = ["CleanerProtocol", "CleanerResult"]
