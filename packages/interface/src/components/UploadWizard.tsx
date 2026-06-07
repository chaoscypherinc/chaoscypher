// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module UploadWizard
 *
 * The visible steps (1.5 + 2) of the upfront domain-confirmation upload
 * wizard, driven by {@link useUploadWizard}. Step 1 (file select) is the
 * existing UploadDialog; this component renders only what comes after Import:
 *
 *   - phase 'analyzing' → an "Analyzing your document…" dialog (the targeted
 *     poll runs in the hook).
 *   - phase 'review'    → the existing ConfirmExtractionDialog, inline, fed
 *     from the polled source. Its Confirm button is disabled while the confirm
 *     mutation is in flight (`submitting`), preventing a double-click → 409.
 *   - phase 'error'     → a small error dialog with a dismiss action.
 *
 * On the 'idle' phase nothing renders. The override fast-path and URL/batch
 * uploads never reach this component (they resolve to 'idle' in the hook).
 */

import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogActions,
  Box,
  Button,
  CircularProgress,
  Typography,
} from '@mui/material';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';
import { ConfirmExtractionDialog } from '../pages/Sources/dialogs/ConfirmExtractionDialog';
import type { ExtractionDomain, ConfirmExtractionOptions } from '../services/api/sourceProcessing';
import type { UseUploadWizardReturn } from '../hooks/useUploadWizard';

interface UploadWizardProps {
  wizard: UseUploadWizardReturn;
  availableDomains: ExtractionDomain[];
  contextWindow?: number;
  groupSize?: number;
  inputPerChunk?: number;
  outputPerChunk?: number;
}

export function UploadWizard({
  wizard,
  availableDomains,
  contextWindow,
  groupSize,
  inputPerChunk,
  outputPerChunk,
}: UploadWizardProps) {
  const { phase, source, error, confirming, confirm, cancel } = wizard;

  const handleConfirm = (options: ConfirmExtractionOptions) => {
    void confirm(options);
  };

  return (
    <>
      {/* Step 1.5 — Analyzing… */}
      <Dialog
        open={phase === 'analyzing'}
        maxWidth="xs"
        fullWidth
        slotProps={{ paper: { sx: ghostDialogPaperSx } }}
      >
        <DialogTitle sx={{ color: 'text.primary' }}>Analyzing your document…</DialogTitle>
        <DialogContent>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 2,
              py: 2,
            }}
          >
            <CircularProgress size={24} sx={{ color: 'primary.main' }} />
            <Typography variant="body2" sx={{ color: 'text.secondary' }}>
              Detecting the best extraction domain. This takes a moment.
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={cancel} sx={ghostCancelBtnSx}>
            Cancel
          </Button>
        </DialogActions>
      </Dialog>

      {/* Step 2 — Review/Confirm. Reuses the parked-source confirm dialog;
          its Confirm button is disabled while `submitting` (confirming). */}
      <ConfirmExtractionDialog
        open={phase === 'review'}
        source={source}
        availableDomains={availableDomains}
        submitting={confirming}
        onClose={cancel}
        onConfirm={handleConfirm}
        contextWindow={contextWindow}
        groupSize={groupSize}
        inputPerChunk={inputPerChunk}
        outputPerChunk={outputPerChunk}
      />

      {/* Error */}
      <Dialog
        open={phase === 'error'}
        onClose={cancel}
        maxWidth="xs"
        fullWidth
        slotProps={{ paper: { sx: ghostDialogPaperSx } }}
      >
        <DialogTitle sx={{ color: 'text.primary' }}>Something went wrong</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ color: 'error.main' }}>
            {error}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button
            variant="outlined"
            onClick={cancel}
            sx={ghostButtonSx(ChaosCypherPalette.primary)}
          >
            Close
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
