// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Checkbox,
  FormControlLabel,
  TextField,
  Box,
} from '@mui/material';
import WarningIcon from '@mui/icons-material/Warning';
import { ghostDialogPaperSx, ghostButtonSx, ghostCancelBtnSx, ghostInputSx } from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';

interface ResetConfirmationDialogProps {
  open: boolean;
  title: string;
  description: string;
  requireConfirmText?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ResetConfirmationDialog({
  open,
  title,
  description,
  requireConfirmText = false,
  onConfirm,
  onCancel,
}: ResetConfirmationDialogProps) {
  const [confirmed, setConfirmed] = useState(false);
  const [confirmText, setConfirmText] = useState('');

  const canConfirm = confirmed && (!requireConfirmText || confirmText === 'CONFIRM');

  const handleCancel = () => {
    setConfirmed(false);
    setConfirmText('');
    onCancel();
  };

  const handleConfirm = () => {
    setConfirmed(false);
    setConfirmText('');
    onConfirm();
  };

  return (
    <Dialog open={open} onClose={handleCancel} maxWidth="sm" fullWidth slotProps={{
      paper: { sx: ghostDialogPaperSx }
    }}>
      <DialogTitle>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1
          }}>
          <WarningIcon color="error" />
          {title}
        </Box>
      </DialogTitle>
      <DialogContent>
        <Typography variant="body1" gutterBottom>
          {description}
        </Typography>

        <Box sx={{
          mt: 2
        }}>
          <FormControlLabel
            control={
              <Checkbox
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
            }
            label="I understand this action cannot be undone"
          />
        </Box>

        {requireConfirmText && (
          <TextField
            fullWidth
            margin="normal"
            label="Type CONFIRM to proceed"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="CONFIRM"
            helperText="Type the word CONFIRM in capital letters"
            sx={ghostInputSx}
          />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleCancel} sx={ghostCancelBtnSx}>Cancel</Button>
        <Button
          onClick={handleConfirm}
          variant="outlined"
          disabled={!canConfirm}
          sx={ghostButtonSx(ChaosCypherPalette.error)}
        >
          Reset
        </Button>
      </DialogActions>
    </Dialog>
  );
}
