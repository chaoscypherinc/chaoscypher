// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module ConfirmExtractionDialog
 * Confirm (or change) the packs detection proposed + the extraction options
 * the confirm endpoint accepts (quick analysis, filtering mode, content
 * filtering) for a parked (awaiting_confirmation) source, before the
 * expensive chunk extraction commits. The pack chips (PackSelect) show the
 * proposed packs pre-selected; no pack is the floor alone, which every
 * source extracts with: when detection proposes nothing (low confidence, no
 * text) that is what the dialog shows selected.
 *
 * An option is sent only when the user changed it here: the confirm request
 * keeps the upload-time choice for every field it does not carry, and the
 * detection proposal carries no options (`ranking`, `confidence`, `packs`,
 * `detected_domain`, `reason`), so seeding from it sent
 * `content_filtering: true` over an upload that had turned filtering off
 * (section-audit 2026-10-12).
 */

import { useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Alert,
  Typography,
  CircularProgress,
} from '@mui/material';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';
import { PackSelect } from '../../../components/upload/PackSelect';
import { packsLabel, proposedPacks } from '../../../utils/packs';
import { ExtractionOptions } from '../../../components/upload/ExtractionOptions';
import type { ExtractionDomain, ConfirmExtractionOptions } from '../../../services/api/sourceProcessing';
import type { UnifiedSource } from '../../../types';

interface ConfirmExtractionDialogProps {
  open: boolean;
  source: UnifiedSource | null;
  availableDomains: ExtractionDomain[];
  submitting: boolean;
  onClose: () => void;
  onConfirm: (options: ConfirmExtractionOptions) => void;
  // Extraction-capacity inputs for the ExtractionOptions token chips.
  contextWindow?: number;
  groupSize?: number;
  inputPerChunk?: number;
  outputPerChunk?: number;
}

/** Why detection proposed what it did, in a few words (the proposal's `reason`). */
const REASON_TEXT: Record<string, string> = {
  one_pack_above_floor: 'one pack scored above the floor',
  packs_above_floor: 'two packs scored above the floor',
  packs_within_margin: 'two packs scored close together',
};

export function ConfirmExtractionDialog({
  open,
  source,
  availableDomains,
  submitting,
  onClose,
  onConfirm,
  contextWindow = 8192,
  groupSize = 4,
  inputPerChunk = 150,
  outputPerChunk = 2000,
}: ConfirmExtractionDialogProps) {
  const proposal = source?.proposed_extraction_options;
  // The registered packs arrive from their own query; until they do,
  // `proposedPacks` (which keeps only registered names) reads as nothing.
  const domainsLoaded = availableDomains.length > 0;
  const proposed = proposedPacks(source, availableDomains);
  // Image-only / <50-char docs short-circuit to a no_text proposal; show a
  // tailored prompt instead of the generic low-confidence one.
  const noText = proposal?.no_text === true;
  const reason = proposal?.reason ? REASON_TEXT[proposal.reason] : undefined;

  // The proposed packs are pre-selected ([] = the floor alone). Reseeded
  // when the dialog opens for a (different) source and when the registered
  // packs arrive: React's adjust-state-during-render pattern, keyed on both,
  // so reopening for the same row does not clobber edits.
  const [selectedPacks, setSelectedPacks] = useState<string[]>(proposed);
  const seedKey = open && source ? `${source.id}:${domainsLoaded}` : null;
  const [prevSeedKey, setPrevSeedKey] = useState<string | null>(seedKey);
  if (seedKey !== prevSeedKey) {
    setPrevSeedKey(seedKey);
    if (seedKey) setSelectedPacks(proposed);
  }
  // Options: undefined until the user changes one here (then it is sent).
  const [analysisDepth, setAnalysisDepth] = useState<'quick' | 'full' | undefined>();
  const [filteringMode, setFilteringMode] = useState<string | undefined>();
  const [contentFiltering, setContentFiltering] = useState<boolean | undefined>();

  const handleConfirm = () => {
    onConfirm({
      // Before the registered packs arrive the chips cannot be edited: send
      // no packs and the API extracts under the proposal.
      packs: domainsLoaded ? selectedPacks : undefined,
      analysis_depth: analysisDepth,
      filtering_mode: filteringMode || undefined,
      content_filtering: contentFiltering,
    });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      slotProps={{ paper: { sx: ghostDialogPaperSx } }}
    >
      <DialogTitle sx={{ color: 'text.primary' }}>Confirm extraction packs</DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
          {noText ? (
            <Alert severity="info" variant="outlined">
              Not enough text to detect — the floor alone is selected. Add a pack below if you
              know one.
            </Alert>
          ) : !domainsLoaded ? (
            <Typography variant="body2" sx={{ color: 'text.secondary' }} data-testid="packs-loading">
              Loading the packs…
            </Typography>
          ) : proposed.length === 0 ? (
            <Alert severity="info" variant="outlined">
              Detection found no pack above the floor — the floor alone (the core types) is
              selected. Add a pack below if you know one.
            </Alert>
          ) : (
            <Typography variant="body2" sx={{ color: 'text.secondary' }} data-testid="proposed-packs">
              Detection proposes {packsLabel(proposed)}
              {reason ? ` (${reason})` : ''}. Add or remove packs below.
            </Typography>
          )}

          {domainsLoaded && (
            <PackSelect
              value={selectedPacks}
              availableDomains={availableDomains}
              onChange={(next) => setSelectedPacks(next ?? [])}
              proposed={proposed}
              contextWindow={contextWindow}
              groupSize={groupSize}
              inputPerChunk={inputPerChunk}
              outputPerChunk={outputPerChunk}
            />
          )}

          <ExtractionOptions
            scope="confirm"
            analysisDepth={analysisDepth ?? 'full'}
            onAnalysisDepthChange={setAnalysisDepth}
            contentFiltering={contentFiltering ?? true}
            onContentFilteringChange={setContentFiltering}
            filteringMode={filteringMode ?? ''}
            onFilteringModeChange={setFilteringMode}
          />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>
          Cancel
        </Button>
        <Button
          variant="outlined"
          onClick={handleConfirm}
          disabled={submitting}
          startIcon={submitting ? <CircularProgress size={16} sx={{ color: 'primary.main' }} /> : undefined}
          sx={ghostButtonSx(ChaosCypherPalette.primary)}
        >
          {submitting ? 'Confirming...' : 'Confirm'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
