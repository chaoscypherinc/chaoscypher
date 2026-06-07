// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  Box,
  Typography,
  Button,
  Alert,
  CircularProgress,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Chip,
  Divider,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import BuildCircleIcon from '@mui/icons-material/BuildCircle';
import { useIndexStatus, useRebuildIndexes } from './hooks/useSearchIndex';
import { accordionSummarySx, accordionBtnSx, accentAccordionSx } from '../../theme/settings';
import EmbeddingProviderConfig from './components/EmbeddingProviderConfig';
import { ACCENT_COLORS } from '../../theme/accentStyles';
import type { Settings } from '../../types';
import { getApiErrorMessage } from '../../utils/errors';

interface SearchTabProps {
  settings: Settings;
  setSettings: (settings: Settings) => void;
}

export default function SearchTab({ settings, setSettings }: SearchTabProps) {
  const { data: indexStatus } = useIndexStatus();
  const rebuildIndexes = useRebuildIndexes();
  const rebuilding = rebuildIndexes.isPending;

  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // What the index was built with — used to detect mismatches instantly.
  const indexModel = indexStatus?.embedding_model || '';
  const indexDimensions = indexStatus?.vector_dimensions || 0;
  const needsRebuild = indexStatus?.needs_rebuild ?? false;

  // Instantly detect mismatch when user changes model or dimensions
  const currentModel = settings.embedding?.model || '';
  const currentDimensions = settings.search?.vector_dimensions || 0;
  const localMismatch = indexModel && currentModel
    ? (currentModel !== indexModel || currentDimensions !== indexDimensions)
    : false;
  const showRebuildWarning = needsRebuild || localMismatch;

  const handleRebuild = async (e?: React.MouseEvent) => {
    e?.stopPropagation();
    setSuccess(null);
    setError(null);
    try {
      const result = await rebuildIndexes.mutateAsync();

      if (result.task_id) {
        setSuccess(
          `Rebuild queued (task ${result.task_id}). ` +
          `Regenerating all embeddings in the background. Check the Queue Monitor for progress.`
        );
      } else {
        setSuccess(
          `Rebuilt successfully. ` +
          `${result.total_nodes || 0} nodes, ` +
          `${result.nodes_with_embeddings || 0} with embeddings, ` +
          `${result.chunks_indexed || 0} chunks indexed.`
        );
      }
    } catch (err) {
      setError(getApiErrorMessage(err) || 'Rebuild failed. Please try again.');
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <EmbeddingProviderConfig settings={settings} setSettings={setSettings} />
      <Divider sx={{ my: 3 }} />
      {success && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>
          {success}
        </Alert>
      )}
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2
        }}>
        <Accordion sx={accentAccordionSx('filtering')}>
          <AccordionSummary
            expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.filtering }} />}
            sx={accordionSummarySx}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
              <BuildCircleIcon sx={{ fontSize: 18, color: ACCENT_COLORS.filtering }} />
              <Typography variant="subtitle2" sx={{
                fontWeight: "medium"
              }}>
                Rebuild Search Indexes
              </Typography>
              {showRebuildWarning && (
                <Chip
                  size="small"
                  label="mismatch detected"
                  color="warning"
                  variant="outlined"
                  sx={{ height: 20, fontSize: '0.7rem' }}
                />
              )}
              <Button
                size="small"
                variant="outlined"
                color="success"
                onClick={handleRebuild}
                disabled={rebuilding}
                sx={accordionBtnSx}
              >
                {rebuilding ? <CircularProgress size={14} /> : 'Rebuild'}
              </Button>
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            {showRebuildWarning && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                <Typography variant="body2">
                  <strong>Embedding mismatch detected.</strong> Stored embeddings don&apos;t match the current model.
                  Search results may be inaccurate. Save settings then click Rebuild to regenerate all embeddings.
                </Typography>
              </Alert>
            )}
            <Alert severity="info">
              <Typography variant="body2" sx={{ mb: 1 }}>
                <strong>Auto-detects</strong> whether embeddings need regeneration:
              </Typography>
              <Typography variant="body2" component="div">
                <ul style={{ marginTop: 4, marginBottom: 0, paddingLeft: 20 }}>
                  <li>If the model or dimensions changed, all embeddings are regenerated from text (queued in background)</li>
                  <li>Otherwise, indexes are rebuilt from stored embeddings (instant)</li>
                </ul>
              </Typography>
            </Alert>
          </AccordionDetails>
        </Accordion>
      </Box>
    </Box>
  );
}
