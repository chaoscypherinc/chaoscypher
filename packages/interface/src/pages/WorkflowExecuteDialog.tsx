// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Workflow Execute Dialog
 *
 * Modal dialog for executing a workflow with JSON input.
 */

import React from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Typography,
  TextField,
  Button,
  CircularProgress,
} from '@mui/material';
import PlayIcon from '@mui/icons-material/PlayArrow';
import {
  ghostInputSx,
  ghostDialogPaperSx,
  ghostButtonSx,
  ghostCancelBtnSx,
} from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';

const CYAN = ChaosCypherPalette.primary;

interface WorkflowExecuteDialogProps {
  open: boolean;
  onClose: () => void;
  workflowName?: string;
  workflowDescription?: string;
  executionInputs: string;
  onExecutionInputsChange: (value: string) => void;
  onExecute: () => void;
  executing: boolean;
}

/** Dialog for executing a workflow with JSON inputs. */
const WorkflowExecuteDialog: React.FC<WorkflowExecuteDialogProps> = ({
  open,
  onClose,
  workflowName,
  workflowDescription,
  executionInputs,
  onExecutionInputsChange,
  onExecute,
  executing,
}) => {
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
      <DialogTitle>Execute Workflow: {workflowName}</DialogTitle>
      <DialogContent>
        <Typography
          variant="body2"
          sx={{
            color: "text.secondary",
            mb: 2
          }}>
          {workflowDescription}
        </Typography>
        <TextField
          label="Inputs (JSON)"
          fullWidth
          multiline
          rows={10}
          value={executionInputs}
          onChange={(e) => onExecutionInputsChange(e.target.value)}
          margin="normal"
          helperText="Enter workflow inputs as JSON"
          sx={ghostInputSx}
        />
      </DialogContent>
      <DialogActions>
        <Button
          onClick={onClose}
          sx={ghostCancelBtnSx}
        >
          Cancel
        </Button>
        <Button
          variant="outlined"
          onClick={onExecute}
          disabled={executing}
          startIcon={executing ? <CircularProgress size={20} sx={{ color: CYAN }} /> : <PlayIcon />}
          sx={ghostButtonSx(CYAN)}
        >
          {executing ? 'Executing...' : 'Execute'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default WorkflowExecuteDialog;
