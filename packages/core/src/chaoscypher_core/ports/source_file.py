# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source file port — retired.

LensSourceProtocol was removed in ADR-0001 (Lenses retirement).
Callers now use:
  - SourceStorageProtocol (ports/storage_sources.py) for source CRUD and
    lifecycle stage transitions (start_commit, complete_commit,
    update_step_progress, get_source).
  - EntityEmbeddingStorageProtocol (ports/storage_embeddings.py) for
    get_entity_embeddings.
"""
