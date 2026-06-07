# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Lexicon Service - API client and service layer for ChaosCypher Lexicon.

Provides a framework-agnostic client and high-level service for interacting
with the ChaosCypher Lexicon package registry. Works in CLI, Cortex, and Neuron.

Components:
- LexiconClient: Low-level HTTP client for lexicon API
- LexiconService: High-level service with credential management
- Storage adapters: FileLexiconStorage (CLI), DictLexiconStorage (Cortex)
- Pydantic models: Type-safe request/response models

Low-level client example:
    from chaoscypher_core.services.lexicon import LexiconClient, AuthConfig

    async with LexiconClient() as client:
        await client.login("username", "password")
        results = await client.search("medical")

High-level service example:
    from chaoscypher_core.services.lexicon import (
        LexiconService,
        FileLexiconStorage,
        LexiconSearchRequest,
    )

    storage = FileLexiconStorage()
    service = LexiconService(storage)

    # Search packages
    results = await service.search(LexiconSearchRequest(query="medical"))
    for pkg in results.packages:
        print(f"{pkg.name} v{pkg.version}")
"""

# Client (low-level)
from chaoscypher_core.services.lexicon.client import (
    AuthConfig,
    DeviceCodeResponse,
    LexiconClient,
    LexiconClientError,
    PackageInfo,
)

# Models (Pydantic)
from chaoscypher_core.services.lexicon.models import (
    LexiconAuthConfig,
    LexiconAuthResponse,
    LexiconAuthStatus,
    LexiconDeviceCodeRequest,
    LexiconDeviceCodeResponse,
    LexiconDownloadRequest,
    LexiconLoginRequest,
    LexiconPackageInfo,
    LexiconPollRequest,
    LexiconSearchRequest,
    LexiconSearchResponse,
    LexiconTokenRequest,
    LexiconUploadRequest,
)

# Service
from chaoscypher_core.services.lexicon.service import LexiconService

# Storage
from chaoscypher_core.services.lexicon.storage import (
    DictLexiconStorage,
    FileLexiconStorage,
    LexiconCredentialStorage,
)


__all__ = [
    # Client (low-level dataclasses)
    "AuthConfig",
    "DeviceCodeResponse",
    # Storage
    "DictLexiconStorage",
    "FileLexiconStorage",
    # Models (Pydantic)
    "LexiconAuthConfig",
    "LexiconAuthResponse",
    "LexiconAuthStatus",
    "LexiconClient",
    "LexiconClientError",
    "LexiconCredentialStorage",
    "LexiconDeviceCodeRequest",
    "LexiconDeviceCodeResponse",
    "LexiconDownloadRequest",
    "LexiconLoginRequest",
    "LexiconPackageInfo",
    "LexiconPollRequest",
    "LexiconSearchRequest",
    "LexiconSearchResponse",
    # Service
    "LexiconService",
    "LexiconTokenRequest",
    "LexiconUploadRequest",
    "PackageInfo",
]
