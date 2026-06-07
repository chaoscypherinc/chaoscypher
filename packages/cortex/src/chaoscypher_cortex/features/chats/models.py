# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat Models.

Pydantic DTOs for chat API requests/responses.

SQLModel table definitions are in chaoscypher.adapters.sqlite.models
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, Field

from chaoscypher_core import policy
from chaoscypher_core.utils.settings_validators import max_length_from_settings
from chaoscypher_cortex.shared.api.models import PaginationMetadata


__all__ = [
    "ChatCountResponse",
    "ChatCreate",
    "ChatListResponse",
    "ChatMessageCreate",
    "ChatMessageResponse",
    "ChatResponse",
    "ChatScopeUpdate",
    "ChatSendRequest",
    "ChatSendResponse",
    "ChatStatus",
    "ChatStatusUpdate",
    "ChatTitleUpdate",
    "PaginatedChatsResponse",
]


# ============================================================================
# Enums
# ============================================================================


class ChatStatus(StrEnum):
    """Lifecycle status of a chat."""

    ACTIVE = "active"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"


# ============================================================================
# Request/Response Models (Pydantic)
# ============================================================================


class ChatCreate(BaseModel):
    """Create chat DTO."""

    title: Annotated[
        str,
        Field(description="Chat title"),
        max_length_from_settings("chat_context.chat_title_max_length"),
    ]
    source_ids: list[str] | None = None
    tag_ids: list[str] | None = None


class ChatMessageCreate(BaseModel):
    """Create message DTO."""

    role: str = Field(max_length=policy.CHAT_ROLE_MAX_LENGTH)
    content: Annotated[
        str,
        Field(description="Message content"),
        max_length_from_settings("chat_context.chat_message_max_length"),
    ]
    extra_metadata: dict[str, Any] | None = None


class ChatMessageResponse(BaseModel):
    """Message response DTO."""

    id: str
    role: str
    content: str
    timestamp: datetime
    extra_metadata: dict[str, Any] | None

    model_config = {"from_attributes": True}


class ChatResponse(BaseModel):
    """Chat response DTO."""

    id: str
    title: str
    status: ChatStatus
    created_at: datetime
    updated_at: datetime
    message_count: int
    source_ids: list[str] | None = None
    messages: list[ChatMessageResponse]

    model_config = {"from_attributes": True}


class ChatListResponse(BaseModel):
    """Chat list response (without messages)."""

    id: str
    title: str
    status: ChatStatus
    created_at: datetime
    updated_at: datetime
    message_count: int
    source_ids: list[str] | None = None

    model_config = {"from_attributes": True}


class PaginatedChatsResponse(BaseModel):
    """Paginated response for listing chats."""

    data: list[ChatListResponse]
    pagination: PaginationMetadata


class ChatStatusUpdate(BaseModel):
    """Status update request."""

    status: ChatStatus


class ChatTitleUpdate(BaseModel):
    """Title update request."""

    title: Annotated[
        str,
        Field(description="Chat title"),
        max_length_from_settings("chat_context.chat_title_max_length"),
    ]


class ChatScopeUpdate(BaseModel):
    """Update chat source scope DTO."""

    source_ids: list[str] | None = None
    tag_ids: list[str] | None = None


class ChatCountResponse(BaseModel):
    """Chat count response."""

    count: int


class ChatSendRequest(BaseModel):
    """Request body for sending a chat message for background processing."""

    content: Annotated[
        str,
        Field(description="Message content"),
        max_length_from_settings("chat_context.chat_message_max_length"),
    ]


class ChatSendResponse(BaseModel):
    """Response from submitting a chat message for background processing."""

    task_id: str
    status: str = "processing"
