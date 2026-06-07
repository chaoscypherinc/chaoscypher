// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * EventTriggerNode: Custom ReactFlow node for individual event triggers
 *
 * Represents a single trigger that can start a workflow when its event
 * conditions are met. Each trigger is displayed as its own node and can
 * be configured independently.
 */

import React, { memo, useCallback } from 'react';
import { Handle, Position, NodeProps, Node } from '@xyflow/react';
import { Box, Typography, Paper, Switch, Chip, Tooltip, useTheme } from '@mui/material';
import { styled } from '@mui/material/styles';
import BoltIcon from '@mui/icons-material/Bolt';
import FilterListIcon from '@mui/icons-material/FilterList';
import type { EventTriggerNodeData } from '../../types';
import { EVENT_SOURCE_INFO, type EventSource } from '../../constants/eventSchemas';

const NodeContainer = styled(Paper, {
  shouldForwardProp: (prop) => prop !== 'isSelected' && prop !== 'isEnabled',
})<{ isSelected?: boolean; isEnabled?: boolean }>(({ theme, isSelected, isEnabled }) => ({
  position: 'relative',
  padding: theme.spacing(1.5),
  minWidth: 200,
  maxWidth: 260,
  border: isSelected
    ? `2px solid ${theme.palette.primary.main}`
    : `1px solid ${isEnabled ? theme.palette.warning.main : theme.palette.grey[400]}`,
  borderRadius: theme.spacing(1.5),
  backgroundColor: theme.palette.background.paper,
  boxShadow: isSelected ? theme.shadows[6] : theme.shadows[2],
  cursor: 'grab',
  transition: 'all 0.2s ease-in-out',
  opacity: isEnabled ? 1 : 0.7,
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

const IconWrapper = styled(Box, {
  shouldForwardProp: (prop) => prop !== 'isEnabled',
})<{ isEnabled?: boolean }>(({ theme, isEnabled }) => ({
  width: 32,
  height: 32,
  borderRadius: '50%',
  backgroundColor: isEnabled ? theme.palette.warning.main : theme.palette.grey[400],
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  transition: 'background-color 0.2s ease',
  '& svg': {
    fontSize: 18,
    color: '#fff',
  },
}));

const NodeTitle = styled(Typography, {
  shouldForwardProp: (prop) => prop !== 'isEnabled',
})<{ isEnabled?: boolean }>(({ theme, isEnabled }) => ({
  fontWeight: 600,
  fontSize: '0.8rem',
  color: isEnabled ? theme.palette.warning.dark : theme.palette.grey[600],
  maxWidth: 140,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}));

const EventLabel = styled(Typography)(({ theme }) => ({
  fontSize: '0.7rem',
  color: theme.palette.text.secondary,
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(0.5),
}));

const ControlsRow = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  marginTop: theme.spacing(1),
  paddingTop: theme.spacing(0.5),
  borderTop: `1px solid ${theme.palette.divider}`,
}));

/**
 * Check if filters object has any actual filters defined
 */
function hasFilters(filters: Record<string, unknown> | null | undefined): boolean {
  if (!filters) return false;
  return Object.keys(filters).length > 0;
}

/**
 * Custom comparison function for React.memo optimization
 */
function areNodesEqual(
  prevProps: NodeProps<Node<EventTriggerNodeData>>,
  nextProps: NodeProps<Node<EventTriggerNodeData>>
): boolean {
  return (
    prevProps.id === nextProps.id &&
    prevProps.selected === nextProps.selected &&
    prevProps.data.triggerId === nextProps.data.triggerId &&
    prevProps.data.name === nextProps.data.name &&
    prevProps.data.eventSource === nextProps.data.eventSource &&
    prevProps.data.enabled === nextProps.data.enabled &&
    JSON.stringify(prevProps.data.filters) === JSON.stringify(nextProps.data.filters)
  );
}

const EventTriggerNodeComponent: React.FC<NodeProps<Node<EventTriggerNodeData>>> = ({
  id: _id,
  data,
  selected,
}) => {
  const theme = useTheme();
  const { name, eventSource, filters, enabled, triggerId } = data;

  // Get event source display info
  const eventInfo = EVENT_SOURCE_INFO[eventSource as EventSource];
  const eventLabel = eventInfo?.label || eventSource;
  const eventDescription = eventInfo?.description || 'Unknown event type';

  const filtersActive = hasFilters(filters);
  const filterCount = filters ? Object.keys(filters).length : 0;

  // Handle switch click without triggering node selection
  const handleSwitchClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
  }, []);

  return (
    <>
      <NodeContainer isSelected={selected} isEnabled={enabled} elevation={selected ? 4 : 1}>
        <NodeHeader>
          <IconWrapper isEnabled={enabled}>
            <BoltIcon />
          </IconWrapper>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Tooltip title={name} placement="top">
              <NodeTitle variant="body2" isEnabled={enabled}>
                {name}
              </NodeTitle>
            </Tooltip>
            <Tooltip title={eventDescription} placement="bottom">
              <EventLabel variant="caption">{eventLabel}</EventLabel>
            </Tooltip>
          </Box>
        </NodeHeader>

        <ControlsRow>
          {/* Enabled toggle */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Switch
              size="small"
              checked={enabled}
              onClick={handleSwitchClick}
              sx={{
                '& .MuiSwitch-switchBase.Mui-checked': {
                  color: theme.palette.warning.main,
                },
                '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
                  backgroundColor: theme.palette.warning.main,
                },
              }}
            />
            <Typography variant="caption" sx={{
              color: "text.secondary"
            }}>
              {enabled ? 'Enabled' : 'Disabled'}
            </Typography>
          </Box>

          {/* Filter indicator */}
          {filtersActive && (
            <Tooltip title={`${filterCount} filter${filterCount !== 1 ? 's' : ''} active`}>
              <Chip
                icon={<FilterListIcon sx={{ fontSize: 14 }} />}
                label={filterCount}
                size="small"
                sx={{
                  height: 20,
                  fontSize: '0.65rem',
                  bgcolor: 'info.main',
                  color: 'info.contrastText',
                  '& .MuiChip-icon': {
                    color: 'inherit',
                  },
                }}
              />
            </Tooltip>
          )}

          {/* New indicator for unsaved triggers */}
          {!triggerId && (
            <Chip
              label="New"
              size="small"
              sx={{
                height: 18,
                fontSize: '0.6rem',
                bgcolor: 'success.light',
                color: 'success.contrastText',
              }}
            />
          )}
        </ControlsRow>
      </NodeContainer>
      {/* Output handle (bottom) - connects to workflow steps */}
      <Handle
        type="source"
        position={Position.Bottom}
        style={{
          width: 12,
          height: 12,
          bottom: -6,
          background: enabled ? theme.palette.warning.main : theme.palette.grey[400],
          border: '2px solid #fff',
        }}
      />
    </>
  );
};

export const EventTriggerNode = memo(EventTriggerNodeComponent, areNodesEqual);
