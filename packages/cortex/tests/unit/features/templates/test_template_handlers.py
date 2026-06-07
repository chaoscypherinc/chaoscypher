# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for templates API handler logic.

Verifies that each handler calls the correct TemplateService method with the
correct arguments and transforms the service dict into a TemplateResponse.
FastAPI DI is bypassed — the service mock is passed directly.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from chaoscypher_core.models import TemplateCreate, TemplateUpdate
from chaoscypher_cortex.features.templates.api import (
    batch_templates_operation,
    create_template,
    delete_template,
    get_template,
    list_templates,
    regenerate_template_embeddings,
    update_template,
)
from chaoscypher_cortex.shared.kernel import BulkOperationRequest, BulkRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _template_dict(template_id: str = "tmpl-1", template_type: str = "node") -> dict:
    """Return a minimal template mapping compatible with TemplateResponse."""
    return {
        "id": template_id,
        "name": "Person",
        "description": "A person entity",
        "template_type": template_type,
        "properties": [],
        "is_system": False,
        "icon": None,
        "color": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }


def _paginated_templates(*template_ids: str) -> dict:
    """Return a service-style paginated result for templates."""
    data = [_template_dict(tid) for tid in template_ids]
    return {
        "data": data,
        "pagination": {
            "total": len(data),
            "page": 1,
            "page_size": 50,
            "total_pages": 1,
            "has_next": False,
            "has_prev": False,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListTemplates:
    """Tests for the list_templates handler."""

    @pytest.mark.asyncio
    async def test_returns_paginated_response(self) -> None:
        """Handler calls list_templates and wraps dicts in PaginatedTemplatesResponse."""
        mock_service = MagicMock()
        mock_service.list_templates.return_value = _paginated_templates("tmpl-1", "tmpl-2")

        result = await list_templates(
            _="test-user",
            template_service=mock_service,
            pagination=(1, 50),
            template_type=None,
        )

        mock_service.list_templates.assert_called_once_with(
            template_type=None, page=1, page_size=50
        )
        assert len(result.data) == 2
        assert result.data[0].id == "tmpl-1"
        assert result.data[1].id == "tmpl-2"
        assert result.pagination.total == 2

    @pytest.mark.asyncio
    async def test_passes_template_type_filter(self) -> None:
        """Handler forwards template_type filter to the service."""
        mock_service = MagicMock()
        mock_service.list_templates.return_value = _paginated_templates("tmpl-1")

        await list_templates(
            _="test-user",
            template_service=mock_service,
            pagination=(1, 25),
            template_type="node",
        )

        mock_service.list_templates.assert_called_once_with(
            template_type="node", page=1, page_size=25
        )

    @pytest.mark.asyncio
    async def test_returns_empty_list(self) -> None:
        """Handler handles empty page correctly."""
        mock_service = MagicMock()
        mock_service.list_templates.return_value = _paginated_templates()

        result = await list_templates(
            _="test-user",
            template_service=mock_service,
            pagination=(1, 50),
            template_type=None,
        )

        assert result.data == []
        assert result.pagination.total == 0


@pytest.mark.unit
class TestCreateTemplate:
    """Tests for the create_template handler."""

    @pytest.mark.asyncio
    async def test_creates_template_and_returns_response(self) -> None:
        """Handler passes TemplateCreate to the service and wraps dict in TemplateResponse."""
        mock_service = MagicMock()
        mock_service.create_template.return_value = _template_dict("tmpl-new")

        template_create = TemplateCreate(name="Location", template_type="node")

        result = await create_template(
            _="test-user",
            template_create=template_create,
            template_service=mock_service,
        )

        mock_service.create_template.assert_called_once_with(template_create)
        assert result.id == "tmpl-new"
        assert result.name == "Person"

    @pytest.mark.asyncio
    async def test_result_is_template_response_instance(self) -> None:
        """create_template returns a TemplateResponse, not a raw dict."""
        from chaoscypher_cortex.features.templates.models import TemplateResponse

        mock_service = MagicMock()
        mock_service.create_template.return_value = _template_dict()

        result = await create_template(
            _="test-user",
            template_create=TemplateCreate(name="X", template_type="node"),
            template_service=mock_service,
        )

        assert isinstance(result, TemplateResponse)


@pytest.mark.unit
class TestGetTemplate:
    """Tests for the get_template handler."""

    @pytest.mark.asyncio
    async def test_returns_template_response(self) -> None:
        """Handler calls get_template with the ID and wraps the dict in TemplateResponse."""
        mock_service = MagicMock()
        mock_service.get_template.return_value = _template_dict("tmpl-99")

        result = await get_template(
            _="test-user",
            template_id="tmpl-99",
            template_service=mock_service,
        )

        mock_service.get_template.assert_called_once_with("tmpl-99")
        assert result.id == "tmpl-99"

    @pytest.mark.asyncio
    async def test_result_is_template_response_instance(self) -> None:
        """get_template always returns a TemplateResponse object."""
        from chaoscypher_cortex.features.templates.models import TemplateResponse

        mock_service = MagicMock()
        mock_service.get_template.return_value = _template_dict()

        result = await get_template(
            _="test-user", template_id="tmpl-1", template_service=mock_service
        )

        assert isinstance(result, TemplateResponse)


@pytest.mark.unit
class TestUpdateTemplate:
    """Tests for the update_template handler."""

    @pytest.mark.asyncio
    async def test_updates_template_and_returns_response(self) -> None:
        """Handler calls update_template with ID and TemplateUpdate, returns TemplateResponse."""
        mock_service = MagicMock()
        updated = _template_dict("tmpl-5")
        updated["description"] = "Updated desc"
        mock_service.update_template.return_value = updated

        template_update = TemplateUpdate(description="Updated desc")

        result = await update_template(
            _="test-user",
            template_id="tmpl-5",
            template_update=template_update,
            template_service=mock_service,
        )

        mock_service.update_template.assert_called_once_with("tmpl-5", template_update)
        assert result.id == "tmpl-5"

    @pytest.mark.asyncio
    async def test_result_is_template_response_instance(self) -> None:
        """update_template returns a TemplateResponse, not a raw dict."""
        from chaoscypher_cortex.features.templates.models import TemplateResponse

        mock_service = MagicMock()
        mock_service.update_template.return_value = _template_dict()

        result = await update_template(
            _="test-user",
            template_id="tmpl-1",
            template_update=TemplateUpdate(),
            template_service=mock_service,
        )

        assert isinstance(result, TemplateResponse)


@pytest.mark.unit
class TestDeleteTemplate:
    """Tests for the delete_template handler."""

    @pytest.mark.asyncio
    async def test_calls_delete_and_returns_204_response(self) -> None:
        """Handler calls delete_template and returns a 204 Response."""
        from fastapi import Response

        mock_service = MagicMock()

        result = await delete_template(
            _="test-user",
            template_id="tmpl-del",
            template_service=mock_service,
            force=False,
        )

        mock_service.delete_template.assert_called_once_with("tmpl-del", force=False)
        assert isinstance(result, Response)
        assert result.status_code == 204

    @pytest.mark.asyncio
    async def test_passes_force_flag_to_service(self) -> None:
        """Handler forwards force=True to the service."""
        mock_service = MagicMock()

        await delete_template(
            _="test-user",
            template_id="tmpl-del",
            template_service=mock_service,
            force=True,
        )

        mock_service.delete_template.assert_called_once_with("tmpl-del", force=True)

    @pytest.mark.asyncio
    async def test_raises_409_when_template_in_use(self) -> None:
        """Handler converts ValueError from the service into HTTP 409 CONFLICT."""
        mock_service = MagicMock()
        mock_service.delete_template.side_effect = ValueError("Template is in use by 3 nodes")

        with pytest.raises(HTTPException) as exc_info:
            await delete_template(
                _="test-user",
                template_id="tmpl-busy",
                template_service=mock_service,
                force=False,
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "TEMPLATE_IN_USE"


@pytest.mark.unit
class TestRegenerateTemplateEmbeddings:
    """Tests for the regenerate_template_embeddings handler."""

    @pytest.mark.asyncio
    async def test_queues_task_and_returns_task_info(self) -> None:
        """Handler enqueues an embedding regen task and returns task_id + status."""
        mock_settings = MagicMock()
        mock_settings.priorities.background = 50
        mock_settings.current_database = "default"

        with patch(
            "chaoscypher_core.queue.queue_client.enqueue_task",
            new=AsyncMock(return_value="task-embed"),
        ):
            result = await regenerate_template_embeddings(
                _="test-user",
                settings=mock_settings,
            )

        assert result.task_id == "task-embed"
        assert result.status == "queued"
        assert "embedding" in result.message.lower()

    @pytest.mark.asyncio
    async def test_uses_llm_queue(self) -> None:
        """regenerate_template_embeddings targets the QUEUE_LLM queue (embeddings need LLM)."""
        from chaoscypher_core.constants import QUEUE_LLM

        mock_settings = MagicMock()
        mock_settings.priorities.background = 50
        mock_settings.current_database = "default"

        captured: list[dict] = []

        async def _capture(**kwargs: object) -> str:
            captured.append(dict(kwargs))
            return "task-123"

        with patch(
            "chaoscypher_core.queue.queue_client.enqueue_task",
            new=_capture,
        ):
            await regenerate_template_embeddings(
                _="test-user",
                settings=mock_settings,
            )

        assert captured[0]["queue"] == QUEUE_LLM


@pytest.mark.unit
class TestBatchTemplatesOperation:
    """Tests for the batch_templates_operation handler."""

    @pytest.mark.asyncio
    async def test_queues_task_and_returns_bulk_response(self) -> None:
        """Handler enqueues a bulk_templates task and returns a BulkResponse with task_id."""
        from chaoscypher_cortex.shared.kernel import BulkResponse

        mock_settings = MagicMock()
        mock_settings.priorities.background = 50

        request = BulkRequest(
            operations=[
                BulkOperationRequest(
                    operation="create",
                    data={"name": "Location", "template_type": "node"},
                ),
                BulkOperationRequest(operation="delete", data={"id": "tmpl-old"}),
            ]
        )

        with patch(
            "chaoscypher_core.queue.queue_client.enqueue_task",
            new=AsyncMock(return_value="task-bulk"),
        ):
            result = await batch_templates_operation(
                _="test-user",
                request=request,
                settings=mock_settings,
            )

        assert isinstance(result, BulkResponse)
        assert result.task_id == "task-bulk"
        assert result.status == "queued"
        assert "2 operations" in result.message

    @pytest.mark.asyncio
    async def test_uses_operations_queue(self) -> None:
        """batch_templates_operation always targets the QUEUE_OPERATIONS queue."""
        from chaoscypher_core.constants import QUEUE_OPERATIONS

        mock_settings = MagicMock()
        mock_settings.priorities.background = 50

        captured: list[dict] = []

        async def _capture(**kwargs: object) -> str:
            captured.append(dict(kwargs))
            return "task-xyz"

        with patch(
            "chaoscypher_core.queue.queue_client.enqueue_task",
            new=_capture,
        ):
            await batch_templates_operation(
                _="test-user",
                request=BulkRequest(operations=[]),
                settings=mock_settings,
            )

        assert captured[0]["queue"] == QUEUE_OPERATIONS
        assert captured[0]["operation"] == "bulk_templates"
