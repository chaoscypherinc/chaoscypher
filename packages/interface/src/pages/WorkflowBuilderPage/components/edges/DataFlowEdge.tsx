// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * DataFlowEdge: Custom edge showing field-level data flow connections
 *
 * Displays a bezier curve with field mapping labels, color-coded by data type.
 * Shows source → target field connections along the edge path.
 */

import React, { useMemo } from 'react';
import { EdgeProps, Edge, getBezierPath, EdgeLabelRenderer } from '@xyflow/react';
import { Box, Typography, Chip, useTheme } from '@mui/material';
import ArrowRightAltIcon from '@mui/icons-material/ArrowRightAlt';
import type { DataFlowEdgeData, FieldConnection } from '../../types';

/**
 * Get stroke width based on selection state and connection count.
 */
function getStrokeWidth(selected: boolean, hasConnections: boolean): number {
  if (selected) return 3;
  if (hasConnections) return 2.5;
  return 2;
}

/**
 * Connection badge showing a single field mapping
 */
interface ConnectionBadgeProps {
  connection: FieldConnection;
  index: number;
  totalConnections: number;
  labelX: number;
  labelY: number;
  sourceX: number;
  targetX: number;
}

const ConnectionBadge: React.FC<ConnectionBadgeProps> = ({
  connection,
  index,
  totalConnections,
  labelX,
  labelY,
  sourceX: _sourceX,
  targetX: _targetX,
}) => {
  const theme = useTheme();

  // Calculate vertical offset for multiple connections
  const verticalSpacing = 22;
  const totalHeight = (totalConnections - 1) * verticalSpacing;
  const yOffset = index * verticalSpacing - totalHeight / 2;

  // Extract just the field name from the full path
  const sourceField = connection.sourceField.includes('.')
    ? connection.sourceField.split('.').pop()
    : connection.sourceField;
  const targetField = connection.targetField.includes('.')
    ? connection.targetField.split('.').pop()
    : connection.targetField;

  return (
    <Box
      sx={{
        position: 'absolute',
        transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY + yOffset}px)`,
        display: 'flex',
        alignItems: 'center',
        gap: 0.5,
        bgcolor: 'background.paper',
        borderRadius: 1,
        px: 0.75,
        py: 0.25,
        border: `1px solid ${theme.palette.divider}`,
        boxShadow: theme.shadows[1],
        pointerEvents: 'all',
        cursor: 'pointer',
        '&:hover': {
          boxShadow: theme.shadows[3],
          borderColor: theme.palette.primary.light,
        },
      }}
      className="nodrag nopan"
    >
      <Typography
        variant="caption"
        sx={{
          fontFamily: 'monospace',
          fontSize: '0.65rem',
          color: 'text.secondary',
          whiteSpace: 'nowrap',
        }}
      >
        {sourceField}
      </Typography>
      <ArrowRightAltIcon
        sx={{
          fontSize: 12,
          color: 'text.disabled',
        }}
      />
      <Typography
        variant="caption"
        sx={{
          fontFamily: 'monospace',
          fontSize: '0.65rem',
          color: 'text.primary',
          fontWeight: 500,
          whiteSpace: 'nowrap',
        }}
      >
        {targetField}
      </Typography>
    </Box>
  );
};

/**
 * Summary badge when there are many connections
 */
interface SummaryBadgeProps {
  count: number;
  labelX: number;
  labelY: number;
}

const SummaryBadge: React.FC<SummaryBadgeProps> = ({ count, labelX, labelY }) => {
  const theme = useTheme();

  return (
    <Chip
      label={`${count} mappings`}
      size="small"
      sx={{
        position: 'absolute',
        transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
        height: 20,
        fontSize: '0.65rem',
        bgcolor: 'background.paper',
        border: `1px solid ${theme.palette.divider}`,
        pointerEvents: 'all',
        cursor: 'pointer',
        '&:hover': {
          bgcolor: 'action.hover',
        },
      }}
      className="nodrag nopan"
    />
  );
};

export const DataFlowEdge: React.FC<EdgeProps<Edge<DataFlowEdgeData>>> = ({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  markerEnd,
  selected,
  data,
}) => {
  const theme = useTheme();

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  // Get field connections from edge data
  const connections = data?.fieldConnections || [];

  // Determine edge color based on selection and connections
  const edgeColor = useMemo(() => {
    if (selected) {
      return theme.palette.primary.main;
    }
    if (connections.length > 0) {
      // Use a blend color when there are mappings
      return theme.palette.info.main;
    }
    return theme.palette.grey[400];
  }, [selected, connections.length, theme]);

  // Determine if we should show individual badges or a summary
  const showIndividualBadges = connections.length <= 3;

  return (
    <>
      {/* Main edge path */}
      <path
        id={id}
        style={{
          ...style,
          stroke: edgeColor,
          strokeWidth: getStrokeWidth(selected ?? false, connections.length > 0),
          fill: 'none',
          transition: 'stroke 0.2s ease, stroke-width 0.2s ease',
        }}
        className="react-flow__edge-path"
        d={edgePath}
        markerEnd={markerEnd}
      />

      {/* Animated dots along path when data is flowing (optional future enhancement) */}
      {connections.length > 0 && (
        <path
          style={{
            stroke: `${edgeColor}40`,
            strokeWidth: 8,
            fill: 'none',
            strokeLinecap: 'round',
          }}
          d={edgePath}
        />
      )}

      <EdgeLabelRenderer>
        {/* Show simple label if no field connections */}
        {connections.length === 0 && data?.label && (
          <Box
            sx={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              fontSize: 11,
              fontWeight: 500,
              bgcolor: 'background.paper',
              px: 1,
              py: 0.25,
              borderRadius: 1,
              border: `1px solid ${theme.palette.divider}`,
              color: 'text.secondary',
              pointerEvents: 'all',
            }}
            className="nodrag nopan"
          >
            {data.label}
          </Box>
        )}

        {/* Show individual connection badges */}
        {showIndividualBadges &&
          connections.map((connection, index) => (
            <ConnectionBadge
              key={`${connection.sourceField}-${connection.targetField}`}
              connection={connection}
              index={index}
              totalConnections={connections.length}
              labelX={labelX}
              labelY={labelY}
              sourceX={sourceX}
              targetX={targetX}
            />
          ))}

        {/* Show summary badge for many connections */}
        {!showIndividualBadges && (
          <SummaryBadge count={connections.length} labelX={labelX} labelY={labelY} />
        )}

        {/* Branch label for conditional edges */}
        {data?.branch && (
          <Chip
            label={data.branch === 'true' ? 'Yes' : 'No'}
            size="small"
            color={data.branch === 'true' ? 'success' : 'error'}
            sx={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY - 30}px)`,
              height: 18,
              fontSize: '0.6rem',
              pointerEvents: 'all',
            }}
            className="nodrag nopan"
          />
        )}
      </EdgeLabelRenderer>
    </>
  );
};
