// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * PortHandle: React Flow handle with tooltip for a single field port.
 *
 * Renders a positioned Handle (input target or output source) with a
 * tooltip showing the field name, description, and required status.
 * Used by MultiPortStepNode for per-field connection points.
 */

import React, { memo } from 'react';
import { Handle, Position } from '@xyflow/react';
import { Box, Typography, Tooltip } from '@mui/material';
import type { FieldSchema } from '../../types';
import { getFieldTypeColor } from '../../types/dataflow';

interface PortHandleProps {
  /** Unique port identifier for this handle. */
  portId: string;
  /** The field schema this handle represents. */
  field: FieldSchema;
  /** Whether this is an input (target/top) or output (source/bottom) handle. */
  direction: 'input' | 'output';
  /** CSS left position as a percentage string (e.g. "25%"). */
  leftPosition: string;
  /** Whether this input port is connected (only relevant for inputs). */
  connected?: boolean;
}

/**
 * Renders a single React Flow handle with a tooltip describing the field.
 */
const PortHandleComponent: React.FC<PortHandleProps> = ({
  portId,
  field,
  direction,
  leftPosition,
  connected,
}) => {
  const typeColor = getFieldTypeColor(field.type);
  const isInput = direction === 'input';
  const isConnected = isInput && connected;

  return (
    <Tooltip
      title={
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600 }}>
            {field.name}
          </Typography>
          <Typography
            variant="caption"
            sx={{ display: "block", opacity: 0.8 }}
          >
            {field.description || `${isInput ? 'Input' : 'Output'}: ${field.type}`}
          </Typography>
          {field.required && isInput && (
            <Typography variant="caption" color="error">
              Required
            </Typography>
          )}
        </Box>
      }
      placement={isInput ? 'top' : 'bottom'}
    >
      <Handle
        type={isInput ? 'target' : 'source'}
        position={isInput ? Position.Top : Position.Bottom}
        id={portId}
        style={{
          left: leftPosition,
          width: 10,
          height: 10,
          ...(isInput
            ? {
                top: -5,
                background: isConnected ? typeColor : `${typeColor}60`,
                border: `2px solid ${isConnected ? '#fff' : typeColor}`,
              }
            : {
                bottom: -5,
                background: typeColor,
                border: '2px solid #fff',
              }),
          transition: 'all 0.2s ease',
        }}
      />
    </Tooltip>
  );
};

export const PortHandle = memo(PortHandleComponent);
