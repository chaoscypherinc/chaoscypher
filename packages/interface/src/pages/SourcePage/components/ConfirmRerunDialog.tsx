// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
} from '@mui/material';

interface ConfirmRerunDialogProps {
  open: boolean;
  chunkIndex: number;
  priorAttemptCount: number;
  onConfirm: () => void;
  onCancel: () => void;
  pending?: boolean;
}

/**
 * Confirmation modal for the per-chunk rerun action.
 *
 * Explains the state-machine consequence (source returns to
 * extracting → extracted → committing → committed) and reassures the
 * user that prior committed entities are preserved by first-write-wins
 * upsert. Surfaces the prior attempt count so users can see the
 * Attempts history is being maintained.
 */
export function ConfirmRerunDialog({
  open,
  chunkIndex,
  priorAttemptCount,
  onConfirm,
  onCancel,
  pending,
}: ConfirmRerunDialogProps) {
  const attemptsLabel =
    priorAttemptCount === 0
      ? 'No prior attempts yet.'
      : priorAttemptCount === 1
        ? '1 prior attempt is preserved in history.'
        : `${priorAttemptCount} prior attempts are preserved in history.`;

  return (
    <Dialog open={open} onClose={onCancel} maxWidth="sm" fullWidth>
      <DialogTitle>Rerun chunk {chunkIndex + 1}?</DialogTitle>
      <DialogContent>
        <DialogContentText component="div">
          <Box sx={{ mb: 1.5 }}>
            This will re-run extraction on this chunk only, using the same
            configuration as the original. The source will briefly return
            to <b>extracting → extracted → committing → committed</b> as
            the result is merged back in.
          </Box>
          <Box sx={{ mb: 1.5 }}>
            <b>Existing entities are preserved</b> — first-write-wins
            upsert ensures prior committed data is never clobbered.
          </Box>
          <Box>{attemptsLabel}</Box>
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          color="primary"
          disabled={pending}
        >
          {pending ? 'Rerunning…' : 'Rerun chunk'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
