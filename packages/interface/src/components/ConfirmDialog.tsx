// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Button,
} from '@mui/material';
import { ghostDialogPaperSx, ghostButtonSx, ghostCancelBtnSx } from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmColor?: 'error' | 'primary' | 'secondary' | 'warning' | 'info' | 'success';
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Delete',
  cancelLabel = 'Cancel',
  confirmColor = 'error',
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Dialog open={open} onClose={onCancel} slotProps={{
      paper: { sx: ghostDialogPaperSx }
    }}>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <DialogContentText>{message}</DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} sx={ghostCancelBtnSx}>{cancelLabel}</Button>
        <Button
          onClick={onConfirm}
          variant="outlined"
          sx={ghostButtonSx(
            confirmColor === 'error' ? ChaosCypherPalette.error
            : confirmColor === 'warning' ? ChaosCypherPalette.warning
            : ChaosCypherPalette.primary
          )}
        >
          {confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
