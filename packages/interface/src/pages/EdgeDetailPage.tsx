// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import { useParams, useNavigate } from 'react-router';
import { Alert, Box, Button, Chip, TextField, Typography, alpha } from '@mui/material';
import EditIcon from '@mui/icons-material/Edit';
import SaveIcon from '@mui/icons-material/Save';
import CancelIcon from '@mui/icons-material/Cancel';
import BackIcon from '@mui/icons-material/ArrowBack';
import DeleteIcon from '@mui/icons-material/Delete';
import { useEdge, useUpdateEdge, useDeleteEdge } from '../services/api/useEdges';
import { useNode } from '../services/api/useNodes';
import { useTemplate } from '../services/api/useTemplates';
import type { Edge } from '../types';
import ConfirmDialog from '../components/ConfirmDialog';
import TemplateIcon from '../components/TemplateIcon';
import DetailPageHeader from '../components/detail/DetailPageHeader';
import DetailPageLayout from '../components/detail/DetailPageLayout';
import MetadataCard from '../components/detail/MetadataCard';
import MetadataRow from '../components/detail/MetadataRow';
import PropertiesEditor from '../components/detail/PropertiesEditor';
import {
  ghostButtonSx,
  ghostCancelBtnSx,
  ghostErrorAlertSx,
  ghostInputSx,
} from '../theme/ghostStyles';
import { glassPanelSx } from '../theme/cardStyles';
import { ChaosCypherPalette, ChaosCypherNeutrals } from '../theme/palette';
import { logger } from '../utils/logger';
import { LoadingState } from '../components/LoadingState';
import { RELATIONSHIP_SYSTEM_KEYS } from '../utils/propertyKeys';
import { formatConfidencePct, confidenceChipColor } from '../utils/confidence';

const CYAN = ChaosCypherPalette.primary;

const linkChipSx = {
  cursor: 'pointer',
  bgcolor: 'transparent',
  color: CYAN,
  borderColor: alpha(CYAN, 0.25),
  '&:hover': {
    borderColor: alpha(CYAN, 0.5),
    bgcolor: alpha(CYAN, 0.04),
  },
};

export default function EdgeDetailPage() {
  const { edgeId } = useParams<{ edgeId: string }>();
  const navigate = useNavigate();

  const { data: relationship, isLoading, isError } = useEdge(edgeId);
  const { data: sourceNode } = useNode(relationship?.source_node_id);
  const { data: targetNode } = useNode(relationship?.target_node_id);
  const { data: template } = useTemplate(relationship?.template_id);

  const updateEdge = useUpdateEdge();
  const deleteEdge = useDeleteEdge();

  const [editing, setEditing] = useState(false);
  const [formData, setFormData] = useState<Partial<Edge>>({});
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleEdit = () => {
    setEditing(true);
    setFormData(relationship || {});
  };

  const handleCancel = () => {
    setEditing(false);
    setFormData(relationship || {});
  };

  const handleSave = async () => {
    if (!edgeId) return;
    try {
      setActionError(null);
      await updateEdge.mutateAsync({
        id: edgeId,
        updates: {
          label: formData.label,
          properties: formData.properties,
        },
      });
      setEditing(false);
    } catch (err) {
      logger.error('Failed to save relationship:', err);
      setActionError('Failed to save relationship');
    }
  };

  const handleConfirmDelete = async () => {
    setConfirmDeleteOpen(false);
    if (!edgeId) return;
    try {
      setActionError(null);
      await deleteEdge.mutateAsync(edgeId);
      navigate('/edges');
    } catch (err) {
      logger.error('Failed to delete relationship:', err);
      setActionError('Failed to delete relationship');
    }
  };

  if (isLoading) {
    return <LoadingState message="Loading relationship..." fullPage />;
  }

  if (isError || !relationship) {
    return (
      <Box>
        <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }}>
          {isError ? 'Failed to load relationship' : 'Relationship not found'}
        </Alert>
        <Button
          startIcon={<BackIcon />}
          onClick={() => navigate('/edges')}
          sx={ghostCancelBtnSx}
        >
          Back to Relationships
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
    <>
      <Button
        variant="outlined"
        startIcon={<DeleteIcon />}
        onClick={() => setConfirmDeleteOpen(true)}
        sx={ghostButtonSx(ChaosCypherPalette.error)}
      >
        Delete
      </Button>
      <Button
        variant="outlined"
        startIcon={<EditIcon />}
        onClick={handleEdit}
        sx={ghostButtonSx(CYAN)}
      >
        Edit
      </Button>
    </>
  );

  const main = (
    <Box sx={{ ...glassPanelSx, p: 3 }}>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <Box>
          <Typography variant="subtitle2" gutterBottom sx={{ color: 'text.secondary' }}>
            Connection
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <Chip
              label={sourceNode?.label || 'Unknown'}
              variant="outlined"
              onClick={() => navigate(`/nodes/${relationship.source_node_id}`)}
              sx={linkChipSx}
            />
            <Typography>→</Typography>
            <Chip
              label={targetNode?.label || 'Unknown'}
              variant="outlined"
              onClick={() => navigate(`/nodes/${relationship.target_node_id}`)}
              sx={linkChipSx}
            />
          </Box>
        </Box>

        <TextField
          label="Label"
          value={editing ? formData.label || '' : relationship.label}
          onChange={(e) => setFormData({ ...formData, label: e.target.value })}
          fullWidth
          disabled={!editing}
          helperText="Describe this relationship (e.g., 'supports', 'contradicts')"
          sx={ghostInputSx}
        />

        <Box sx={{ borderBottom: '1px solid rgba(255, 255, 255, 0.06)', my: 1 }} />

        <PropertiesEditor
          properties={editing ? formData.properties : relationship.properties}
          editing={editing}
          onChange={(properties) => setFormData({ ...formData, properties })}
          excludeKeys={RELATIONSHIP_SYSTEM_KEYS}
        />
      </Box>
    </Box>
  );

  const relProps = relationship.properties ?? {};
  const confidencePct = formatConfidencePct(relProps.confidence);
  const sentRef =
    relProps.sent_ref === null || relProps.sent_ref === undefined || relProps.sent_ref === ''
      ? null
      : String(relProps.sent_ref);
  const chunkIndex =
    typeof relProps.chunk_index === 'number' ? String(relProps.chunk_index) : null;

  const templateChip = (
    <Chip
      label={template?.name || relationship.template_id}
      size="small"
      variant="outlined"
      sx={{ borderColor: 'rgba(255, 255, 255, 0.15)', color: ChaosCypherNeutrals.textSecondary }}
    />
  );

  // Collapsed summary leads with the at-a-glance confidence; when an edge has
  // no confidence (e.g. manually created) it falls back to the template chip.
  const summary = confidencePct ? (
    <Chip
      label={`Confidence ${confidencePct}`}
      size="small"
      variant="outlined"
      sx={{
        color: confidenceChipColor(relProps.confidence),
        borderColor: alpha(confidenceChipColor(relProps.confidence), 0.4),
        bgcolor: alpha(confidenceChipColor(relProps.confidence), 0.08),
      }}
    />
  ) : (
    templateChip
  );

  const sidebar = (
    <MetadataCard collapsible summary={summary}>
      <MetadataRow label="ID">
        <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>
          {relationship.id}
        </Typography>
      </MetadataRow>
      <MetadataRow label="Template">
        <Box sx={{ mt: 0.5 }}>{templateChip}</Box>
      </MetadataRow>
      <MetadataRow label="Source">
        <Box sx={{ mt: 0.5 }}>
          <Chip
            label={sourceNode?.label || 'Unknown'}
            size="small"
            variant="outlined"
            onClick={() => navigate(`/nodes/${relationship.source_node_id}`)}
            sx={linkChipSx}
          />
        </Box>
      </MetadataRow>
      <MetadataRow label="Target">
        <Box sx={{ mt: 0.5 }}>
          <Chip
            label={targetNode?.label || 'Unknown'}
            size="small"
            variant="outlined"
            onClick={() => navigate(`/nodes/${relationship.target_node_id}`)}
            sx={linkChipSx}
          />
        </Box>
      </MetadataRow>
      {confidencePct && (
        <MetadataRow label="Confidence">
          <Box sx={{ mt: 0.5 }}>
            <Chip
              label={confidencePct}
              size="small"
              variant="outlined"
              sx={{
                color: confidenceChipColor(relProps.confidence),
                borderColor: alpha(confidenceChipColor(relProps.confidence), 0.4),
                bgcolor: alpha(confidenceChipColor(relProps.confidence), 0.08),
              }}
            />
          </Box>
        </MetadataRow>
      )}
      {sentRef && (
        <MetadataRow label="Sentence Reference">
          <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>
            {sentRef}
          </Typography>
        </MetadataRow>
      )}
      {chunkIndex && (
        <MetadataRow label="Chunk Index">
          <Typography variant="body2">{chunkIndex}</Typography>
        </MetadataRow>
      )}
      <MetadataRow label="Created">
        <Typography variant="body2">
          {new Date(relationship.created_at).toLocaleString()}
        </Typography>
      </MetadataRow>
      {relationship.updated_at && (
        <MetadataRow label="Updated">
          <Typography variant="body2">
            {new Date(relationship.updated_at).toLocaleString()}
          </Typography>
        </MetadataRow>
      )}
    </MetadataCard>
  );

  return (
    <Box>
      <DetailPageHeader
        title={editing ? 'Edit Relationship' : relationship.label || ''}
        icon={
          <TemplateIcon
            template={template}
            fallbackTemplateId={relationship.template_id}
            variant="edge"
            size={24}
            containerSize={40}
          />
        }
        onBack={() => navigate('/edges')}
        actions={headerActions}
      />
      {actionError && (
        <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }}>
          {actionError}
        </Alert>
      )}
      <DetailPageLayout main={main} sidebar={sidebar} />
      <ConfirmDialog
        open={confirmDeleteOpen}
        title="Confirm Delete"
        message="Are you sure you want to delete this relationship?"
        onConfirm={handleConfirmDelete}
        onCancel={() => setConfirmDeleteOpen(false)}
      />
    </Box>
  );
}
