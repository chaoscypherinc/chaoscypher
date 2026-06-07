// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TestExecutionModal: Dialog for testing workflow execution
 *
 * Allows users to provide test inputs via dynamic form, execute the workflow,
 * and view step-by-step execution results. Falls back to JSON editor for
 * advanced use cases. Also provides access to execution history.
 */

import React from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Box,
  Typography,
  Alert,
  Paper,
  IconButton,
  Tooltip,
  Tabs,
  Tab,
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import CodeIcon from '@mui/icons-material/Code';
import HistoryIcon from '@mui/icons-material/History';
import { DynamicFormRenderer } from '../forms/DynamicFormRenderer';
import { ExecutionHistoryPanel } from '../panels/ExecutionHistoryPanel';
import { TestResultDisplay } from './TestResultDisplay';
import { useTestExecution } from '../../hooks/useTestExecution';
import {
  ghostInputSx,
  ghostDialogPaperSx,
  ghostButtonSx,
  ghostCancelBtnSx,
  ghostErrorAlertSx,
  ghostInfoAlertSx,
  ghostTabsSx,
} from '../../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../../theme/palette';

interface TestExecutionModalProps {
  open: boolean;
  onClose: () => void;
  workflowId: string | null;
  workflowName: string;
  inputSchema?: Record<string, unknown>;
  onStepStatusChange?: (stepStatuses: Record<string, string>) => void;
}

export const TestExecutionModal: React.FC<TestExecutionModalProps> = ({
  open,
  onClose,
  workflowId,
  workflowName,
  inputSchema,
  onStepStatusChange,
}) => {
  const {
    formValues,
    setFormValues,
    inputsJson,
    setInputsJson,
    showJsonEditor,
    inputError,
    clearInputError,
    hasValidSchema,
    handleToggleJsonEditor,
    isExecuting,
    execution,
    error,
    clearError,
    handleExecute,
    handleCancel,
    activeTab,
    setActiveTab,
    showOutputs,
    toggleOutput,
  } = useTestExecution({ open, workflowId, inputSchema, onStepStatusChange });

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
      <DialogTitle sx={{ pb: 0 }}>
        Test Workflow: {workflowName}
      </DialogTitle>
      {/* Tabs */}
      <Box sx={{ borderBottom: 1, borderColor: 'rgba(255, 255, 255, 0.06)', px: 3 }}>
        <Tabs
          value={activeTab}
          onChange={(_, newValue) => setActiveTab(newValue)}
          aria-label="execution tabs"
          sx={ghostTabsSx}
        >
          <Tab icon={<PlayArrowIcon />} iconPosition="start" label="Run Test" />
          <Tab icon={<HistoryIcon />} iconPosition="start" label="History" />
        </Tabs>
      </Box>
      <DialogContent>
        {/* Tab 0: Run Test */}
        {activeTab === 0 && (
          <>
        {/* Input Section */}
        <Box sx={{ mb: 3 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
            <Typography variant="subtitle2">
              Workflow Inputs
            </Typography>
            {hasValidSchema && (
              <Tooltip title={showJsonEditor ? 'Use form input' : 'Use JSON editor'}>
                <IconButton
                  aria-label={showJsonEditor ? 'Use form input' : 'Use JSON editor'}
                  size="small"
                  onClick={handleToggleJsonEditor}
                  disabled={isExecuting}
                >
                  <CodeIcon fontSize="small" color={showJsonEditor ? 'primary' : 'inherit'} />
                </IconButton>
              </Tooltip>
            )}
          </Box>

          {inputError && (
            <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }} onClose={clearInputError}>
              {inputError}
            </Alert>
          )}

          {showJsonEditor ? (
            // JSON Editor Mode
            (<TextField
              fullWidth
              multiline
              rows={6}
              value={inputsJson}
              onChange={(e) => setInputsJson(e.target.value)}
              disabled={isExecuting}
              sx={{ ...ghostInputSx, fontFamily: 'monospace', fontSize: '0.85rem' }}
              placeholder="{}"
              helperText="Provide input data as JSON"
            />)
          ) : hasValidSchema ? (
            // Dynamic Form Mode
            (<Paper
              variant="outlined"
              sx={{
                p: 2,
                bgcolor: 'rgba(0, 0, 0, 0.2)',
                border: '1px solid rgba(255, 255, 255, 0.06)',
                borderRadius: '8px',
              }}
            >
              <DynamicFormRenderer
                schema={inputSchema as Record<string, unknown>}
                values={formValues}
                onChange={setFormValues}
                allowReferences={false}
                label="Test Inputs"
              />
            </Paper>)
          ) : (
            // No schema - show message and JSON editor
            (<>
              <Alert severity="info" sx={{ mb: 2, ...ghostInfoAlertSx }}>
                No input schema defined for this workflow. You can still provide inputs as JSON.
              </Alert>
              <TextField
                fullWidth
                multiline
                rows={4}
                value={inputsJson}
                onChange={(e) => setInputsJson(e.target.value)}
                disabled={isExecuting}
                sx={{ ...ghostInputSx, fontFamily: 'monospace', fontSize: '0.85rem' }}
                placeholder="{}"
                helperText="Provide input data as JSON (optional)"
              />
            </>)
          )}
        </Box>

        {/* Error Display */}
        {error && (
          <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }} onClose={clearError}>
            {error}
          </Alert>
        )}

        {/* Execution Results */}
        {execution && (
          <TestResultDisplay
            execution={execution}
            showOutputs={showOutputs}
            toggleOutput={toggleOutput}
          />
        )}
          </>
        )}

        {/* Tab 1: Execution History */}
        {activeTab === 1 && workflowId && (
          <ExecutionHistoryPanel
            workflowId={workflowId}
            maxItems={20}
            autoRefresh={true}
            refreshInterval={5000}
          />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isExecuting} sx={ghostCancelBtnSx}>
          Close
        </Button>
        {activeTab === 0 && (
          isExecuting ? (
            <Button
              variant="outlined"
              startIcon={<StopIcon />}
              onClick={handleCancel}
              sx={ghostButtonSx(ChaosCypherPalette.error)}
            >
              Cancel
            </Button>
          ) : (
            <Button
              variant="outlined"
              startIcon={<PlayArrowIcon />}
              onClick={handleExecute}
              disabled={!workflowId}
              sx={ghostButtonSx(ChaosCypherPalette.primary)}
            >
              Execute
            </Button>
          )
        )}
      </DialogActions>
    </Dialog>
  );
};
