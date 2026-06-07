# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""Text preparation utilities for LLM extraction.

Prepares text content for sending to LLM extraction by applying essential
text cleaning operations. This ensures consistent, clean input regardless
of how the text was originally stored.

This module provides a lightweight text preparation function that handles:
- BOM (Byte Order Mark) removal
- Control character removal (except newlines/tabs)
- Unicode normalization (NFC)
- Encoding fixes (mojibake repair via ftfy)
- Whitespace normalization

Example:
    from chaoscypher_core.services.sources.engine.extraction.utils.text_preparation import (
        prepare_text_for_extraction,
    )

    raw_text = "\ufeffSome text with\x00control chars"
    clean_text = prepare_text_for_extraction(raw_text)
    # clean_text = "Some text with control chars"

"""

import unicodedata

import structlog

from chaoscypher_core.utils.text_patterns import (
    CONTROL_CHAR_PATTERN,
    UNICODE_WHITESPACE_PATTERN,
)


logger = structlog.get_logger(__name__)


# BOM characters to remove
_BOM_CHARS = (
    "\ufeff",  # UTF-8/UTF-16 BOM
    "\ufffe",  # UTF-16 reversed BOM
)


def prepare_text_for_extraction(text: str) -> str:
    r"""Prepare text for LLM extraction by applying essential cleaning.

    This function applies a series of text cleaning operations to ensure
    clean, consistent input for LLM extraction. It handles common issues
    that can cause LLM failures or poor extraction quality:

    1. BOM removal - Removes byte order marks that can confuse models
    2. Encoding fixes - Repairs mojibake (garbled text from encoding errors)
    3. Unicode normalization - Ensures consistent character representation
    4. Control char removal - Removes non-printable characters (keeps newlines/tabs)
    5. Whitespace normalization - Standardizes various whitespace types

    Args:
        text: Raw text content to prepare for extraction.

    Returns:
        Cleaned text ready for LLM extraction.

    Example:
        >>> prepare_text_for_extraction("\ufeffHello\x00World")
        'Hello World'

    """
    if not text:
        return text

    original_length = len(text)
    operations: list[str] = []

    # Step 1: Remove BOM characters
    for bom in _BOM_CHARS:
        if text.startswith(bom):
            text = text[len(bom) :]
            operations.append("bom_removal")
            break

    # Step 2: Fix encoding issues (mojibake) using ftfy if available
    try:
        import ftfy

        fixed = ftfy.fix_text(
            text,
            normalization="NFC",
            fix_character_width=True,
            fix_line_breaks=True,
            fix_surrogates=True,
            remove_terminal_escapes=True,
            uncurl_quotes=False,  # Preserve smart quotes
        )
        if fixed != text:
            text = fixed
            operations.append("encoding_fix")
    except ImportError:
        # ftfy not installed - skip encoding fixes
        pass
    except Exception:
        # Encoding fix failed - continue without it
        logger.debug("extraction_text_prep_encoding_fix_failed")

    # Step 3: Normalize Unicode to NFC
    normalized = unicodedata.normalize("NFC", text)
    if normalized != text:
        text = normalized
        operations.append("unicode_normalize")

    # Step 4: Remove control characters (except newlines and tabs)
    cleaned = CONTROL_CHAR_PATTERN.sub("", text)
    if cleaned != text:
        text = cleaned
        operations.append("control_char_removal")

    # Step 5: Normalize unicode whitespace to standard space
    cleaned = UNICODE_WHITESPACE_PATTERN.sub(" ", text)
    if cleaned != text:
        text = cleaned
        operations.append("whitespace_normalize")

    # Step 6: Normalize line endings to \n
    if "\r\n" in text or "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        operations.append("line_ending_normalize")

    # Log if any operations were applied
    if operations:
        logger.debug(
            "extraction_text_prepared",
            operations=operations,
            original_length=original_length,
            cleaned_length=len(text),
            chars_removed=original_length - len(text),
        )

    return text


__all__ = ["prepare_text_for_extraction"]
