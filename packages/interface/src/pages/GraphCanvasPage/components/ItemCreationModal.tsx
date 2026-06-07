// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ItemCreationModal: Modal for creating new items
 * Features:
 * - Template selection
 * - Initial title input
 * - Position parameter
 */

import React, { useState, useEffect, useMemo } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Alert,
  Box,
  Typography,
} from '@mui/material';
import { Template } from '../../../types';
import { isSystemTemplate } from '../../../constants/templates';
import { useTemplates } from '../../../services/api/useTemplates';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';
import { LoadingState } from '../../../components/LoadingState';
import { getApiErrorMessage } from '../../../utils/errors';

interface ItemCreationModalProps {
  open: boolean;
  onClose: () => void;
  onCreate: (templateId: string, position?: { x: number; y: number }) => void;
  position?: { x: number; y: number };
}

export const ItemCreationModal: React.FC<ItemCreationModalProps> = ({
  open,
  onClose,
  onCreate,
  position,
}) => {
  const [selectedTemplateId, setSelectedTemplateId] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const { data: allTemplates, isLoading, isError, error: queryError } = useTemplates('node', {
    enabled: open,
  });

  // Filter node templates - exclude lens and workflow (system) types.
  const templates = useMemo(
    () =>
      (allTemplates ?? []).filter(
        (t: Template) => t.template_type === 'node' && !isSystemTemplate(t.id),
      ),
    [allTemplates],
  );

  const loadError = isError
    ? getApiErrorMessage(queryError) || 'Failed to load templates'
    : null;
  const error = formError ?? loadError;

  // Select the first template by default once the list resolves.
  useEffect(() => {
    if (templates.length > 0 && !selectedTemplateId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelectedTemplateId(templates[0].id);
    }
  }, [templates, selectedTemplateId]);

  const handleCreate = () => {
    if (!selectedTemplateId) {
      setFormError('Please select a template');
      return;
    }

    onCreate(selectedTemplateId, position);
    handleClose();
  };

  const handleClose = () => {
    setSelectedTemplateId('');
    setFormError(null);
    onClose();
  };

  const loading = isLoading;

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth slotProps={{
      paper: { sx: ghostDialogPaperSx }
    }}>
      <DialogTitle>Create New Item</DialogTitle>
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
            <FormControl fullWidth sx={{ mb: 2 }}>
              <InputLabel>Template</InputLabel>
              <Select
                value={selectedTemplateId}
                onChange={(e) => setSelectedTemplateId(e.target.value)}
                label="Template"
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

            {templates.length === 0 && (
              <Alert severity="warning">
                No item templates available. Create a template first.
              </Alert>
            )}

            {position && (
              <Typography
                variant="caption"
                sx={{
                  color: "text.secondary",
                  display: "block",
                  mt: 1
                }}>
                Position: ({Math.round(position.x)}, {Math.round(position.y)})
              </Typography>
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
          disabled={loading || !selectedTemplateId || templates.length === 0}
        >
          Create
        </Button>
      </DialogActions>
    </Dialog>
  );
};
