# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""What a memory must never hold: keys and passwords (spec 2026-10-20 decision 2).

One pattern list, read by ``remember`` before anything is stored. A match names its kind so
the refusal can say what it saw without echoing the text.
"""

from __future__ import annotations

import re


SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("API key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.")),
    (
        "credential",
        re.compile(r"(?i)\b(password|passwd|api[_ -]?key|secret|token)\s*[:=]\s*\S{8,}"),
    ),
)
"""``(kind, pattern)`` in the order tried; the first match names the kind."""


def looks_like_secret(text: str) -> str | None:
    """The kind of secret ``text`` looks like, or None when it looks like none."""
    for kind, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return kind
    return None
