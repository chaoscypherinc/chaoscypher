// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { Dialog, DialogContent, DialogTitle, IconButton } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import type { SourceQualityScore } from '../../../../types';
import { QualityMetrics } from '../QualityMetricSection';

export interface QualityBreakdownDialogProps {
  open: boolean;
  score: SourceQualityScore | null;
  loading: boolean;
  onClose: () => void;
  onRecalculate: () => Promise<void> | void;
}

export function QualityBreakdownDialog({
  open,
  score,
  loading,
  onClose,
  onRecalculate,
}: QualityBreakdownDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center' }}>
        <span style={{ flex: 1 }}>Quality breakdown</span>
        <IconButton aria-label="close" onClick={onClose} size="small">
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>
      <DialogContent>
        {score ? (
          <QualityMetrics score={score} loading={loading} onRecalculate={onRecalculate} />
        ) : (
          <div style={{ padding: '24px 0', color: '#888' }}>
            No score yet — click "Recalculate" on the chip to compute.
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
