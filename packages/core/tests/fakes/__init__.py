# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Reusable test fakes for in-process pipeline coverage.

Import the concrete fakes from their submodules:

    from tests.fakes.llm import FakeLLMProvider, LLMResponseStrategy

The embedding fake is shared with the CLI tests, so it lives in
``chaoscypher_core.testing.embedding`` (``FakeEmbeddingProvider``).

See the mocked pipeline test fixtures
for the why-and-what.
"""

# Submodules expose their own __all__; this package is a namespace only.
__all__: list[str] = []
