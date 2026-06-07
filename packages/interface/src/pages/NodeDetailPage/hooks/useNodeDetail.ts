// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import {
  useNode,
  useNodeCitations,
  useNodeConnections,
  useNodeSourceImages,
  useUpdateNode,
  useDeleteNode,
} from '../../../services/api/useNodes';
import { useTemplate } from '../../../services/api/useTemplates';
import type { Node, Template, Citation, ConnectedNode } from '../../../types';
import { logger } from '../../../utils/logger';

interface UseNodeDetailState {
  entity: Node | null;
  template: Template | null;
  loading: boolean;
  error: string | null;
  editing: boolean;
  formData: Partial<Node>;
  activeTab: number;
  confirmDeleteOpen: boolean;
  sourceImages: { filename: string; url: string }[];
  expandedImage: string | null;
  citations: Citation[];
  citationsLoading: boolean;
  citationsTotal: number;
  connections: ConnectedNode[];
  connectionsLoading: boolean;
  connectionsTotal: number;
  connectionsSortBy: string;
  connectionsHasMore: boolean;
}

interface UseNodeDetailActions {
  setFormData: (data: Partial<Node>) => void;
  setActiveTab: (tab: number) => void;
  setConnectionsSortBy: (sortBy: string) => void;
  setExpandedImage: (url: string | null) => void;
  handleEdit: () => void;
  handleCancel: () => void;
  handleSave: () => Promise<void>;
  handleDelete: () => void;
  handleConfirmDelete: () => Promise<void>;
  closeConfirmDelete: () => void;
  loadConnections: (reset?: boolean) => Promise<void>;
}

/**
 * Encapsulates all data fetching and mutation state for the NodeDetailPage.
 * Returns `{ state, actions }` so the consuming component can destructure
 * either bundle without long prop lists.
 *
 * Server state runs through TanStack Query (`useNode`, the dependent
 * `useTemplate`, the infinite `useNodeConnections`, the lazy `useNodeCitations`,
 * and `useNodeSourceImages`); only genuine UI state (`editing`, `formData`,
 * `activeTab`, the delete-confirm dialog, the expanded image, and a transient
 * `actionError` for save/delete failures) stays in local `useState`. The
 * exposed `{ state, actions }` shape is unchanged from the legacy
 * fetch+useState implementation so the page and its tab components are
 * untouched.
 */
export function useNodeDetail(nodeId: string | undefined): {
  state: UseNodeDetailState;
  actions: UseNodeDetailActions;
} {
  const navigate = useNavigate();

  const [editing, setEditing] = useState(false);
  const [formData, setFormData] = useState<Partial<Node>>({});
  const [activeTab, setActiveTab] = useState(0);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [expandedImage, setExpandedImage] = useState<string | null>(null);
  const [connectionsSortBy, setConnectionsSortBy] = useState('edge_count');
  // Save/delete failures surface through the same `error` field the legacy
  // hook used; load failure is derived from the node query below.
  const [actionError, setActionError] = useState<string | null>(null);

  const nodeQuery = useNode(nodeId);
  const entity = nodeQuery.data ?? null;

  const updateNode = useUpdateNode();
  const deleteNode = useDeleteNode();

  // `formData` is only ever read while `editing` is true (the tabs render
  // `editing ? formData : entity`); `handleEdit` seeds it from the freshly
  // loaded entity, so no effect-driven sync is needed — matching the
  // EdgeDetailPage reference pattern.

  // Dependent template query — resolves a tick after the node, like the edge
  // page's template lookup.
  const templateQuery = useTemplate(entity?.template_id);
  const template = templateQuery.data ?? null;

  // Connections: infinite + sortable. The query key includes `connectionsSortBy`
  // so changing the sort resets to page 1 automatically (no manual reload).
  const connectionsQuery = useNodeConnections(nodeId, connectionsSortBy);
  const connections = useMemo(
    () => connectionsQuery.data?.pages.flatMap((p) => p.items) ?? [],
    [connectionsQuery.data],
  );
  const connectionsTotal = connectionsQuery.data?.pages[0]?.total ?? 0;
  const connectionsHasMore = connectionsQuery.hasNextPage ?? false;

  // Citations are loaded lazily once the Sources tab (index 2) is opened.
  const citationsQuery = useNodeCitations(nodeId, activeTab === 2);
  const citations = citationsQuery.data?.items ?? [];

  // Citation total: prefer the entity's pre-seeded count so the Statistics
  // sidebar and Sources tab badge are right before the tab is ever opened;
  // once citations are fetched, the response's total is authoritative.
  const citationsTotal =
    citationsQuery.data?.total ??
    (typeof entity?.citation_count === 'number' ? entity.citation_count : 0);

  // Source images derive from the entity's source document id.
  const sourceDocId =
    (entity?.properties?.source_document_id as string | undefined) ?? undefined;
  const sourceImagesQuery = useNodeSourceImages(sourceDocId ?? null);
  const sourceImages = sourceImagesQuery.data ?? [];

  const loadConnections = useCallback(
    async (reset: boolean = false) => {
      if (!nodeId) return;
      if (reset) {
        await connectionsQuery.refetch();
      } else {
        await connectionsQuery.fetchNextPage();
      }
    },
    [nodeId, connectionsQuery],
  );

  const handleEdit = useCallback(() => {
    setEditing(true);
    setFormData(entity || {});
  }, [entity]);

  const handleCancel = useCallback(() => {
    setEditing(false);
    setActionError(null);
    setFormData(entity || {});
  }, [entity]);

  const handleSave = useCallback(async () => {
    if (!nodeId) return;
    try {
      setActionError(null);
      await updateNode.mutateAsync({
        id: nodeId,
        updates: {
          label: formData.label,
          properties: formData.properties,
        },
      });
      setEditing(false);
    } catch (err) {
      logger.error('Failed to save entity:', err);
      setActionError('Failed to save entity');
    }
  }, [nodeId, formData.label, formData.properties, updateNode]);

  const handleDelete = useCallback(() => {
    if (!nodeId) return;
    setConfirmDeleteOpen(true);
  }, [nodeId]);

  const handleConfirmDelete = useCallback(async () => {
    setConfirmDeleteOpen(false);
    if (!nodeId) return;
    try {
      setActionError(null);
      await deleteNode.mutateAsync(nodeId);
      navigate('/nodes');
    } catch (err) {
      logger.error('Failed to delete entity:', err);
      setActionError('Failed to delete entity');
    }
  }, [nodeId, navigate, deleteNode]);

  const closeConfirmDelete = useCallback(() => setConfirmDeleteOpen(false), []);

  // Load error drives the full error page; action (save/delete) errors render
  // as the inline alert. Both flow through the single `error` field to keep
  // the legacy `{ state }` contract intact.
  const error = actionError ?? (nodeQuery.isError ? 'Failed to load entity' : null);

  return {
    state: {
      entity,
      template,
      loading: nodeQuery.isLoading,
      error,
      editing,
      formData,
      activeTab,
      confirmDeleteOpen,
      sourceImages,
      expandedImage,
      citations,
      citationsLoading: citationsQuery.isLoading && activeTab === 2,
      citationsTotal,
      connections,
      connectionsLoading: connectionsQuery.isFetching,
      connectionsTotal,
      connectionsSortBy,
      connectionsHasMore,
    },
    actions: {
      setFormData,
      setActiveTab,
      setConnectionsSortBy,
      setExpandedImage,
      handleEdit,
      handleCancel,
      handleSave,
      handleDelete,
      handleConfirmDelete,
      closeConfirmDelete,
      loadConnections,
    },
  };
}
