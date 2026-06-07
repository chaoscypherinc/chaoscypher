# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Processing Validators.

Validation logic for source processing operations.
Extracted from source_processing_router.py for better separation.

Uses domain exceptions (not HTTPException) for framework independence.
The API layer is responsible for converting these to HTTP responses.
"""

from typing import TYPE_CHECKING, Any, Protocol

import structlog

from chaoscypher_core.exceptions import ExternalServiceError


if TYPE_CHECKING:
    # Protocol for objects that provide get_file() method
    class SourceFileProvider(Protocol):
        """Protocol for source file access."""

        def get_file(self, file_id: str, database_name: str) -> Any:
            """Get file by ID."""
            ...


logger = structlog.get_logger(__name__)


class SourceFileValidators:
    """Validation utilities for source_processing operations.

    Provides centralized validation logic for:
    - File existence and readiness for operations
    - Status checks
    - Service availability

    Raises domain exceptions (ExternalServiceError) instead of
    HTTPException for framework independence.
    """

    def __init__(
        self,
        source_manager: SourceFileProvider | None = None,
        llm_provider: Any = None,
        database_name: str | None = None,
    ):
        """Initialize validators with proper dependency injection.

        Args:
            source_manager: Object with get_file() method (e.g., SqliteAdapter)
            llm_provider: LLM provider for validation
            database_name: Database name for source file lookups

        """
        self.source_manager = source_manager
        self.llm_provider = llm_provider
        self.database_name = database_name

    def require_source_processing_service(self) -> None:
        """Check if source_processing service is available.

        Raises:
            ExternalServiceError: If source_processing manager not initialized

        """
        if not self.source_manager:
            msg = "SourceProcessing"
            raise ExternalServiceError(msg, "Manager not initialized")
