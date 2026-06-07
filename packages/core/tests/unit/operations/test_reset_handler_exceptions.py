# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: reset_handler raises ValidationError for missing database_name.

Covers all four handlers:
- handle_reset_knowledge_base
- handle_reset_all
- handle_graph_cleanup
- handle_cleanup_orphans
"""

from __future__ import annotations

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.reset_handler import (
    handle_cleanup_orphans,
    handle_graph_cleanup,
    handle_reset_all,
    handle_reset_knowledge_base,
)


class TestResetKnowledgeBaseValidation:
    """handle_reset_knowledge_base raises ValidationError when database_name missing."""

    @pytest.mark.asyncio
    async def test_missing_database_name_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            await handle_reset_knowledge_base(data={})

        assert exc_info.value.field == "database_name"
        assert exc_info.value.code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_not_stdlib_value_error(self) -> None:
        """Confirm stdlib ValueError is no longer raised."""
        with pytest.raises(ValidationError):
            await handle_reset_knowledge_base(data={"database_name": ""})


class TestResetAllValidation:
    """handle_reset_all raises ValidationError when database_name missing."""

    @pytest.mark.asyncio
    async def test_missing_database_name_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            await handle_reset_all(data={})

        assert exc_info.value.field == "database_name"
        assert exc_info.value.code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_empty_database_name_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            await handle_reset_all(data={"database_name": ""})

        assert exc_info.value.field == "database_name"


class TestGraphCleanupValidation:
    """handle_graph_cleanup raises ValidationError when database_name missing."""

    @pytest.mark.asyncio
    async def test_missing_database_name_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            await handle_graph_cleanup(data={})

        assert exc_info.value.field == "database_name"
        assert exc_info.value.code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_not_stdlib_value_error(self) -> None:
        with pytest.raises(ValidationError):
            await handle_graph_cleanup(data={"database_name": None})


class TestCleanupOrphansValidation:
    """handle_cleanup_orphans raises ValidationError when database_name missing."""

    @pytest.mark.asyncio
    async def test_missing_database_name_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            await handle_cleanup_orphans(data={})

        assert exc_info.value.field == "database_name"
        assert exc_info.value.code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_not_stdlib_value_error(self) -> None:
        with pytest.raises(ValidationError):
            await handle_cleanup_orphans(data={"database_name": ""})
