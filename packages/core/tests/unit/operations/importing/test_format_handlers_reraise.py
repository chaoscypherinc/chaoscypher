# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: format-import handlers re-raise so the queue retries.

Audit fix #H/core (handle_import_ccx swallow). Per-handler swallows
hide retryable failures from the queue framework. Spec pin:
unconditional re-raise (not narrowed to specific types).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_handle_import_ccx_reraises_on_arbitrary_exception() -> None:
    # As of 20a1b28be (2026-05-22) the handler delegates to
    # ``ImportService.import_from_bytes`` rather than calling the
    # (never-existed) ``graph_repository.import_graph_from_ccx`` method,
    # so the failure must be injected at the new call site.
    from unittest.mock import AsyncMock, patch

    from chaoscypher_core.operations.importing.format_handler import handle_import_ccx

    graph_repository = MagicMock()
    data = {"file_content": "AAAA", "merge": False}

    with patch(
        "chaoscypher_core.services.package.importer.service.ImportService.import_from_bytes",
        new=AsyncMock(side_effect=RuntimeError("disk full")),
    ):
        with pytest.raises(RuntimeError, match="disk full"):
            await handle_import_ccx(data, graph_repository)


@pytest.mark.asyncio
async def test_handle_import_ccx_validation_error_still_raises() -> None:
    """Even ValidationError now propagates so the queue can decide retryability."""
    from chaoscypher_core.exceptions import ValidationError
    from chaoscypher_core.operations.importing.format_handler import handle_import_ccx

    data = {"file_content": None, "merge": False}

    with pytest.raises(ValidationError):
        await handle_import_ccx(data, MagicMock())


@pytest.mark.asyncio
async def test_handle_lexicon_import_reraises_on_download_failure() -> None:
    from chaoscypher_core.operations.importing import format_handler

    with pytest.MonkeyPatch.context() as mp:

        async def boom(*args, **kwargs):
            raise ConnectionError("lexicon down")

        # Patch the LexiconService.download path
        mp.setattr(
            "chaoscypher_core.services.lexicon.LexiconService.download",
            boom,
        )

        data = {
            "owner_username": "u",
            "repo_name": "r",
            "version": "1.0",
            "database_name": "default",
        }

        with pytest.raises(ConnectionError, match="lexicon down"):
            await format_handler.handle_lexicon_import(data, MagicMock())


@pytest.mark.asyncio
async def test_handle_import_ccx_logs_before_raising(caplog) -> None:
    """logger.exception('import_ccx_operation_failed', ...) is preserved."""
    from unittest.mock import AsyncMock, patch

    import structlog

    from chaoscypher_core.operations.importing.format_handler import handle_import_ccx

    graph_repository = MagicMock()

    with patch(
        "chaoscypher_core.services.package.importer.service.ImportService.import_from_bytes",
        new=AsyncMock(side_effect=RuntimeError("oops")),
    ):
        with structlog.testing.capture_logs() as cap:
            with pytest.raises(RuntimeError):
                await handle_import_ccx({"file_content": "AAAA", "merge": False}, graph_repository)

    events = [r["event"] for r in cap]
    assert "import_ccx_operation_failed" in events
