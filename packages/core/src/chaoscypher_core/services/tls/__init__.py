# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""TLS certificate management services."""

from chaoscypher_core.services.tls.service import generate_self_signed_cert


__all__ = ["generate_self_signed_cert"]
