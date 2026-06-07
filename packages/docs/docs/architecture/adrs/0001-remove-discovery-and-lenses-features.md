---
title: "ADR-0001: Remove Discovery and Lenses Features"
description: Decision record for removing the Knowledge Weaver discovery feature and Lenses views from Chaos Cypher to reduce maintenance scope.
---

# 0001. Remove Discovery and Lenses Features

Date: 2026-02-05

## Status

Accepted

## Context

Chaos Cypher includes two AI-powered features for enriching the knowledge graph:

1. **Discovery (Knowledge Weaver)**: Uses vector similarity + LLM inference to find non-obvious relationships between existing graph nodes and propose new edges.

2. **Lenses**: Builds filtered "views" on the knowledge graph using AI-generated rules.

Both features share infrastructure:
- `SuggestionServiceBase` - Abstract base class for session/suggestion management
- `SessionRepository` protocol - Persistence interface for sessions and suggestions
- Database tables: `DiscoverySession`, `DiscoverySuggestion`, `LensSession`, `LensSuggestion`

**Problem**: These features have not been tested in months and have zero dedicated test coverage. The codebase has undergone major changes since they were added. The current priority is establishing a solid, well-tested data extraction pipeline (upload → chunk → extract entities → commit to graph) before adding advanced AI-powered enrichment features.

**Size**: ~3,500 lines across 25+ files, touching 89 files via imports.

## Decision

Remove both Discovery and Lenses features entirely, including:
- Core services and engines
- Storage adapters and protocols
- SQLModel entities
- Cortex API endpoints
- Frontend components
- Shared infrastructure (SuggestionServiceBase, SessionRepository)

Preserve key architectural concepts in this ADR for future reference.

## Rationale

### Why Remove (Not Stub or Disable)

1. **Dead code is technical debt** - Unused code still requires maintenance, creates confusion, and can mask real issues
2. **No tests** - Zero dedicated test files means we can't verify correctness after changes
3. **Simpler codebase** - 3,500 fewer lines to maintain, read, and understand
4. **Clean rebuild opportunity** - When ready, rebuild with proper TDD, real data to test against, and clearer requirements

### Why Not Keep for Later

1. **Architecture can be documented** (see below) - Don't need code to remember patterns
2. **Implementation was possibly over-engineered** - Duplicate classes, complex batch processing
3. **Requirements will be clearer later** - After extraction pipeline is stable and users provide feedback

### Alternatives Considered

| Option | Pros | Cons |
|--------|------|------|
| Keep as-is | No work now | Technical debt, untested, confusing |
| Disable via feature flag | Reversible | Still maintaining dead code |
| Move to separate branch | Git history preserved | Stale code, merge conflicts later |
| **Remove + document (chosen)** | Clean codebase, patterns preserved | Must rebuild from scratch |

## Architectural Concepts Worth Preserving

### 1. Session-Based Workflow Pattern

AI analysis tasks follow a session lifecycle:

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running
    running --> completed
    running --> failed

    classDef default fill:#12121e,stroke:#7b2ff7,color:#e0e0f0
    class queued,running,completed,failed default
```

**Key fields**: session_id, status, progress (0-100), current_batch, total_batches, nodes_processed, suggestions_found, error

**Why it worked**: Enables async task tracking, progress reporting, and resumability.

### 2. Suggestion Approval Workflow

```mermaid
stateDiagram-v2
    [*] --> pending
    pending --> approved
    approved --> committed
    pending --> rejected

    classDef default fill:#12121e,stroke:#7b2ff7,color:#e0e0f0
    class pending,approved,committed,rejected default
```

Suggestions include:
- `source_node_id`, `target_node_id` - The entities to connect
- `relationship_type` - Proposed edge type (e.g., "works_for", "located_in")
- `confidence` - AI confidence score (0.0-1.0)
- `reasoning` - Natural language explanation

**Bulk operations**: Approve/reject by confidence threshold (e.g., approve all > 0.8)

### 3. Vector Similarity + LLM Inference Pipeline

```mermaid
graph LR
    A["Filter nodes by template type"] --> B["Vector similarity search (top-k)"]
    B --> C["LLM inference (relationship)"]
    C --> D["Store suggestion with confidence"]

    style A fill:#12121e,stroke:#00fff0,color:#e0e0f0
    style B fill:#12121e,stroke:#7b2ff7,color:#e0e0f0
    style C fill:#12121e,stroke:#ff2d95,color:#e0e0f0
    style D fill:#12121e,stroke:#ff6d00,color:#e0e0f0
```

**Key insight**: Vector similarity finds *candidates* efficiently, LLM determines *relationship type* with reasoning. This two-stage approach balances speed and accuracy.

### 4. Protocol-Based Storage Abstraction

```python
class SessionRepository(Protocol):
    def create_session(self, session_id: str, ...) -> None: ...
    def get_session(self, session_id: str, ...) -> dict | None: ...
    def update_session(self, session_id: str, ...) -> None: ...
    def create_suggestion(self, session_id: str, ...) -> str: ...
    def get_suggestions(self, ...) -> list[dict]: ...
    # etc.
```

**Why it worked**: Decouples business logic from storage. Easy to test with mocks, swap backends.

### 5. Shared Base Class Pattern

```python
class SuggestionServiceBase:
    """Shared session/suggestion management for Discovery and Lenses."""

    def __init__(self, database_name: str, session_type: str, session_repository: SessionRepository):
        self.session_type = session_type  # "edge_discovery" or "lens_build"
        # ...

    # Common operations: create_session, update_session, create_suggestion, etc.
```

**Why it worked**: DRY for common CRUD operations. Subclasses implement domain-specific analysis logic.

## Future Rebuild Recommendations

When ready to rebuild these features:

1. **Start with tests** - Define expected behavior before implementation
2. **Use real data** - Test against actual extracted entities, not mocks
3. **Simpler first** - Start with single-node discovery, add batch processing only if needed
4. **CLI support** - Add CLI commands alongside API for easier testing
5. **Incremental rollout** - Ship one feature (Discovery) before adding another (Lenses)

## Consequences

### Positive

- **~3,500 lines removed** - Simpler, more maintainable codebase
- **Clearer focus** - Team can concentrate on core extraction pipeline
- **No untested code** - Reduces risk of hidden bugs
- **Clean architecture preserved** - This ADR captures key patterns

### Negative

- **Feature loss** - No AI-powered relationship discovery until rebuilt
- **Rebuild effort** - Will need to reimplement from scratch (estimated: 1-2 weeks when prioritized)
- **Shared infrastructure removed** - If other features later need session/suggestion patterns, must rebuild

### Neutral

- **Core extraction unaffected** - Upload, chunking, entity extraction, graph commit all remain
- **Search unaffected** - RAG/vector search continues to work
- **Git history preserved** - Old implementation remains in git if needed for reference

## Files to Remove

### Core Package (`packages/core/src/chaoscypher_core/`)

```
services/discovery/                    # Entire directory (~2,100 lines)
services/lenses/                       # Entire directory
adapters/sqlite/mixins/discovery.py    # Storage mixin
adapters/sqlite/models.py              # DiscoverySession, DiscoverySuggestion, LensSession, LensSuggestion
ports/storage.py                       # DiscoveryStorageProtocol section
ports/session.py                       # SessionRepository protocol
utils/suggestion_service_base.py       # Shared base class
models.py                              # DiscoveryFilterParams, DiscoveryTaskStatus, Suggestion, SuggestionCreate
```

### Cortex Package (`packages/cortex/src/chaoscypher_cortex/`)

```
features/discovery/                    # Entire directory
features/lenses/                       # Entire directory
```

### Neuron Package (`packages/neuron/src/chaoscypher_neuron/`)

```
operations_worker.py                   # Remove discovery_analysis handler
```

### Interface Package (`packages/interface/src/`)

```
components/discovery/                  # Entire directory
pages/DiscoveryPage.tsx
hooks/useDiscovery.ts
services/api/discovery.ts
# Plus references in App.tsx, Layout.tsx, etc.
```

## References

- Original Discovery implementation: git history pre-2026-02-05
- SuggestionServiceBase pattern: `packages/core/src/chaoscypher_core/utils/suggestion_service_base.py` (before removal)
