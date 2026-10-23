# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Neuron package test fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

from chaoscypher_core.testing import (
    data_dir_in_tmp,  # noqa: F401 — autouse: the data directory is a tmp dir
    llm_cache_in_tmp,  # noqa: F401 — autouse: the LLM caches live in a tmp dir
    ollama_offline,  # noqa: F401 — autouse: no test reaches Ollama
)


sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from worker_harness import worker_harness  # noqa: F401 — re-exposed as a fixture

from chaoscypher_core.testing import structlog_for_caplog  # noqa: F401 — re-exposed as a fixture
