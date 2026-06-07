// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TestResultDisplay: Renders execution results for a workflow test run.
 *
 * Shows overall status, step-by-step execution details with expandable
 * outputs, final output on success, and error messages on failure.
 */

import React, { memo } from 'react';
import {
  Box,
  Typography,
  Stepper,
  Step,
  StepLabel,
  StepContent,
  Alert,
  CircularProgress,
  Chip,
  Paper,
  Divider,
  IconButton,
  Collapse,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty';
import type { WorkflowExecutionDetail } from '../../../../services/api/workflows';
import { ghostErrorAlertSx } from '../../../../theme/ghostStyles';
import { formatDurationMs } from '../../../../utils/formatters';

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <HourglassEmptyIcon color="disabled" />,
  running: <CircularProgress size={20} sx={{ color: 'primary.main' }} />,
  completed: <CheckCircleIcon sx={{ color: 'success.main' }} />,
  failed: <ErrorIcon sx={{ color: 'error.main' }} />,
  skipped: <HourglassEmptyIcon color="disabled" />,
};

const STATUS_COLORS: Record<string, 'default' | 'primary' | 'secondary' | 'error' | 'info' | 'success' | 'warning'> = {
  pending: 'default',
  running: 'info',
  completed: 'success',
  failed: 'error',
  skipped: 'warning',
  cancelled: 'warning',
};

/** Format a duration in milliseconds with '-' fallback. */
const formatDuration = (ms?: number): string => formatDurationMs(ms ?? null);

interface TestResultDisplayProps {
  /** The execution detail to display. */
  execution: WorkflowExecutionDetail;
  /** Map of step IDs to their output visibility state. */
  showOutputs: Record<string, boolean>;
  /** Toggle output visibility for a specific step. */
  toggleOutput: (stepId: string) => void;
}

/**
 * Renders the execution results section including status, step details,
 * final output, and error messages.
 */
const TestResultDisplayComponent: React.FC<TestResultDisplayProps> = ({
  execution,
  showOutputs,
  toggleOutput,
}) => {
  return (
    <Box>
      <Divider sx={{ my: 2, borderColor: 'rgba(255, 255, 255, 0.06)' }} />

      {/* Overall Status */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2 }}>
        <Typography variant="subtitle2">Status:</Typography>
        <Chip
          label={execution.status}
          color={STATUS_COLORS[execution.status]}
          size="small"
        />
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          Duration: {formatDuration(execution.duration_ms)}
        </Typography>
      </Box>

      {/* Step Execution Details */}
      {execution.step_executions && execution.step_executions.length > 0 && (
        <Paper
          variant="outlined"
          sx={{
            p: 2,
            bgcolor: 'rgba(0, 0, 0, 0.2)',
            border: '1px solid rgba(255, 255, 255, 0.06)',
            borderRadius: '8px',
          }}
        >
          <Typography variant="subtitle2" gutterBottom>
            Step Execution
          </Typography>
          <Stepper orientation="vertical" activeStep={-1}>
            {execution.step_executions.map((step, index) => (
              <Step key={step.id} completed={step.status === 'completed'}>
                <StepLabel
                  error={step.status === 'failed'}
                  icon={STATUS_ICONS[step.status]}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Typography variant="body2">
                      Step {index + 1}
                    </Typography>
                    <Chip
                      label={step.status}
                      size="small"
                      color={STATUS_COLORS[step.status]}
                      sx={{ height: 18, fontSize: '0.7rem' }}
                    />
                    <Typography variant="caption" sx={{ color: "text.secondary" }}>
                      {formatDuration(step.duration_ms)}
                    </Typography>
                    <IconButton
                      aria-label={showOutputs[step.id] ? "Collapse" : "Expand"}
                      size="small"
                      onClick={() => toggleOutput(step.id)}
                    >
                      {showOutputs[step.id] ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                    </IconButton>
                  </Box>
                </StepLabel>
                <StepContent>
                  <Collapse in={showOutputs[step.id]}>
                    {step.error_message && (
                      <Alert severity="error" sx={{ mb: 1, ...ghostErrorAlertSx }}>
                        {step.error_message}
                      </Alert>
                    )}
                    {step.outputs && (
                      <Paper
                        sx={{
                          p: 1,
                          bgcolor: 'rgba(0, 0, 0, 0.2)',
                          border: '1px solid rgba(255, 255, 255, 0.06)',
                          borderRadius: '8px',
                        }}
                      >
                        <Typography variant="caption" component="pre" sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
                          {JSON.stringify(step.outputs, null, 2)}
                        </Typography>
                      </Paper>
                    )}
                  </Collapse>
                </StepContent>
              </Step>
            ))}
          </Stepper>
        </Paper>
      )}

      {/* Final Output */}
      {execution.status === 'completed' && execution.outputs && (
        <Box sx={{ mt: 2 }}>
          <Typography variant="subtitle2" gutterBottom>
            Final Output
          </Typography>
          <Paper
            sx={{
              p: 2,
              bgcolor: 'rgba(29, 233, 182, 0.06)',
              border: '1px solid rgba(29, 233, 182, 0.15)',
              borderRadius: '8px',
            }}
          >
            <Typography variant="body2" component="pre" sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(execution.outputs, null, 2)}
            </Typography>
          </Paper>
        </Box>
      )}

      {/* Error Message */}
      {execution.status === 'failed' && execution.error_message && (
        <Alert severity="error" sx={{ mt: 2, ...ghostErrorAlertSx }}>
          {execution.error_message}
        </Alert>
      )}
    </Box>
  );
};

export const TestResultDisplay = memo(TestResultDisplayComponent);
