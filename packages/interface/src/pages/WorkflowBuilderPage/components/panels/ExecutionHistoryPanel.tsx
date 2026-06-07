// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ExecutionHistoryPanel: Displays workflow execution history with status and details
 *
 * Shows a list of past executions with their status, duration, and ability to view
 * detailed output. Can be used in the workflow builder or as a standalone component.
 */

import React, { useState } from 'react';
import {
  Box,
  Typography,
  List,
  ListItemButton,
  ListItemText,
  ListItemIcon,
  Chip,
  CircularProgress,
  Alert,
  IconButton,
  Collapse,
  Paper,
  Divider,
  Button,
  Tooltip,
  Skeleton,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import CancelIcon from '@mui/icons-material/Cancel';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import RefreshIcon from '@mui/icons-material/Refresh';
import HistoryIcon from '@mui/icons-material/History';
import type { WorkflowExecutionDetail } from '../../../../services/api/workflows';
import {
  useWorkflowExecutions,
  useWorkflowExecution,
} from '../../../../services/api/useWorkflowExecutions';
import { formatDurationMs, formatRelativeTime } from '../../../../utils/formatters';

interface ExecutionHistoryPanelProps {
  workflowId: string;
  compact?: boolean;
  maxItems?: number;
  onExecutionSelect?: (executionId: string) => void;
  autoRefresh?: boolean;
  refreshInterval?: number;
}

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <HourglassEmptyIcon color="disabled" />,
  running: <PlayArrowIcon color="info" />,
  completed: <CheckCircleIcon color="success" />,
  failed: <ErrorIcon color="error" />,
  cancelled: <CancelIcon color="warning" />,
};

const STATUS_COLORS: Record<string, 'default' | 'primary' | 'secondary' | 'error' | 'info' | 'success' | 'warning'> = {
  pending: 'default',
  running: 'info',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
};

export const ExecutionHistoryPanel: React.FC<ExecutionHistoryPanelProps> = ({
  workflowId,
  compact = false,
  maxItems = 10,
  onExecutionSelect,
  autoRefresh = false,
  refreshInterval = 5000,
}) => {
  const [expandedExecution, setExpandedExecution] = useState<string | null>(null);

  // List query. Auto-refresh (when enabled) is driven by the hook's
  // `refetchInterval`, which polls only while an execution is running/pending.
  const executionsQuery = useWorkflowExecutions(workflowId, {
    maxItems,
    pollInterval: autoRefresh ? refreshInterval : 0,
  });
  const executions = executionsQuery.data ?? [];
  const isLoading = executionsQuery.isLoading;
  const error = executionsQuery.isError ? 'Failed to load execution history' : null;
  const refetchExecutions = () => {
    void executionsQuery.refetch();
  };

  // Detail query for the currently-expanded execution. TanStack caches the
  // result per execution id, so re-expanding a previously-viewed row is
  // instant and details survive the panel's lifetime.
  const detailQuery = useWorkflowExecution(workflowId, expandedExecution);

  // Toggle expansion; the detail query fires automatically once an id is set.
  const handleToggleExpand = (executionId: string) => {
    if (expandedExecution === executionId) {
      setExpandedExecution(null);
      return;
    }

    setExpandedExecution(executionId);

    if (onExecutionSelect) {
      onExecutionSelect(executionId);
    }
  };

  // Format duration (delegate to centralized formatter)
  const formatDuration = (ms?: number): string => formatDurationMs(ms ?? null);

  // Format timestamp (delegate to centralized formatter)
  const formatTime = (timestamp?: string): string => formatRelativeTime(timestamp);

  if (isLoading) {
    return (
      <Box sx={{ p: 2 }}>
        {[1, 2, 3].map(i => (
          <Skeleton key={i} variant="rectangular" height={60} sx={{ mb: 1, borderRadius: 1 }} />
        ))}
      </Box>
    );
  }

  if (error) {
    return (
      <Box sx={{ p: 2 }}>
        <Alert severity="error" action={
          <Button size="small" onClick={refetchExecutions}>Retry</Button>
        }>
          {error}
        </Alert>
      </Box>
    );
  }

  if (executions.length === 0) {
    return (
      <Box sx={{ p: 3, textAlign: 'center' }}>
        <HistoryIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
        <Typography variant="body2" sx={{
          color: "text.secondary"
        }}>
          No executions yet
        </Typography>
        <Typography variant="caption" sx={{
          color: "text.disabled"
        }}>
          Run the workflow to see execution history
        </Typography>
      </Box>
    );
  }

  return (
    <Box>
      {/* Header with refresh button */}
      {!compact && (
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', p: 1, pb: 0 }}>
          <Typography variant="subtitle2" sx={{
            color: "text.secondary"
          }}>
            Recent Executions ({executions.length})
          </Typography>
          <Tooltip title="Refresh">
            <IconButton aria-label="Refresh" size="small" onClick={refetchExecutions}>
              <RefreshIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>
      )}
      <List dense={compact} disablePadding>
        {executions.map((execution, index) => (
          <React.Fragment key={execution.id}>
            {index > 0 && <Divider />}
            <ListItemButton
              onClick={() => handleToggleExpand(execution.id)}
              sx={{
                py: compact ? 1 : 1.5,
              }}
            >
              <ListItemIcon sx={{ minWidth: 36 }}>
                {execution.status === 'running' ? (
                  <CircularProgress size={20} />
                ) : (
                  STATUS_ICONS[execution.status]
                )}
              </ListItemIcon>

              <ListItemText
                primary={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Chip
                      label={execution.status}
                      size="small"
                      color={STATUS_COLORS[execution.status]}
                      sx={{ height: 20, fontSize: '0.7rem' }}
                    />
                    <Typography variant="caption" sx={{
                      color: "text.secondary"
                    }}>
                      {formatTime(execution.created_at)}
                    </Typography>
                  </Box>
                }
                secondary={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.5 }}>
                    <Typography variant="caption" sx={{
                      color: "text.secondary"
                    }}>
                      Duration: {formatDuration(execution.duration_ms)}
                    </Typography>
                    <Typography variant="caption" sx={{
                      color: "text.disabled"
                    }}>
                      • {execution.triggered_by}
                    </Typography>
                  </Box>
                }
              />

              <IconButton aria-label={expandedExecution === execution.id ? "Collapse" : "Expand"} size="small" edge="end">
                {expandedExecution === execution.id ? <ExpandLessIcon /> : <ExpandMoreIcon />}
              </IconButton>
            </ListItemButton>

            {/* Expanded Details */}
            <Collapse in={expandedExecution === execution.id} timeout="auto" unmountOnExit>
              <Box sx={{ px: 2, pb: 2, bgcolor: 'action.hover' }}>
                {expandedExecution === execution.id && detailQuery.isLoading ? (
                  <Box sx={{ py: 2, textAlign: 'center' }}>
                    <CircularProgress size={24} />
                  </Box>
                ) : expandedExecution === execution.id && detailQuery.data ? (
                  <ExecutionDetailView detail={detailQuery.data} />
                ) : (
                  <Typography variant="caption" sx={{
                    color: "text.secondary"
                  }}>
                    Loading details...
                  </Typography>
                )}
              </Box>
            </Collapse>
          </React.Fragment>
        ))}
      </List>
    </Box>
  );
};

// Sub-component to display execution details
interface ExecutionDetailViewProps {
  detail: WorkflowExecutionDetail;
}

const ExecutionDetailView: React.FC<ExecutionDetailViewProps> = ({ detail }) => {
  const [showInputs, setShowInputs] = useState(false);
  const [showOutputs, setShowOutputs] = useState(false);

  return (
    <Box sx={{ pt: 1 }}>
      {/* Error message if failed */}
      {detail.error_message && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {detail.error_message}
        </Alert>
      )}
      {/* Inputs */}
      {detail.inputs && Object.keys(detail.inputs).length > 0 && (
        <Box sx={{ mb: 1.5 }}>
          <Button
            size="small"
            variant="text"
            onClick={() => setShowInputs(!showInputs)}
            startIcon={showInputs ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          >
            Inputs
          </Button>
          <Collapse in={showInputs}>
            <Paper variant="outlined" sx={{ p: 1, mt: 0.5, bgcolor: 'background.default' }}>
              <Typography
                variant="caption"
                component="pre"
                sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap', m: 0 }}
              >
                {JSON.stringify(detail.inputs, null, 2)}
              </Typography>
            </Paper>
          </Collapse>
        </Box>
      )}
      {/* Outputs */}
      {detail.outputs && Object.keys(detail.outputs).length > 0 && (
        <Box sx={{ mb: 1.5 }}>
          <Button
            size="small"
            variant="text"
            onClick={() => setShowOutputs(!showOutputs)}
            startIcon={showOutputs ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          >
            Outputs
          </Button>
          <Collapse in={showOutputs}>
            <Paper variant="outlined" sx={{ p: 1, mt: 0.5, bgcolor: 'background.default' }}>
              <Typography
                variant="caption"
                component="pre"
                sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap', m: 0 }}
              >
                {JSON.stringify(detail.outputs, null, 2)}
              </Typography>
            </Paper>
          </Collapse>
        </Box>
      )}
      {/* Step Executions */}
      {detail.step_executions && detail.step_executions.length > 0 && (
        <Box>
          <Typography
            variant="caption"
            sx={{
              fontWeight: "medium",
              mb: 1,
              display: 'block'
            }}>
            Steps ({detail.step_executions.length})
          </Typography>
          {detail.step_executions.map((step, idx) => (
            <Paper
              key={step.id}
              variant="outlined"
              sx={{
                p: 1,
                mb: 0.5,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                bgcolor: step.status === 'failed' ? 'error.50' : 'background.default',
              }}
            >
              <Box sx={{ minWidth: 20 }}>
                {step.status === 'running' ? (
                  <CircularProgress size={16} />
                ) : (
                  STATUS_ICONS[step.status]
                )}
              </Box>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="caption" sx={{
                  fontWeight: "medium"
                }}>
                  Step {idx + 1}
                </Typography>
                {step.error_message && (
                  <Typography variant="caption" color="error" sx={{ display: 'block' }}>
                    {step.error_message}
                  </Typography>
                )}
              </Box>
              <Typography variant="caption" sx={{
                color: "text.secondary"
              }}>
                {step.duration_ms ? `${step.duration_ms}ms` : '-'}
              </Typography>
            </Paper>
          ))}
        </Box>
      )}
      {/* Execution ID */}
      <Typography
        variant="caption"
        sx={{
          color: "text.disabled",
          mt: 1,
          display: 'block'
        }}>
        ID: {detail.id}
      </Typography>
    </Box>
  );
};
