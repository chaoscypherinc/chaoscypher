// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Workflow Card
 *
 * Displays a single workflow as a card with metadata, status chips,
 * and action buttons for execution, editing, and management.
 */

import React from 'react';
import {
  Box,
  Typography,
  Chip,
  IconButton,
  Switch,
  Tooltip,
} from '@mui/material';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import PlayIcon from '@mui/icons-material/PlayArrow';
import CopyIcon from '@mui/icons-material/FileCopy';
import HistoryIcon from '@mui/icons-material/History';
import ViewIcon from '@mui/icons-material/Visibility';
import { getCardStyle, CardColors } from '../theme/cardStyles';
import { ghostSwitchSx } from '../theme/ghostStyles';

interface Workflow {
  id: string;
  name: string;
  description?: string;
  category?: string;
  is_system: boolean;
  is_active: boolean;
  expose_as_ai_tool: boolean;
  icon?: string;
  last_executed_at?: string;
}

interface WorkflowCardProps {
  workflow: Workflow;
  onExecute: () => void;
  onViewSteps: () => void;
  onEdit: () => void;
  onHistory: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onToggleActive: () => void;
}

/** Card displaying a single workflow with action buttons. */
const WorkflowCard: React.FC<WorkflowCardProps> = ({
  workflow,
  onExecute,
  onViewSteps,
  onEdit,
  onHistory,
  onDuplicate,
  onDelete,
  onToggleActive,
}) => {
  return (
    <Box sx={{ flex: '1 1 calc(33.333% - 11px)', minWidth: 300 }}>
      <Box sx={{ ...getCardStyle(CardColors.info, false), p: 2.5, display: 'flex', flexDirection: 'column', alignItems: 'stretch', height: '100%', minHeight: 240 }}>
        <Box sx={{ flexGrow: 1, mb: 2 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2, gap: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexGrow: 1 }}>
              {workflow.icon && <span style={{ fontSize: '1.5rem' }}>{workflow.icon}</span>}
              <Typography variant="h6" sx={{ fontWeight: 600 }}>{workflow.name}</Typography>
            </Box>
            <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', justifyContent: 'flex-end', flexShrink: 0 }}>
              {workflow.is_system && <Chip label="System" size="small" color="primary" />}
              {workflow.expose_as_ai_tool && <Chip label="AI Tool" size="small" color="secondary" />}
              {!workflow.is_active && <Chip label="Inactive" size="small" color="warning" />}
            </Box>
          </Box>

          {workflow.description && (
            <Typography
              variant="body2"
              sx={{
                color: "text.secondary",
                mb: 2,
                lineHeight: 1.5
              }}>
              {workflow.description}
            </Typography>
          )}

          {workflow.category && (
            <Chip label={workflow.category} size="small" variant="outlined" sx={{ mb: 1.5 }} />
          )}

          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              display: "block"
            }}>
            Last run: {workflow.last_executed_at
              ? new Date(workflow.last_executed_at).toLocaleString()
              : 'Never'}
          </Typography>
        </Box>

        <Box sx={{ display: 'flex', gap: 0.5, pt: 2, borderTop: 1, borderColor: 'rgba(255, 255, 255, 0.06)', justifyContent: 'flex-start' }}>
          <Tooltip title="Execute">
            <IconButton aria-label="Execute" size="small" onClick={onExecute} disabled={!workflow.is_active} sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
              <PlayIcon />
            </IconButton>
          </Tooltip>
          <Tooltip title="View Steps">
            <IconButton aria-label="View Steps" size="small" onClick={onViewSteps} sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
              <ViewIcon />
            </IconButton>
          </Tooltip>
          <Tooltip title="Edit Workflow">
            <IconButton aria-label="Edit Workflow" size="small" onClick={onEdit} sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
              <EditIcon />
            </IconButton>
          </Tooltip>
          <Tooltip title="Execution History">
            <IconButton aria-label="Execution History" size="small" onClick={onHistory} sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
              <HistoryIcon />
            </IconButton>
          </Tooltip>
          {!workflow.is_system && (
            <>
              <Tooltip title="Duplicate">
                <IconButton aria-label="Duplicate workflow" size="small" onClick={onDuplicate} sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
                  <CopyIcon />
                </IconButton>
              </Tooltip>
              <Tooltip title="Delete">
                <IconButton aria-label="Delete workflow" size="small" onClick={onDelete} sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
                  <DeleteIcon />
                </IconButton>
              </Tooltip>
            </>
          )}
          <Tooltip title={workflow.is_active ? 'Deactivate' : 'Activate'}>
            <Switch
              size="small"
              checked={workflow.is_active}
              onChange={onToggleActive}
              sx={ghostSwitchSx}
            />
          </Tooltip>
        </Box>
      </Box>
    </Box>
  );
};

export default WorkflowCard;
