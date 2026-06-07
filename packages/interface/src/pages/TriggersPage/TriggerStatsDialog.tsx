// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import React from 'react';
import {
  Box,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
} from '@mui/material';
import {
  ghostDialogPaperSx,
  ghostButtonSx,
  ghostCancelBtnSx,
} from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';

const CYAN = ChaosCypherPalette.primary;

interface TriggerStats {
  total_executions: number;
  successful_executions: number;
  failed_executions: number;
  success_rate: number;
  average_duration_ms: number;
}

interface TriggerStatsDialogProps {
  open: boolean;
  triggerName: string | undefined;
  stats: TriggerStats | null;
  onClose: () => void;
  onEditInWorkflow: () => void;
}

export const TriggerStatsDialog: React.FC<TriggerStatsDialogProps> = ({
  open,
  triggerName,
  stats,
  onClose,
  onEditInWorkflow,
}) => {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth slotProps={{
      paper: { sx: ghostDialogPaperSx }
    }}>
      <DialogTitle sx={{ color: 'text.primary' }}>{triggerName} - Statistics</DialogTitle>
      <DialogContent>
        {stats && (
          <Box sx={{ py: 1 }}>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
              <Box sx={{ flex: '1 1 calc(50% - 8px)' }}>
                <Typography variant="body2" sx={{
                  color: "text.secondary"
                }}>Total Executions</Typography>
                <Typography variant="h5" sx={{ color: 'text.primary' }}>{stats.total_executions}</Typography>
              </Box>
              <Box sx={{ flex: '1 1 calc(50% - 8px)' }}>
                <Typography variant="body2" sx={{
                  color: "text.secondary"
                }}>Success Rate</Typography>
                <Typography variant="h5" sx={{ color: stats.success_rate >= 0.9 ? 'success.main' : 'warning.main' }}>
                  {(stats.success_rate * 100).toFixed(1)}%
                </Typography>
              </Box>
              <Box sx={{ flex: '1 1 calc(50% - 8px)' }}>
                <Typography variant="body2" sx={{
                  color: "text.secondary"
                }}>Successful</Typography>
                <Typography variant="h6" sx={{ color: 'success.main' }}>{stats.successful_executions}</Typography>
              </Box>
              <Box sx={{ flex: '1 1 calc(50% - 8px)' }}>
                <Typography variant="body2" sx={{
                  color: "text.secondary"
                }}>Failed</Typography>
                <Typography variant="h6" sx={{ color: 'error.main' }}>{stats.failed_executions}</Typography>
              </Box>
              <Box sx={{ width: '100%' }}>
                <Typography variant="body2" sx={{
                  color: "text.secondary"
                }}>Average Duration</Typography>
                <Typography variant="h6" sx={{ color: 'text.primary' }}>{stats.average_duration_ms.toFixed(0)} ms</Typography>
              </Box>
            </Box>
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>
          Close
        </Button>
        <Button
          variant="outlined"
          onClick={onEditInWorkflow}
          sx={ghostButtonSx(CYAN)}
        >
          Edit in Workflow
        </Button>
      </DialogActions>
    </Dialog>
  );
};
