# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool Handler Classes.

Handler classes for different tool categories:
- node_handlers.py: Node CRUD operations
- edge_handlers.py: Edge operations
- template_handlers.py: Template operations
- analytics_handlers.py: Graph analytics
- external_handlers.py: Research operations
- summarize_handlers.py: Document summarization
- graphrag_handlers.py: GraphRAG-enhanced retrieval
- decorators.py: Shared error-handling decorators

Used by ToolExecutorService for strategy pattern delegation.
"""

from chaoscypher_core.services.workflows.tools.engine.handlers.analytics_handlers import (
    AnalyticsToolHandlers,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.decorators import tool_handler
from chaoscypher_core.services.workflows.tools.engine.handlers.edge_handlers import EdgeToolHandlers
from chaoscypher_core.services.workflows.tools.engine.handlers.external_handlers import (
    ExternalToolHandlers,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.node_handlers import NodeToolHandlers
from chaoscypher_core.services.workflows.tools.engine.handlers.summarize_handlers import (
    SummarizeToolHandlers,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.template_handlers import (
    TemplateToolHandlers,
)


__all__ = [
    "AnalyticsToolHandlers",
    "EdgeToolHandlers",
    "ExternalToolHandlers",
    "GraphRAGToolHandlers",
    "NodeToolHandlers",
    "SummarizeToolHandlers",
    "TemplateToolHandlers",
    "tool_handler",
]
