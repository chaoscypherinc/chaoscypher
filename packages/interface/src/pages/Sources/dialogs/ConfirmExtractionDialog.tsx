// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module ConfirmExtractionDialog
 * Confirm (or override) the auto-detected extraction domain + full extraction
 * options for a parked (awaiting_confirmation) source, before the expensive
 * chunk extraction commits. Mounts the same fully-controlled ExtractionOptions
 * used at upload, seeded from the detection proposal, plus a top-3 ranked
 * quick-pick row (best pre-selected). When detection wasn't confident the
 * selector defaults to the concrete fallback domain (generic) rather than the
 * ambiguous "Auto" sentinel, so the user sees what extraction will actually run.
 */

import { useMemo, useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Chip,
  Alert,
  Typography,
  ToggleButton,
  ToggleButtonGroup,
  CircularProgress,
} from '@mui/material';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';
import { DomainSelect } from '../../../components/upload/DomainSelect';
import { ExtractionOptions } from '../../../components/upload/ExtractionOptions';
import type { ExtractionDomain, ConfirmExtractionOptions } from '../../../services/api/sourceProcessing';
import type { UnifiedSource } from '../../../types';

// The catch-all extraction domain the backend falls back to when detection
// finds nothing confident (registry's `generic` plugin). Shown explicitly in
// the confirm dialog so the user sees the concrete domain, not a bare "Auto".
const FALLBACK_DOMAIN = 'generic';

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
  const top3 = useMemo(() => (source?.detection_ranking ?? []).slice(0, 3), [source?.detection_ranking]);
  const lowConfidence = source?.detection_low_confidence === true || top3.length === 0;
  const proposal = source?.proposed_extraction_options;
  // Image-only / <50-char docs short-circuit to a no_text proposal; show a
  // tailored prompt instead of the generic low-confidence one.
  const noText = proposal?.no_text === true;

  // Controlled extraction-option state, seeded from the proposal. The best
  // ranked domain is pre-selected unless detection wasn't confident.
  const [selectedDomain, setSelectedDomain] = useState<string>('__auto__');
  const [analysisDepth, setAnalysisDepth] = useState<'quick' | 'full'>('full');
  const [filteringMode, setFilteringMode] = useState<string>('');
  const [contentFiltering, setContentFiltering] = useState(true);
  const [extractEntities, setExtractEntities] = useState(true);
  const [enableVision, setEnableVision] = useState(false);
  const [enableNormalization, setEnableNormalization] = useState(false);
  const [skipDuplicates, setSkipDuplicates] = useState(false);

  // Reseed every time the dialog opens for a (different) source.
  useEffect(() => {
    if (!open || !source) return;
    // When detection wasn't confident, show the *concrete* domain extraction
    // would fall back to (generic) rather than the ambiguous "Auto" sentinel —
    // the user already passed through an "Auto" choice once, so re-showing
    // "Auto" here tells them nothing about what will run. Prefer the backend's
    // own fallback (proposal.domain, set to generic on no/low-confidence
    // detection); degrade to the known catch-all, then to Auto only if no
    // generic domain is registered.
    const concreteFallback =
      (proposal?.domain && availableDomains.some((d) => d.name === proposal.domain) && proposal.domain) ||
      (availableDomains.some((d) => d.name === FALLBACK_DOMAIN) ? FALLBACK_DOMAIN : '__auto__');
    setSelectedDomain(lowConfidence ? concreteFallback : (top3[0]?.domain ?? concreteFallback));
    setAnalysisDepth(proposal?.analysis_depth ?? 'full');
    setFilteringMode(proposal?.filtering_mode ?? '');
    setContentFiltering(proposal?.content_filtering ?? true);
    // open + source.id keyed so reopening for the same row doesn't clobber edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, source?.id]);

  const handleConfirm = () => {
    onConfirm({
      domain: selectedDomain === '__auto__' ? undefined : selectedDomain,
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
      <DialogTitle sx={{ color: 'text.primary' }}>Confirm extraction domain</DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
          {noText ? (
            <Alert severity="info" variant="outlined">
              Not enough text to detect — defaulting to Generic. Pick a specific domain below if
              you know it.
            </Alert>
          ) : lowConfidence ? (
            <Alert severity="info" variant="outlined">
              Detection wasn&apos;t confident — defaulting to Generic. Pick a specific domain below
              if you know it.
            </Alert>
          ) : (
            <Box>
              <Typography variant="caption" sx={{ color: 'text.secondary', mb: 0.5, display: 'block' }}>
                Recommended (top {top3.length})
              </Typography>
              <ToggleButtonGroup
                exclusive
                size="small"
                value={selectedDomain}
                onChange={(_e, value: string | null) => {
                  if (value) setSelectedDomain(value);
                }}
                sx={{ flexWrap: 'wrap', gap: 0.5 }}
              >
                {top3.map((r) => (
                  <ToggleButton key={r.domain} value={r.domain} sx={{ textTransform: 'none', gap: 0.75 }}>
                    {r.domain}
                    <Chip label={r.score.toFixed(1)} size="small" sx={{ height: 18, fontSize: '0.65rem' }} />
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
            </Box>
          )}

          {extractEntities && availableDomains.length > 0 && (
            <DomainSelect
              selectedDomain={selectedDomain}
              availableDomains={availableDomains}
              onDomainChange={setSelectedDomain}
              contextWindow={contextWindow}
              groupSize={groupSize}
              inputPerChunk={inputPerChunk}
              outputPerChunk={outputPerChunk}
            />
          )}

          <ExtractionOptions
            extractEntities={extractEntities}
            onExtractEntitiesChange={setExtractEntities}
            enableVision={enableVision}
            onEnableVisionChange={setEnableVision}
            showNormalizationOption={false}
            enableNormalization={enableNormalization}
            onNormalizationChange={setEnableNormalization}
            analysisDepth={analysisDepth}
            onAnalysisDepthChange={setAnalysisDepth}
            contentFiltering={contentFiltering}
            onContentFilteringChange={setContentFiltering}
            filteringMode={filteringMode}
            onFilteringModeChange={setFilteringMode}
            skipDuplicates={skipDuplicates}
            onSkipDuplicatesChange={setSkipDuplicates}
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
