// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * LinkCreationModal: Modal for creating new links
 * Features:
 * - Template selection
 * - Label input
 * - Source/target display
 */

import React, { useState, useEffect, useMemo } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Alert,
  Box,
  Typography,
} from '@mui/material';
import { Template } from '../../../types';
import { useTemplates } from '../../../services/api/useTemplates';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';
import { LoadingState } from '../../../components/LoadingState';
import { getApiErrorMessage } from '../../../utils/errors';

interface LinkCreationModalProps {
  open: boolean;
  onClose: () => void;
  onCreate: (sourceId: string, targetId: string, edgeTemplateId: string, label?: string) => void;
  sourceId?: string;
  targetId?: string;
}

export const LinkCreationModal: React.FC<LinkCreationModalProps> = ({
  open,
  onClose,
  onCreate,
  sourceId,
  targetId,
}) => {
  const [selectedTemplateId, setSelectedTemplateId] = useState('');
  const [label, setLabel] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const { data: allTemplates, isLoading, isError, error: queryError } = useTemplates('edge', {
    enabled: open,
  });

  const templates = useMemo(
    () => (allTemplates ?? []).filter((t: Template) => t.template_type === 'edge'),
    [allTemplates],
  );

  const loadError = isError
    ? getApiErrorMessage(queryError) || 'Failed to load templates'
    : null;
  const error = formError ?? loadError;

  // Select the first template (and its name as the default label) once the
  // list resolves.
  useEffect(() => {
    if (templates.length > 0 && !selectedTemplateId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelectedTemplateId(templates[0].id);
      setLabel(templates[0].name);
    }
  }, [templates, selectedTemplateId]);

  const handleCreate = () => {
    if (!selectedTemplateId) {
      setFormError('Please select a template');
      return;
    }

    if (!sourceId || !targetId) {
      setFormError('Source and target items are required');
      return;
    }

    onCreate(sourceId, targetId, selectedTemplateId, label);
    handleClose();
  };

  const handleClose = () => {
    setSelectedTemplateId('');
    setLabel('');
    setFormError(null);
    onClose();
  };

  const loading = isLoading;

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth slotProps={{
      paper: { sx: ghostDialogPaperSx }
    }}>
      <DialogTitle>Create New Link</DialogTitle>
      <DialogContent>
        {loading && (
          <LoadingState message="Loading templates..." minHeight="200px" />
        )}

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {!loading && (
          <>
            <Box sx={{ mb: 2, p: 2, bgcolor: 'background.default', borderRadius: 1 }}>
              <Typography variant="body2" gutterBottom sx={{
                color: "text.secondary"
              }}>
                Connection
              </Typography>
              <Typography variant="body1">
                <strong>From:</strong> {sourceId || 'Not selected'}
              </Typography>
              <Typography variant="body1">
                <strong>To:</strong> {targetId || 'Not selected'}
              </Typography>
            </Box>

            <FormControl fullWidth sx={{ mb: 2 }}>
              <InputLabel>Link Template</InputLabel>
              <Select
                value={selectedTemplateId}
                onChange={(e) => {
                  setSelectedTemplateId(e.target.value);
                  const template = templates.find(t => t.id === e.target.value);
                  if (template) {
                    setLabel(template.name);
                  }
                }}
                label="Link Template"
              >
                {templates.map((template) => (
                  <MenuItem key={template.id} value={template.id}>
                    <Box>
                      <Typography variant="body1">{template.name}</Typography>
                      {template.description && (
                        <Typography variant="caption" sx={{
                          color: "text.secondary"
                        }}>
                          {template.description}
                        </Typography>
                      )}
                    </Box>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            <TextField
              fullWidth
              label="Label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Enter link label"
              sx={{ mb: 2 }}
            />

            {templates.length === 0 && (
              <Alert severity="warning">
                No link templates available. Create a template first.
              </Alert>
            )}
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} sx={ghostCancelBtnSx}>Cancel</Button>
        <Button
          onClick={handleCreate}
          variant="outlined"
          sx={ghostButtonSx(ChaosCypherPalette.primary)}
          disabled={loading || !selectedTemplateId || !sourceId || !targetId || templates.length === 0}
        >
          Create
        </Button>
      </DialogActions>
    </Dialog>
  );
};
