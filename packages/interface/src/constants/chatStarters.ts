// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/** Starter prompts shown in the omnibar chat mode and on the empty chat page.
 *
 * Deliberately graph-structural, dataset-agnostic questions that vector-only
 * RAG cannot answer — each flexes centrality, traversal, or community tools
 * so a first-time user immediately sees what GraphRAG adds.
 */
export const CHAT_STARTERS = [
  {
    icon: '🔗',
    label: 'Most connected entities — and how the top two relate',
    prompt:
      'Which entities are most connected in this graph, and how are the top two related to each other?',
  },
  {
    icon: '🧭',
    label: 'Trace the path between two key entities',
    prompt:
      'Pick two important entities from this graph and trace the connection path between them, explaining each link.',
  },
  {
    icon: '🕸️',
    label: 'What clusters exist, and who bridges them?',
    prompt:
      'What clusters or communities exist in this data, and which entities act as bridges between them?',
  },
];
