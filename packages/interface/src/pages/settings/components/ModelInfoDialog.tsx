// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ModelInfoDialog: Displays detailed model metadata in a dialog.
 *
 * Shows key-value pairs from an OllamaModelShowResponse in a
 * monospace format for easy inspection of model parameters,
 * template, and configuration.
 */

import React from 'react';
import {
  Box,
  Typography,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
} from '@mui/material';
import type { OllamaModelShowResponse } from '../../../types/settings';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ModelInfoDialogProps {
  /** Whether the dialog is open. */
  open: boolean;
  /** Callback to close the dialog. */
  onClose: () => void;
  /** Model info data to display, or null if not yet loaded. */
  modelInfo: OllamaModelShowResponse | null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const ModelInfoDialog = React.memo(function ModelInfoDialog({
  open,
  onClose,
  modelInfo,
}: ModelInfoDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Model Info</DialogTitle>
      <DialogContent>
        {modelInfo && (
          <Box sx={{ fontFamily: 'monospace', fontSize: '0.85rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {Object.entries(modelInfo).map(([key, value]) => (
              <Box key={key} sx={{ mb: 1 }}>
                <Typography
                  variant="caption"
                  sx={{ color: 'text.secondary', fontWeight: 600 }}
                >
                  {key}
                </Typography>
                <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                  {typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
                </Typography>
              </Box>
            ))}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
});
