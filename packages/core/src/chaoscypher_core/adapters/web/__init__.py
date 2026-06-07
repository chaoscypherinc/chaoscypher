# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Web Adapters - External Web Service Integrations.

Adapters for interacting with external web services.

Example:
    from chaoscypher_core.adapters.web import WebScraper

"""

from chaoscypher_core.adapters.web.search import FetchResult, WebScraper


__all__ = ["FetchResult", "WebScraper"]
