# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Standardized error response helpers for API endpoints.

Prevents leaking internal details to clients while maintaining server-side logging.

Includes exception handlers for domain exceptions.
"""

from typing import Any

import structlog
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette import status

from chaoscypher_core.exceptions import (
    ChaosCypherException,  # noqa: TC001 - used at runtime in isinstance()
)
from chaoscypher_cortex.shared.api.responses import ErrorCode, ErrorDetail


logger = structlog.get_logger(__name__)


def sanitize_filename(filename: str | None) -> str:
    """Sanitize an uploaded filename to prevent path traversal and injection.

    Args:
        filename: Raw filename from upload (may contain path separators or special chars).

    Returns:
        Safe filename string.

    """
    import re
    from pathlib import PurePath

    if not filename:
        return "unknown"
    # Strip path components and null bytes
    name = PurePath(filename).name.replace("\x00", "")
    # Remove problematic characters
    name = re.sub(r'[<>:"|?*]', "", name)
    # POSIX NAME_MAX / NTFS filename limit -- filesystem constraint, not tunable
    max_length = 255
    if len(name) > max_length:
        p = PurePath(name)
        base, ext = p.stem, p.suffix
        name = base[: max_length - len(ext)] + ext
    return name or "unknown"


def raise_if_not_found[T](result: T | None, detail: str = "Resource not found") -> T:
    """Raise HTTP 404 if *result* is falsy, otherwise return the narrowed value.

    Eliminates the two-line ``if not result: raise HTTPException(404, ...)``
    pattern repeated across API endpoints.  The return type narrows
    ``T | None`` to ``T`` at call sites, enabling mypy to understand that
    the value is guaranteed non-None after this call.

    Args:
        result: Value returned by a service/repository lookup.
        detail: Human-readable message sent to the client.

    Returns:
        The original *result*, guaranteed to be truthy.

    Raises:
        HTTPException: 404 when *result* is falsy.

    """
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="NOT_FOUND", message=detail).model_dump(),
        )
    return result


def sanitize_error_message(error: Exception) -> str:
    """Sanitize error message for client consumption.

    Never expose internal details like file paths, SQL, stack traces.
    """
    # Generic message for all unexpected errors
    return "An unexpected error occurred. Please contact support if this persists."


def create_error_response(
    status_code: int,
    error_code: str,
    public_message: str,
    internal_error: Exception | None = None,
    log_level: str = "error",
) -> HTTPException:
    """Create standardized error response.

    Args:
        status_code: HTTP status code
        error_code: Application error code
        public_message: Safe message for client
        internal_error: Actual exception (logged but not sent to client)
        log_level: Logging level (error, warning, info)

    """
    # Log internal error with full details
    if internal_error:
        log_func = getattr(logger, log_level)
        log_func("api_error", message=public_message, exc_info=internal_error)

    return HTTPException(
        status_code=status_code,
        detail=ErrorDetail(code=error_code, message=public_message).model_dump(),
    )


def resource_not_found_error(resource_type: str, resource_id: str) -> HTTPException:
    """Return standard 404 response."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=ErrorDetail(
            code=f"{resource_type.upper()}_NOT_FOUND",
            message=f"{resource_type.capitalize()} '{resource_id}' not found",
        ).model_dump(),
    )


def validation_error(
    operation: str,
    internal_error: Exception | None = None,
    *,
    user_message: str | None = None,
) -> HTTPException:
    """Return standard validation error response.

    ``user_message`` overrides the default ``"Invalid data provided for <op>"``
    template when the caller has a useful, server-constructed reason it wants
    to surface to the UI (e.g. ``"Source is not currently processing"``).
    Only pass strings that are safe to display — never raw user input.
    """
    if internal_error:
        logger.warning(
            "validation_failed",
            operation=operation,
            error_type=type(internal_error).__name__,
            error_message=str(internal_error),
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ErrorDetail(
            code=ErrorCode.VALIDATION_FAILED,
            message=user_message or f"Invalid data provided for {operation}",
        ).model_dump(),
    )


def operation_error(operation: str, internal_error: Exception | None = None) -> HTTPException:
    """Return standard 500 response for unexpected errors."""
    if internal_error:
        logger.exception(
            "operation_failed",
            operation=operation,
            error_type=type(internal_error).__name__,
            error_message=str(internal_error),
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=ErrorDetail(
            code=ErrorCode.OPERATION_FAILED,
            message=(
                sanitize_error_message(internal_error) if internal_error else "Operation failed"
            ),
        ).model_dump(),
    )


# ============================================================================
# Domain Exception Handler
# ============================================================================


async def chaoscypher_exception_handler(
    request: Request, exc: ChaosCypherException
) -> JSONResponse:
    """Convert domain exceptions to HTTP responses.

    This handler converts ChaosCypherException and its subclasses into
    appropriate HTTP responses with correct status codes and error formats.

    Registered in app_factory.py:
        app.add_exception_handler(ChaosCypherException, chaoscypher_exception_handler)

    Args:
        request: FastAPI request object
        exc: Domain exception (ChaosCypherException or subclass)

    Returns:
        JSONResponse with appropriate status code and error detail

    """
    # Map exception types to HTTP status codes
    status_map = {
        "NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "VALIDATION_ERROR": status.HTTP_400_BAD_REQUEST,
        "CONFLICT": status.HTTP_409_CONFLICT,
        "INVALID_STATE": status.HTTP_409_CONFLICT,
        "LLM_NOT_VERIFIED": status.HTTP_409_CONFLICT,
        "EXTRACTION_MODEL_MISSING": status.HTTP_409_CONFLICT,
        "PERMISSION_DENIED": status.HTTP_403_FORBIDDEN,
        "AUTHENTICATION_ERROR": status.HTTP_401_UNAUTHORIZED,
        "OPERATION_ERROR": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "EXTERNAL_SERVICE_ERROR": status.HTTP_503_SERVICE_UNAVAILABLE,
        "RATE_LIMIT_ERROR": status.HTTP_429_TOO_MANY_REQUESTS,
        "QUEUE_FULL": status.HTTP_429_TOO_MANY_REQUESTS,
        "INSUFFICIENT_STORAGE": status.HTTP_507_INSUFFICIENT_STORAGE,
        "INTERNAL_ERROR": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "DATA_INTEGRITY_ERROR": status.HTTP_500_INTERNAL_SERVER_ERROR,
        # LLM provider error codes
        "LLM_AUTHENTICATION_ERROR": status.HTTP_401_UNAUTHORIZED,
        "LLM_RATE_LIMIT_ERROR": status.HTTP_429_TOO_MANY_REQUESTS,
        "LLM_MODEL_ERROR": status.HTTP_400_BAD_REQUEST,
        "LLM_CONTENT_FILTER_ERROR": status.HTTP_400_BAD_REQUEST,
        "LLM_CONTEXT_LENGTH_ERROR": status.HTTP_400_BAD_REQUEST,
        "LLM_SERVICE_ERROR": status.HTTP_503_SERVICE_UNAVAILABLE,
        "MODEL_CAPABILITY_ERROR": status.HTTP_400_BAD_REQUEST,
        "LLM_ERROR": status.HTTP_500_INTERNAL_SERVER_ERROR,
        # 2026-05-21 P0: self-imposed token-budget cap. Mirrors
        # LLM_RATE_LIMIT_ERROR's 429 — the operator has rate-limited
        # themselves via settings.llm.max_tokens_per_{source,day}; the
        # message + cap guidance lives in the error body. Permanent
        # (is_retryable=False) so the queue doesn't retry — operator
        # must raise the cap or wait for UTC rollover.
        "LLM_SPEND_CAP_EXCEEDED": status.HTTP_429_TOO_MANY_REQUESTS,
        # Phase 5b (2026-05-08): encrypted PDF detection
        "ENCRYPTED_PDF": status.HTTP_422_UNPROCESSABLE_ENTITY,
        # 2026-05-21 (P0 OOM guard): file exceeds settings.loader.max_disk_bytes.
        # 413 is the RFC 9110 fit for "the request payload is larger than the
        # server is willing to accept" — closer than 422 (syntactically valid
        # but semantically wrong) because the file is well-formed; we just
        # won't materialise it into RAM at 5-10x its disk size.
        "LOADER_FILE_TOO_LARGE": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    }

    # Get status code
    http_status = status_map.get(exc.code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Log the exception
    log_level = "warning" if http_status < 500 else "error"
    log_func = getattr(logger, log_level)

    log_func(
        "domain_exception_raised",
        exception_type=type(exc).__name__,
        exception_code=exc.code,
        message=exc.message,
        details=exc.details,
        path=request.url.path,
        method=request.method,
        status_code=http_status,
        exc_info=(http_status >= 500),  # Full stack trace for 5xx errors
    )

    # Build response
    response_data: dict[str, str | dict] = {
        "error": exc.code,
        "message": exc.message,
    }

    # Include details if present
    if exc.details:
        response_data["details"] = exc.details

    # Add retry-after header for rate limit errors
    headers = {}
    retry_after = getattr(exc, "retry_after", None)
    if retry_after:
        headers["Retry-After"] = str(retry_after)

    return JSONResponse(status_code=http_status, content=response_data, headers=headers)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Rewrite every ``HTTPException`` into the unified response envelope.

    Maps ``exc.detail`` (dict / string / other) into the
    ``{error, message, details}`` shape used by the domain handler.

    Before 2026-04-18, raising ``HTTPException(detail=ErrorDetail(...).model_dump())``
    produced ``{"detail": {"code": ..., "message": ..., "details": ..., "field": ...}}``
    on the wire. That shape diverged from the domain-exception handler's
    ``{"error": ..., "message": ..., "details": ...}`` envelope and the
    global handler's ``{"detail": ..., "code": ...}`` — three incompatible
    shapes that API consumers had to switch on.

    This handler maps every HTTPException into the ``{error, message, details}``
    shape. ``detail`` may be:

    - a dict with ``code``/``message`` (typical ``ErrorDetail`` dump)
    - a plain string (legacy sites — ``"Resource not found"``)
    - any other JSON-serializable value (rare; wrapped as-is in ``details``)

    The response keeps the HTTP status from ``exc.status_code`` and preserves
    any headers (e.g., ``Retry-After``, ``WWW-Authenticate``).
    """
    # FastAPI annotates HTTPException.detail as Any, but Starlette's superclass
    # narrows it to str | None — cast to Any so the dict/other branches stay
    # reachable for our richer error envelopes.
    detail: Any = exc.detail
    response_data: dict[str, str | dict] = {}

    if isinstance(detail, dict):
        response_data["error"] = str(
            detail.get("code") or detail.get("error") or f"HTTP_{exc.status_code}"
        )
        response_data["message"] = str(detail.get("message") or f"HTTP {exc.status_code}")
        inner_details = detail.get("details")
        if inner_details:
            response_data["details"] = inner_details
        field = detail.get("field")
        if field:
            if "details" not in response_data:
                response_data["details"] = {}
            if isinstance(response_data["details"], dict):
                response_data["details"].setdefault("field", field)
    elif isinstance(detail, str):
        response_data["error"] = f"HTTP_{exc.status_code}"
        response_data["message"] = detail
    else:
        response_data["error"] = f"HTTP_{exc.status_code}"
        response_data["message"] = f"HTTP {exc.status_code}"
        if detail is not None:
            response_data["details"] = {"raw": str(detail)}

    headers = getattr(exc, "headers", None) or {}
    return JSONResponse(status_code=exc.status_code, content=response_data, headers=headers)


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle FastAPI / Pydantic request-body validation errors.

    FastAPI's default ``RequestValidationError`` handler emits
    ``{"detail": [list-of-errors]}``. Rewrite to the unified envelope:
    ``{"error": "VALIDATION_FAILED", "message": "...", "details": {"errors": [...]}}``.

    Pydantic v2's ``errors()`` returns entries whose ``ctx.error`` is the
    original ``Exception`` instance (e.g. ``ValueError``) — that's not
    JSON-serializable, so passing it straight into ``JSONResponse``
    raised ``TypeError`` and fell through to the global handler,
    surfacing every validation failure as 500 INTERNAL_ERROR instead
    of 422 VALIDATION_FAILED. Coerce the offending fields to strings
    here.

    Args:
        request: FastAPI request (unused; accepted for handler signature).
        exc: RequestValidationError / ValidationError from Pydantic.

    """
    # Runtime import avoids a hard dep on FastAPI internals at module load.
    from fastapi.exceptions import RequestValidationError

    errors: list[dict] = []
    if isinstance(exc, RequestValidationError):
        for err in exc.errors():
            safe = dict(err)
            ctx = safe.get("ctx")
            if isinstance(ctx, dict) and "error" in ctx:
                # ``ctx['error']`` is the raw Python exception object.
                ctx = dict(ctx)
                ctx["error"] = str(ctx["error"])
                safe["ctx"] = ctx
            # ``loc`` is a tuple — JSON encodes tuples as lists, so this
            # is fine without coercion, but normalize for clarity.
            if "loc" in safe and isinstance(safe["loc"], tuple):
                safe["loc"] = list(safe["loc"])
            errors.append(safe)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "VALIDATION_FAILED",
            "message": "Request body failed validation",
            "details": {"errors": errors},
        },
    )


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unhandled exceptions.

    Emits the unified ``{error, message, details}`` envelope at HTTP 500
    with no ``str(exc)`` leakage — the traceback is logged server-side
    via ``logger.exception``.
    """
    logger.exception("unhandled_exception", path=request.url.path, method=request.method)
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
        },
    )
