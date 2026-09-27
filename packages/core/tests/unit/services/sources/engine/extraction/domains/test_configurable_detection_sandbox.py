# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``detection.patterns[].regex`` must cross the user-regex sandbox.

A domain ``.jsonld`` supplies regex text through two fields.
``content_exclusions.custom_patterns`` has always gone through
``compile_safe`` (512-char cap, 100 ms per-match timeout, non-matching
result on timeout). ``detection.patterns[].regex`` was compiled with bare
stdlib ``re`` and searched on every ingest, so a catastrophic-backtracking
pattern hung the extraction worker on the first document that triggered it
— against a module that documents itself as "the ONLY boundary at which
user-supplied regex text crosses into the extraction pipeline" and a trust
boundary (``plugins/TRUST_BOUNDARY.md``) that classifies ``domains/*.jsonld``
as data rather than code.

Bound: these configs are operator-placed plugin files. Nothing in the tree
writes into ``{data_dir}/plugins/domains/``, so a hostile package cannot
plant one — this is a robustness boundary, not a remote-attacker path.

**Why the first test is structural, not timing-based.** The obvious
regression test — run a catastrophic pattern and assert it returns within
N seconds — cannot fail correctly here. Unsandboxed backtracking happens
inside stdlib ``re``, which does not release the GIL, so once the pattern
runs it starves every other thread: the assertion never gets to execute
and the suite *hangs* instead of failing. Moving the call onto a daemon
thread does not help, for the same reason (measured: a reverted fix hung
the file for >5 min rather than failing). So the property is pinned by
type instead — sandboxed or not is a structural fact, and it fails in
microseconds.
"""

from __future__ import annotations

import time
from typing import Any

from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
    ConfigurableDomain,
)
from chaoscypher_core.services.sources.engine.extraction.safe_user_regex import SafeUserRegex


def _domain(regex: str) -> ConfigurableDomain:
    return ConfigurableDomain(
        {"name": "sandbox-test", "detection": {"patterns": [{"regex": regex, "weight": 1.0}]}}
    )


def _confidence_floor(domain: ConfigurableDomain) -> Any:
    detection: dict[str, Any] = domain.config.get("detection", {})
    return detection.get("confidence", {}).get("base_score", 0.1)


class TestDetectionPatternSandbox:
    def test_detection_patterns_compile_into_the_sandbox(self) -> None:
        """The mutation-killer: a detection pattern is a ``SafeUserRegex``.

        Reverting ``_compile_patterns`` to ``re.compile`` fails this
        immediately. See the module docstring for why this is asserted
        structurally rather than by timing.
        """
        domain = _domain(r"\binvoice\b")
        assert len(domain._compiled_patterns) == 1
        compiled, weight = domain._compiled_patterns[0]
        assert isinstance(compiled, SafeUserRegex)
        assert weight == 1.0

    def test_catastrophic_pattern_does_not_hang_detection(self) -> None:
        """``(a+)+$`` on 61 chars hangs stdlib ``re``; it must not hang here.

        Measured on this tree: stdlib ``re.search`` had not returned after
        15 s on this input, while the sandboxed path returns in ~1 ms. The
        budget is loose on purpose — the property is terminate-vs-hang.
        """
        domain = _domain("(a+)+$")
        start = time.monotonic()
        can_handle, _confidence = domain.can_analyze("a" * 60 + "!", "doc.txt", {})
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"detection took {elapsed:.3f}s — sandbox not enforced"
        assert can_handle is False

    def test_timeout_path_returns_no_match_rather_than_raising(self) -> None:
        """A pattern that really does exceed the 100 ms budget bails out.

        Uses the ``(a?){N}a{N}`` shape from ``test_safe_user_regex.py``,
        which the regex module cannot shortcut, to exercise the timeout
        arm itself rather than merely a faster engine.
        """
        n = 400
        domain = _domain(rf"(a?){{{n}}}a{{{n}}}")
        _can_handle, confidence = domain.can_analyze("a" * n, "doc.txt", {})

        # Timed out → treated as no-match, so the pattern contributes no boost.
        assert domain._compiled_patterns[0][0].timeout_count >= 1
        assert confidence == _confidence_floor(domain)

    def test_overlong_pattern_is_skipped_not_raised(self) -> None:
        """A pattern past ``MAX_PATTERN_LENGTH`` is dropped with a warning.

        Mirrors ``compile_custom_patterns``: invalid entries are omitted,
        never raised, so one bad pattern cannot break domain loading.
        """
        domain = _domain("a" * 600)
        assert domain._compiled_patterns == []

    def test_valid_pattern_still_matches(self) -> None:
        """The sandbox is a drop-in: a normal pattern still scores."""
        domain = _domain(r"\binvoice\b")
        _can_handle, with_match = domain.can_analyze("this invoice is due", "doc.txt", {})
        _can_handle2, without_match = domain.can_analyze("nothing here", "doc.txt", {})
        assert with_match > without_match

    def test_detection_is_case_sensitive_as_before(self) -> None:
        """``flags=0`` preserves the stdlib call's case sensitivity.

        ``compile_safe`` defaults to ``IGNORECASE|MULTILINE`` for parity
        with ``compile_custom_patterns``; the detection call site must not
        inherit that, or case-sensitive shipped patterns (the ``reference``
        domain's RFC-2119 ``MUST``/``SHALL``) would start matching prose.
        """
        domain = _domain(r"\bMUST\b")
        _c1, upper = domain.can_analyze("this MUST happen", "doc.txt", {})
        _c2, lower = domain.can_analyze("this must happen", "doc.txt", {})
        assert upper > lower
