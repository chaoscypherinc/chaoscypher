// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * EdgePanel: Properties panel content for a selected edge
 *
 * Displays edge connection info (source/target) and provides a button
 * to delete the connection.
 */

import React from 'react';
import { Box, Typography, IconButton, Button } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import DeleteIcon from '@mui/icons-material/Delete';
import type { Edge } from '@xyflow/react';
import { ghostButtonSx } from '../../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../../theme/palette';

interface EdgePanelProps {
  /** The selected edge to display properties for. */
  selectedEdge: Edge;
  /** Callback to delete the selected edge. */
  onDeleteEdge: () => void;
  /** Callback to close the properties panel. */
  onClose: () => void;
}

/**
 * Renders edge connection details and a delete action.
 */
export const EdgePanel: React.FC<EdgePanelProps> = ({
  selectedEdge,
  onDeleteEdge,
  onClose,
}) => {
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography variant="subtitle1" sx={{
            fontWeight: 600
          }}>
            Edge Properties
          </Typography>
          <IconButton aria-label="Close" size="small" onClick={onClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>
      </Box>
      {/* Content */}
      <Box sx={{ p: 2 }}>
        <Typography variant="body2" gutterBottom sx={{
          color: "text.secondary"
        }}>
          Connection from <strong>{selectedEdge.source}</strong> to{' '}
          <strong>{selectedEdge.target}</strong>
        </Typography>

        <Button
          variant="outlined"
          sx={{ ...ghostButtonSx(ChaosCypherPalette.error), mt: 2 }}
          startIcon={<DeleteIcon />}
          onClick={onDeleteEdge}
          fullWidth
        >
          Delete Connection
        </Button>
      </Box>
    </Box>
  );
};
