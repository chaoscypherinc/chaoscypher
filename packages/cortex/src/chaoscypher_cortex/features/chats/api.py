# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat API Endpoints.

GET    /api/v1/chats - List chats
POST   /api/v1/chats - Create chat
DELETE /api/v1/chats - Delete all chats
GET    /api/v1/chats/{id} - Get chat with messages
DELETE /api/v1/chats/{id} - Delete chat
PATCH  /api/v1/chats/{id}/status - Update chat status
PATCH  /api/v1/chats/{id}/scope - Update chat source scope
DELETE /api/v1/chats/{id}/scope - Clear chat source scope
POST   /api/v1/chats/{id}/messages - Add message to chat
GET    /api/v1/chats/{id}/messages - Get chat messages
POST   /api/v1/chats/{id}/stream - Stream AI chat response (SSE)
POST   /api/v1/chats/{id}/send - Submit message for background processing
GET    /api/v1/chats/{id}/events - Subscribe to live chat events (SSE)
POST   /api/v1/chats/{id}/generate_title - Auto-generate chat title from first message
GET    /api/v1/chats/stats/count - Get chat count
GET    /api/v1/chats/_schema/sse_event - Schema anchor for ChatSSEEvent (OpenAPI codegen)
"""

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from chaoscypher_core.app_config import Settings, get_config_manager, get_settings
from chaoscypher_core.constants import OP_CHAT_BACKGROUND, QUEUE_LLM
from chaoscypher_core.database import get_sqlite_adapter
from chaoscypher_core.queue import queue_client, subscribe_chat_events
from chaoscypher_core.repo_factories import (
    get_graph_repository,
    get_search_repository,
)
from chaoscypher_core.services.chat import ChatService
from chaoscypher_core.streaming.chat import (
    ChatSSEEnvelope,
    format_sse_event,
    stream_chat_response,
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
    from chaoscypher_core.ports.storage_chats import ChatStorageProtocol
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
    )
    total = chat_service.count_chats()
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
    chat_service.update_chat(
        chat_id=chat_id,
        updates={"title": title_update.title},
    )
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
    chat_service.update_chat_status(
        chat_id=chat_id,
        status=status_update.status,
    )
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
    """Resolve a pending tool-call approval for an active chat stream.

    Returns ``204`` on success, ``404`` if no pending approval matches.
    The streaming handler is waiting on ``PendingApproval.wait()``; this
    endpoint wakes that waiter with the user's decision.
    """
    # Import locally to avoid loading the streaming sub-package at module
    # import time (the streaming package pulls in LLM providers).
    from chaoscypher_core.streaming.chat.approval import pending_approvals

    resolved = await pending_approvals.resolve(chat_id, body.tool_call_id, body.decision)
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no pending approval",
        )


# ============================================================================
# Streaming Chat Endpoint
# ============================================================================


def get_chat_streaming_service(
    database_name: str,
    stream_adapter: ChatStorageProtocol,
) -> ChatService:
    """Construct a ChatService for streaming responses.

    Streaming chat needs its own ChatService because the storage adapter
    has a per-request lifecycle (created with the stream, cleaned up when
    the stream closes). Regular chat endpoints share a singleton adapter
    via ``get_chat_service``. This factory is the ONE documented exception
    to the uniform DI pattern in Cortex — do not use elsewhere.

    Args:
        database_name: Database name for filtering chats/messages.
        stream_adapter: Per-request storage adapter bound to this SSE
            stream's lifetime. The caller owns its ``connect()`` /
            ``disconnect()`` lifecycle.

    Returns:
        A ``ChatService`` wired to the per-request adapter.

    """
    return ChatService(storage=stream_adapter, database_name=database_name)


@router.post(
    "/{chat_id}/stream",
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
    openapi_extra={
        "responses": {
            "200": {
                "description": (
                    "Server-Sent Events stream. "
                    "Each ``data:`` line carries a JSON-encoded ChatSSEEvent payload "
                    "(one of 13 discriminated variants; see ChatSSEEnvelope schema)."
                ),
                "content": {
                    "text/event-stream": {
                        "schema": {"$ref": "#/components/schemas/ChatSSEEnvelope"}
                    }
                },
            }
        },
    },
)
async def stream_chat(
    chat_id: str,
    message: ChatMessageCreate,
    settings: Annotated[Settings, Depends(get_settings)],
    _: CurrentUsername,
) -> EventSourceResponse:
    """Stream AI chat response with tool calling (SSE format).

    Streams responses in real-time using Server-Sent Events. Tool calls
    run synchronously during the stream.

    **Event Types:**
    - `content`: LLM response content (delta and accumulated)
    - `thinking_delta`: Reasoning process (if enabled)
    - `thinking`: Complete thinking block
    - `tool_calls`: List of tools to execute
    - `tool_start`: Tool execution started
    - `tool_result`: Tool execution result
    - `done`: Stream completed successfully
    - `error`: Error occurred

    - Single-user mode: the local operator owns everything.

    Returns 409 ``LLM_NOT_VERIFIED`` if the configured LLM provider has
    not been verified — frontend deeplinks the user to Settings → LLM.

    **Disconnect Behavior:**
    If the HTTP client disconnects mid-stream (tab closed, network drop),
    the underlying ``EventSourceResponse`` cancels this generator and the
    in-flight LLM work is lost — the assistant message is NOT persisted.
    Clients needing durable chat must use ``POST /chats/{id}/send`` to
    queue the work on the background worker and ``GET /chats/{id}/events``
    to observe progress; those survive disconnect.
    """
    from chaoscypher_core.services.llm import require_extraction_ready

    await require_extraction_ready(settings)
    # All adapters + sessions for streaming are created directly (bypassing
    # get_sqlite_adapter / get_chat_service / get_current_session DI) so
    # that none of them are closed before the SSE stream finishes.
    # Streaming responses outlive the middleware dispatch cycle — the
    # stream generator disconnects its own adapter (and therefore its
    # session) in the finally block.
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

    stream_adapter = SqliteAdapter(db_path=str(settings.app_db_path))
    stream_adapter.connect()

    chat_service = get_chat_streaming_service(
        database_name=settings.current_database,
        stream_adapter=stream_adapter,
    )
    search_repo = get_search_repository(database_name=settings.current_database)
    # Build graph_repo off the stream-scoped adapter's session so it shares
    # the stream's lifetime (not the request session, which closes when
    # FastAPI dispatch returns).
    if stream_adapter.session is None:
        raise RuntimeError("stream_adapter session is not connected")
    graph_repo = get_graph_repository(stream_adapter.session, settings.current_database)
    config_manager = get_config_manager()

    async def _stream_with_cleanup() -> AsyncIterator[bytes]:
        """Wrap stream_chat_response and disconnect the adapter when done."""
        try:
            async for chunk in stream_chat_response(
                chat_id=chat_id,
                user_message=message.content,
                chat_service=chat_service,
                graph_manager=graph_repo,
                search_manager=search_repo,
                config_manager=config_manager,
                settings=settings,
                indexing_manager=stream_adapter,
                source_storage=stream_adapter,
            ):
                yield chunk
        finally:
            stream_adapter.disconnect()

    return EventSourceResponse(_stream_with_cleanup())


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
    raise_if_not_found(chat_service.get_chat(chat_id), f"Chat {chat_id} not found")

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
    # Get chat messages to find the first user message
    messages = chat_service.get_chat_messages(chat_id)
    first_user_msg = next(
        (m["content"] for m in messages if m.get("role") == "user"),
        None,
    )

    if not first_user_msg:
        # No user message found, return chat as-is
        return chat_service.get_chat(chat_id)

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
        "and all 13 event variant models as named ``#/components/schemas`` "
        "entries so Phase 7 OpenAPI→TypeScript codegen can produce a typed "
        "discriminated union. Returns 501 if called."
    ),
    tags=["schema"],
)
async def sse_event_schema_anchor() -> ChatSSEEnvelope:
    """Return 501; exists only to anchor ChatSSEEvent in the OpenAPI schema.

    Visibility is intentional — ``response_model=ChatSSEEnvelope`` is the
    only place the type is advertised, so the ``$ref`` in
    ``stream_chat``'s ``openapi_extra`` would dangle if this operation
    were hidden. Phase 7 TS codegen reads the resulting
    ``#/components/schemas`` entries to emit the typed discriminated
    union.

    Raises:
        HTTPException: 501 Not Implemented on every call.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Schema anchor endpoint — not callable at runtime.",
    )
