# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Hypothesis property-based tests for foundational Core helpers.

These tests generate inputs and assert invariants rather than checking
specific examples. They catch entire classes of edge-case bugs that
example-based tests miss (empty input, byte-order marks, multi-byte
boundaries, off-by-one chunk-start, etc.).

Runtime budget
--------------
Each test caps at ``max_examples=100`` (Hypothesis default) so the suite
total stays under a minute. Individual tests should complete in well
under 10 seconds.

Where they run
--------------
- ``make test`` / ``make ci`` — yes (pytest auto-discovers ``tests/property/``).
- Pre-commit — **no**. Hypothesis tests are intentionally not wired into
  ``.pre-commit-config.yaml`` because they shrink failing examples on
  failure, which can add many seconds to a commit. The pre-commit hooks
  cover staged-file lint + type + dead-code; property invariants are a
  full-suite concern.
"""
