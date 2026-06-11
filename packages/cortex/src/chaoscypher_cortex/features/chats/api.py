# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat API Endpoints.

GET    /api/v1/chats - List chats (q = server-side title search)
POST   /api/v1/chats - Create chat
DELETE /api/v1/chats - Delete all chats
GET    /api/v1/chats/{id} - Get chat with messages
DELETE /api/v1/chats/{id} - Delete chat
PATCH  /api/v1/chats/{id} - Update chat title
PATCH  /api/v1/chats/{id}/status - Update chat status
PATCH  /api/v1/chats/{id}/scope - Update chat source scope
DELETE /api/v1/chats/{id}/scope - Clear chat source scope
POST   /api/v1/chats/{id}/messages - Add message to chat
GET    /api/v1/chats/{id}/messages - Get chat messages
POST   /api/v1/chats/{id}/send - Submit message (replace_from_message_id = edit-and-resend)
POST   /api/v1/chats/{id}/cancel - Stop the in-flight background turn
POST   /api/v1/chats/{id}/retry - Re-run a failed turn (no message duplication)
POST   /api/v1/chats/{id}/regenerate - Drop the last answer and re-run the turn
GET    /api/v1/chats/{id}/export - Export conversation (json | markdown)
POST   /api/v1/chats/{id}/tool_decision - Approve/reject a gated tool call
GET    /api/v1/chats/{id}/events - Subscribe to live chat events (SSE)
POST   /api/v1/chats/{id}/generate_title - Auto-generate chat title from first message
GET    /api/v1/chats/stats/count - Get chat count
GET    /api/v1/chats/_schema/sse_event - Schema anchor for ChatSSEEvent (OpenAPI codegen)
"""

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.constants import OP_CHAT_BACKGROUND, QUEUE_LLM
from chaoscypher_core.database import get_sqlite_adapter
from chaoscypher_core.queue import queue_client, subscribe_chat_events
from chaoscypher_core.services.chat import ChatService
from chaoscypher_core.streaming.chat import (
    ChatSSEEnvelope,
    format_sse_event,
)
from chaoscypher_cortex.features.chats.models import (
    ChatCountResponse,
    ChatCreate,
    ChatListResponse,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatResponse,
    ChatScopeUpdate,
    ChatSendRequest,
    ChatSendResponse,
    ChatStatus,
    ChatStatusUpdate,
    ChatTitleUpdate,
    PaginatedChatsResponse,
)
from chaoscypher_cortex.features.chats.repository import ChatScopeRepository
from chaoscypher_cortex.features.chats.service import ChatFeatureService
from chaoscypher_cortex.shared.api.dependencies import (
    PageParams,
)
from chaoscypher_cortex.shared.api.errors import raise_if_not_found
from chaoscypher_cortex.shared.api.models import PaginationMetadata
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
    RATE_LIMIT_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


if TYPE_CHECKING:
    from chaoscypher_core.ports.types import MessageDict

logger = structlog.get_logger(__name__)

# Create router
router = APIRouter()


# Dependency to get chat service
def get_chat_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatService:
    """Get ChatService instance (uses engine service with SQLite adapter)."""
    # Get singleton storage adapter
    adapter = get_sqlite_adapter(database_name=settings.current_database)

    # Return engine service directly (no wrapper)
    return ChatService(storage=adapter, database_name=settings.current_database)


def get_chat_feature_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatFeatureService:
    """Construct the Cortex chat orchestration service.

    Wires the engine ``ChatService`` together with a
    ``ChatScopeRepository`` so the three scope-aware handlers
    (``POST /chats``, ``PATCH /chats/{id}/scope``,
    ``DELETE /chats/{id}/scope``) can stay one-liners.
    """
    adapter = get_sqlite_adapter(database_name=settings.current_database)
    engine_service = ChatService(storage=adapter, database_name=settings.current_database)
    scope_repo = ChatScopeRepository(adapter, settings.current_database)
    return ChatFeatureService(engine_service, scope_repo)


# ============================================================================
# Chat CRUD Endpoints
# ============================================================================


@router.get(
    "",
    response_model=PaginatedChatsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def list_chats(
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    pagination: PageParams,
    _: CurrentUsername,
    scoped: bool | None = Query(default=None, description="Filter by scope status"),
    q: str | None = Query(
        default=None,
        max_length=200,
        description="Case-insensitive title substring filter",
    ),
) -> PaginatedChatsResponse:
    """List all chats (without messages).

    - Single-user mode: the local operator owns everything.
    """
    from typing import cast

    page, page_size = pagination
    offset = (page - 1) * page_size
    chats = chat_service.list_chats(
        offset=offset,
        limit=page_size,
        scoped=scoped,
        search=q,
    )
    total = chat_service.count_chats(scoped=scoped, search=q)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    return PaginatedChatsResponse(
        data=cast("list[ChatListResponse]", chats),
        pagination=PaginationMetadata(
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.post(
    "",
    response_model=ChatResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def create_chat(
    chat_create: ChatCreate,
    feature_service: Annotated[ChatFeatureService, Depends(get_chat_feature_service)],
    _: CurrentUsername,
) -> dict[str, Any]:
    """Create a new chat.

    - Single-user mode: the local operator owns everything.
    """
    return feature_service.create_chat_with_scope(chat_create)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def delete_all_chats(
    _: CurrentUsername,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> None:
    """Delete all chats for the current user/database.

    - Single-user mode: the local operator owns everything.
    """
    service.delete_all_chats()


@router.get(
    "/{chat_id}",
    response_model=ChatResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_chat(
    chat_id: str,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    _: CurrentUsername,
) -> dict[str, Any]:
    """Get chat by ID with all messages.

    - Single-user mode: the local operator owns everything.
    """
    return raise_if_not_found(chat_service.get_chat(chat_id), f"Chat {chat_id} not found")


@router.delete(
    "/{chat_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def delete_chat(
    chat_id: str,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    _: CurrentUsername,
) -> None:
    """Delete a chat and all its messages.

    - Single-user mode: the local operator owns everything.
    """
    chat_service.delete_chat(chat_id)


@router.patch(
    "/{chat_id}",
    response_model=ChatResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def update_chat(
    chat_id: str,
    title_update: ChatTitleUpdate,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    _: CurrentUsername,
) -> dict[str, Any] | None:
    """Update chat title.

    - Single-user mode: the local operator owns everything.
    """
    updated = chat_service.update_chat(
        chat_id=chat_id,
        updates={"title": title_update.title},
    )
    raise_if_not_found(updated, f"Chat {chat_id} not found")
    return chat_service.get_chat(chat_id)


@router.patch(
    "/{chat_id}/status",
    response_model=ChatResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def update_chat_status(
    chat_id: str,
    status_update: ChatStatusUpdate,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    _: CurrentUsername,
) -> dict[str, Any] | None:
    """Update chat status.

    Valid statuses: 'active', 'processing', 'completed', 'error'

    - Single-user mode: the local operator owns everything.
    """
    updated = chat_service.update_chat_status(
        chat_id=chat_id,
        status=status_update.status,
    )
    raise_if_not_found(updated, f"Chat {chat_id} not found")
    return chat_service.get_chat(chat_id)


# ============================================================================
# Message Endpoints
# ============================================================================


@router.post(
    "/{chat_id}/messages",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def add_message(
    chat_id: str,
    message_create: ChatMessageCreate,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    _: CurrentUsername,
) -> MessageDict | None:
    """Add a message to a chat.

    - Single-user mode: the local operator owns everything.
    """
    # Existence check: the FK on chat_messages would otherwise surface a
    # missing chat as an IntegrityError 500 instead of a 404.
    raise_if_not_found(chat_service.get_chat(chat_id), f"Chat {chat_id} not found")
    return chat_service.add_message(
        chat_id=chat_id,
        role=message_create.role,
        content=message_create.content,
        extra_metadata=message_create.extra_metadata,
    )


@router.get(
    "/{chat_id}/messages",
    response_model=list[ChatMessageResponse],
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def get_chat_messages(
    chat_id: str,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    _: CurrentUsername,
) -> list[MessageDict]:
    """Get all messages for a chat.

    Messages are returned in chronological order (oldest first).

    - Single-user mode: the local operator owns everything.
    """
    return chat_service.get_chat_messages(chat_id)


# ============================================================================
# Source Scope Endpoints
# ============================================================================


@router.patch(
    "/{chat_id}/scope",
    response_model=ChatResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def update_chat_scope(
    chat_id: str,
    scope_update: ChatScopeUpdate,
    feature_service: Annotated[ChatFeatureService, Depends(get_chat_feature_service)],
    _: CurrentUsername,
) -> dict[str, Any]:
    """Update the source scope of a chat."""
    result = feature_service.update_scope_with_message(chat_id, scope_update)
    return raise_if_not_found(result, "Chat not found")


@router.delete(
    "/{chat_id}/scope",
    response_model=ChatResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def clear_chat_scope(
    chat_id: str,
    feature_service: Annotated[ChatFeatureService, Depends(get_chat_feature_service)],
    _: CurrentUsername,
) -> dict[str, Any]:
    """Clear the source scope of a chat."""
    result = feature_service.clear_scope_with_message(chat_id)
    return raise_if_not_found(result, "Chat not found")


# ============================================================================
# Tool Approval Endpoint
# ============================================================================


class ToolDecisionRequest(BaseModel):
    """Body of ``POST /chats/{chat_id}/tool_decision``.

    The UI sends this after the user clicks Approve or Reject in the
    tool-approval dialog raised by the ``tool_approval_required`` SSE event.
    """

    tool_call_id: str = Field(
        description="LLM-assigned tool_call_id matching the pending approval."
    )
    decision: Literal["approve", "reject"] = Field(
        description="User's decision for the pending tool call."
    )


@router.post("/{chat_id}/tool_decision", status_code=status.HTTP_204_NO_CONTENT)
async def decide_tool_call(
    chat_id: str,
    body: ToolDecisionRequest,
    _: CurrentUsername,
) -> None:
    """Resolve a pending tool-call approval for an active chat turn.

    Returns ``204`` on success, ``404`` if no pending approval matches.
    The shared chat tool loop (running in the neuron worker) polls a
    Valkey key for the decision; this endpoint flips that key.
    """
    from chaoscypher_core.streaming.chat.approval_broker import resolve_tool_approval

    resolved = await resolve_tool_approval(chat_id, body.tool_call_id, body.decision)
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no pending approval",
        )


# ============================================================================
# Stop/Cancel Endpoint
# ============================================================================


@router.post(
    "/{chat_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def cancel_chat_turn(
    chat_id: str,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    _: CurrentUsername,
) -> dict[str, str]:
    """Request cancellation of the chat's in-flight background turn.

    Sets the Valkey cancel flag the shared chat tool loop polls at step
    boundaries; the worker lands at the next boundary, persists the
    partial answer with a "stopped" notice, and publishes
    ``done {status: "cancelled"}``.

    Returns ``202 {"status": "cancelling"}`` on success, ``404`` for an
    unknown chat, ``409`` when no turn is in progress, and ``503`` when
    the cancellation transport is unavailable.

    - Single-user mode: the local operator owns everything.
    """
    from chaoscypher_core.streaming.chat.cancellation import request_cancel

    chat = raise_if_not_found(chat_service.get_chat(chat_id), f"Chat {chat_id} not found")
    if chat.get("status") != ChatStatus.PROCESSING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No turn in progress",
        )
    if not await request_cancel(chat_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cancellation transport unavailable",
        )
    logger.info("chat_cancel_accepted", chat_id=chat_id)
    return {"status": "cancelling"}


@router.post(
    "/{chat_id}/retry",
    response_model=ChatSendResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def retry_chat_turn(
    chat_id: str,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: CurrentUsername,
) -> ChatSendResponse:
    """Re-enqueue the chat's last turn WITHOUT adding a new user message.

    A failed worker run persisted nothing (buffered-flush idempotency), so
    the persisted history already ends with the user's message — re-running
    the worker rebuilds the turn from it. This is what the UI's Retry button
    calls; re-POSTing through ``/send`` would duplicate the user message.

    Returns ``202`` with the new task id, ``404`` for an unknown chat, and
    ``409`` when a turn is already processing or the history has no user
    message to answer.

    - Single-user mode: the local operator owns everything.
    """
    from chaoscypher_core.services.llm import require_extraction_ready

    chat = raise_if_not_found(chat_service.get_chat(chat_id), f"Chat {chat_id} not found")
    if chat.get("status") == ChatStatus.PROCESSING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A turn is already in progress",
        )
    if not any(m.get("role") == "user" for m in chat.get("messages") or []):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No user message to retry",
        )
    await require_extraction_ready(settings)

    chat_service.update_chat_status(chat_id, "processing")
    task_id = await queue_client.enqueue_task(
        queue=QUEUE_LLM,
        operation=OP_CHAT_BACKGROUND,
        data={"chat_id": chat_id, "database_name": settings.current_database},
        priority=settings.priorities.interactive,
        metadata={"chat_id": chat_id},
    )
    logger.info("chat_retry_enqueued", chat_id=chat_id, task_id=task_id)
    return ChatSendResponse(task_id=task_id, status="processing")


@router.post(
    "/{chat_id}/regenerate",
    response_model=ChatSendResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def regenerate_chat_turn(
    chat_id: str,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: CurrentUsername,
) -> ChatSendResponse:
    """Regenerate the last answer: drop it and re-run the turn.

    Truncates everything after the last user message (the old answer and
    its tool rows) and re-enqueues the background turn from the remaining
    history. ``404`` unknown chat; ``409`` when a turn is already
    processing or there is no user message to answer.

    - Single-user mode: the local operator owns everything.
    """
    from chaoscypher_core.services.llm import require_extraction_ready

    chat = raise_if_not_found(chat_service.get_chat(chat_id), f"Chat {chat_id} not found")
    if chat.get("status") == ChatStatus.PROCESSING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A turn is already in progress",
        )
    last_user = next(
        (m for m in reversed(chat.get("messages") or []) if m.get("role") == "user"),
        None,
    )
    if not last_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No user message to regenerate from",
        )
    await require_extraction_ready(settings)

    chat_service.truncate_from_message(chat_id, last_user["id"], inclusive=False)
    chat_service.update_chat_status(chat_id, "processing")
    task_id = await queue_client.enqueue_task(
        queue=QUEUE_LLM,
        operation=OP_CHAT_BACKGROUND,
        data={"chat_id": chat_id, "database_name": settings.current_database},
        priority=settings.priorities.interactive,
        metadata={"chat_id": chat_id},
    )
    logger.info("chat_regenerate_enqueued", chat_id=chat_id, task_id=task_id)
    return ChatSendResponse(task_id=task_id, status="processing")


# ============================================================================
# Export Endpoint
# ============================================================================


@router.get(
    "/{chat_id}/export",
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def export_chat(
    chat_id: str,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    _: CurrentUsername,
    export_format: Annotated[
        Literal["json", "markdown"],
        Query(alias="format", description="Export format"),
    ] = "json",
) -> Response:
    """Export a conversation as JSON (default) or Markdown.

    JSON returns ``{"data": <full chat object>}`` (the shape the web UI's
    download button consumes). Markdown returns a ``text/markdown``
    attachment with role headings, entity markers reduced to bold labels,
    and citations rendered as footnotes carrying the source filename and
    sentence text where the persisted metadata has them.

    - Single-user mode: the local operator owns everything.
    """
    chat = raise_if_not_found(chat_service.get_chat(chat_id), f"Chat {chat_id} not found")
    if export_format == "markdown":
        from chaoscypher_cortex.features.chats.export import render_chat_markdown

        return Response(
            content=render_chat_markdown(chat),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="chat-{chat_id}.md"'},
        )
    return JSONResponse({"data": jsonable_encoder(chat)})


# ============================================================================
# Background Chat Endpoints
# ============================================================================


@router.post(
    "/{chat_id}/send",
    response_model=ChatSendResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **RATE_LIMIT_RESPONSE,
    },
)
async def send_message(
    chat_id: str,
    message: ChatSendRequest,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: CurrentUsername,
) -> ChatSendResponse:
    """Submit a user message for background processing.

    Saves the user message, sets chat status to 'processing', and enqueues
    the chat completion task to the LLM queue.

    Returns 202 Accepted with the task ID so the client can poll or subscribe
    to events via GET /{chat_id}/events.

    Returns 409 ``LLM_NOT_VERIFIED`` if the configured LLM provider has
    not been verified — frontend deeplinks the user to Settings → LLM.

    - Single-user mode: the local operator owns everything.
    """
    from chaoscypher_core.services.llm import require_extraction_ready

    await require_extraction_ready(settings)
    chat = raise_if_not_found(chat_service.get_chat(chat_id), f"Chat {chat_id} not found")

    # Edit-and-resend: replace an existing user message (and everything
    # after it) with this content, atomically before the new row is added.
    if message.replace_from_message_id:
        anchor = next(
            (
                m
                for m in chat.get("messages") or []
                if m.get("id") == message.replace_from_message_id
            ),
            None,
        )
        if not anchor or anchor.get("role") != "user":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="replace_from_message_id must be a user message in this chat",
            )
        chat_service.truncate_from_message(chat_id, message.replace_from_message_id, inclusive=True)

    chat_service.add_message(chat_id, role="user", content=message.content)
    chat_service.update_chat_status(chat_id, "processing")

    task_id = await queue_client.enqueue_task(
        queue=QUEUE_LLM,
        operation=OP_CHAT_BACKGROUND,
        data={"chat_id": chat_id, "database_name": settings.current_database},
        priority=settings.priorities.interactive,
        metadata={"chat_id": chat_id},
    )

    logger.info("chat_send_enqueued", chat_id=chat_id, task_id=task_id)

    return ChatSendResponse(task_id=task_id, status="processing")


@router.get(
    "/{chat_id}/events",
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def chat_events(
    chat_id: str,
    request: Request,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    _: CurrentUsername,
) -> EventSourceResponse:
    """Subscribe to live chat events for a background processing session (SSE).

    Reconnectable SSE stream that delivers chat processing events as they occur.
    On connect the current chat status is checked; if the chat is already
    'active' or 'completed' a done event is emitted immediately.  If the chat
    is in 'error' state an error event is emitted and the stream closes.
    Otherwise the endpoint subscribes to Valkey pub/sub and forwards events
    until a 'done' or 'error' event is received or the client disconnects.

    **Event Types:**
    - ``content``: LLM response content delta
    - ``tool_start``: Tool execution started
    - ``tool_result``: Tool execution result
    - ``done``: Processing completed successfully
    - ``error``: Processing failed

    - Single-user mode: the local operator owns everything.
    """

    async def event_generator() -> AsyncIterator[bytes]:
        """Generate SSE events for the chat session."""
        chat = chat_service.get_chat(chat_id)
        if chat is None:
            yield format_sse_event("error", {"error": f"Chat {chat_id} not found"})
            return

        chat_status = chat.get("status", ChatStatus.ACTIVE)

        if chat_status in (ChatStatus.ACTIVE, ChatStatus.COMPLETED):
            yield format_sse_event("done", {"status": chat_status})
            return

        if chat_status == ChatStatus.ERROR:
            yield format_sse_event(
                "error",
                {
                    "error": "Chat processing failed. Please try again.",
                    "error_code": "CHAT_PROCESSING_FAILED",
                },
            )
            return

        try:
            async for event in subscribe_chat_events(chat_id):
                if await request.is_disconnected():
                    break

                event_type = event.get("type", "unknown")
                event_data = event.get("data", {})
                yield format_sse_event(event_type, event_data)

                if event_type in ("done", "error"):
                    break
        except asyncio.CancelledError:
            raise
        except ConnectionError as exc:
            logger.warning(
                "chat_events_subscription_disconnected",
                chat_id=chat_id,
                error_type=type(exc).__name__,
            )
            yield format_sse_event(
                "error",
                {
                    "error": "Connection to event stream lost. Please try again.",
                    "error_code": "SUBSCRIPTION_ERROR",
                },
            )
        except Exception as exc:
            logger.exception(
                "chat_events_subscription_failed",
                chat_id=chat_id,
                error_type=type(exc).__name__,
            )
            yield format_sse_event(
                "error",
                {
                    "error": "Event stream failed due to an internal error.",
                    "error_code": "STREAM_INTERNAL_ERROR",
                },
            )

    return EventSourceResponse(event_generator())


# ============================================================================
# Title Generation Endpoint
# ============================================================================


@router.post(
    "/{chat_id}/generate_title",
    response_model=ChatResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def generate_title(
    chat_id: str,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: CurrentUsername,
) -> dict[str, Any] | None:
    """Auto-generate a chat title from the first user message.

    Uses a lightweight LLM call to produce a short (3-6 word) title
    based on the conversation's first user message.

    - Single-user mode: the local operator owns everything.
    """
    # Existence check first: a missing chat previously fell through to a
    # None return and a ResponseValidationError 500.
    chat = raise_if_not_found(chat_service.get_chat(chat_id), f"Chat {chat_id} not found")

    # Find the first user message (get_chat already loaded the messages)
    messages = chat.get("messages", [])
    first_user_msg = next(
        (m["content"] for m in messages if m.get("role") == "user"),
        None,
    )

    if not first_user_msg:
        # No user message found, return chat as-is
        return chat

    # Truncate very long messages to keep the LLM call cheap
    title_max_chars = settings.chat_context.title_generation_max_chars
    truncated = (
        first_user_msg[:title_max_chars]
        if len(first_user_msg) > title_max_chars
        else first_user_msg
    )

    try:
        from chaoscypher_core.llm_queue.queue_factory import get_llm_queue_service
        from chaoscypher_core.ports.llm import TaskType

        llm_queue = get_llm_queue_service()

        task_id = await llm_queue.queue_operation(
            task_type=TaskType.CHAT,
            operation_name="chat_completion",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Generate a short title (3-6 words) for the chat message "
                        "inside <user_message> tags. "
                        "Reply with ONLY the title, nothing else.\n\n"
                        "Examples:\n"
                        "hello -> Friendly Greeting\n"
                        "how do I parse JSON in python -> Parsing JSON in Python\n"
                        "can you help me debug this error -> Debugging an Error\n\n"
                        f"<user_message>\n{truncated}\n</user_message>"
                    ),
                },
            ],
            priority=settings.priorities.interactive,
            metadata={"chat_id": chat_id, "operation_type": "title_generation"},
            stream=False,
            temperature=settings.llm.ai_temperature,
        )
        response = await llm_queue.wait_for_result(task_id)

        # Extract title from response.
        # - Thinking models: if thinking field exists, content is clean.
        # - Non-thinking models: content is the direct response.
        # - Thinking fallback: provider moved thinking→content (thinking=None),
        #   content is long garbage. Detect via word count.
        raw = response.get("content", "").strip()
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        raw = lines[-1] if lines else ""
        title = raw.strip("\"'*").strip().rstrip(".")
        if len(title.split()) > settings.chat_context.auto_generated_title_max_words:
            title = ""

        if not title:
            title = "Untitled Chat"

        if title:
            chat_service.update_chat(chat_id=chat_id, updates={"title": title})
    except Exception:
        logger.warning("title_generation_failed", chat_id=chat_id, exc_info=True)
        # Silently fall through — chat keeps its existing title

    return chat_service.get_chat(chat_id)


# ============================================================================
# Statistics Endpoints
# ============================================================================


@router.get(
    "/stats/count",
    response_model=ChatCountResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def get_chat_count(
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    _: CurrentUsername,
) -> ChatCountResponse:
    """Get total chat count.

    - Single-user mode: the local operator owns everything.
    """
    count = chat_service.count_chats()
    return ChatCountResponse(count=count)


# ============================================================================
# Schema Introspection Endpoints
# ============================================================================


@router.get(
    "/_schema/sse_event",
    response_model=ChatSSEEnvelope,
    include_in_schema=True,
    summary="ChatSSEEvent schema anchor",
    description=(
        "Schema-only endpoint — never call at runtime. "
        "Its sole purpose is to force FastAPI to register ``ChatSSEEvent`` "
        "and all 15 event variant models as named ``#/components/schemas`` "
        "entries so Phase 7 OpenAPI→TypeScript codegen can produce a typed "
        "discriminated union. Returns 501 if called."
    ),
    tags=["schema"],
)
async def sse_event_schema_anchor() -> ChatSSEEnvelope:
    """Return 501; exists only to anchor ChatSSEEvent in the OpenAPI schema.

    Visibility is intentional — ``response_model=ChatSSEEnvelope`` is the
    only place the type is advertised. The OpenAPI→TypeScript codegen
    reads the resulting ``#/components/schemas`` entries to emit the
    typed discriminated union consumed by the GET /chats/{id}/events
    client.

    Raises:
        HTTPException: 501 Not Implemented on every call.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Schema anchor endpoint — not callable at runtime.",
    )
