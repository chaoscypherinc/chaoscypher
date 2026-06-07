# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Common Response Models.

Standardized API responses and request models shared across feature slices.
"""

from typing import Any

from pydantic import BaseModel, model_validator


class BulkOperationRequest(BaseModel):
    """Single operation within a bulk request."""

    operation: str  # 'create', 'update', 'delete'
    data: dict[str, Any]


class BulkRequest(BaseModel):
    """Batch operations request."""

    operations: list[BulkOperationRequest]

    @model_validator(mode="after")
    def _validate_operations_count(self) -> BulkRequest:
        """Reject requests whose operations count exceeds the configured cap."""
        from chaoscypher_core.app_config import get_settings

        cap = get_settings().batching.bulk_request_max_operations
        if len(self.operations) > cap:
            msg = (
                f"BulkRequest operations count {len(self.operations)} exceeds "
                f"configured maximum {cap} (setting: batching.bulk_request_max_operations)"
            )
            raise ValueError(msg)
        return self


class BulkResponse(BaseModel):
    """Batch operation response."""

    task_id: str
    status: str
    message: str
