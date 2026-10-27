// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useSearchHighlight: Search and template filtering for sigma.
 *
 * Produces highlightedNodeIds and hiddenNodeIds sets consumed by
 * sigma nodeReducer/edgeReducer (in GraphCanvasContent). Only real nodes
 * are searched and filtered; synthetic hubs are not data.
 */

import { useState, useCallback, useMemo } from 'react';
import type Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../types';
import { isSyntheticNode } from '../types';

interface UseSearchHighlightProps {
  graph: Graph<NodeAttributes, EdgeAttributes>;
  templateFilters: string[];
}

/**
 * Ids of the real nodes a search matches (title, then any property value),
 * in graph order. Empty for a blank query.
 */
export function searchMatches(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  query: string,
): string[] {
  const searchTerm = query.toLowerCase().trim();
  if (!searchTerm) return [];
  const matches: string[] = [];
  graph.forEachNode((id, attrs) => {
    if (isSyntheticNode(id)) return;
    if (attrs.title?.toLowerCase().includes(searchTerm)) {
      matches.push(id);
      return;
    }
    if (attrs.content && typeof attrs.content === 'object') {
      for (const value of Object.values(attrs.content)) {
        if (value == null) continue;
        if (String(value).toLowerCase().includes(searchTerm)) {
          matches.push(id);
          return;
        }
      }
    }
  });
  return matches;
}

export function useSearchHighlight({ graph, templateFilters }: UseSearchHighlightProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [highlightedNodeIds, setHighlightedNodeIds] = useState<Set<string>>(new Set());

  const handleSearch = useCallback(
    (query: string) => {
      setSearchQuery(query);
      setHighlightedNodeIds(new Set(searchMatches(graph, query)));
    },
    [graph],
  );

  // Set of node IDs hidden by template filters
  const hiddenNodeIds = useMemo(() => {
    if (templateFilters.length === 0) return new Set<string>();

    const hidden = new Set<string>();
    graph.forEachNode((id, attrs) => {
      if (isSyntheticNode(id)) return;
      if (!templateFilters.includes(attrs.templateId)) {
        hidden.add(id);
      }
    });
    return hidden;
  }, [graph, templateFilters]);

  const hasActiveSearch = searchQuery.trim().length > 0;

  return {
    handleSearch,
    highlightedNodeIds,
    hiddenNodeIds,
    hasActiveSearch,
  };
}
