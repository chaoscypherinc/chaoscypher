// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
} from '@mui/material';
import type { UnifiedSource } from '../../../types';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';

interface DeleteDialogProps {
  open: boolean;
  source: UnifiedSource | null;
  onClose: () => void;
  onConfirm: () => void;
}

export function DeleteDialog({ open, source, onClose, onConfirm }: DeleteDialogProps) {
  const isActive = source?.stage === 'active';

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth slotProps={{
      paper: { sx: ghostDialogPaperSx }
    }}>
      <DialogTitle>Delete {isActive ? 'Source' : 'Processing Source'}</DialogTitle>
      <DialogContent>
        <Typography>
          {isActive
            ? 'Are you sure you want to permanently delete this source? This will remove all chunks and citations. This action cannot be undone.'
            : 'Are you sure you want to delete this processing source? This action cannot be undone.'}
        </Typography>
        {source && (
          <Typography
            variant="body2"
            sx={{
              color: "text.secondary",
              mt: 2
            }}>
            <strong>Title:</strong> {source.title}
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>Cancel</Button>
        <Button variant="outlined" sx={ghostButtonSx(ChaosCypherPalette.error)} onClick={onConfirm}>
          Delete
        </Button>
      </DialogActions>
    </Dialog>
  );
}
