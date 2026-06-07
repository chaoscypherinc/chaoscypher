// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * PortRow: Single port row displaying field info within a step node.
 *
 * Renders a field name, type chip, and connection indicator dot for
 * either an input or output port direction.
 */

import React, { memo } from 'react';
import { Box, Typography, Chip } from '@mui/material';
import type { FieldSchema } from '../../types';
import { getFieldTypeColor } from '../../types/dataflow';

interface PortRowProps {
  /** The field schema describing the port. */
  field: FieldSchema;
  /** Whether this is an input or output port. */
  direction: 'input' | 'output';
  /** Whether this port is connected to another node. */
  connected?: boolean;
}

/**
 * Renders a single port row with a connection dot, field name, and type chip.
 */
const PortRowComponent: React.FC<PortRowProps> = ({ field, direction, connected }) => {
  const typeColor = getFieldTypeColor(field.type);

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: direction === 'input' ? 'flex-start' : 'flex-end',
        py: 0.25,
        px: 1,
        '&:hover': {
          bgcolor: 'action.hover',
        },
      }}
    >
      {direction === 'input' && (
        <Box
          sx={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            bgcolor: connected ? typeColor : `${typeColor}40`,
            mr: 0.75,
            flexShrink: 0,
          }}
        />
      )}
      <Typography
        variant="caption"
        sx={{
          fontFamily: 'monospace',
          fontSize: '0.65rem',
          fontWeight: field.required ? 600 : 400,
          color: connected ? 'text.primary' : 'text.secondary',
          flex: 1,
          textAlign: direction,
        }}
      >
        {field.name}
        {field.required && direction === 'input' && (
          <Typography component="span" color="error" sx={{ fontSize: '0.6rem' }}>
            *
          </Typography>
        )}
      </Typography>
      <Chip
        label={field.type}
        size="small"
        sx={{
          height: 14,
          fontSize: '0.55rem',
          mx: 0.5,
          bgcolor: `${typeColor}15`,
          color: typeColor,
          '& .MuiChip-label': { px: 0.5 },
        }}
      />
      {direction === 'output' && (
        <Box
          sx={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            bgcolor: connected ? typeColor : `${typeColor}40`,
            ml: 0.75,
            flexShrink: 0,
          }}
        />
      )}
    </Box>
  );
};

export const PortRow = memo(PortRowComponent);
