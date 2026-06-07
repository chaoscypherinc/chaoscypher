// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useSearchHighlight: Search and template filtering for sigma.
 *
 * Produces highlightedNodeIds and hiddenNodeIds sets consumed by
 * sigma nodeReducer/edgeReducer (in GraphCanvasContent).
 */

import { useState, useCallback, useMemo } from 'react';
import type Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../types';

interface UseSearchHighlightProps {
  graph: Graph<NodeAttributes, EdgeAttributes>;
  templateFilters: string[];
}

export function useSearchHighlight({ graph, templateFilters }: UseSearchHighlightProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [highlightedNodeIds, setHighlightedNodeIds] = useState<Set<string>>(new Set());

  const handleSearch = useCallback(
    (query: string) => {
      setSearchQuery(query);

      if (!query.trim()) {
        setHighlightedNodeIds(new Set());
        return;
      }

      const searchTerm = query.toLowerCase().trim();
      const matchingIds = new Set<string>();

      graph.forEachNode((id, attrs) => {
        // Search in title
        if (attrs.title?.toLowerCase().includes(searchTerm)) {
          matchingIds.add(id);
          return;
        }

        // Search in property values
        if (attrs.content && typeof attrs.content === 'object') {
          for (const value of Object.values(attrs.content)) {
            if (value == null) continue;
            if (String(value).toLowerCase().includes(searchTerm)) {
              matchingIds.add(id);
              return;
            }
          }
        }
      });

      setHighlightedNodeIds(matchingIds);
    },
    [graph],
  );

  // Set of node IDs hidden by template filters
  const hiddenNodeIds = useMemo(() => {
    if (templateFilters.length === 0) return new Set<string>();

    const hidden = new Set<string>();
    graph.forEachNode((id, attrs) => {
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
