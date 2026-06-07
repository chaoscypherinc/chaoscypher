// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * WorkflowEdge: Custom ReactFlow edge for standard workflow connections
 *
 * Displays a bezier curve connecting workflow steps with optional label
 * and selection highlighting.
 */

import React from 'react';
import { EdgeProps, Edge, getBezierPath, EdgeLabelRenderer } from '@xyflow/react';
import { useTheme } from '@mui/material';
import type { WorkflowEdgeData } from '../../types';

export const WorkflowEdge: React.FC<EdgeProps<Edge<WorkflowEdgeData>>> = ({
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

  const edgeColor = selected ? theme.palette.primary.main : theme.palette.grey[400];

  return (
    <>
      <path
        id={id}
        style={{
          ...style,
          stroke: edgeColor,
          strokeWidth: selected ? 3 : 2,
          fill: 'none',
        }}
        className="react-flow__edge-path"
        d={edgePath}
        markerEnd={markerEnd}
      />

      {/* Optional label */}
      {data?.label && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              fontSize: 11,
              fontWeight: 500,
              backgroundColor: theme.palette.background.paper,
              padding: '2px 6px',
              borderRadius: 4,
              border: `1px solid ${theme.palette.divider}`,
              color: theme.palette.text.secondary,
              pointerEvents: 'all',
            }}
            className="nodrag nopan"
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
};
