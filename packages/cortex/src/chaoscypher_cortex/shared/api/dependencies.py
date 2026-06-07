# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared API Dependencies.

Reusable FastAPI ``Depends()`` callables for common query parameter patterns
such as pagination validation, ensuring consistent defaults and limits
across all list endpoints.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Query

from chaoscypher_core.app_config import Settings, get_settings


# ============================================================================
# Safe Factory Helper
# ============================================================================


def safe_create[T](factory: Callable[..., T], *args: Any, **kwargs: Any) -> T | None:
    """Create a service or client, returning None on any failure.

    Used by health and diagnostics endpoints to build optional
    dependencies that should not prevent the parent endpoint from
    responding when one subsystem is unavailable.

    Args:
        factory: Callable to create the service or client.
        *args: Positional arguments forwarded to *factory*.
        **kwargs: Keyword arguments forwarded to *factory*.

    Returns:
        The created instance, or ``None`` if *factory* raised.

    """
    try:
        return factory(*args, **kwargs)
    except Exception:
        return None


# ============================================================================
# Pagination Dependencies
# ============================================================================


def validate_page_size(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int | None = Query(default=None, ge=1, description="Items per page"),
    settings: Settings = Depends(get_settings),
) -> tuple[int, int]:
    """Validate and normalize page/page_size pagination parameters.

    Applies the configured default when ``page_size`` is None and clamps
    the value to ``settings.pagination.max_page_size``.

    Returns:
        Tuple of (page, page_size) with validated values.

    """
    if page_size is None:
        page_size = settings.pagination.default_page_size
    page_size = min(page_size, settings.pagination.max_page_size)
    return page, page_size


def validate_limit(
    limit: int | None = Query(default=None, ge=1, description="Maximum number of results"),
    settings: Settings = Depends(get_settings),
) -> int:
    """Validate and normalize a ``limit`` pagination parameter.

    Applies the configured default when ``limit`` is None and clamps
    the value to ``settings.pagination.max_page_size``.

    Returns:
        Validated limit value.

    """
    if limit is None:
        limit = settings.pagination.default_page_size
    return min(limit, settings.pagination.max_page_size)


# Type aliases for cleaner endpoint signatures
PageParams = Annotated[tuple[int, int], Depends(validate_page_size)]
LimitParam = Annotated[int, Depends(validate_limit)]


def paginate_list(
    items: list,
    page: int,
    page_size: int,
) -> dict:
    """Paginate a flat list into standard {data, pagination} format.

    For services that return all results without built-in pagination,
    this slices the list and builds the pagination metadata.

    Args:
        items: Full list of items.
        page: 1-based page number.
        page_size: Items per page.

    Returns:
        Dict with ``data`` and ``pagination`` keys.

    """
    total = len(items)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "data": items[start:end],
        "pagination": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
    }
