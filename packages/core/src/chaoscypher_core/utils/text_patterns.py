# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared compiled regex patterns for text cleaning.

Provides pre-compiled patterns used by multiple modules (text_cleaner,
text_preparation) to avoid duplication and per-call recompilation.

Available patterns:

- ``CONTROL_CHAR_PATTERN`` -- Matches ASCII control characters (0x00-0x08,
  0x0B, 0x0C, 0x0E-0x1F), DEL (0x7F), and C1 control characters (0x80-0x9F).
  Excludes tab (0x09), newline (0x0A), and carriage return (0x0D).

- ``UNICODE_WHITESPACE_PATTERN`` -- Matches unicode whitespace characters
  (non-breaking space, en/em spaces, zero-width spaces, narrow no-break space,
  medium mathematical space, ideographic space) for normalization to standard
  ASCII space.

Example:
    from chaoscypher_core.utils.text_patterns import (
        CONTROL_CHAR_PATTERN,
        UNICODE_WHITESPACE_PATTERN,
    )

    cleaned = CONTROL_CHAR_PATTERN.sub("", text)
    normalized = UNICODE_WHITESPACE_PATTERN.sub(" ", text)

"""

import re


# Control character pattern (excludes tab, newline, carriage return)
# Matches: 0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F, 0x7F, 0x80-0x9F
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Unicode whitespace characters to normalize to standard space
UNICODE_WHITESPACE_PATTERN = re.compile(r"[\u00a0\u2000-\u200b\u202f\u205f\u3000]")


__all__ = [
    "CONTROL_CHAR_PATTERN",
    "UNICODE_WHITESPACE_PATTERN",
]
