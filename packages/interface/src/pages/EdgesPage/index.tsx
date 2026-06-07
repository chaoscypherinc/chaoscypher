// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useCallback, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router';
import {
  Box,
  Typography,
  Button,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import LinkIcon from '@mui/icons-material/Link';
import { edgeApi } from '../../services/api/edges';
import { nodeApi } from '../../services/api/nodes';
import { templateApi } from '../../services/api/templates';
import { settingsApi } from '../../services/api/settings';
import type { Edge, Node, Template, EdgeCreateRequest } from '../../types';
import { LoadingState } from '../../components/LoadingState';
import { EmptyState } from '../../components/EmptyState';
import { filterNonSystemTemplates } from '../../constants/templates';
import { useCRUDPage } from '../../hooks/useCRUDPage';
import { useSort } from '../../hooks/useSort';
import { useNotification } from '../../contexts/useNotification';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { usePropertyEditor } from '../../hooks/usePropertyEditor';
import ConfirmDialog from '../../components/ConfirmDialog';
import { EdgeTable } from './EdgeTable';
import { EdgeFormDialog } from './EdgeFormDialog';
import { logger } from '../../utils/logger';

type EdgeSortField = 'label' | 'source' | 'target';

const PAGE_SIZE = 50;

export default function EdgesPage() {
  const navigate = useNavigate();
  const { notify } = useNotification();

  // Supplementary data
  const [nodes, setNodes] = useState<Node[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [allTemplates, setAllTemplates] = useState<Template[]>([]);

  // Pagination state
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);


  // Form state
  const [formData, setFormData] = useState<Partial<EdgeCreateRequest>>({
    template_id: '',
    source_node_id: '',
    target_node_id: '',
    label: '',
    properties: {},
  });
  const propEditor = usePropertyEditor(formData.properties || {});
  const deleteDialog = useConfirmDialog<string>();

  // Sorting via shared hook
  const { sortField, sortDirection, toggleSort } = useSort<EdgeSortField>('label');

  // Node lookup map
  const nodeMap = useMemo(() => new Map(nodes.map(n => [n.id, n])), [nodes]);
  const getNodeLabel = useCallback((nodeId: string) => nodeMap.get(nodeId)?.label || nodeId, [nodeMap]);

  /** Get the template object + fallback id for a node by its ID. */
  const getNodeTemplate = useCallback(
    (nodeId: string): { template: Template | undefined; fallbackId: string } => {
      const node = nodeMap.get(nodeId);
      const template = node ? allTemplates.find(t => t.id === node.template_id) : undefined;
      return { template, fallbackId: node?.template_id || nodeId };
    },
    [nodeMap, allTemplates],
  );


  // Fetch edges for current page + supplementary data on first load
  const supplementaryLoaded = useRef(false);

  const loadDataFn = useCallback(async () => {
    // Supplementary data loads in parallel on first call only
    const supplementaryPromise = !supplementaryLoaded.current
      ? Promise.all([
          nodeApi.list({ minimal: true }),
          templateApi.list('edge'),
          templateApi.list(),
          settingsApi.get(),
        ])
      : null;

    const edgesResponse = await edgeApi.listPaginated(page, PAGE_SIZE, { minimal: true });

    if (supplementaryPromise) {
      const [nodesData, edgeTemplatesData, allTemplatesData] = await supplementaryPromise;
      setNodes(nodesData);
      setTemplates(filterNonSystemTemplates(edgeTemplatesData));
      setAllTemplates(allTemplatesData);
      supplementaryLoaded.current = true;
    }

    setTotal(edgesResponse.pagination.total);
    return edgesResponse.data;
  }, [page]);

  // CRUD hook for shared state management
  const {
    data: firstPageEdges,
    loading,
    selectedIds: selectedEdges,
    dialogOpen,
    editingEntity: editingEdge,
    handleSelectAll,
    handleSelectItem: handleSelectEdge,
    handleBulkDelete,
    confirmBulkDelete,
    cancelBulkDelete,
    bulkDeleteConfirm,
    handleCreate: openCreateDialog,
    handleEdit: openEditDialog,
    handleCloseDialog,
    loadData: reloadData,
    ProgressDialog,
  } = useCRUDPage<Edge>({
    entityName: 'edges',
    entityDisplayName: 'Relationship',
    loadDataFn,
  });

  // Current page data
  const allEdges = firstPageEdges;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  const handlePageChange = (newPage: number) => {
    setPage(newPage);
  };

  // Sorted edges for display
  const sortedEdges = useMemo(() => {
    return [...allEdges].sort((a, b) => {
      let aVal: string;
      let bVal: string;

      switch (sortField) {
        case 'label':
          aVal = (a.label || '').toLowerCase();
          bVal = (b.label || '').toLowerCase();
          break;
        case 'source':
          aVal = getNodeLabel(a.source_node_id).toLowerCase();
          bVal = getNodeLabel(b.source_node_id).toLowerCase();
          break;
        case 'target':
          aVal = getNodeLabel(a.target_node_id).toLowerCase();
          bVal = getNodeLabel(b.target_node_id).toLowerCase();
          break;
        default:
          return 0;
      }

      return sortDirection === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    });
  }, [allEdges, sortField, sortDirection, getNodeLabel]);

  // Custom create: initialize form then open dialog
  const handleCreate = () => {
    setFormData({
      template_id: templates[0]?.id || '',
      source_node_id: '',
      target_node_id: '',
      label: '',
      properties: {},
    });
    propEditor.setProperties({});
    openCreateDialog();
  };

  // Custom edit: populate form then open dialog
  const handleEdit = (edge: Edge) => {
    setFormData({
      template_id: edge.template_id,
      source_node_id: edge.source_node_id,
      target_node_id: edge.target_node_id,
      label: edge.label,
      properties: edge.properties,
    });
    propEditor.setProperties(edge.properties || {});
    openEditDialog(edge);
  };

  const handleDelete = (id: string) => {
    deleteDialog.open(id);
  };

  const handleConfirmDelete = async () => {
    await deleteDialog.confirm(async () => {
      try {
        await edgeApi.delete(deleteDialog.data!);
        setTotal(prev => prev - 1);
        await reloadData();
      } catch (error) {
        logger.error('Failed to delete edge:', error);
        notify('Failed to delete relationship', 'error');
      }
    });
  };

  const handleSave = async () => {
    try {
      if (!formData.source_node_id || !formData.target_node_id) {
        notify('Please select both source and target entities', 'warning');
        return;
      }

      if (editingEdge) {
        await edgeApi.update(editingEdge.id, {
          label: formData.label,
          properties: propEditor.properties,
        });
      } else {
        await edgeApi.create({ ...formData, properties: propEditor.properties } as EdgeCreateRequest);
      }
      handleCloseDialog();
      reloadData();
    } catch (error) {
      logger.error('Failed to save edge:', error);
      notify('Failed to save relationship', 'error');
    }
  };

  if (loading) {
    return <LoadingState message="Loading relationships..." fullPage />;
  }

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          flexWrap: "wrap",
          gap: 2,
          justifyContent: "space-between",
          alignItems: { xs: "flex-start", sm: "center" },
          mb: 3
        }}>
        <Box>
          <Typography variant="h4">Relationships</Typography>
          <Typography variant="caption" color="textSecondary">
            Showing {allEdges.length} of {total} relationships
            {total > PAGE_SIZE && ` (page ${page} of ${totalPages})`}
          </Typography>
        </Box>
        <Box
          sx={{
            display: "flex",
            flexWrap: "wrap",
            gap: 2
          }}>
          {selectedEdges.size > 0 && (
            <Button
              variant="outlined"
              color="error"
              startIcon={<DeleteSweepIcon />}
              onClick={handleBulkDelete}
            >
              Delete {selectedEdges.size} Selected
            </Button>
          )}
          <Button
            variant="outlined"
            startIcon={<AddIcon />}
            onClick={handleCreate}
            disabled={templates.length === 0 || nodes.length < 2}
          >
            Create Relationship
          </Button>
        </Box>
      </Box>
      {templates.length === 0 ? (
        <EmptyState
          message="No relationship templates available"
          secondaryMessage="Create a relationship template first before creating relationships."
          icon={<LinkIcon />}
        />
      ) : nodes.length < 2 ? (
        <EmptyState
          message="Need at least 2 entities"
          secondaryMessage="You need at least 2 entities to create relationships. Create some entities first."
          icon={<LinkIcon />}
        />
      ) : allEdges.length === 0 ? (
        <EmptyState
          message="No relationships yet"
          secondaryMessage="Click 'Create Relationship' to connect entities together."
          actionLabel="Create Relationship"
          onAction={handleCreate}
          icon={<LinkIcon />}
        />
      ) : (
        <EdgeTable
          edges={sortedEdges}
          allEdges={allEdges}
          templates={templates}
          selectedEdges={selectedEdges}
          sortField={sortField}
          sortDirection={sortDirection}
          page={page}
          totalPages={totalPages}
          total={total}
          pageSize={PAGE_SIZE}
          getNodeLabel={getNodeLabel}
          getNodeTemplate={getNodeTemplate}
          onToggleSort={toggleSort}
          onSelectAll={handleSelectAll}
          onSelectEdge={handleSelectEdge}
          onEdit={handleEdit}
          onDelete={handleDelete}
          onNavigate={(edgeId) => navigate(`/edges/${edgeId}`)}
          onPageChange={handlePageChange}
        />
      )}
      <EdgeFormDialog
        open={dialogOpen}
        editing={editingEdge}
        formData={formData}
        templates={templates}
        nodes={nodes}
        propEditor={propEditor}
        onFormChange={setFormData}
        onSave={handleSave}
        onClose={handleCloseDialog}
      />
      <ConfirmDialog
        open={deleteDialog.isOpen}
        title="Confirm Delete"
        message="Are you sure you want to delete this relationship?"
        onConfirm={handleConfirmDelete}
        onCancel={deleteDialog.close}
      />
      <ConfirmDialog
        open={bulkDeleteConfirm.open}
        title="Confirm Bulk Delete"
        message={bulkDeleteConfirm.message}
        onConfirm={confirmBulkDelete}
        onCancel={cancelBulkDelete}
      />
      <ProgressDialog />
    </Box>
  );
}
