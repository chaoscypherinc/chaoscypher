// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogActions,
  Box,
  Typography,
  LinearProgress,
  Button,
  Alert,
  List,
  ListItem,
  ListItemText,
  Collapse,
} from '@mui/material';
import ExpandMore from '@mui/icons-material/ExpandMore';
import ExpandLess from '@mui/icons-material/ExpandLess';
import { ghostDialogPaperSx, ghostButtonSx } from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';

export interface BulkProgress {
  open: boolean;
  current: number;
  total: number;
  status: string;
  errors: Array<{ operation_index: number; error: string }>;
  isComplete: boolean;
}

interface BulkProgressDialogProps {
  progress: BulkProgress;
  onClose: () => void;
}

/**
 * Dialog component that shows progress for bulk operations.
 * Displays a progress bar, status text, and error details.
 */
export function BulkProgressDialog({ progress, onClose }: BulkProgressDialogProps) {
  const [showErrors, setShowErrors] = useState(false);
  const percentage = progress.total > 0 ? (progress.current / progress.total) * 100 : 0;
  const hasErrors = progress.errors.length > 0;

  const handleClose = () => {
    onClose();
    setShowErrors(false);
  };

  return (
    <Dialog
      open={progress.open}
      onClose={(_event, reason) => {
        if (reason === 'escapeKeyDown' && !progress.isComplete) return;
        onClose();
      }}
      maxWidth="sm"
      fullWidth
      slotProps={{
        paper: { sx: ghostDialogPaperSx }
      }}
    >
      <DialogContent>
        <Box sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>
            {progress.isComplete ? 'Bulk Operation Complete' : 'Bulk Operation in Progress'}
          </Typography>
          <Typography variant="body2" gutterBottom sx={{
            color: "text.secondary"
          }}>
            {progress.status}
          </Typography>
          <Box sx={{ mt: 2 }}>
            <LinearProgress variant="determinate" value={percentage} />
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                mt: 0.5,
                display: 'block'
              }}>
              {progress.current} / {progress.total} ({percentage.toFixed(0)}%)
            </Typography>
          </Box>

          {/* Show errors if there are any */}
          {progress.isComplete && hasErrors && (
            <Box sx={{ mt: 2 }}>
              <Alert
                severity="warning"
                sx={{ mb: 1 }}
                action={
                  <Button
                    color="inherit"
                    size="small"
                    onClick={() => setShowErrors(!showErrors)}
                    endIcon={showErrors ? <ExpandLess /> : <ExpandMore />}
                  >
                    {showErrors ? 'Hide' : 'Show'} Details
                  </Button>
                }
              >
                {progress.errors.length} operation(s) failed
              </Alert>
              <Collapse in={showErrors}>
                <List dense sx={{ maxHeight: 200, overflow: 'auto', bgcolor: 'action.hover', borderRadius: 1 }}>
                  {progress.errors.slice(0, 20).map((err) => (
                    <ListItem key={`err-${err.operation_index}-${err.error}`} sx={{ py: 0.5 }}>
                      <ListItemText
                        primary={err.error}
                        slotProps={{
                          primary: { variant: 'body2' }
                        }}
                      />
                    </ListItem>
                  ))}
                  {progress.errors.length > 20 && (
                    <ListItem>
                      <ListItemText
                        primary={`... and ${progress.errors.length - 20} more`}
                        slotProps={{
                          primary: { variant: 'body2', color: 'text.secondary' }
                        }}
                      />
                    </ListItem>
                  )}
                </List>
              </Collapse>
            </Box>
          )}
        </Box>
      </DialogContent>
      {progress.isComplete && hasErrors && (
        <DialogActions>
          <Button onClick={handleClose} variant="outlined" sx={ghostButtonSx(ChaosCypherPalette.primary)}>
            Close
          </Button>
        </DialogActions>
      )}
    </Dialog>
  );
}
