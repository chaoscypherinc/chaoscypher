// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ConditionalEdge: Custom ReactFlow edge for conditional branches
 *
 * Displays a colored edge indicating true (green) or false (red) branch
 * from a conditional node.
 */

import React from 'react';
import { EdgeProps, Edge, getBezierPath, EdgeLabelRenderer } from '@xyflow/react';
import { useTheme } from '@mui/material';
import type { ConditionalEdgeData } from '../../types';

export const ConditionalEdge: React.FC<EdgeProps<Edge<ConditionalEdgeData>>> = ({
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

  // Color based on branch type
  const branchColor =
    data?.branch === 'true' ? theme.palette.success.main : theme.palette.error.main;

  const edgeColor = selected ? theme.palette.primary.main : branchColor;

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

      {/* Branch label */}
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            fontSize: 10,
            fontWeight: 600,
            backgroundColor: branchColor,
            color: '#fff',
            padding: '2px 8px',
            borderRadius: 10,
            textTransform: 'uppercase',
            pointerEvents: 'all',
          }}
          className="nodrag nopan"
        >
          {data?.branch || 'true'}
        </div>
      </EdgeLabelRenderer>
    </>
  );
};
