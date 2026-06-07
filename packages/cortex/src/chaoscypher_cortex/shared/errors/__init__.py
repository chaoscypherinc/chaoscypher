# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Branded HTML error pages + content-negotiated response helpers."""

from chaoscypher_cortex.shared.errors.branded_page import (
    negotiated_error_response,
    render_branded_error,
)


__all__ = ["negotiated_error_response", "render_branded_error"]
