# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 2 Task C: SourceCommitService consumes port-typed deps.

Verifies the commit service accepts an injected ``RetryPolicyPort`` and
``EmbeddingProviderProtocol``, and that the two runtime adapter imports
(``retry_on_db_lock_async`` at L313, ``create_embedding_provider`` at
L1173) no longer appear in the service body. The lazy fallback inside
``_get_embedding_provider`` remains as a bootstrap convenience for
non-Engine callers and is allowlisted in the Phase 2 CC012 lint rule.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock


COMMIT_SERVICE_FILE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "chaoscypher_core"
    / "services"
    / "sources"
    / "engine"
    / "commit"
    / "service.py"
)


def test_commit_service_no_retry_adapter_import() -> None:
    source = COMMIT_SERVICE_FILE.read_text(encoding="utf-8")
    assert "from chaoscypher_core.adapters.sqlite.retry" not in source, (
        "commit service must route retry through RetryPolicyPort"
    )


def test_commit_service_adapter_embedding_import_only_in_fallback() -> None:
    """Only one allowlisted late import remains — the _get_embedding_provider fallback."""
    source = COMMIT_SERVICE_FILE.read_text(encoding="utf-8")
    hits = source.count("from chaoscypher_core.adapters.embedding import create_embedding_provider")
    assert hits == 1, (
        f"expected exactly one (allowlisted) adapter embedding import in the "
        f"lazy fallback; found {hits}"
    )


def test_commit_service_constructor_accepts_ports() -> None:
    from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
    from chaoscypher_core.utils.retry import DbLockRetryPolicy

    settings = MagicMock()
    settings.current_database = "test_db"
    custom_policy = DbLockRetryPolicy(max_retries=2)

    svc = SourceCommitService(
        graph_repository=MagicMock(),
        source_repository=MagicMock(),
        sources_repository=MagicMock(),
        indexing_repository=MagicMock(),
        search_repository=MagicMock(),
        settings=settings,
        retry_policy=custom_policy,
        embedding_provider=MagicMock(),
    )
    assert svc._retry_policy is custom_policy
    assert svc._embedding_provider is not None


def test_commit_service_defaults_retry_to_db_lock_policy() -> None:
    from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
    from chaoscypher_core.utils.retry import DbLockRetryPolicy

    settings = MagicMock()
    settings.current_database = "test_db"

    svc = SourceCommitService(
        graph_repository=MagicMock(),
        source_repository=MagicMock(),
        sources_repository=MagicMock(),
        indexing_repository=MagicMock(),
        search_repository=MagicMock(),
        settings=settings,
    )
    assert isinstance(svc._retry_policy, DbLockRetryPolicy)
    assert svc._embedding_provider is None, (
        "no default; lazy-constructed only on first _get_embedding_provider() call"
    )


def test_commit_service_injected_embedding_bypasses_adapter() -> None:
    """When a provider is injected, _get_embedding_provider never hits the adapter."""
    from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService

    settings = MagicMock()
    settings.current_database = "test_db"
    fake_provider = MagicMock()

    svc = SourceCommitService(
        graph_repository=MagicMock(),
        source_repository=MagicMock(),
        sources_repository=MagicMock(),
        indexing_repository=MagicMock(),
        search_repository=MagicMock(),
        settings=settings,
        embedding_provider=fake_provider,
    )
    assert svc._get_embedding_provider() is fake_provider


def test_service_ast_has_no_module_level_retry_import() -> None:
    tree = ast.parse(COMMIT_SERVICE_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "chaoscypher_core.adapters.sqlite.retry"
        ):
            raise AssertionError("module-level retry adapter import must not exist")
