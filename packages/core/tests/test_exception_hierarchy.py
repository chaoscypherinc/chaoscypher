# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify every Core custom exception inherits from ChaosCypherException.

Reason: Cortex's chaoscypher_exception_handler keys on ChaosCypherException.
Orphan exceptions leak as generic HTTP 500s — see architecture review 2026-04-18.
"""

import pytest

from chaoscypher_core.exceptions import (
    ChaosCypherException,
    ExternalServiceError,
    ValidationError,
)
from chaoscypher_core.services.compose.merger import MergerError
from chaoscypher_core.services.compose.resolver import ResolverError
from chaoscypher_core.services.compose.service import ComposeError
from chaoscypher_core.services.lexicon.client import LexiconClientError
from chaoscypher_core.services.package.archive.extract import ArchiveSecurityError
from chaoscypher_core.services.sources.loaders.archive.exceptions import ArchiveLoaderError


@pytest.mark.parametrize(
    ("exc_cls", "expected_base"),
    [
        (ArchiveLoaderError, ChaosCypherException),
        (MergerError, ChaosCypherException),
        (ResolverError, ChaosCypherException),
        (ComposeError, ChaosCypherException),
        (LexiconClientError, ExternalServiceError),
        (ArchiveSecurityError, ValidationError),
    ],
)
def test_exception_inherits_from_expected_base(exc_cls, expected_base):
    assert issubclass(exc_cls, expected_base), (
        f"{exc_cls.__name__} must inherit from {expected_base.__name__}"
    )
    assert issubclass(exc_cls, ChaosCypherException)
