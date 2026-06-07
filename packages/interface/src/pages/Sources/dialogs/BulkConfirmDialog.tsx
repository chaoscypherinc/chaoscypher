// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module BulkConfirmDialog
 * Bulk-confirm parked (awaiting_confirmation) sources. Each CONFIDENT source is
 * confirmed with its detected domain (ranking[0]). LOW-CONFIDENCE sources
 * (`detection_low_confidence === true` or empty ranking) are EXCLUDED from the
 * bulk payload and flagged for individual review — the single-source
 * ConfirmExtractionDialog is the right path for them (human picks the domain).
 *
 * Per-item: a failure on one confident row never aborts the others (contrast
 * index.tsx:185-206's abort-on-first delete loop); failures surface inline
 * against the offending file.
 */

import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Chip,
  Typography,
  CircularProgress,
} from '@mui/material';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import ErrorOutlinedIcon from '@mui/icons-material/ErrorOutlined';
import WarningAmberOutlinedIcon from '@mui/icons-material/WarningAmberOutlined';
import { AccentSection } from '../../../components/AccentSection';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';
import type { UnifiedSource } from '../../../types';

/**
 * Item passed to `onConfirmAll`. `domain` is informational/display-only — the
 * bulk endpoint is source_ids-only and re-reads the stored detected_domain on
 * the backend, so the per-item domain field is not sent in the payload (Task
 * 5.7 wiring: do not forward `domain` to the bulk API call).
 */
export interface BulkConfirmItem {
  source_id: string;
  domain?: string;
}

export interface BulkConfirmError {
  source_id: string;
  error: string;
}

interface BulkConfirmDialogProps {
  open: boolean;
  sources: UnifiedSource[];
  submitting: boolean;
  errors: BulkConfirmError[];
  onClose: () => void;
  onConfirmAll: (items: BulkConfirmItem[]) => void;
}

/** Returns true when a source is NOT safe to bulk-confirm as-detected. */
function isLowConfidence(s: UnifiedSource): boolean {
  return !!s.detection_low_confidence || !s.detection_ranking?.length;
}

export function BulkConfirmDialog({
  open,
  sources,
  submitting,
  errors,
  onClose,
  onConfirmAll,
}: BulkConfirmDialogProps) {
  const errorById = new Map(errors.map((e) => [e.source_id, e.error]));

  const confidentSources = sources.filter((s) => !isLowConfidence(s));
  const lowConfidenceCount = sources.length - confidentSources.length;
  const allLowConfidence = sources.length > 0 && confidentSources.length === 0;

  const handleConfirmAll = () => {
    onConfirmAll(
      confidentSources.map((s) => ({
        source_id: s.id,
        domain: s.detection_ranking?.[0]?.domain,
      })),
    );
  };

  const confirmLabel = (() => {
    if (submitting) return 'Confirming...';
    if (lowConfidenceCount > 0 && confidentSources.length > 0) {
      return `Confirm ${confidentSources.length} detected`;
    }
    return 'Confirm All';
  })();

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      slotProps={{ paper: { sx: ghostDialogPaperSx } }}
    >
      <DialogTitle sx={{ color: 'text.primary' }}>
        Confirm {sources.length} source{sources.length === 1 ? '' : 's'}
      </DialogTitle>
      <DialogContent>
        <Typography variant="caption" sx={{ color: 'text.secondary', mb: 1, display: 'block' }}>
          Each source is confirmed with its recommended domain. Use the single
          source dialog to override a domain.
        </Typography>
        {lowConfidenceCount > 0 && (
          <Typography
            variant="caption"
            sx={{ color: ChaosCypherPalette.warning, mb: 1.5, display: 'block' }}
          >
            {allLowConfidence
              ? `All ${lowConfidenceCount} source${lowConfidenceCount === 1 ? '' : 's'} need individual review — open each one to confirm its domain.`
              : `${lowConfidenceCount} source${lowConfidenceCount === 1 ? '' : 's'} need individual review and will not be bulk-confirmed.`}
          </Typography>
        )}
        <AccentSection color="file" sx={{ maxHeight: 280, overflow: 'auto' }}>
          {sources.map((s, idx) => {
            const low = isLowConfidence(s);
            const top = s.detection_ranking?.[0]?.domain;
            const err = errorById.get(s.id);
            return (
              <Box
                key={s.id}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1.5,
                  py: 1,
                  borderBottom: idx < sources.length - 1 ? '1px solid' : 'none',
                  borderColor: 'rgba(255, 255, 255, 0.06)',
                  opacity: low ? 0.75 : 1,
                }}
              >
                <UploadFileIcon
                  sx={{
                    fontSize: 20,
                    color: low ? ChaosCypherPalette.warning : ChaosCypherPalette.primary,
                  }}
                />
                <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                  <Typography variant="body2" noWrap sx={{ fontWeight: 600, color: 'text.primary' }}>
                    {s.title}
                  </Typography>
                  {err ? (
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                      <ErrorOutlinedIcon sx={{ fontSize: 13, color: 'error.main' }} />
                      <Typography variant="caption" color="error">
                        {err}
                      </Typography>
                    </Box>
                  ) : low ? (
                    <Chip
                      icon={<WarningAmberOutlinedIcon sx={{ fontSize: '13px !important' }} />}
                      label="Review individually — detection wasn't confident"
                      size="small"
                      sx={{
                        height: 20,
                        fontSize: '0.68rem',
                        color: ChaosCypherPalette.warning,
                        borderColor: `${ChaosCypherPalette.warning}55`,
                        bgcolor: `${ChaosCypherPalette.warning}14`,
                        '& .MuiChip-icon': { color: ChaosCypherPalette.warning },
                      }}
                      variant="outlined"
                    />
                  ) : (
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                      {top ?? 'choose a domain'}
                    </Typography>
                  )}
                </Box>
              </Box>
            );
          })}
        </AccentSection>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>
          Cancel
        </Button>
        <Button
          variant="outlined"
          onClick={handleConfirmAll}
          disabled={submitting || sources.length === 0 || allLowConfidence}
          startIcon={submitting ? <CircularProgress size={16} sx={{ color: 'primary.main' }} /> : undefined}
          sx={ghostButtonSx(ChaosCypherPalette.primary)}
          title={allLowConfidence ? 'All sources need individual review' : undefined}
        >
          {confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
