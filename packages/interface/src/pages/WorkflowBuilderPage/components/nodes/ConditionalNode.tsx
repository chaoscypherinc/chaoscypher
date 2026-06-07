// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ConditionalNode: Custom ReactFlow node for if/else branching
 *
 * Diamond-shaped node representing conditional logic with two output branches
 * (true and false) for workflow path splitting.
 */

import React, { memo } from 'react';
import { Handle, Position, NodeProps, Node } from '@xyflow/react';
import { Box, Typography, useTheme } from '@mui/material';
import { styled } from '@mui/material/styles';
import CallSplitIcon from '@mui/icons-material/CallSplit';
import type { ConditionalNodeData } from '../../types';

const DiamondContainer = styled(Box, {
  shouldForwardProp: (prop) => prop !== 'isSelected',
})<{ isSelected?: boolean }>(({ theme, isSelected }) => ({
  position: 'relative',
  width: 120,
  height: 120,
  transform: 'rotate(45deg)',
  border: isSelected
    ? `2px solid ${theme.palette.primary.main}`
    : `1px solid ${theme.palette.warning.main}`,
  borderRadius: theme.spacing(1),
  backgroundColor: theme.palette.background.paper,
  boxShadow: isSelected ? theme.shadows[6] : theme.shadows[2],
  cursor: 'grab',
  transition: 'all 0.2s ease-in-out',
  '&:hover': {
    boxShadow: theme.shadows[4],
    transform: 'rotate(45deg) scale(1.02)',
  },
}));

const DiamondContent = styled(Box)(({ theme }) => ({
  position: 'absolute',
  top: '50%',
  left: '50%',
  transform: 'translate(-50%, -50%) rotate(-45deg)',
  textAlign: 'center',
  width: '100%',
  padding: theme.spacing(1),
}));

const IconWrapper = styled(Box)(({ theme }) => ({
  width: 32,
  height: 32,
  borderRadius: '50%',
  backgroundColor: theme.palette.warning.main,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  margin: '0 auto',
  marginBottom: theme.spacing(0.5),
  '& svg': {
    fontSize: 18,
    color: '#fff',
  },
}));

const NodeTitle = styled(Typography)(({ theme }) => ({
  fontWeight: 600,
  fontSize: '0.75rem',
  color: theme.palette.warning.dark,
}));

const ConditionPreview = styled(Typography)(({ theme }) => ({
  fontSize: '0.65rem',
  color: theme.palette.text.secondary,
  maxWidth: 80,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  margin: '0 auto',
}));

const BranchLabel = styled(Typography)<{ branch: 'true' | 'false' }>(({ theme, branch }) => ({
  position: 'absolute',
  fontSize: '0.65rem',
  fontWeight: 600,
  color: branch === 'true' ? theme.palette.success.main : theme.palette.error.main,
  ...(branch === 'true'
    ? { right: -30, top: '50%', transform: 'translateY(-50%) rotate(-45deg)' }
    : { left: -30, top: '50%', transform: 'translateY(-50%) rotate(-45deg)' }),
}));

/**
 * Custom comparison function for React.memo optimization
 */
function areNodesEqual(
  prevProps: NodeProps<Node<ConditionalNodeData>>,
  nextProps: NodeProps<Node<ConditionalNodeData>>
) {
  return (
    prevProps.id === nextProps.id &&
    prevProps.selected === nextProps.selected &&
    prevProps.data.name === nextProps.data.name &&
    JSON.stringify(prevProps.data.condition) === JSON.stringify(nextProps.data.condition)
  );
}

const ConditionalNodeComponent: React.FC<NodeProps<Node<ConditionalNodeData>>> = ({ data, selected }) => {
  const theme = useTheme();

  // Format condition for preview
  const conditionPreview = data.condition
    ? `${data.condition.field} ${data.condition.operator}`
    : 'No condition';

  return (
    <>
      {/* Input handle (top) */}
      <Handle
        type="target"
        position={Position.Top}
        style={{
          width: 12,
          height: 12,
          top: -6,
          background: theme.palette.warning.main,
          border: '2px solid #fff',
        }}
      />

      <DiamondContainer isSelected={selected}>
        <DiamondContent>
          <IconWrapper>
            <CallSplitIcon />
          </IconWrapper>
          <NodeTitle variant="body2">{data.name || 'Condition'}</NodeTitle>
          <ConditionPreview variant="caption">{conditionPreview}</ConditionPreview>
        </DiamondContent>

        {/* Branch labels */}
        <BranchLabel branch="true">True</BranchLabel>
        <BranchLabel branch="false">False</BranchLabel>
      </DiamondContainer>

      {/* True branch handle (right) */}
      <Handle
        type="source"
        position={Position.Right}
        id="true"
        className="handle-true"
        style={{
          width: 12,
          height: 12,
          right: -6,
          background: theme.palette.success.main,
          border: '2px solid #fff',
        }}
      />

      {/* False branch handle (left) */}
      <Handle
        type="source"
        position={Position.Left}
        id="false"
        className="handle-false"
        style={{
          width: 12,
          height: 12,
          left: -6,
          background: theme.palette.error.main,
          border: '2px solid #fff',
        }}
      />
    </>
  );
};

export const ConditionalNode = memo(ConditionalNodeComponent, areNodesEqual);
