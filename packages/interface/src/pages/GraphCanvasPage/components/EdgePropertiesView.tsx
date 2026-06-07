// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * EdgePropertiesView: Read-only display of edge (link) properties.
 *
 * Shows edge metadata (ID, type, direction), label, all properties,
 * and a delete button.
 */

import React from 'react';
import { Box, Typography, TextField, Divider, Button } from '@mui/material';
import DeleteIcon from '@mui/icons-material/DeleteOutlined';
import type { GraphEdgeData } from '../types';
import { ghostButtonSx } from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';

interface EdgePropertiesViewProps {
  /** The selected edge ID. */
  edgeId: string | null;
  /** The selected edge data. */
  edgeData: GraphEdgeData;
  /** Callback to delete the edge. */
  onDeleteEdge: () => void;
}

/**
 * Renders the read-only properties view for a selected graph edge.
 */
const EdgePropertiesView: React.FC<EdgePropertiesViewProps> = ({
  edgeId,
  edgeData,
  onDeleteEdge,
}) => {
  return (
    <>
      <Typography variant="h6" gutterBottom>
        Link Properties
      </Typography>
      <Divider sx={{ my: 2 }} />
      {/* Basic Edge Info */}
      <Typography
        variant="caption"
        gutterBottom
        sx={{
          color: "text.secondary",
          display: "block"
        }}>
        ID: {edgeId}
      </Typography>
      <Typography
        variant="caption"
        gutterBottom
        sx={{
          color: "text.secondary",
          display: "block"
        }}>
        Type: {edgeData.type || 'edge'}
      </Typography>
      <Typography
        variant="caption"
        gutterBottom
        sx={{
          color: "text.secondary",
          display: "block",
          mb: 2
        }}>
        {edgeData.sourceId} &rarr; {edgeData.targetId}
      </Typography>
      {/* Label */}
      <TextField
        label="Label"
        fullWidth
        value={edgeData.label || ''}
        disabled
        helperText="Edge label (read-only)"
        sx={{ mb: 2 }}
      />
      {/* All Edge Properties */}
      {edgeData.properties && Object.keys(edgeData.properties).length > 0 && (
        <>
          <Typography variant="subtitle2" gutterBottom sx={{ mt: 1 }}>
            Edge Properties
          </Typography>
          {Object.entries(edgeData.properties)
            .filter(([key]) => key !== 'type')
            .map(([key, value]) => (
              <Box key={key} sx={{ mb: 2 }}>
                <Typography
                  variant="caption"
                  sx={{
                    color: "text.secondary",
                    display: "block"
                  }}>
                  {key}
                </Typography>
                <Typography variant="body2">
                  {typeof value === 'object'
                    ? JSON.stringify(value, null, 2)
                    : String(value || 'N/A')}
                </Typography>
              </Box>
            ))
          }
        </>
      )}
      <Divider sx={{ my: 2 }} />
      <Button
        variant="outlined"
        sx={ghostButtonSx(ChaosCypherPalette.error)}
        startIcon={<DeleteIcon />}
        onClick={onDeleteEdge}
        fullWidth
      >
        Delete Link
      </Button>
    </>
  );
};

export default EdgePropertiesView;
