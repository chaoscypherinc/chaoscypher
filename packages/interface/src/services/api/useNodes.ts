// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for nodes.
 *
 * Introduced with the EdgeDetailPage migration off raw fetch+useState
 * (`useNode` as a dependent single-item query). Extended with the
 * NodeDetailPage migration: `useUpdateNode` / `useDeleteNode` are the detail
 * page mutations (invalidate the node + the nodes list), and the read hooks
 * `useNodeConnections` (infinite, sortable) / `useNodeCitations` (lazy) /
 * `useNodeSourceImages` (derived from a source-document id) back the detail
 * page's Connections / Sources tabs and its source-image sidebar.
 *
 * `useNode` accepts a nullable id and stays disabled until one is present, so
 * it composes cleanly as a dependent query (e.g. an edge's source/target node
 * fetched after the edge resolves). The other read hooks follow the same
 * nullable-id + `enabled` gating so callers can defer them (lazy tabs).
 */

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';

import { nodeApi } from './nodes';
import { apiClient } from './client';
import type {
  Node,
  Citation,
  CitationListResponse,
  ConnectedNode,
  ConnectionsResponse,
} from '../../types';

const NODES_QUERY_KEY = ['nodes'] as const;

function nodeQueryKey(nodeId: string) {
  return ['node', nodeId] as const;
}

function nodeConnectionsQueryKey(nodeId: string, sortBy: string) {
  return ['node', nodeId, 'connections', sortBy] as const;
}

function nodeCitationsQueryKey(nodeId: string) {
  return ['node', nodeId, 'citations'] as const;
}

function nodeSourceImagesQueryKey(sourceDocId: string) {
  return ['node', 'source-images', sourceDocId] as const;
}

export function useNode(nodeId: string | null | undefined) {
  return useQuery<Node>({
    queryKey: nodeId ? nodeQueryKey(nodeId) : ['node', 'none'],
    queryFn: () => nodeApi.get(nodeId as string),
    enabled: nodeId != null,
  });
}

export function useUpdateNode() {
  const qc = useQueryClient();
  return useMutation<Node, Error, { id: string; updates: Partial<Node> }>({
    mutationFn: ({ id, updates }) => nodeApi.update(id, updates),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: nodeQueryKey(data.id) });
      void qc.invalidateQueries({ queryKey: NODES_QUERY_KEY });
    },
  });
}

export function useDeleteNode() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id) => nodeApi.delete(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: NODES_QUERY_KEY });
    },
  });
}

interface NodeConnectionsPage {
  items: ConnectedNode[];
  total: number;
  hasNext: boolean;
  page: number;
}

/**
 * Infinite, sortable list of a node's connected entities. The Connections tab
 * appends pages via `fetchNextPage`; changing `sortBy` swaps the query key so
 * the list resets to page 1. Stays disabled until a node id is present.
 */
export function useNodeConnections(
  nodeId: string | null | undefined,
  sortBy: string,
) {
  return useInfiniteQuery<NodeConnectionsPage>({
    queryKey: nodeId
      ? nodeConnectionsQueryKey(nodeId, sortBy)
      : ['node', 'none', 'connections', sortBy],
    queryFn: async ({ pageParam }) => {
      const page = (pageParam as number) ?? 1;
      const response: ConnectionsResponse = await nodeApi.getConnections(
        nodeId as string,
        sortBy,
        page,
      );
      return {
        items: response.data,
        total: response.pagination.total,
        hasNext: response.pagination.has_next,
        page,
      };
    },
    initialPageParam: 1,
    getNextPageParam: (lastPage) => (lastPage.hasNext ? lastPage.page + 1 : undefined),
    enabled: nodeId != null,
  });
}

/**
 * A node's source citations. Disabled by default (`enabled` flag) so the
 * detail page can defer the fetch until the Sources tab is opened.
 */
export function useNodeCitations(
  nodeId: string | null | undefined,
  enabled: boolean,
) {
  return useQuery<{ items: Citation[]; total: number }>({
    queryKey: nodeId ? nodeCitationsQueryKey(nodeId) : ['node', 'none', 'citations'],
    queryFn: async () => {
      const response: CitationListResponse = await nodeApi.getCitations(nodeId as string);
      return { items: response.data, total: response.pagination.total };
    },
    enabled: nodeId != null && enabled,
  });
}

interface SourceImage {
  filename: string;
  url: string;
}

/**
 * Images attached to the node's originating source document. Derived from the
 * entity payload's `source_document_id`; disabled until that id is known.
 * Mirrors the legacy detail-page behaviour: the raw ``{filename, url}[]`` from
 * the directory-scan endpoint is returned as-is (no API_BASE rewrite — the
 * sidebar consumes the bare urls), and a missing/404 source surfaces as a
 * query error the caller treats as "no images" by falling back to an empty
 * list.
 */
export function useNodeSourceImages(sourceDocId: string | null | undefined) {
  return useQuery<SourceImage[]>({
    queryKey: sourceDocId
      ? nodeSourceImagesQueryKey(sourceDocId)
      : ['node', 'source-images', 'none'],
    queryFn: async () => {
      const response = await apiClient.get<SourceImage[]>(
        `/sources/${sourceDocId as string}/images`,
      );
      return response.data;
    },
    enabled: sourceDocId != null,
  });
}
