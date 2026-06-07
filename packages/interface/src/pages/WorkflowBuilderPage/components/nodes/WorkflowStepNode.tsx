// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * WorkflowStepNode: Custom ReactFlow node for workflow tool steps
 *
 * Displays a tool execution step with icon, name, and configuration preview.
 * Supports selection, connection handles, and execution status indicators.
 */

import React, { memo } from 'react';
import { Handle, Position, NodeProps, Node } from '@xyflow/react';
import { Box, Typography, Paper, Chip, useTheme } from '@mui/material';
import { styled } from '@mui/material/styles';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import CodeIcon from '@mui/icons-material/Code';
import StorageIcon from '@mui/icons-material/Storage';
import HttpIcon from '@mui/icons-material/Http';
import BuildIcon from '@mui/icons-material/Build';
import type { WorkflowStepNodeData } from '../../types';
import { CategoryColors } from '../../../../theme/colors';

// Tool category icons
const CATEGORY_ICONS: Record<string, React.ElementType> = {
  ai: SmartToyIcon,
  graph: AccountTreeIcon,
  logic: CodeIcon,
  data: StorageIcon,
  http: HttpIcon,
  external: HttpIcon,
  templates: BuildIcon,
  template: BuildIcon,
};

interface NodeContainerProps {
  isSelected?: boolean;
  categoryColor?: string;
  executionStatus?: string;
}

const NodeContainer = styled(Paper, {
  shouldForwardProp: (prop) =>
    prop !== 'isSelected' && prop !== 'categoryColor' && prop !== 'executionStatus',
})<NodeContainerProps>(({ theme, isSelected, categoryColor, executionStatus }) => ({
  position: 'relative',
  padding: theme.spacing(1.5),
  minWidth: 180,
  maxWidth: 280,
  border: isSelected
    ? `2px solid ${theme.palette.primary.main}`
    : `1px solid ${categoryColor || theme.palette.divider}`,
  borderRadius: theme.spacing(1),
  backgroundColor: theme.palette.background.paper,
  boxShadow: isSelected ? theme.shadows[6] : theme.shadows[2],
  cursor: 'grab',
  transition: 'all 0.2s ease-in-out',
  // Execution status styling
  ...(executionStatus === 'running' && {
    borderColor: theme.palette.info.main,
    boxShadow: `0 0 0 2px ${theme.palette.info.main}40`,
    animation: 'pulse 1.5s infinite',
  }),
  ...(executionStatus === 'completed' && {
    borderColor: theme.palette.success.main,
  }),
  ...(executionStatus === 'failed' && {
    borderColor: theme.palette.error.main,
    boxShadow: `0 0 0 2px ${theme.palette.error.main}40`,
  }),
  '&:hover': {
    boxShadow: theme.shadows[4],
    transform: 'scale(1.02)',
  },
  '@keyframes pulse': {
    '0%': { opacity: 1 },
    '50%': { opacity: 0.7 },
    '100%': { opacity: 1 },
  },
}));

const NodeHeader = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1),
  marginBottom: theme.spacing(0.5),
}));

const IconWrapper = styled(Box)<{ bgcolor?: string }>(({ theme, bgcolor }) => ({
  width: 32,
  height: 32,
  borderRadius: theme.spacing(0.5),
  backgroundColor: bgcolor || theme.palette.grey[200],
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  '& svg': {
    fontSize: 18,
    color: '#fff',
  },
}));

const NodeTitle = styled(Typography)(({ theme: _theme }) => ({
  fontWeight: 600,
  fontSize: '0.875rem',
  lineHeight: 1.2,
  wordBreak: 'break-word',
}));

const NodeDescription = styled(Typography)(({ theme }) => ({
  fontSize: '0.75rem',
  color: theme.palette.text.secondary,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  display: '-webkit-box',
  WebkitLineClamp: 2,
  WebkitBoxOrient: 'vertical',
}));

/**
 * Custom comparison function for React.memo optimization
 */
function areNodesEqual(prevProps: NodeProps<Node<WorkflowStepNodeData>>, nextProps: NodeProps<Node<WorkflowStepNodeData>>) {
  return (
    prevProps.id === nextProps.id &&
    prevProps.selected === nextProps.selected &&
    prevProps.data.name === nextProps.data.name &&
    prevProps.data.toolCategory === nextProps.data.toolCategory &&
    prevProps.data.executionStatus === nextProps.data.executionStatus &&
    JSON.stringify(prevProps.data.configuration) === JSON.stringify(nextProps.data.configuration)
  );
}

const WorkflowStepNodeComponent: React.FC<NodeProps<Node<WorkflowStepNodeData>>> = ({
  data,
  selected,
}) => {
  const theme = useTheme();
  const categoryColor = CategoryColors[data.toolCategory] || theme.palette.grey[500];
  const IconComponent = CATEGORY_ICONS[data.toolCategory] || BuildIcon;

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
          background: categoryColor,
          border: '2px solid #fff',
        }}
      />

      <NodeContainer
        isSelected={selected}
        categoryColor={categoryColor}
        executionStatus={data.executionStatus}
        elevation={selected ? 4 : 1}
      >
        <NodeHeader>
          <IconWrapper bgcolor={categoryColor}>
            <IconComponent />
          </IconWrapper>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <NodeTitle variant="body2">{data.name}</NodeTitle>
            <Chip
              label={data.toolCategory}
              size="small"
              sx={{
                height: 16,
                fontSize: '0.65rem',
                bgcolor: `${categoryColor}20`,
                color: categoryColor,
                mt: 0.25,
              }}
            />
          </Box>
        </NodeHeader>

        {data.description && (
          <NodeDescription variant="body2">{data.description}</NodeDescription>
        )}

        {/* Execution status indicator */}
        {data.executionStatus && data.executionStatus !== 'pending' && (
          <Chip
            label={data.executionStatus}
            size="small"
            color={
              data.executionStatus === 'completed'
                ? 'success'
                : data.executionStatus === 'failed'
                ? 'error'
                : data.executionStatus === 'running'
                ? 'info'
                : 'default'
            }
            sx={{ mt: 1, height: 20, fontSize: '0.7rem' }}
          />
        )}
      </NodeContainer>

      {/* Output handle (bottom) */}
      <Handle
        type="source"
        position={Position.Bottom}
        style={{
          width: 12,
          height: 12,
          bottom: -6,
          background: categoryColor,
          border: '2px solid #fff',
        }}
      />
    </>
  );
};

export const WorkflowStepNode = memo(WorkflowStepNodeComponent, areNodesEqual);
