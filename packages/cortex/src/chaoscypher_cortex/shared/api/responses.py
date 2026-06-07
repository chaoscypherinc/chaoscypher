# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Standardized API Response Models.

Provides consistent response structures across all v1 endpoints.
"""

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Standardized error detail structure."""

    code: str = Field(..., description="Machine-readable error code (e.g., 'RESOURCE_NOT_FOUND')")
    message: str = Field(..., description="Human-readable error message")
    field: str | None = Field(default=None, description="Field name if validation error")
    details: dict[str, Any] | None = Field(default=None, description="Additional error context")


class UnifiedErrorResponse(BaseModel):
    """Unified error envelope used by every exception handler.

    Corresponds to the wire format produced by ``errors.py`` handlers:
    ``{"error": <CODE>, "message": <human>, "details": <object>}``.

    Declared as a Pydantic model so FastAPI's ``responses=`` machinery can
    describe it in the generated OpenAPI schema.
    """

    error: str = Field(
        ...,
        description=("Machine-readable error code (e.g., 'NOT_FOUND', 'VALIDATION_FAILED')."),
    )
    message: str = Field(..., description="Human-readable error message.")
    details: dict[str, Any] | None = Field(
        default=None, description="Optional structured error context."
    )


class QueuedResetResponse(BaseModel):
    """202 response returned when a reset or cleanup operation is queued.

    Used by every endpoint that dispatches a heavy reset/cleanup to the
    operations queue (settings reset_all, settings reset_knowledge_base,
    settings cleanup_orphans, graph cleanup, etc.). The client polls
    ``GET /queue/tasks/{task_id}/result`` (or subscribes to task
    events) for the final ResetResponse payload.
    """

    task_id: str
    status: str = "queued"
    operation_type: str
    message: str = "Reset operation queued for background execution"


# Error Code Constants
class ErrorCode:
    """Standard error codes across the API."""

    VALIDATION_FAILED = "VALIDATION_FAILED"
    OPERATION_FAILED = "OPERATION_FAILED"


# ============================================================================
# Common OpenAPI `responses=` blocks
# ============================================================================
#
# Spread these into route decorators so OpenAPI describes every error shape
# a client can see. Compose them as dict merges: the route lists
# ``**COMMON_ERROR_RESPONSES, **AUTH_ERROR_RESPONSES, **NOT_FOUND_RESPONSE``
# and adds any endpoint-specific entries. Every handler emits the same
# ``UnifiedErrorResponse`` body; only the per-status ``description`` changes.

COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "model": UnifiedErrorResponse,
        "description": "Bad request — malformed input.",
    },
    422: {
        "model": UnifiedErrorResponse,
        "description": "Unprocessable entity — request body failed validation.",
    },
    500: {
        "model": UnifiedErrorResponse,
        "description": "Internal server error.",
    },
}

AUTH_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "model": UnifiedErrorResponse,
        "description": "Unauthorized — missing or invalid credentials.",
    },
    403: {
        "model": UnifiedErrorResponse,
        "description": "Forbidden — authenticated but not permitted.",
    },
}

NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    404: {
        "model": UnifiedErrorResponse,
        "description": "Resource not found.",
    },
}

CONFLICT_RESPONSE: dict[int | str, dict[str, Any]] = {
    409: {
        "model": UnifiedErrorResponse,
        "description": "Conflict — resource state or uniqueness constraint.",
    },
}

RATE_LIMIT_RESPONSE: dict[int | str, dict[str, Any]] = {
    429: {
        "model": UnifiedErrorResponse,
        "description": "Too many requests — queue full or rate limit exceeded.",
    },
}

SERVICE_UNAVAILABLE_RESPONSE: dict[int | str, dict[str, Any]] = {
    503: {
        "model": UnifiedErrorResponse,
        "description": "Service unavailable — upstream or disabled feature.",
    },
}
