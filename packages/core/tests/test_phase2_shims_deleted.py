# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 1 / Phase 2 / Phase 3 shim-deletion regression suite.

Every Phase 1 backwards-compat shim has now been deleted and migrated to
its canonical location. These tests pin the deletions so a future revert
or accidental re-introduction fails CI.

- Phase 2 Task L (small caller sets, deleted in 2026-04):
    * ``chaoscypher_core.adapters.sqlite.retry``
    * ``chaoscypher_core.adapters.llm.system_tools``
    * ``chaoscypher_core.services.sources.engine.extraction.utils.template_visuals``

- Phase 3 (deferred from Phase 2 retrospective; deleted across 2026-04):
    * ``chaoscypher_core.adapters.llm.metrics``
    * ``chaoscypher_core.adapters.llm.types``

``adapters/embedding/models.py`` is intentionally NOT in this list — it is
the canonical home for ``CloudModel`` / ``CuratedModel`` /
``ModelValidationResult`` (the ``EmbeddingHealthStatus`` shim part of
that file was removed separately in ``2399637d6``). All remaining
callers are internal to the embedding adapter package.
"""

from __future__ import annotations

import importlib


def test_sqlite_retry_shim_deleted() -> None:
    try:
        importlib.import_module("chaoscypher_core.adapters.sqlite.retry")
    except ModuleNotFoundError:
        return
    raise AssertionError("adapters.sqlite.retry shim must not exist")


def test_llm_system_tools_shim_deleted() -> None:
    try:
        importlib.import_module("chaoscypher_core.adapters.llm.system_tools")
    except ModuleNotFoundError:
        return
    raise AssertionError("adapters.llm.system_tools shim must not exist")


def test_template_visuals_shim_deleted() -> None:
    try:
        importlib.import_module(
            "chaoscypher_core.services.sources.engine.extraction.utils.template_visuals"
        )
    except ModuleNotFoundError:
        return
    raise AssertionError("template_visuals shim must not exist")


def test_llm_metrics_shim_deleted() -> None:
    try:
        importlib.import_module("chaoscypher_core.adapters.llm.metrics")
    except ModuleNotFoundError:
        return
    raise AssertionError("adapters.llm.metrics shim must not exist")


def test_llm_types_shim_deleted() -> None:
    try:
        importlib.import_module("chaoscypher_core.adapters.llm.types")
    except ModuleNotFoundError:
        return
    raise AssertionError("adapters.llm.types shim must not exist")


def test_canonical_retry_import_still_works() -> None:
    from chaoscypher_core.utils.retry import (
        DbLockRetryPolicy,
        retry_on_db_lock_async,
        retry_on_db_lock_sync,
    )

    assert callable(retry_on_db_lock_sync)
    assert callable(retry_on_db_lock_async)
    assert DbLockRetryPolicy is not None


def test_canonical_system_tools_import_still_works() -> None:
    from chaoscypher_core.services.workflows.tools.system_tools import execute_system_tool

    assert callable(execute_system_tool)


def test_canonical_template_visuals_import_still_works() -> None:
    from chaoscypher_core.templates.visuals import (
        resolve_edge_visuals,
        resolve_node_visuals,
    )

    assert callable(resolve_edge_visuals)
    assert callable(resolve_node_visuals)
