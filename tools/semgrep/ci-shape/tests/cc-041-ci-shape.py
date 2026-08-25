# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
#
# CI-SHAPE fixture for cc-041-asyncio-run-in-tests. Not a pytest module
# (the hyphenated name is uncollectable on purpose).
#
# Its parent directory is named ``tests`` deliberately: semgrep's BUILT-IN
# default ignore list drops every ``test/`` and ``tests/`` path, which is
# exactly how CC040 and CC041 came to scan zero targets in `make
# lint-claude`. packages/core/tests/unit/scripts/test_semgrep_rule_selftests.py
# scans the PARENT directory (``tools/semgrep/ci-shape``) the way CI does —
# a directory target, not an explicit file path — so this file is only
# reachable while the repo-root .semgrepignore keeps test paths in scope.
#
# Exactly ONE violation lives here; the self-test asserts that count.

import asyncio


async def some_coro():
    return 1


def test_bad():
    asyncio.run(some_coro())
