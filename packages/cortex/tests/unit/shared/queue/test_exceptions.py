# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify QueueUnavailableError inherits from the Core exception hierarchy.

Reason: Cortex's chaoscypher_exception_handler keys on ChaosCypherException.
RuntimeError subclasses leak as generic HTTP 500s — see architecture review 2026-04-18.
"""

from chaoscypher_core.exceptions import ChaosCypherException, ExternalServiceError
from chaoscypher_core.queue.client import QueueUnavailableError


def test_queue_unavailable_error_inherits_from_external_service_error():
    assert issubclass(QueueUnavailableError, ExternalServiceError)
    assert issubclass(QueueUnavailableError, ChaosCypherException)
