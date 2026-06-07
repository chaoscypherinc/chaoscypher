// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from '@mui/material';
import {
  ghostButtonSx,
  ghostCancelBtnSx,
  ghostDialogPaperSx,
} from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';
import type { Source } from '../../../types';

interface DeleteSourceDialogProps {
  open: boolean;
  source: Source;
  onClose: () => void;
  onConfirm: () => void;
}

/**
 * Confirmation dialog for SourcePage delete action. Shows chunk count
 * and warning that the operation is irreversible.
 */
export function DeleteSourceDialog({
  open,
  source,
  onClose,
  onConfirm,
}: DeleteSourceDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      slotProps={{ paper: { sx: ghostDialogPaperSx } }}
    >
      <DialogTitle>Delete Source</DialogTitle>
      <DialogContent>
        <Typography>
          Are you sure you want to permanently delete "{source.title || source.filename}"?
          This will remove all {source.chunk_count} chunks and associated data.
          This action cannot be undone.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>Cancel</Button>
        <Button
          variant="outlined"
          onClick={onConfirm}
          sx={ghostButtonSx(ChaosCypherPalette.error)}
        >
          Delete
        </Button>
      </DialogActions>
    </Dialog>
  );
}
