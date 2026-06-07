# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Databases Service.

Business logic for multi-database management.
"""

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.app_config import get_config_manager, reload_settings
from chaoscypher_core.database.engine import (
    database_exists,
    init_database,
)
from chaoscypher_core.exceptions import NotFoundError, ValidationError
from chaoscypher_cortex.features.databases.models import (
    CurrentDatabaseResponse,
    DatabaseListResponse,
    DatabaseResponse,
    DatabaseSwitchResponse,
)


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.database.repository import DatabaseRepository

logger = structlog.get_logger(__name__)


class DatabasesService:
    """Service for multi-database operations."""

    def __init__(self, database_repository: DatabaseRepository, settings: Settings):
        """Initialize databases service.

        Args:
            database_repository: DatabaseRepository instance
            settings: Settings instance

        """
        self.database_repository = database_repository
        self.settings = settings

    def list_databases(self) -> DatabaseListResponse:
        """List all available databases."""
        databases = self.database_repository.list_databases()
        return DatabaseListResponse(
            databases=[DatabaseResponse.model_validate(db.model_dump()) for db in databases]
        )

    def get_current_database(self) -> CurrentDatabaseResponse:
        """Get the currently active database."""
        current_db_name = self.settings.current_database

        db_info = self.database_repository.get_database(current_db_name)
        if not db_info:
            msg = "Database"
            raise NotFoundError(msg, current_db_name)

        return CurrentDatabaseResponse(
            current=current_db_name,
            info=DatabaseResponse.model_validate(db_info.model_dump()),
        )

    def switch_database(self, name: str) -> DatabaseSwitchResponse:
        """Switch to a different database.

        Args:
            name: Database name to switch to

        Returns:
            Switch response with success status

        Raises:
            HTTPException: If database not found

        """
        # Verify database exists
        db_info = self.database_repository.get_database(name)
        if not db_info:
            msg = "Database"
            raise NotFoundError(msg, name)

        # Auto-initialize app.db if it doesn't exist for the new database
        if not database_exists(name):
            logger.info("database_auto_initializing", database_name=name)
            init_database(name)
            logger.info("database_initialized", database_name=name)

        # Update current_database setting
        settings_manager = get_config_manager()
        settings_manager.update_settings({"current_database": name})

        # Invalidate global settings cache so subsequent requests use new database
        reload_settings()

        logger.info("database_switched", database_name=name)

        return DatabaseSwitchResponse(
            success=True,
            message=f"Database switched to '{name}' successfully. Refresh the page to load the new database.",
            database=name,
        )

    def get_database(self, name: str) -> DatabaseResponse:
        """Get information about a specific database."""
        db_info = self.database_repository.get_database(name)
        if not db_info:
            msg = "Database"
            raise NotFoundError(msg, name)

        return DatabaseResponse.model_validate(db_info.model_dump())

    def create_database(self, name: str) -> DatabaseResponse:
        """Create a new database.

        Args:
            name: Database name (alphanumeric, underscores, hyphens)

        Returns:
            Created database info

        Raises:
            HTTPException: If name invalid or database already exists

        """
        try:
            db_info = self.database_repository.create_database(name)
            return DatabaseResponse.model_validate(db_info.model_dump())
        except ValueError as e:
            raise ValidationError(str(e)) from e

    def delete_database(self, name: str) -> None:
        """Delete a database.

        Args:
            name: Database name to delete

        Raises:
            HTTPException: If trying to delete current database or database doesn't exist

        """
        # Safety check - cannot delete current database
        current_db = self.settings.current_database
        if name == current_db:
            msg = "Cannot delete the currently active database. Switch to another database first."
            raise ValidationError(msg)

        try:
            self.database_repository.delete_database(name)
        except ValueError as e:
            raise ValidationError(str(e)) from e
