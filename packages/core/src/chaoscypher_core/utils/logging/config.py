# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Structured Logging Configuration for Chaos Cypher.

Uses structlog for production-ready structured logging with:
- JSON output for production
- Human-readable output for development
- Correlation ID tracking via context variables
- Exception formatting
- Performance metrics

Security: Safe logging without sensitive data exposure
Correctness: Battle-tested structlog library
Maintainability: Replaces 200+ lines of custom formatters with ~50 lines
"""

import logging
import sys
from typing import IO, TYPE_CHECKING, cast

import structlog


if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger
    from structlog.types import Processor


# ============================================================================
# Configuration Functions (Single Responsibility: Setup Logging)
# ============================================================================


def configure_logging(
    use_json: bool = False,
    log_level: str = "INFO",
    _correlation_id_var_name: str = "correlation_id",
    stream: IO[str] | None = None,
) -> None:
    """Configure application-wide structured logging with structlog.

    This replaces 200+ lines of custom JSON/Development formatters with
    battle-tested structlog library.

    Args:
        use_json: If True, output JSON logs (production). If False, human-readable (development).
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        _correlation_id_var_name: Name of the context variable for correlation IDs (reserved)
        stream: Output stream for log messages. Defaults to sys.stdout.
            Use sys.stderr for MCP servers where stdout is reserved for protocol messages.

    Example:
        >>> # Development mode (human-readable, colored output)
        >>> configure_logging(use_json=False, log_level="DEBUG")
        >>>
        >>> # Production mode (JSON output for log aggregation)
        >>> configure_logging(use_json=True, log_level="INFO")

    """
    # Idempotency check - prevent duplicate configuration
    if getattr(logging, "_chaoscypher_logging_configured", False):
        return
    logging._chaoscypher_logging_configured = True  # type: ignore[attr-defined]

    if stream is None:
        stream = sys.stdout

    log_level_int = getattr(logging, log_level.upper())

    # Define shared processor chain (order matters!)
    # These processors prepare the event dict before rendering
    shared_processors: list[Processor] = [
        # Add log level to event dict
        structlog.stdlib.add_log_level,
        # Add logger name to event dict
        structlog.stdlib.add_logger_name,
        # Merge context variables (like correlation_id)
        structlog.contextvars.merge_contextvars,
        # Add timestamp in ISO 8601 format
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        # Add exception info if present
        structlog.processors.ExceptionRenderer(),
        # Add source location for warnings/errors
        structlog.processors.CallsiteParameterAdder(
            [
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            ],
        ),
    ]

    # Choose renderer based on mode
    if use_json:
        # Production: JSON output for log aggregation systems
        renderer: structlog.processors.JSONRenderer | structlog.dev.ConsoleRenderer = (
            structlog.processors.JSONRenderer()
        )
    else:
        # Development: Human-readable output, colors only when writing to a terminal.
        # Supervisord captures stdout to log files where ANSI codes are unreadable.
        renderer = structlog.dev.ConsoleRenderer(
            colors=stream.isatty() if hasattr(stream, "isatty") else False,
            exception_formatter=structlog.dev.plain_traceback,
        )

    # Configure stdlib logging to work with structlog
    # This sets up handlers with structlog formatting for ALL loggers (including uvicorn)
    logging.basicConfig(
        format="%(message)s",
        level=log_level_int,
        force=True,  # Override any existing configuration
        handlers=[logging.StreamHandler(stream)],
    )

    # Wrap standard library loggers with structlog formatter
    # The ProcessorFormatter handles rendering for stdlib loggers (uvicorn, etc.)
    for handler in logging.root.handlers:
        handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=renderer,
                foreign_pre_chain=shared_processors,
            )
        )

    # Configure structlog itself
    # CRITICAL: Use wrap_for_formatter instead of renderer to avoid double rendering
    # The handler's ProcessorFormatter already has the renderer, so structlog just
    # prepares the event dict and hands off to stdlib logging for final rendering
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Set specific log levels for third-party libraries
    # Uvicorn: Hide startup/info messages, only show warnings/errors
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

    # Other noisy libraries
    logging.getLogger("fastapi").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("filelock").setLevel(logging.WARNING)

    # Get root logger and log startup message
    logger = structlog.get_logger()
    logger.info(
        "logging_configured",
        mode="json" if use_json else "development",
        level=log_level,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger instance.

    Args:
        name: Logger name (usually __name__). If None, returns root logger.

    Returns:
        Configured structlog logger

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("user_login", user_id=123, ip="192.168.1.1")
        >>> # Output (JSON mode): {"event": "user_login", "user_id": 123, "ip": "192.168.1.1", ...}
        >>> # Output (dev mode):  2025-01-15 10:30:45 [info     ] user_login    user_id=123 ip=192.168.1.1

    """
    return cast("BoundLogger", structlog.get_logger(name))


__all__ = [
    "configure_logging",
    "get_logger",
]
