// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Workflow Steps Dialog
 *
 * Modal dialog displaying the step-by-step configuration of a workflow,
 * including tool types, prompts, system prompts, and dependencies.
 */

import React from 'react';
import {
  Box,
  Typography,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Accordion,
  AccordionSummary,
  AccordionDetails,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import {
  ghostDialogPaperSx,
  ghostCancelBtnSx,
} from '../theme/ghostStyles';

export interface WorkflowStep {
  id: string;
  workflow_id: string;
  step_number: number;
  name: string;
  description?: string;
  tool_type: 'system_tool' | 'user_tool' | 'workflow';
  tool_id: string;
  configuration: Record<string, unknown>;
  depends_on: string[];
  continue_on_error: boolean;
  thinking_mode?: string;
}

interface WorkflowStepsDialogProps {
  open: boolean;
  onClose: () => void;
  workflowName?: string;
  steps: WorkflowStep[];
}

/** Dialog showing the step-by-step configuration of a workflow. */
const WorkflowStepsDialog: React.FC<WorkflowStepsDialogProps> = ({
  open,
  onClose,
  workflowName,
  steps,
}) => {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      slotProps={{
        paper: { sx: ghostDialogPaperSx }
      }}
    >
      <DialogTitle>Workflow Steps: {workflowName}</DialogTitle>
      <DialogContent>
        {steps.map((step, index) => (
          <Accordion
            key={step.id}
            defaultExpanded={index === 0}
            sx={{
              bgcolor: 'transparent',
              border: '1px solid rgba(255, 255, 255, 0.06)',
              '&:before': { display: 'none' },
              '&.Mui-expanded': { margin: '8px 0' },
            }}
          >
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                <Chip label={step.step_number} size="small" color="primary" />
                <Typography variant="subtitle1" sx={{ fontWeight: 'bold' }}>
                  {step.name}
                </Typography>
                <Chip label={step.tool_id} size="small" variant="outlined" />
              </Box>
            </AccordionSummary>
            <AccordionDetails>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {step.description && (
                  <Box>
                    <Typography variant="caption" sx={{
                      color: "text.secondary"
                    }}>Description:</Typography>
                    <Typography variant="body2">{step.description}</Typography>
                  </Box>
                )}

                <Box>
                  <Typography variant="caption" sx={{
                    color: "text.secondary"
                  }}>Tool Type:</Typography>
                  <Typography variant="body2">{step.tool_type}</Typography>
                </Box>

                {step.depends_on && step.depends_on.length > 0 && (
                  <Box>
                    <Typography variant="caption" sx={{
                      color: "text.secondary"
                    }}>Dependencies:</Typography>
                    <Typography variant="body2">{step.depends_on.join(', ')}</Typography>
                  </Box>
                )}

                {step.configuration && Object.keys(step.configuration).length > 0 && (
                  <Box>
                    <Typography
                      variant="caption"
                      sx={{
                        color: "text.secondary",
                        fontWeight: 'bold',
                        mb: 1
                      }}>
                      Configuration:
                    </Typography>
                    {Boolean(step.configuration.prompt) && (
                      <Box sx={{ mb: 2 }}>
                        <Typography variant="caption" color="primary" sx={{ fontWeight: 'bold' }}>
                          Prompt:
                        </Typography>
                        <Box
                          sx={{
                            mt: 0.5,
                            p: 2,
                            bgcolor: 'rgba(0, 0, 0, 0.3)',
                            border: '1px solid rgba(255, 255, 255, 0.06)',
                            borderRadius: 1,
                            fontFamily: 'monospace',
                            fontSize: '0.875rem',
                            whiteSpace: 'pre-wrap',
                            maxHeight: 300,
                            overflow: 'auto',
                            color: 'rgba(255, 255, 255, 0.85)',
                          }}
                        >
                          {String(step.configuration.prompt)}
                        </Box>
                      </Box>
                    )}
                    {Boolean(step.configuration.system_prompt) && (
                      <Box sx={{ mb: 2 }}>
                        <Typography variant="caption" color="secondary" sx={{ fontWeight: 'bold' }}>
                          System Prompt:
                        </Typography>
                        <Box
                          sx={{
                            mt: 0.5,
                            p: 2,
                            bgcolor: 'rgba(0, 0, 0, 0.3)',
                            border: '1px solid rgba(255, 255, 255, 0.06)',
                            borderRadius: 1,
                            fontFamily: 'monospace',
                            fontSize: '0.875rem',
                            whiteSpace: 'pre-wrap',
                            maxHeight: 200,
                            overflow: 'auto',
                            color: 'rgba(255, 255, 255, 0.85)',
                          }}
                        >
                          {String(step.configuration.system_prompt)}
                        </Box>
                      </Box>
                    )}
                    {Object.entries(step.configuration).map(([key, value]) => {
                      if (key === 'prompt' || key === 'system_prompt') return null;
                      return (
                        <Box key={key} sx={{ mb: 1 }}>
                          <Typography variant="caption" sx={{
                            color: "text.secondary"
                          }}>
                            {key}:
                          </Typography>
                          <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.875rem' }}>
                            {typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
                          </Typography>
                        </Box>
                      );
                    })}
                  </Box>
                )}
              </Box>
            </AccordionDetails>
          </Accordion>
        ))}
      </DialogContent>
      <DialogActions>
        <Button
          onClick={onClose}
          sx={ghostCancelBtnSx}
        >
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default WorkflowStepsDialog;
