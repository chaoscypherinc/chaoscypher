// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * SchemaDisplay: Read-only display of input/output schema fields
 *
 * Shows field names, types, and descriptions in a compact format.
 * Used to help users understand what data a tool expects and produces.
 */

import React from 'react';
import {
  Box,
  Typography,
  Chip,
  Tooltip,
  Paper,
} from '@mui/material';
import InputIcon from '@mui/icons-material/Input';
import OutputIcon from '@mui/icons-material/Output';
import type { FieldSchema } from '../../types/dataflow';
import { getFieldTypeColor } from '../../types/dataflow';

interface SchemaDisplayProps {
  /** Schema fields to display */
  fields: FieldSchema[];
  /** Direction: input or output */
  direction: 'input' | 'output';
  /** Optional title override */
  title?: string;
  /** Show empty state message */
  emptyMessage?: string;
}

/**
 * SchemaDisplay component
 */
export const SchemaDisplay: React.FC<SchemaDisplayProps> = ({
  fields,
  direction,
  title,
  emptyMessage,
}) => {
  const Icon = direction === 'input' ? InputIcon : OutputIcon;
  const defaultTitle = direction === 'input' ? 'Input Fields' : 'Output Fields';
  const displayTitle = title || defaultTitle;

  if (fields.length === 0) {
    return (
      <Box sx={{ py: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <Icon fontSize="small" color="action" />
          <Typography variant="body2" sx={{
            fontWeight: 500
          }}>
            {displayTitle}
          </Typography>
        </Box>
        <Typography variant="caption" sx={{
          color: "text.secondary"
        }}>
          {emptyMessage || `No ${direction} fields defined.`}
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ py: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
        <Icon fontSize="small" color="action" />
        <Typography variant="body2" sx={{
          fontWeight: 500
        }}>
          {displayTitle}
        </Typography>
        <Chip
          label={fields.length}
          size="small"
          sx={{ height: 18, fontSize: '0.65rem' }}
        />
      </Box>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
        {fields.map((field) => (
          <FieldRow key={field.name} field={field} />
        ))}
      </Box>
    </Box>
  );
};

/**
 * Single field row
 */
interface FieldRowProps {
  field: FieldSchema;
}

const FieldRow: React.FC<FieldRowProps> = ({ field }) => {
  const typeColor = getFieldTypeColor(field.type);

  return (
    <Paper
      variant="outlined"
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        px: 1.5,
        py: 0.75,
        bgcolor: 'action.hover',
      }}
    >
      {/* Type indicator bar */}
      <Box
        sx={{
          width: 3,
          height: 24,
          borderRadius: 1,
          bgcolor: typeColor,
          flexShrink: 0,
        }}
      />
      {/* Field name */}
      <Typography
        variant="body2"
        sx={{
          fontFamily: 'monospace',
          fontSize: '0.8rem',
          fontWeight: field.required ? 600 : 400,
        }}
      >
        {field.name}
        {field.required && (
          <Typography component="span" color="error" sx={{ ml: 0.25 }}>
            *
          </Typography>
        )}
      </Typography>
      {/* Type chip */}
      <Chip
        label={field.type}
        size="small"
        sx={{
          height: 18,
          fontSize: '0.6rem',
          bgcolor: `${typeColor}20`,
          color: typeColor,
          fontWeight: 500,
        }}
      />
      {/* Description tooltip */}
      {field.description && (
        <Tooltip title={field.description} placement="top" arrow>
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              cursor: 'help'
            }}>
            {field.description}
          </Typography>
        </Tooltip>
      )}
    </Paper>
  );
};
