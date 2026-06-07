// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useGraphStore: Owns the graphology Graph instance (single source of truth)
 *
 * Provides the graph ref and mutation helpers. Sigma reads from
 * this graph directly — mutations trigger automatic re-renders.
 */

import { useState, useCallback } from 'react';
import Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../types';

export function useGraphStore() {
  // Lazy state initializer creates the Graph instance once and stores it in
  // React state so render-time access doesn't trip the React Compiler `refs`
  // rule. The Graph itself is mutable and never replaced — we never call
  // setGraph — so this behaves identically to a ref.
  const [graph] = useState<Graph<NodeAttributes, EdgeAttributes>>(
    () => new Graph<NodeAttributes, EdgeAttributes>({ multi: false, type: 'directed' }),
  );

  const addNode = useCallback(
    (id: string, attrs: NodeAttributes) => {
      if (!graph.hasNode(id)) {
        graph.addNode(id, attrs);
      }
    },
    [graph],
  );

  const updateNode = useCallback(
    (id: string, attrs: Partial<NodeAttributes>) => {
      if (graph.hasNode(id)) {
        graph.updateNodeAttributes(id, (prev) => ({ ...prev, ...attrs }));
      }
    },
    [graph],
  );

  const dropNode = useCallback(
    (id: string) => {
      if (graph.hasNode(id)) {
        graph.dropNode(id);
      }
    },
    [graph],
  );

  const addEdge = useCallback(
    (id: string, source: string, target: string, attrs: EdgeAttributes) => {
      if (!graph.hasEdge(id) && graph.hasNode(source) && graph.hasNode(target)) {
        graph.addEdgeWithKey(id, source, target, attrs);
      }
    },
    [graph],
  );

  const updateEdge = useCallback(
    (id: string, attrs: Partial<EdgeAttributes>) => {
      if (graph.hasEdge(id)) {
        graph.updateEdgeAttributes(id, (prev) => ({ ...prev, ...attrs }));
      }
    },
    [graph],
  );

  const dropEdge = useCallback(
    (id: string) => {
      if (graph.hasEdge(id)) {
        graph.dropEdge(id);
      }
    },
    [graph],
  );

  const clear = useCallback(() => {
    graph.clear();
  }, [graph]);

  return {
    graph,
    addNode,
    updateNode,
    dropNode,
    addEdge,
    updateEdge,
    dropEdge,
    clear,
  };
}
