# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A guard that stops a chat stream when the model starts repeating itself.

Small thinking models loop on contradictory context: Qwen3.5 9B wrote about
100,000 characters of ``*   Okay.`` on 2 to 10 answers of every 120-question
benchmark run (2026-10-04), and the product's chat gave it the same seven
minutes per answer until the token budget cut it off. Extraction has had a
loop detector since 2026-05 (``_StreamLoopDetector``), but it reads pipe
lines, not prose; this one reads whatever the model streams, thinking or
answer, one completed line at a time.

Two rules, both on normalised lines (whitespace folded, list markers
dropped, three characters or more): a line repeated ``max_consecutive``
times in a row, or any one line seen ``max_occurrences`` times in the
stream. :func:`trim_repeats` cuts the repeated tail off text the guard
stopped, so the answer before the loop is kept.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field


_MARKER = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")


def normalise_line(line: str) -> str:
    """The line with its list marker dropped and whitespace folded."""
    return " ".join(_MARKER.sub("", line).split())


@dataclass
class RepetitionGuard:
    """Feed it stream deltas; ``tripped`` says the stream should stop.

    Attributes:
        max_consecutive: Identical lines in a row that count as a loop.
        max_occurrences: Times one line may appear in the whole stream.
        reason: Why it tripped (``""`` until it does).
    """

    max_consecutive: int = 8
    max_occurrences: int = 25
    reason: str = ""
    _buffer: str = field(default="", repr=False)
    _last: str = field(default="", repr=False)
    _streak: int = field(default=0, repr=False)
    _seen: Counter[str] = field(default_factory=Counter, repr=False)

    @property
    def tripped(self) -> bool:
        """Whether a loop has been detected; the stream should stop."""
        return bool(self.reason)

    def feed(self, delta: str) -> bool:
        """Add streamed text; return True once a loop is detected (and stay True)."""
        if self.tripped:
            return True
        self._buffer += delta
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if self._check(line):
                return True
        return False

    def _check(self, raw: str) -> bool:
        """Count one completed line; True when it makes a loop."""
        line = normalise_line(raw)
        if len(line) < 3:
            return False
        if line == self._last:
            self._streak += 1
        else:
            self._last, self._streak = line, 1
        self._seen[line] += 1
        if self._streak >= self.max_consecutive:
            self.reason = f"line repeated {self._streak} times in a row: {line[:60]!r}"
        elif self._seen[line] >= self.max_occurrences:
            self.reason = f"line seen {self._seen[line]} times: {line[:60]!r}"
        return self.tripped


def trim_repeats(text: str, max_consecutive: int = 8) -> str:
    """``text`` cut before the run of repeated lines that a guard stopped it on.

    The run is found from the end: the last line's normalised form, walked back
    while it repeats; when that run is at least ``max_consecutive`` long, or the
    last line repeats at all and the run reaches the end, the text ends before
    the run. Otherwise the text is returned as it was.
    """
    lines = text.split("\n")
    end = len(lines)
    while end > 0 and len(normalise_line(lines[end - 1])) < 3:
        end -= 1  # trailing blank or marker-only lines belong to the run
    if end == 0:
        return text
    last = normalise_line(lines[end - 1])
    start = end
    while start > 0 and normalise_line(lines[start - 1]) == last:
        start -= 1
    if end - start >= 2:
        return "\n".join(lines[:start]).rstrip()
    return text
