// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router';
import { logger } from '../../utils/logger';
import {
  Box,
  Typography,
  Button,
  Alert,
  FormControlLabel,
  Switch,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import { templateApi } from '../../services/api/templates';
import { settingsApi } from '../../services/api/settings';
import type { Template, Settings } from '../../types';
import type { PropertyDefinition } from '../../components/PropertyEditor';
import { useCRUDPage } from '../../hooks/useCRUDPage';
import { LoadingState } from '../../components/LoadingState';
import { filterNonSystemTemplates } from '../../constants/templates';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import ConfirmDialog from '../../components/ConfirmDialog';
import { getApiErrorMessage } from '../../utils/errors';
import GhostPagination from '../../components/GhostPagination';
import { TemplateTable } from './TemplateTable';
import { TemplateFormDialog } from './TemplateFormDialog';
import type { TemplateFormData } from './TemplateFormDialog';

export default function TemplatesPage() {
  const navigate = useNavigate();

  // Additional state not managed by useCRUDPage
  const [, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showSystemTemplates, setShowSystemTemplates] = useState(false);
  const [page, setPage] = useState(1);
  const [rowsPerPage] = useState(50);
  const [formData, setFormData] = useState<TemplateFormData>({
    name: '',
    description: '',
    template_type: 'node',
    properties: [],
    icon: null,
    color: null,
  });
  const deleteDialog = useConfirmDialog<Template>();
  const [confirmForceDelete, setConfirmForceDelete] = useState<{ open: boolean; template?: Template; message: string }>({ open: false, message: '' });

  // Stable load function for useCRUDPage
  const loadDataFn = useCallback(async () => {
    const settingsData = await settingsApi.get();
    setSettings(settingsData);
    const data = await templateApi.list();
    return data;
  }, []);

  // Use CRUD hook for common state and operations
  const {
    data: templates,
    loading,
    selectedIds: selectedTemplates,
    dialogOpen,
    editingEntity: editingTemplate,
    handleSelectItem: handleSelectTemplate,
    handleBulkDelete: performBulkDelete,
    confirmBulkDelete,
    cancelBulkDelete,
    bulkDeleteConfirm,
    handleCreate: openCreateDialog,
    handleEdit: openEditDialog,
    handleCloseDialog,
    loadData: loadTemplates,
    setSelectedIds: setSelectedTemplates,
    ProgressDialog,
  } = useCRUDPage<Template>({
    entityName: 'templates',
    entityDisplayName: 'Template',
    loadDataFn,
  });

  // First, always filter out infrastructure templates (workflow, lens, workflow_step)
  // These are internal system templates that users should never see
  const nonInfrastructureTemplates = filterNonSystemTemplates(templates);

  // Then filter based on showSystemTemplates toggle for knowledge templates
  const filteredTemplates = nonInfrastructureTemplates.filter(template =>
    showSystemTemplates ? true : !template.is_system
  );

  // Count selectable templates (non-system)
  const selectableTemplates = filteredTemplates.filter(t => !t.is_system);

  // Client-side pagination
  const paginatedTemplates = useMemo(() => {
    const startIndex = (page - 1) * rowsPerPage;
    const endIndex = startIndex + rowsPerPage;
    return filteredTemplates.slice(startIndex, endIndex);
  }, [filteredTemplates, page, rowsPerPage]);

  const totalPages = Math.ceil(filteredTemplates.length / rowsPerPage);

  // Reset to page 1 when filters change
  const handleToggleSystemTemplates = (checked: boolean) => {
    setShowSystemTemplates(checked);
    setPage(1);
  };

  // Custom handleSelectAll that only selects non-system templates
  const handleSelectAll = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (event.target.checked) {
      // Only select non-system templates
      const selectableTemplateIds = selectableTemplates.map(t => t.id);
      setSelectedTemplates(new Set(selectableTemplateIds));
    } else {
      setSelectedTemplates(new Set());
    }
  };

  // Custom create handler with form initialization
  const handleCreate = () => {
    setFormData({
      name: '',
      description: '',
      template_type: 'node',
      properties: [],
      icon: null,
      color: null,
    });
    setError(null);
    openCreateDialog();
  };

  // Custom edit handler with form initialization and system template check
  const handleEdit = (template: Template) => {
    if (template.is_system) {
      setError('System templates cannot be edited');
      return;
    }

    setFormData({
      name: template.name,
      description: template.description || '',
      template_type: template.template_type as 'node' | 'edge',
      properties: template.properties as PropertyDefinition[],
      icon: template.icon || null,
      color: template.color || null,
    });
    setError(null);
    openEditDialog(template);
  };

  const handleDelete = (template: Template) => {
    if (template.is_system) {
      setError('System templates cannot be deleted');
      return;
    }
    deleteDialog.open(template);
  };

  const handleConfirmDelete = async () => {
    const template = deleteDialog.data;
    if (!template) return;

    await deleteDialog.confirm(async () => {
      try {
        await templateApi.delete(template.id);
        await loadTemplates();
        setError(null);
      } catch (error) {
        logger.error('Failed to delete template:', error);
        const errorMessage = getApiErrorMessage(error);

        // Check if error is about nodes using the template
        if (errorMessage.includes('currently used by') || errorMessage.includes('force=True')) {
          setConfirmForceDelete({
            open: true,
            template,
            message: `${errorMessage}\n\nWould you like to FORCE DELETE this template?\n\nWARNING: This will also delete all items using this template!`,
          });
        } else {
          setError(errorMessage);
        }
      }
    });
  };

  const handleConfirmForceDelete = async () => {
    const template = confirmForceDelete.template;
    setConfirmForceDelete({ open: false, message: '' });
    if (!template) return;

    try {
      await templateApi.delete(template.id, true); // force=true
      await loadTemplates();
      setError(null);
    } catch (forceError) {
      logger.error('Failed to force delete template:', forceError);
      setError(getApiErrorMessage(forceError));
    }
  };

  const handleSave = async () => {
    try {
      if (!formData.name.trim()) {
        setError('Template name is required');
        return;
      }

      // Validate: prevent use of reserved "system_" prefix
      const nameLower = formData.name.toLowerCase();
      if (nameLower.startsWith('system_') || nameLower.startsWith('system ')) {
        setError('Template names cannot start with "system_" or "system " - this prefix is reserved for system templates');
        return;
      }

      if (editingTemplate) {
        await templateApi.update(editingTemplate.id, {
          name: formData.name,
          description: formData.description,
          properties: formData.properties,
          icon: formData.icon,
          color: formData.color,
        });
      } else {
        await templateApi.create({
          name: formData.name,
          description: formData.description,
          template_type: formData.template_type,
          properties: formData.properties,
          icon: formData.icon,
          color: formData.color,
        });
      }

      handleCloseDialog();
      await loadTemplates();
      setError(null);
    } catch (error) {
      logger.error('Failed to save template:', error);
      setError(getApiErrorMessage(error));
    }
  };

  if (loading) {
    return <LoadingState message="Loading templates..." fullPage />;
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
        <Typography variant="h4">Templates</Typography>
        <Box
          sx={{
            display: "flex",
            flexWrap: "wrap",
            gap: 2
          }}>
          {selectedTemplates.size > 0 && (
            <Button
              variant="outlined"
              color="error"
              startIcon={<DeleteSweepIcon />}
              onClick={performBulkDelete}
            >
              Delete {selectedTemplates.size} Selected
            </Button>
          )}
          <Button variant="outlined" startIcon={<AddIcon />} onClick={handleCreate}>
            Create Template
          </Button>
        </Box>
      </Box>
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      <Box
        sx={{
          display: "flex",
          flexWrap: "wrap",
          gap: 2,
          justifyContent: "space-between",
          alignItems: { xs: "flex-start", sm: "center" },
          mb: 2
        }}>
        <Box>
          <Typography color="textSecondary">
            Templates define the structure of items and links in your knowledge graph.
          </Typography>
          <Typography variant="caption" color="textSecondary">
            Showing {filteredTemplates.length} of {templates.length} templates
            {selectableTemplates.length > 0 && ` (${selectableTemplates.length} selectable)`}
          </Typography>
        </Box>
        <FormControlLabel
          control={
            <Switch
              checked={showSystemTemplates}
              onChange={(e) => handleToggleSystemTemplates(e.target.checked)}
            />
          }
          label="Show system templates"
        />
      </Box>
      <TemplateTable
        templates={paginatedTemplates}
        selectableCount={selectableTemplates.length}
        selectedTemplates={selectedTemplates}
        showSystemTemplates={showSystemTemplates}
        onSelectAll={handleSelectAll}
        onSelectTemplate={handleSelectTemplate}
        onEdit={handleEdit}
        onDelete={handleDelete}
        onNavigate={(path) => navigate(path)}
      />
      {/* Pagination Controls */}
      {totalPages > 1 && (
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            mt: 3
          }}>
          <GhostPagination
            page={page}
            totalPages={totalPages}
            total={filteredTemplates.length}
            pageSize={rowsPerPage}
            onPageChange={setPage}
          />
        </Box>
      )}
      <TemplateFormDialog
        open={dialogOpen}
        editing={editingTemplate}
        formData={formData}
        onFormChange={setFormData}
        onSave={handleSave}
        onClose={handleCloseDialog}
      />
      <ConfirmDialog
        open={deleteDialog.isOpen}
        title="Confirm Delete"
        message={deleteDialog.data ? `Are you sure you want to delete template "${deleteDialog.data.name}"?` : ''}
        onConfirm={handleConfirmDelete}
        onCancel={deleteDialog.close}
      />
      <ConfirmDialog
        open={confirmForceDelete.open}
        title="Force Delete Template"
        message={confirmForceDelete.message}
        confirmLabel="Force Delete"
        onConfirm={handleConfirmForceDelete}
        onCancel={() => setConfirmForceDelete({ open: false, message: '' })}
      />
      <ConfirmDialog
        open={bulkDeleteConfirm.open}
        title="Confirm Bulk Delete"
        message={bulkDeleteConfirm.message}
        onConfirm={confirmBulkDelete}
        onCancel={cancelBulkDelete}
      />
      {/* Bulk Operation Progress Dialog */}
      <ProgressDialog />
    </Box>
  );
}
