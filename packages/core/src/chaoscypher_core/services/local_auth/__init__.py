# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Single-user local auth primitives (credentials file + session cookies + API keys)."""

from chaoscypher_core.services.local_auth.api_keys import (
    API_KEY_PREFIX,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)
from chaoscypher_core.services.local_auth.credentials import (
    ApiKeyRecord,
    CredentialsData,
    CredentialsFile,
    UserRecord,
)
from chaoscypher_core.services.local_auth.errors import (
    ApiKeyNotFound,
    CorruptCredentialsFile,
    CredentialsNotInitialized,
    InvalidPassword,
    InvalidSessionCookie,
    LocalAuthError,
    UsernameMismatch,
)
from chaoscypher_core.services.local_auth.session import (
    SessionPayload,
    decode_session,
    encode_session,
)


__all__ = [
    "API_KEY_PREFIX",
    "ApiKeyNotFound",
    "ApiKeyRecord",
    "CorruptCredentialsFile",
    "CredentialsData",
    "CredentialsFile",
    "CredentialsNotInitialized",
    "InvalidPassword",
    "InvalidSessionCookie",
    "LocalAuthError",
    "SessionPayload",
    "UserRecord",
    "UsernameMismatch",
    "decode_session",
    "encode_session",
    "generate_api_key",
    "hash_api_key",
    "verify_api_key",
]
