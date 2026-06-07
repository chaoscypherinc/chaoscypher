# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared API Infrastructure.

Provides common utilities for building consistent FastAPI endpoints:
- Response models (ErrorDetail, ErrorCode)
- Shared cross-feature DTOs (TriggerResponse)
- Error handling with sanitized error messages
- Pagination dependencies (PageParams, LimitParam)
"""

from chaoscypher_cortex.shared.api.dependencies import (
    LimitParam,
    PageParams,
    safe_create,
    validate_limit,
    validate_page_size,
)
from chaoscypher_cortex.shared.api.errors import (
    create_error_response,
    operation_error,
    raise_if_not_found,
    resource_not_found_error,
    sanitize_error_message,
    sanitize_filename,
    validation_error,
)
from chaoscypher_cortex.shared.api.models import (
    PaginationMetadata,
    TriggerResponse,
    TriggerSummaryResponse,
)
from chaoscypher_cortex.shared.api.responses import (
    ErrorCode,
    ErrorDetail,
)


__all__ = [
    "ErrorCode",
    "ErrorDetail",
    "LimitParam",
    "PageParams",
    "PaginationMetadata",
    "TriggerResponse",
    "TriggerSummaryResponse",
    "create_error_response",
    "operation_error",
    "raise_if_not_found",
    "resource_not_found_error",
    "safe_create",
    "sanitize_error_message",
    "sanitize_filename",
    "validate_limit",
    "validate_page_size",
    "validation_error",
]
