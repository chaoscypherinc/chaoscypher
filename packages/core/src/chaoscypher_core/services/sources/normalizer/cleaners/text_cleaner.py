# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""Text cleaner for encoding, unicode, and whitespace normalization.

Handles fundamental text cleaning operations using ftfy and cleantext libraries:
- Encoding fixes (mojibake repair)
- Unicode normalization (NFC)
- Whitespace normalization
- Control character removal
- BOM (Byte Order Mark) removal

Example:
    from chaoscypher_core.services.sources.normalizer.cleaners import TextCleaner
    from chaoscypher_core.services.sources.normalizer.models import NormalizerSettings

    settings = NormalizerSettings()
    cleaner = TextCleaner(settings)

    content = "Ã©ncÃ¶ding\\x00issues  here"
    result = cleaner.clean(content)
    # result.content = "encoding issues here"
    # result.ops = ["encoding_fix", "control_char_removal", "whitespace_normalize"]
    # result.chars_removed = len(content) - len(result.content)

"""

import re
import unicodedata
from typing import TYPE_CHECKING, ClassVar

import structlog

from chaoscypher_core.plugins.base import PluginMetadata
from chaoscypher_core.services.sources.normalizer.cleaners.base import CleanerResult
from chaoscypher_core.utils.text_patterns import (
    CONTROL_CHAR_PATTERN,
    UNICODE_WHITESPACE_PATTERN,
)


if TYPE_CHECKING:
    from chaoscypher_core.settings import NormalizerSettings


logger = structlog.get_logger(__name__)


class TextCleaner:
    """Cleaner for encoding, unicode, and whitespace issues.

    Applies fundamental text cleaning operations in a specific order:
    1. Encoding fixes (ftfy) - repairs mojibake and encoding errors
    2. Unicode normalization (NFC) - consistent unicode representation
    3. Control character removal - strips non-printable characters
    4. Whitespace normalization - consistent spacing

    Attributes:
        settings: Configuration controlling which operations to apply.

    Example:
        cleaner = TextCleaner(NormalizerSettings())

        # Fix mojibake encoding
        text = "cafÃ©"  # UTF-8 decoded as Latin-1
        result = cleaner.clean(text)
        assert result.content == "café"
        assert "encoding_fix" in result.ops

    """

    metadata: ClassVar[PluginMetadata] = PluginMetadata(
        name="text_cleaner",
        version="1.0.0",
        description="Encoding normalization, unicode folding, whitespace cleanup.",
        priority=20,
    )

    def __init__(self, settings: NormalizerSettings) -> None:
        """Initialize the text cleaner.

        Args:
            settings: Normalizer settings controlling cleaner behavior.

        """
        self.settings = settings

    @property
    def name(self) -> str:
        """Return the cleaner name."""
        return "text_cleaner"

    def clean(self, content: str, metadata: dict | None = None) -> CleanerResult:
        """Clean text content with encoding and whitespace fixes.

        Applies cleaning operations based on settings configuration.
        Operations are applied in order: encoding → unicode → control chars → whitespace.

        Args:
            content: The text content to clean.
            metadata: Optional metadata (unused by this cleaner).

        Returns:
            :class:`CleanerResult` with cleaned content, ops list, and
            ``chars_removed`` populated from the before/after length delta.
            ``lines_removed`` and ``paragraphs_deduplicated`` stay 0 — the
            text cleaner doesn't operate at those granularities.

        """
        if not content:
            return CleanerResult(content=content)

        operations: list[str] = []
        result = content

        # Step 1: Fix encoding issues (mojibake)
        if self.settings.enable_encoding_fix:
            result, fixed = self._fix_encoding(result)
            if fixed:
                operations.append("encoding_fix")

        # Step 2: Normalize Unicode to NFC
        if self.settings.enable_unicode_normalize:
            result, normalized = self._normalize_unicode(result)
            if normalized:
                operations.append("unicode_normalize")

        # Step 3: Remove control characters
        if self.settings.enable_control_char_removal:
            result, removed = self._remove_control_chars(result)
            if removed:
                operations.append("control_char_removal")

        # Step 4: Normalize whitespace
        if self.settings.enable_whitespace_normalize:
            result, normalized = self._normalize_whitespace(result)
            if normalized:
                operations.append("whitespace_normalize")

        # Step 5: Remove BOM if present
        result, bom_removed = self._remove_bom(result)
        if bom_removed:
            operations.append("bom_removal")

        # Workstream 11 (2026-05-08): chars_removed is the net delta. NFC
        # normalization can in principle change length in either
        # direction, so clamp at zero — counters are monotonic-increase
        # observability, not a length-comparison primitive.
        chars_removed = max(0, len(content) - len(result))

        if operations:
            logger.debug(
                "text_cleaning_complete",
                operations=operations,
                original_length=len(content),
                cleaned_length=len(result),
                chars_removed=chars_removed,
            )

        return CleanerResult(
            content=result,
            ops=operations,
            chars_removed=chars_removed,
        )

    def _fix_encoding(self, text: str) -> tuple[str, bool]:
        """Fix encoding issues using ftfy.

        Args:
            text: Text with potential encoding issues.

        Returns:
            Tuple of (fixed_text, was_changed).

        """
        try:
            import ftfy

            fixed = ftfy.fix_text(
                text,
                normalization="NFC",
                fix_character_width=self.settings.ftfy_fix_character_width,
                fix_line_breaks=self.settings.ftfy_fix_line_breaks,
                fix_surrogates=True,
                remove_terminal_escapes=True,
                uncurl_quotes=False,  # Preserve smart quotes
            )
            return fixed, fixed != text
        except ImportError:
            logger.warning("ftfy_not_installed", message="Install ftfy for encoding fixes")
            return text, False
        except Exception:
            logger.exception("encoding_fix_failed")
            return text, False

    def _normalize_unicode(self, text: str) -> tuple[str, bool]:
        """Normalize Unicode to NFC form.

        NFC (Canonical Decomposition, followed by Canonical Composition) ensures
        consistent representation of characters that can be encoded multiple ways.

        Args:
            text: Text to normalize.

        Returns:
            Tuple of (normalized_text, was_changed).

        """
        normalized = unicodedata.normalize("NFC", text)
        return normalized, normalized != text

    def _remove_control_chars(self, text: str) -> tuple[str, bool]:
        """Remove control characters except newlines and tabs.

        Removes ASCII control characters (0x00-0x1F) except:
        - Newline (0x0A)
        - Carriage return (0x0D)
        - Tab (0x09)

        Also removes DEL (0x7F) and C1 control characters (0x80-0x9F).

        Args:
            text: Text with potential control characters.

        Returns:
            Tuple of (cleaned_text, had_control_chars).

        """
        cleaned = CONTROL_CHAR_PATTERN.sub("", text)
        return cleaned, cleaned != text

    def _normalize_whitespace(self, text: str) -> tuple[str, bool]:
        """Normalize whitespace in text.

        Performs the following normalizations:
        - Convert all whitespace types to standard space/newline
        - Collapse multiple spaces to single space
        - Collapse 3+ newlines to 2 newlines (preserve paragraph breaks)
        - Strip trailing whitespace from lines
        - Strip leading/trailing whitespace from document

        Args:
            text: Text with irregular whitespace.

        Returns:
            Tuple of (normalized_text, was_changed).

        """
        original = text

        # Replace various whitespace chars with standard space
        # (non-breaking space, em space, en space, etc.)
        text = UNICODE_WHITESPACE_PATTERN.sub(" ", text)

        # Normalize line endings to \n
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Collapse multiple spaces (but not newlines) to single space
        text = re.sub(r"[^\S\n]+", " ", text)

        # Strip trailing whitespace from each line
        text = re.sub(r" +\n", "\n", text)

        # Collapse 3+ newlines to 2 (preserve paragraph breaks)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Strip leading/trailing whitespace
        text = text.strip()

        return text, text != original

    def _remove_bom(self, text: str) -> tuple[str, bool]:
        """Remove Byte Order Mark if present.

        Args:
            text: Text potentially starting with BOM.

        Returns:
            Tuple of (text_without_bom, had_bom).

        """
        bom_chars = [
            "\ufeff",  # UTF-8/UTF-16 BOM
            "\ufffe",  # UTF-16 reversed BOM
        ]
        for bom in bom_chars:
            if text.startswith(bom):
                return text[len(bom) :], True
        return text, False


__all__ = ["TextCleaner"]
