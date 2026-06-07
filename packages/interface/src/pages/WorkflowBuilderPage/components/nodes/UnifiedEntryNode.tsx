// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * UnifiedEntryNode: Combined workflow start node
 *
 * Displays both workflow input fields and event trigger data sources
 * in a single entry point node. Shows output ports for each available
 * field that downstream steps can connect to.
 */

import React, { memo } from 'react';
import { Handle, Position, NodeProps, Node } from '@xyflow/react';
import {
  Box,
  Typography,
  Chip,
  Tooltip,
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import InputIcon from '@mui/icons-material/Input';
import BoltIcon from '@mui/icons-material/Bolt';
import type { UnifiedEntryNodeData, FieldSchema } from '../../types';
import {
  EVENT_SOURCE_INFO,
  type EventSource,
} from '../../constants/eventSchemas';
import { getFieldTypeColor, createPortId } from '../../types/dataflow';
import { CardColors } from '../../../../theme/cardStyles';

/**
 * Field row component showing a single field with its output port
 */
const FieldRow: React.FC<{
  field: FieldSchema;
  nodeId: string;
  index: number;
  totalFields: number;
}> = ({ field, nodeId, index: _index, totalFields: _totalFields }) => {
  const typeColor = getFieldTypeColor(field.type);
  const portId = createPortId(nodeId, field.name);

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        py: 0.5,
        px: 1,
        position: 'relative',
        '&:hover': {
          bgcolor: 'action.hover',
        },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1 }}>
        <Typography
          variant="caption"
          sx={{
            fontFamily: 'monospace',
            fontWeight: field.required ? 600 : 400,
          }}
        >
          {field.name}
        </Typography>
        <Chip
          label={field.type}
          size="small"
          sx={{
            height: 16,
            fontSize: '0.6rem',
            bgcolor: `${typeColor}20`,
            color: typeColor,
            '& .MuiChip-label': { px: 0.5 },
          }}
        />
        {field.required && (
          <Typography variant="caption" color="error" sx={{ fontSize: '0.6rem' }}>
            *
          </Typography>
        )}
      </Box>

      {/* Output handle for this field */}
      <Tooltip title={field.description || `Output: ${field.name}`} placement="right">
        <Handle
          type="source"
          position={Position.Right}
          id={portId}
          style={{
            width: 10,
            height: 10,
            background: typeColor,
            border: '2px solid white',
            right: -5,
          }}
        />
      </Tooltip>
    </Box>
  );
};

/**
 * Section component for grouping fields
 */
const FieldSection: React.FC<{
  title: string;
  icon: React.ReactNode;
  fields: FieldSchema[];
  nodeId: string;
  startIndex: number;
  totalFields: number;
}> = ({ title, icon, fields, nodeId, startIndex, totalFields }) => {
  if (fields.length === 0) return null;

  return (
    <Box sx={{ mt: 1 }}>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.5,
          px: 1,
          py: 0.5,
          bgcolor: 'action.selected',
        }}
      >
        {icon}
        <Typography
          variant="caption"
          sx={{
            fontWeight: 600,
            color: "text.secondary"
          }}>
          {title}
        </Typography>
        <Chip
          label={fields.length}
          size="small"
          sx={{
            height: 14,
            fontSize: '0.55rem',
            ml: 'auto',
          }}
        />
      </Box>
      <Box>
        {fields.map((field, idx) => (
          <FieldRow
            key={field.name}
            field={field}
            nodeId={nodeId}
            index={startIndex + idx}
            totalFields={totalFields}
          />
        ))}
      </Box>
    </Box>
  );
};

/**
 * UnifiedEntryNode component
 */
export const UnifiedEntryNode: React.FC<NodeProps<Node<UnifiedEntryNodeData>>> = memo(
  ({ id, data, selected }) => {
    const {
      label = 'Start',
      workflowInputs = [],
      eventSource,
      eventFields = [],
    } = data;

    // Get event source info
    const eventInfo = eventSource ? EVENT_SOURCE_INFO[eventSource as EventSource] : null;

    // Calculate total fields for port positioning
    const totalFields = workflowInputs.length + eventFields.length;

    return (
      <Box
        sx={{
          minWidth: 280,
          maxWidth: 320,
          bgcolor: 'background.paper',
          border: 2,
          borderColor: selected ? 'primary.main' : 'success.main',
          borderRadius: 2,
          boxShadow: selected ? 4 : 2,
          overflow: 'hidden',
          transition: 'all 0.2s ease',
        }}
      >
        {/* Header */}
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            px: 1.5,
            py: 1,
            bgcolor: 'success.main',
            color: 'success.contrastText',
          }}
        >
          <PlayArrowIcon fontSize="small" />
          <Typography variant="subtitle2" sx={{
            fontWeight: 600
          }}>
            {label}
          </Typography>
        </Box>
        {/* Workflow Inputs Section */}
        {workflowInputs.length > 0 && (
          <FieldSection
            title="WORKFLOW INPUTS"
            icon={<InputIcon sx={{ fontSize: 14, color: 'text.secondary' }} />}
            fields={workflowInputs}
            nodeId={id}
            startIndex={0}
            totalFields={totalFields}
          />
        )}
        {/* Event Trigger Section */}
        <Box sx={{ px: 1, py: 1 }}>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              mb: 1,
            }}
          >
            <BoltIcon sx={{ fontSize: 14, color: 'warning.main' }} />
            <Typography
              variant="caption"
              sx={{
                fontWeight: 600,
                color: "text.secondary"
              }}>
              EVENT TRIGGER
            </Typography>
          </Box>

          {/* Event source display */}
          {eventInfo ? (
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                p: 1,
                bgcolor: 'warning.main',
                color: 'warning.contrastText',
                borderRadius: 1,
                mb: 1,
              }}
            >
              <BoltIcon fontSize="small" />
              <Box>
                <Typography variant="caption" sx={{
                  fontWeight: 600
                }}>
                  {eventInfo.label}
                </Typography>
                <Typography
                  variant="caption"
                  sx={{
                    display: "block",
                    opacity: 0.8,
                    fontSize: '0.6rem'
                  }}>
                  {eventInfo.description}
                </Typography>
              </Box>
            </Box>
          ) : (
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                px: 1
              }}>
              Manual trigger (no event data)
            </Typography>
          )}
        </Box>
        {/* Event Fields */}
        {eventFields.length > 0 && (
          <FieldSection
            title="EVENT DATA"
            icon={<BoltIcon sx={{ fontSize: 14, color: 'warning.main' }} />}
            fields={eventFields}
            nodeId={id}
            startIndex={workflowInputs.length}
            totalFields={totalFields}
          />
        )}
        {/* Empty state */}
        {totalFields === 0 && (
          <Box sx={{ p: 2, textAlign: 'center' }}>
            <Typography variant="caption" sx={{
              color: "text.secondary"
            }}>
              No input fields defined.
              <br />
              Configure workflow inputs in settings.
            </Typography>
          </Box>
        )}
        {/* Single output handle if no fields (for simple connection) */}
        {totalFields === 0 && (
          <Handle
            type="source"
            position={Position.Bottom}
            id="output"
            style={{
              width: 12,
              height: 12,
              background: CardColors.success,
              border: '2px solid white',
            }}
          />
        )}
      </Box>
    );
  }
);

UnifiedEntryNode.displayName = 'UnifiedEntryNode';
