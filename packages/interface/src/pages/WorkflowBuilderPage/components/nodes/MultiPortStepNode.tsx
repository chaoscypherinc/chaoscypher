// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * MultiPortStepNode: Enhanced workflow step node with multi-port data flow
 *
 * Displays a tool execution step with dynamic input/output ports for each
 * field in the tool's schema. Ports are color-coded by data type and
 * positioned along the top (inputs) and bottom (outputs) of the node.
 */

import React, { memo, useMemo } from 'react';
import { Handle, Position, NodeProps, Node } from '@xyflow/react';
import {
  Box,
  Typography,
  Paper,
  Chip,
  Divider,
  useTheme,
} from '@mui/material';
import { styled } from '@mui/material/styles';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import CodeIcon from '@mui/icons-material/Code';
import StorageIcon from '@mui/icons-material/Storage';
import HttpIcon from '@mui/icons-material/Http';
import BuildIcon from '@mui/icons-material/Build';
import type { MultiPortStepNodeData } from '../../types';
import { createPortId } from '../../types/dataflow';
import { CategoryColors } from '../../../../theme/colors';
import { PortList } from './PortList';
import { PortHandle } from './PortHandle';

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
  minWidth: 240,
  maxWidth: 320,
  border: isSelected
    ? `2px solid ${theme.palette.primary.main}`
    : `1px solid ${categoryColor || theme.palette.divider}`,
  borderRadius: theme.spacing(1),
  backgroundColor: theme.palette.background.paper,
  boxShadow: isSelected ? theme.shadows[6] : theme.shadows[2],
  cursor: 'grab',
  transition: 'all 0.2s ease-in-out',
  overflow: 'hidden',
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
  padding: theme.spacing(1, 1.5),
  borderBottom: `1px solid ${theme.palette.divider}`,
}));

const IconWrapper = styled(Box)<{ bgcolor?: string }>(({ theme, bgcolor }) => ({
  width: 28,
  height: 28,
  borderRadius: theme.spacing(0.5),
  backgroundColor: bgcolor || theme.palette.grey[200],
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  '& svg': {
    fontSize: 16,
    color: '#fff',
  },
}));

/**
 * Custom comparison function for React.memo optimization
 */
function areNodesEqual(
  prevProps: NodeProps<Node<MultiPortStepNodeData>>,
  nextProps: NodeProps<Node<MultiPortStepNodeData>>
) {
  return (
    prevProps.id === nextProps.id &&
    prevProps.selected === nextProps.selected &&
    prevProps.data.name === nextProps.data.name &&
    prevProps.data.toolCategory === nextProps.data.toolCategory &&
    prevProps.data.executionStatus === nextProps.data.executionStatus &&
    prevProps.data.inputPorts?.length === nextProps.data.inputPorts?.length &&
    prevProps.data.outputPorts?.length === nextProps.data.outputPorts?.length &&
    JSON.stringify(prevProps.data.configuration) === JSON.stringify(nextProps.data.configuration)
  );
}

/**
 * MultiPortStepNode component
 */
const MultiPortStepNodeComponent: React.FC<NodeProps<Node<MultiPortStepNodeData>>> = ({
  id,
  data,
  selected,
}) => {
  const theme = useTheme();
  const categoryColor = CategoryColors[data.toolCategory] || theme.palette.grey[500];
  const IconComponent = CATEGORY_ICONS[data.toolCategory] || BuildIcon;

  // Extract fields from ports
  const inputFields = useMemo(() => {
    return (data.inputPorts || []).map((port) => port.schema);
  }, [data.inputPorts]);

  const outputFields = useMemo(() => {
    return (data.outputPorts || []).map((port) => port.schema);
  }, [data.outputPorts]);

  // Check which fields are connected (based on configuration having a template reference)
  const connectedInputs = useMemo(() => {
    const connected = new Set<string>();
    if (data.configuration) {
      Object.entries(data.configuration).forEach(([key, value]) => {
        if (typeof value === 'string' && value.includes('{{ steps.')) {
          connected.add(key);
        }
      });
    }
    return connected;
  }, [data.configuration]);

  // Calculate handle positions
  const inputHandlePositions = useMemo(() => {
    const count = inputFields.length;
    if (count === 0) return [];
    return inputFields.map((_, index) => {
      const offset = (index + 1) / (count + 1);
      return `${offset * 100}%`;
    });
  }, [inputFields]);

  const outputHandlePositions = useMemo(() => {
    const count = outputFields.length;
    if (count === 0) return [];
    return outputFields.map((_, index) => {
      const offset = (index + 1) / (count + 1);
      return `${offset * 100}%`;
    });
  }, [outputFields]);

  return (
    <>
      {/* Input handles at top */}
      {inputFields.map((field, index) => (
        <PortHandle
          key={createPortId(id, field.name)}
          portId={createPortId(id, field.name)}
          field={field}
          direction="input"
          leftPosition={inputHandlePositions[index]}
          connected={connectedInputs.has(field.name)}
        />
      ))}
      {/* Fallback single input handle when no input fields */}
      {inputFields.length === 0 && (
        <Handle
          type="target"
          position={Position.Top}
          id="input"
          style={{
            width: 12,
            height: 12,
            top: -6,
            background: categoryColor,
            border: '2px solid #fff',
          }}
        />
      )}
      <NodeContainer
        isSelected={selected}
        categoryColor={categoryColor}
        executionStatus={data.executionStatus}
        elevation={selected ? 4 : 1}
      >
        {/* Header */}
        <NodeHeader>
          <IconWrapper bgcolor={categoryColor}>
            <IconComponent />
          </IconWrapper>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 600,
                fontSize: '0.8rem',
                lineHeight: 1.2
              }}>
              {data.name}
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.25 }}>
              <Chip
                label={data.toolCategory}
                size="small"
                sx={{
                  height: 14,
                  fontSize: '0.6rem',
                  bgcolor: `${categoryColor}20`,
                  color: categoryColor,
                }}
              />
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
                  sx={{ height: 14, fontSize: '0.6rem' }}
                />
              )}
            </Box>
          </Box>
        </NodeHeader>

        {/* Input Fields Section */}
        {inputFields.length > 0 && (
          <PortList
            fields={inputFields}
            direction="input"
            connectedFields={connectedInputs}
          />
        )}

        {/* Divider between inputs and outputs */}
        {inputFields.length > 0 && outputFields.length > 0 && (
          <Divider sx={{ mx: 1 }} />
        )}

        {/* Output Fields Section */}
        {outputFields.length > 0 && (
          <PortList fields={outputFields} direction="output" />
        )}

        {/* Empty state when no ports defined */}
        {inputFields.length === 0 && outputFields.length === 0 && (
          <Box sx={{ p: 1.5 }}>
            {data.description && (
              <Typography
                variant="caption"
                sx={{
                  color: "text.secondary",
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden'
                }}>
                {data.description}
              </Typography>
            )}
          </Box>
        )}
      </NodeContainer>
      {/* Output handles at bottom */}
      {outputFields.map((field, index) => (
        <PortHandle
          key={createPortId(id, field.name)}
          portId={createPortId(id, field.name)}
          field={field}
          direction="output"
          leftPosition={outputHandlePositions[index]}
        />
      ))}
      {/* Fallback single output handle when no output fields */}
      {outputFields.length === 0 && (
        <Handle
          type="source"
          position={Position.Bottom}
          id="output"
          style={{
            width: 12,
            height: 12,
            bottom: -6,
            background: categoryColor,
            border: '2px solid #fff',
          }}
        />
      )}
    </>
  );
};

export const MultiPortStepNode = memo(MultiPortStepNodeComponent, areNodesEqual);
