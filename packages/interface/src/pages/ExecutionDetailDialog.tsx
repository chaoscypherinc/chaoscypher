// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Execution Detail Dialog
 *
 * Displays detailed information about a single workflow execution
 * including status, inputs/outputs, and step-by-step execution details.
 */

import React, { useState } from 'react';
import {
  Box,
  Typography,
  Chip,
  CircularProgress,
  Alert,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Stepper,
  Step,
  StepLabel,
  StepContent,
  Collapse,
  IconButton,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import {
  ghostDialogPaperSx,
  ghostCancelBtnSx,
  ghostErrorAlertSx,
} from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';
import type { WorkflowExecutionDetail } from '../services/api/workflows';
import { formatDurationMs } from '../utils/formatters';

const CYAN = ChaosCypherPalette.primary;

/** Status chip icons for each execution state. */
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import CancelIcon from '@mui/icons-material/Cancel';

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <HourglassEmptyIcon fontSize="small" />,
  running: <PlayArrowIcon fontSize="small" />,
  completed: <CheckCircleIcon fontSize="small" />,
  failed: <ErrorIcon fontSize="small" />,
  cancelled: <CancelIcon fontSize="small" />,
};

const STATUS_COLORS: Record<string, 'default' | 'primary' | 'secondary' | 'error' | 'info' | 'success' | 'warning'> = {
  pending: 'default',
  running: 'info',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
};

interface ExecutionDetailDialogProps {
  open: boolean;
  onClose: () => void;
  execution: WorkflowExecutionDetail | null;
  isLoading: boolean;
}

/** Dialog showing detailed view of a single workflow execution. */
export const ExecutionDetailDialog: React.FC<ExecutionDetailDialogProps> = ({
  open,
  onClose,
  execution,
  isLoading,
}) => {
  const [showInputs, setShowInputs] = useState(true);
  const [showOutputs, setShowOutputs] = useState(true);
  const [expandedSteps, setExpandedSteps] = useState<Record<string, boolean>>({});

  const toggleStep = (stepId: string) => {
    setExpandedSteps(prev => ({ ...prev, [stepId]: !prev[stepId] }));
  };

  const formatDuration = (ms?: number): string => formatDurationMs(ms ?? null);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      slotProps={{
        paper: { sx: ghostDialogPaperSx }
      }}
    >
      <DialogTitle>
        Execution Details
      </DialogTitle>
      <DialogContent>
        {isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress sx={{ color: CYAN }} />
          </Box>
        ) : execution ? (
          <Box>
            {/* Status and Duration */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 3 }}>
              <Chip
                icon={execution.status === 'running' ? <CircularProgress size={14} /> : STATUS_ICONS[execution.status] as React.ReactElement}
                label={execution.status}
                color={STATUS_COLORS[execution.status]}
              />
              <Typography variant="body2" sx={{
                color: "text.secondary"
              }}>
                Duration: {formatDuration(execution.duration_ms)}
              </Typography>
              <Typography variant="body2" sx={{
                color: "text.secondary"
              }}>
                Triggered by: {execution.triggered_by}
              </Typography>
            </Box>

            {/* Error Message */}
            {execution.error_message && (
              <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }}>
                {execution.error_message}
              </Alert>
            )}

            {/* Execution ID */}
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                display: 'block',
                mb: 2
              }}>
              Execution ID: {execution.id}
            </Typography>

            {/* Inputs */}
            {execution.inputs && Object.keys(execution.inputs).length > 0 && (
              <Box sx={{ mb: 2 }}>
                <Button
                  size="small"
                  onClick={() => setShowInputs(!showInputs)}
                  startIcon={showInputs ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                  sx={{ color: 'rgba(255, 255, 255, 0.6)', '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' } }}
                >
                  Inputs
                </Button>
                <Collapse in={showInputs}>
                  <Box sx={{ p: 2, mt: 1, bgcolor: 'rgba(0, 0, 0, 0.2)', border: '1px solid rgba(255, 255, 255, 0.06)', borderRadius: 1 }}>
                    <Typography
                      variant="body2"
                      component="pre"
                      sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap', m: 0, color: 'rgba(255, 255, 255, 0.85)' }}
                    >
                      {JSON.stringify(execution.inputs, null, 2)}
                    </Typography>
                  </Box>
                </Collapse>
              </Box>
            )}

            {/* Outputs */}
            {execution.outputs && Object.keys(execution.outputs).length > 0 && (
              <Box sx={{ mb: 2 }}>
                <Button
                  size="small"
                  onClick={() => setShowOutputs(!showOutputs)}
                  startIcon={showOutputs ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                  sx={{ color: 'rgba(255, 255, 255, 0.6)', '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' } }}
                >
                  Outputs
                </Button>
                <Collapse in={showOutputs}>
                  <Box sx={{ p: 2, mt: 1, bgcolor: 'rgba(0, 0, 0, 0.2)', border: '1px solid rgba(255, 255, 255, 0.06)', borderRadius: 1 }}>
                    <Typography
                      variant="body2"
                      component="pre"
                      sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap', m: 0, color: 'rgba(255, 255, 255, 0.85)' }}
                    >
                      {JSON.stringify(execution.outputs, null, 2)}
                    </Typography>
                  </Box>
                </Collapse>
              </Box>
            )}

            {/* Step Executions */}
            {execution.step_executions && execution.step_executions.length > 0 && (
              <Box>
                <Typography variant="subtitle2" gutterBottom>
                  Step Executions ({execution.step_executions.length})
                </Typography>
                <Stepper orientation="vertical" activeStep={-1}>
                  {execution.step_executions.map((step, index) => (
                    <Step key={step.id} completed={step.status === 'completed'}>
                      <StepLabel
                        error={step.status === 'failed'}
                        icon={
                          step.status === 'running' ? (
                            <CircularProgress size={20} sx={{ color: CYAN }} />
                          ) : (
                            STATUS_ICONS[step.status]
                          )
                        }
                      >
                        <Box
                          sx={{ display: 'flex', alignItems: 'center', gap: 1, cursor: 'pointer' }}
                          onClick={() => toggleStep(step.id)}
                        >
                          <Typography variant="body2">Step {index + 1}</Typography>
                          <Chip
                            label={step.status}
                            size="small"
                            color={STATUS_COLORS[step.status]}
                            sx={{ height: 20, fontSize: '0.7rem' }}
                          />
                          <Typography variant="caption" sx={{
                            color: "text.secondary"
                          }}>
                            {formatDuration(step.duration_ms)}
                          </Typography>
                          <IconButton aria-label={expandedSteps[step.id] ? "Collapse" : "Expand"} size="small" sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
                            {expandedSteps[step.id] ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                          </IconButton>
                        </Box>
                      </StepLabel>
                      <StepContent>
                        <Collapse in={expandedSteps[step.id]}>
                          {step.error_message && (
                            <Alert severity="error" sx={{ mb: 1, ...ghostErrorAlertSx }}>
                              {step.error_message}
                            </Alert>
                          )}
                          {step.outputs && (
                            <Box sx={{ p: 1, bgcolor: 'rgba(0, 0, 0, 0.2)', border: '1px solid rgba(255, 255, 255, 0.06)', borderRadius: 1 }}>
                              <Typography
                                variant="caption"
                                component="pre"
                                sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap', m: 0, color: 'rgba(255, 255, 255, 0.85)' }}
                              >
                                {JSON.stringify(step.outputs, null, 2)}
                              </Typography>
                            </Box>
                          )}
                        </Collapse>
                      </StepContent>
                    </Step>
                  ))}
                </Stepper>
              </Box>
            )}
          </Box>
        ) : (
          <Typography sx={{
            color: "text.secondary"
          }}>No execution data available</Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};
