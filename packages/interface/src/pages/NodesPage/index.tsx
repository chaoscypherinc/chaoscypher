// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router';
import {
  Box,
  Typography,
  Button,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import InventoryIcon from '@mui/icons-material/Inventory';
import { nodeApi } from '../../services/api/nodes';
import { templateApi } from '../../services/api/templates';
import { settingsApi } from '../../services/api/settings';
import type { Node, Template, NodeCreateRequest } from '../../types';
import { LoadingState } from '../../components/LoadingState';
import { EmptyState } from '../../components/EmptyState';
import { filterKnowledgeNodes, filterNonSystemTemplates } from '../../constants/templates';
import { useCRUDPage } from '../../hooks/useCRUDPage';
import { useNotification } from '../../contexts/useNotification';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { usePropertyEditor } from '../../hooks/usePropertyEditor';
import ConfirmDialog from '../../components/ConfirmDialog';
import { NodeTable } from './NodeTable';
import { NodeFormDialog } from './NodeFormDialog';
import { logger } from '../../utils/logger';

const PAGE_SIZE = 50;

export default function NodesPage() {
  const navigate = useNavigate();
  const { notify } = useNotification();

  // Supplementary data
  const [templates, setTemplates] = useState<Template[]>([]);

  // Pagination state
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  // Form state
  const [formData, setFormData] = useState<Partial<NodeCreateRequest>>({
    template_id: '',
    label: '',
    properties: {},
  });
  const propEditor = usePropertyEditor(formData.properties || {});
  const deleteDialog = useConfirmDialog<string>();

  // Fetch nodes for current page + supplementary data on first load
  const supplementaryLoaded = useRef(false);

  const loadDataFn = useCallback(async () => {
    // Supplementary data loads in parallel on first call only
    const supplementaryPromise = !supplementaryLoaded.current
      ? Promise.all([templateApi.list('node'), settingsApi.get()])
      : null;

    const nodesResponse = await nodeApi.listPaginated(page, PAGE_SIZE, { includeStats: true });

    if (supplementaryPromise) {
      const [templatesData] = await supplementaryPromise;
      setTemplates(filterNonSystemTemplates(templatesData));
      supplementaryLoaded.current = true;
    }

    setTotal(nodesResponse.pagination.total);
    return filterKnowledgeNodes(nodesResponse.data);
  }, [page]);

  // CRUD hook for shared state management
  const {
    data: firstPageNodes,
    loading,
    selectedIds: selectedNodes,
    dialogOpen,
    editingEntity: editingNode,
    handleSelectAll,
    handleSelectItem: handleSelectNode,
    handleBulkDelete,
    confirmBulkDelete,
    cancelBulkDelete,
    bulkDeleteConfirm,
    handleCreate: openCreateDialog,
    handleEdit: openEditDialog,
    handleCloseDialog,
    loadData: reloadData,
    ProgressDialog,
  } = useCRUDPage<Node>({
    entityName: 'nodes',
    entityDisplayName: 'Entity',
    loadDataFn,
  });

  // Current page data
  const allNodes = firstPageNodes;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  const handlePageChange = (newPage: number) => {
    setPage(newPage);
  };

  // Custom create: initialize form then open dialog
  const handleCreate = () => {
    setFormData({
      template_id: templates[0]?.id || '',
      label: '',
      properties: {},
    });
    propEditor.setProperties({});
    openCreateDialog();
  };

  // Custom edit: populate form then open dialog
  const handleEdit = (node: Node) => {
    setFormData({
      template_id: node.template_id,
      label: node.label,
      properties: node.properties,
    });
    propEditor.setProperties(node.properties || {});
    openEditDialog(node);
  };

  const handleDelete = (id: string) => {
    deleteDialog.open(id);
  };

  const handleConfirmDelete = async () => {
    await deleteDialog.confirm(async () => {
      try {
        await nodeApi.delete(deleteDialog.data!);
        setTotal(prev => prev - 1);
        await reloadData();
      } catch (error) {
        logger.error('Failed to delete node:', error);
        notify('Failed to delete entity', 'error');
      }
    });
  };

  const handleSave = async () => {
    try {
      if (editingNode) {
        await nodeApi.update(editingNode.id, {
          label: formData.label,
          properties: propEditor.properties,
        });
      } else {
        await nodeApi.create({ ...formData, properties: propEditor.properties } as NodeCreateRequest);
      }
      handleCloseDialog();
      reloadData();
    } catch (error) {
      logger.error('Failed to save node:', error);
      notify('Failed to save entity', 'error');
    }
  };

  if (loading) {
    return <LoadingState message="Loading entities..." fullPage />;
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
          <Typography variant="h4">Entities</Typography>
          <Typography variant="caption" color="textSecondary">
            Showing {allNodes.length} of {total} entities
          </Typography>
        </Box>
        <Box
          sx={{
            display: "flex",
            flexWrap: "wrap",
            gap: 2
          }}>
          {selectedNodes.size > 0 && (
            <Button
              variant="outlined"
              color="error"
              startIcon={<DeleteSweepIcon />}
              onClick={handleBulkDelete}
            >
              Delete {selectedNodes.size} Selected
            </Button>
          )}
          <Button
            variant="outlined"
            startIcon={<AddIcon />}
            onClick={handleCreate}
            disabled={templates.length === 0}
          >
            Create Entity
          </Button>
        </Box>
      </Box>
      {templates.length === 0 ? (
        <EmptyState
          message="No templates available"
          secondaryMessage="Create an entity template first before creating entities."
          icon={<InventoryIcon />}
        />
      ) : allNodes.length === 0 && !loading ? (
        <EmptyState
          message="No entities yet"
          secondaryMessage="Get started by creating your first entity."
          actionLabel="Create Entity"
          onAction={handleCreate}
          icon={<InventoryIcon />}
        />
      ) : (
        <NodeTable
          nodes={allNodes}
          templates={templates}
          selectedNodes={selectedNodes}
          page={page}
          totalPages={totalPages}
          total={total}
          pageSize={PAGE_SIZE}
          onSelectAll={handleSelectAll}
          onSelectNode={handleSelectNode}
          onViewDetail={(id) => navigate(`/nodes/${id}`)}
          onEdit={handleEdit}
          onDelete={handleDelete}
          onNavigate={(path) => navigate(path)}
          onPageChange={handlePageChange}
        />
      )}
      <NodeFormDialog
        open={dialogOpen}
        editing={editingNode}
        formData={formData}
        templates={templates}
        propEditor={propEditor}
        onFormChange={setFormData}
        onSave={handleSave}
        onClose={handleCloseDialog}
      />
      <ConfirmDialog
        open={deleteDialog.isOpen}
        title="Confirm Delete"
        message="Are you sure you want to delete this entity?"
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
