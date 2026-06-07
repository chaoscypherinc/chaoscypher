// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import { useParams, useNavigate } from 'react-router';
import {
  Alert,
  Box,
  Button,
  Chip,
} from '@mui/material';
import EditIcon from '@mui/icons-material/Edit';
import SaveIcon from '@mui/icons-material/Save';
import CancelIcon from '@mui/icons-material/Cancel';
import BackIcon from '@mui/icons-material/ArrowBack';
import DeleteIcon from '@mui/icons-material/Delete';
import {
  useTemplate,
  useUpdateTemplate,
  useDeleteTemplate,
} from '../../services/api/useTemplates';
import { getApiErrorMessage } from '../../utils/errors';
import type { Template } from '../../types';
import type { PropertyDefinition } from '../../components/PropertyEditor';
import ConfirmDialog from '../../components/ConfirmDialog';
import TemplateIcon from '../../components/TemplateIcon';
import DetailPageHeader from '../../components/detail/DetailPageHeader';
import DetailPageLayout from '../../components/detail/DetailPageLayout';
import {
  ghostButtonSx,
  ghostCancelBtnSx,
  ghostErrorAlertSx,
} from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';
import { LoadingState } from '../../components/LoadingState';
import { logger } from '../../utils/logger';
import { TemplateEditorPanel } from './TemplateEditorPanel';
import { TemplateMetadataSidebar } from './TemplateMetadataSidebar';

export default function TemplateDetailPage() {
  const { templateId } = useParams<{ templateId: string }>();
  const navigate = useNavigate();

  const templateQuery = useTemplate(templateId);
  const template = templateQuery.data ?? null;
  const updateTemplate = useUpdateTemplate();
  const deleteTemplate = useDeleteTemplate();

  // `error` carries both load-failure text and the page's own validation /
  // mutation messages (system-template guards, name rules, force-delete
  // outcomes); kept as local UI state. Load failure is derived from the query.
  const [actionError, setActionError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [formData, setFormData] = useState<Partial<Template>>({});
  const [activeTab, setActiveTab] = useState(0);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [confirmForceDelete, setConfirmForceDelete] = useState<{
    open: boolean;
    message: string;
  }>({ open: false, message: '' });

  const error = actionError ?? (templateQuery.isError ? 'Failed to load template' : null);

  // `formData` is only read while `editing` is true (the editor panel renders
  // `editing ? formData : template`); `handleEdit` seeds it from the freshly
  // loaded template, so no effect-driven sync is needed.

  const handleEdit = () => {
    if (template?.is_system) {
      setActionError('System templates cannot be edited');
      return;
    }
    setEditing(true);
    setFormData(template || {});
  };

  const handleCancel = () => {
    setEditing(false);
    setFormData(template || {});
    setActionError(null);
  };

  const handleSave = async () => {
    if (!templateId) return;
    try {
      if (!formData.name?.trim()) {
        setActionError('Template name is required');
        return;
      }
      const nameLower = formData.name.toLowerCase();
      if (nameLower.startsWith('system_') || nameLower.startsWith('system ')) {
        setActionError(
          'Template names cannot start with "system_" or "system " - this prefix is reserved for system templates',
        );
        return;
      }
      await updateTemplate.mutateAsync({
        id: templateId,
        updates: {
          name: formData.name,
          description: formData.description,
          properties: formData.properties as PropertyDefinition[],
          icon: formData.icon,
          color: formData.color,
        },
      });
      setEditing(false);
      setActionError(null);
    } catch (err) {
      logger.error('Failed to save template:', err);
      setActionError(getApiErrorMessage(err));
    }
  };

  const handleDelete = () => {
    if (!templateId) return;
    if (template?.is_system) {
      setActionError('System templates cannot be deleted');
      return;
    }
    setConfirmDeleteOpen(true);
  };

  const handleConfirmDelete = async () => {
    setConfirmDeleteOpen(false);
    if (!templateId) return;
    try {
      await deleteTemplate.mutateAsync({ id: templateId });
      navigate('/templates');
    } catch (err) {
      logger.error('Failed to delete template:', err);
      const errorMessage = getApiErrorMessage(err);
      if (errorMessage.includes('currently used by') || errorMessage.includes('force=True')) {
        setConfirmForceDelete({
          open: true,
          message: `${errorMessage}\n\nWould you like to FORCE DELETE this template?\n\nWARNING: This will also delete all items using this template!`,
        });
      } else {
        setActionError(errorMessage);
      }
    }
  };

  const handleConfirmForceDelete = async () => {
    setConfirmForceDelete({ open: false, message: '' });
    if (!templateId) return;
    try {
      await deleteTemplate.mutateAsync({ id: templateId, force: true });
      navigate('/templates');
    } catch (forceError) {
      logger.error('Failed to force delete template:', forceError);
      setActionError(getApiErrorMessage(forceError));
    }
  };

  if (templateQuery.isLoading) {
    return <LoadingState message="Loading template..." fullPage />;
  }

  if (!template) {
    return (
      <Box>
        <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }}>
          {error || 'Template not found'}
        </Alert>
        <Button
          startIcon={<BackIcon />}
          onClick={() => navigate('/templates')}
          sx={ghostCancelBtnSx}
        >
          Back to Templates
        </Button>
      </Box>
    );
  }

  const headerActions = editing ? (
    <>
      <Button startIcon={<CancelIcon />} onClick={handleCancel} sx={ghostCancelBtnSx}>
        Cancel
      </Button>
      <Button
        variant="outlined"
        startIcon={<SaveIcon />}
        onClick={handleSave}
        sx={ghostButtonSx(ChaosCypherPalette.success)}
      >
        Save
      </Button>
    </>
  ) : (
    !template.is_system && (
      <>
        <Button
          variant="outlined"
          startIcon={<DeleteIcon />}
          onClick={handleDelete}
          sx={ghostButtonSx(ChaosCypherPalette.error)}
        >
          Delete
        </Button>
        <Button
          variant="outlined"
          startIcon={<EditIcon />}
          onClick={handleEdit}
          sx={ghostButtonSx(ChaosCypherPalette.primary)}
        >
          Edit
        </Button>
      </>
    )
  );

  return (
    <Box>
      <DetailPageHeader
        title={editing ? 'Edit Template' : template.name}
        icon={
          <TemplateIcon
            template={template}
            variant={template.template_type === 'edge' ? 'edge' : 'node'}
            size={18}
            containerSize={32}
          />
        }
        onBack={() => navigate('/templates')}
        titleSuffix={
          template.is_system ? (
            <Chip label="System" color="warning" variant="outlined" />
          ) : undefined
        }
        actions={headerActions}
      />
      {error && (
        <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }}>
          {error}
        </Alert>
      )}
      <DetailPageLayout
        main={
          <TemplateEditorPanel
            template={template}
            editing={editing}
            formData={formData}
            activeTab={activeTab}
            onActiveTabChange={setActiveTab}
            onFormDataChange={setFormData}
          />
        }
        sidebar={<TemplateMetadataSidebar template={template} />}
        mainFlex={2}
      />
      <ConfirmDialog
        open={confirmDeleteOpen}
        title="Confirm Delete"
        message="Are you sure you want to delete this template?"
        onConfirm={handleConfirmDelete}
        onCancel={() => setConfirmDeleteOpen(false)}
      />
      <ConfirmDialog
        open={confirmForceDelete.open}
        title="Force Delete Template"
        message={confirmForceDelete.message}
        confirmLabel="Force Delete"
        onConfirm={handleConfirmForceDelete}
        onCancel={() => setConfirmForceDelete({ open: false, message: '' })}
      />
    </Box>
  );
}
