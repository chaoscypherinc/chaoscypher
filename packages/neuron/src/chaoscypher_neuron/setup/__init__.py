# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Setup modules for the Neuron worker.

Provides initialization routines for shared context, LLM queue handlers,
and Operations queue handlers.  Each setup phase is a standalone async
function that populates or reads from the shared context dictionary.
"""

from .llm_handlers import setup_llm_handlers
from .ops_handlers import setup_operations_handlers
from .shared import setup_shared


__all__ = [
    "setup_llm_handlers",
    "setup_operations_handlers",
    "setup_shared",
]
