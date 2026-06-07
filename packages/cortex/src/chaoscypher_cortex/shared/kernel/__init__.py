# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain Kernel.

Core domain primitives and base abstractions for the VSA architecture.

Provides foundational building blocks used across all feature slices in the
backend, including standardized response models for consistent API contracts.

The kernel follows the VSA (Vertical Slice Architecture) pattern where each
feature is self-contained, but common patterns like response formatting
are shared via these kernel abstractions.

Example:
    from chaoscypher_cortex.shared.kernel import BulkRequest, BulkResponse

    @router.post("/batch", response_model=BulkResponse)
    async def batch_operation(request: BulkRequest) -> BulkResponse:
        ...

"""

from chaoscypher_cortex.shared.kernel.responses import (
    BulkOperationRequest,
    BulkRequest,
    BulkResponse,
)


__all__ = [
    "BulkOperationRequest",
    "BulkRequest",
    "BulkResponse",
]
