// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import React from 'react';
import { Link as RouterLink } from 'react-router';
import {
  Box,
  Typography,
  IconButton,
  Tooltip,
  Breadcrumbs,
  Link,
  alpha,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import RefreshIcon from '@mui/icons-material/Refresh';
import EditIcon from '@mui/icons-material/Edit';
import { ChaosCypherPalette } from '../../theme/palette';
import { glassPanelSx } from '../../theme/cardStyles';
import type { Workflow, WorkflowStats } from '../../services/api/workflows';
import { formatDurationMs } from '../../utils/formatters';

const CYAN = ChaosCypherPalette.primary;

interface ExecutionHistoryHeaderProps {
  workflow: Workflow | null;
  stats: WorkflowStats | null;
  onBack: () => void;
  onEdit: () => void;
  onRefresh: () => void;
}

export const ExecutionHistoryHeader: React.FC<ExecutionHistoryHeaderProps> = ({
  workflow,
  stats,
  onBack,
  onEdit,
  onRefresh,
}) => {
  const formatDuration = (ms?: number): string => formatDurationMs(ms ?? null);

  return (
    <>
      {/* Breadcrumbs */}
      <Breadcrumbs sx={{ mb: 2 }}>
        <Link
          component={RouterLink}
          to="/automations"
          underline="hover"
          sx={{ color: alpha(CYAN, 0.7), '&:hover': { color: CYAN } }}
        >
          Automations
        </Link>
        <Typography sx={{
          color: "text.primary"
        }}>
          {workflow?.name || 'Workflow'} - Execution History
        </Typography>
      </Breadcrumbs>
      {/* Header */}
      <Box sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 2, mb: 3 }}>
        <Tooltip title="Back to Automations">
          <IconButton aria-label="Back to Automations" onClick={onBack} sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
            <ArrowBackIcon />
          </IconButton>
        </Tooltip>

        <Box sx={{ flex: 1 }}>
          <Typography variant="h5">
            {workflow?.name || 'Workflow'} - Execution History
          </Typography>
          {workflow?.description && (
            <Typography variant="body2" sx={{
              color: "text.secondary"
            }}>
              {workflow.description}
            </Typography>
          )}
        </Box>

        <Tooltip title="Edit Workflow">
          <IconButton
            aria-label="Edit Workflow"
            onClick={onEdit}
            sx={{ color: CYAN, '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}
          >
            <EditIcon />
          </IconButton>
        </Tooltip>

        <Tooltip title="Refresh">
          <IconButton aria-label="Refresh" onClick={onRefresh} sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
            <RefreshIcon />
          </IconButton>
        </Tooltip>
      </Box>
      {/* Stats Cards */}
      {stats && (
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 2, mb: 3 }}>
          <Box sx={{ ...glassPanelSx, p: 2, textAlign: 'center' }}>
            <Typography variant="h4">{stats.total_executions}</Typography>
            <Typography variant="body2" sx={{
              color: "text.secondary"
            }}>Total Runs</Typography>
          </Box>
          <Box sx={{ ...glassPanelSx, p: 2, textAlign: 'center' }}>
            <Typography variant="h4" sx={{ color: 'success.main' }}>{stats.successful_executions}</Typography>
            <Typography variant="body2" sx={{
              color: "text.secondary"
            }}>Successful</Typography>
          </Box>
          <Box sx={{ ...glassPanelSx, p: 2, textAlign: 'center' }}>
            <Typography variant="h4" sx={{ color: 'error.main' }}>{stats.failed_executions}</Typography>
            <Typography variant="body2" sx={{
              color: "text.secondary"
            }}>Failed</Typography>
          </Box>
          <Box sx={{ ...glassPanelSx, p: 2, textAlign: 'center' }}>
            <Typography variant="h4">
              {stats.avg_duration_ms ? formatDuration(stats.avg_duration_ms) : '-'}
            </Typography>
            <Typography variant="body2" sx={{
              color: "text.secondary"
            }}>Avg Duration</Typography>
          </Box>
        </Box>
      )}
    </>
  );
};
