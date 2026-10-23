# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""One definition of each fact the cache shares with the benchmark (grep-style guards).

The file hash a pack's store, its snapshot and its SHA256SUMS are checked by,
and the providers that honour a seed, each had two or three copies that could
drift apart; these tests fail when a new copy appears.
"""

from __future__ import annotations

import re
from pathlib import Path

from chaoscypher_core.testing.repo_files import repo_root


def _python_sources() -> list[Path]:
    """Every shipped Python module, plus the reference-pack installer (tests excluded)."""
    root = repo_root()
    files = sorted(root.glob("packages/*/src/**/*.py"))
    installer = root / "internal" / "benchmarks" / "reference" / "install.py"
    if installer.is_file():  # not in the Docker test image or the public export
        files.append(installer)
    return files


def _definitions(pattern: str) -> list[str]:
    """``path:line`` of every line matching ``pattern`` in :func:`_python_sources`."""
    regex = re.compile(pattern)
    root = repo_root()
    return [
        f"{path.relative_to(root)}:{number}"
        for path in _python_sources()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if regex.search(line)
    ]


def test_file_sha256_has_one_definition() -> None:
    """``chaoscypher_core.cache.export.file_sha256``; the benchmark's copies call it."""
    found = _definitions(r"^\s*def (file_sha256|_file_sha256|_sha256)\(")
    assert len(found) == 1 and found[0].startswith(
        "packages/core/src/chaoscypher_core/cache/export.py:"
    ), found


def test_seed_honouring_providers_has_one_definition() -> None:
    """``chaoscypher_core.utils.decoding.SEED_HONOURING_PROVIDERS``: the cache policy and rows read it."""
    found = _definitions(r"^SEED_HONOURING_PROVIDERS\b.*=")
    assert len(found) == 1 and found[0].startswith(
        "packages/core/src/chaoscypher_core/utils/decoding.py"
    ), found


def test_the_cache_and_the_rows_agree_on_who_honours_a_seed() -> None:
    from chaoscypher_core.benchmark.results import provider_honours_seed
    from chaoscypher_core.cache.keys import Decoding
    from chaoscypher_core.cache.policy import seeded
    from chaoscypher_core.utils.decoding import SEED_HONOURING_PROVIDERS

    pinned = Decoding(
        seed=42, temperature=0.0, num_ctx=None, max_tokens=None, thinking=False, repeat_penalty=None
    )
    for provider in (*SEED_HONOURING_PROVIDERS, "openai", "anthropic", "gemini"):
        assert seeded(pinned, provider) is provider_honours_seed(provider)
