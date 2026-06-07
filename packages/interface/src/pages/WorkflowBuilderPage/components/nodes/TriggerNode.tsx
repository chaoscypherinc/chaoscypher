// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TriggerNode: Custom ReactFlow node for workflow triggers (start nodes)
 *
 * Represents the entry point of a workflow, displaying the event source
 * that initiates workflow execution.
 */

import React, { memo } from 'react';
import { Handle, Position, NodeProps, Node } from '@xyflow/react';
import { Box, Typography, Paper, useTheme } from '@mui/material';
import { styled } from '@mui/material/styles';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import type { TriggerNodeData } from '../../types';

const NodeContainer = styled(Paper, {
  shouldForwardProp: (prop) => prop !== 'isSelected',
})<{ isSelected?: boolean }>(({ theme, isSelected }) => ({
  position: 'relative',
  padding: theme.spacing(1.5),
  minWidth: 160,
  maxWidth: 220,
  border: isSelected
    ? `2px solid ${theme.palette.primary.main}`
    : `1px solid ${theme.palette.success.main}`,
  borderRadius: theme.spacing(2), // More rounded for trigger nodes
  backgroundColor: theme.palette.background.paper,
  boxShadow: isSelected ? theme.shadows[6] : theme.shadows[2],
  cursor: 'grab',
  transition: 'all 0.2s ease-in-out',
  '&:hover': {
    boxShadow: theme.shadows[4],
    transform: 'scale(1.02)',
  },
}));

const NodeHeader = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1),
}));

const IconWrapper = styled(Box)(({ theme }) => ({
  width: 36,
  height: 36,
  borderRadius: '50%',
  backgroundColor: theme.palette.success.main,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  '& svg': {
    fontSize: 20,
    color: '#fff',
  },
}));

const NodeTitle = styled(Typography)(({ theme }) => ({
  fontWeight: 600,
  fontSize: '0.875rem',
  color: theme.palette.success.dark,
}));

const EventLabel = styled(Typography)(({ theme }) => ({
  fontSize: '0.75rem',
  color: theme.palette.text.secondary,
  marginTop: theme.spacing(0.5),
}));

/**
 * Custom comparison function for React.memo optimization
 */
function areNodesEqual(prevProps: NodeProps<Node<TriggerNodeData>>, nextProps: NodeProps<Node<TriggerNodeData>>) {
  return (
    prevProps.id === nextProps.id &&
    prevProps.selected === nextProps.selected &&
    prevProps.data.eventSource === nextProps.data.eventSource &&
    prevProps.data.label === nextProps.data.label
  );
}

const TriggerNodeComponent: React.FC<NodeProps<Node<TriggerNodeData>>> = ({ data, selected }) => {
  const theme = useTheme();

  return (
    <>
      <NodeContainer isSelected={selected} elevation={selected ? 4 : 1}>
        <NodeHeader>
          <IconWrapper>
            <PlayArrowIcon />
          </IconWrapper>
          <Box>
            <NodeTitle variant="body2">Trigger</NodeTitle>
            <EventLabel variant="caption">{data.label || data.eventSource || 'Manual'}</EventLabel>
          </Box>
        </NodeHeader>
      </NodeContainer>

      {/* Output handle (bottom) */}
      <Handle
        type="source"
        position={Position.Bottom}
        style={{
          width: 12,
          height: 12,
          bottom: -6,
          background: theme.palette.success.main,
          border: '2px solid #fff',
        }}
      />
    </>
  );
};

export const TriggerNode = memo(TriggerNodeComponent, areNodesEqual);
