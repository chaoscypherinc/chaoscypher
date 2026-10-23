// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * GraphRagAccordion — what the chat's graph context may read, on the General
 * Settings tab. One switch today: whether relationships no cited sentence
 * states ("unevidenced": the extraction keeps them, flagged, rather than
 * deleting them) may enter the chat context. Off by default.
 *
 * Changes go through `PATCH /api/v1/settings` (the `graphrag` section,
 * persisted to settings.yaml), not the page-wide Save, so a toggle applies
 * to the next chat immediately — the Cache accordion's pattern.
 */

import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  FormControlLabel,
  LinearProgress,
  Switch,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import HubIcon from '@mui/icons-material/Hub';
import { accordionSummarySx, accentAccordionSx } from '../../theme/settings';
import { ACCENT_COLORS } from '../../theme/accentStyles';
import { ghostErrorAlertSx, ghostSwitchSx } from '../../theme/ghostStyles';
import { getApiErrorMessage } from '../../utils/errors';
import { useGraphRagSettings } from './hooks/useGraphRagSettings';

const ACCENT = 'info';

export const UNEVIDENCED_LABEL = 'Use unevidenced relationships in chat context';

export default function GraphRagAccordion() {
  const { graphrag, loading, error, pending, update } = useGraphRagSettings();
  const enabled = graphrag?.include_unevidenced_edges === true;

  // Failures land in the hook's `error` (shown below); swallow the rejection here.
  const handleToggle = (next: boolean) => {
    update({ include_unevidenced_edges: next }).catch(() => {});
  };

  return (
    <Accordion sx={accentAccordionSx(ACCENT)}>
      <AccordionSummary
        expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS[ACCENT] }} />}
        sx={accordionSummarySx}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
          <HubIcon sx={{ fontSize: 18, color: ACCENT_COLORS[ACCENT] }} />
          <Box>
            <Typography variant="subtitle2" sx={{ fontWeight: 'medium' }}>
              Chat context
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              What the chat reads from the knowledge graph
            </Typography>
          </Box>
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        {error && (
          <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }}>
            {getApiErrorMessage(error) || 'Failed to load the GraphRAG settings'}
          </Alert>
        )}
        {loading && <LinearProgress sx={{ mb: 2 }} />}
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={enabled}
              disabled={loading || pending || graphrag === null}
              onChange={(e) => handleToggle(e.target.checked)}
              sx={ghostSwitchSx}
              slotProps={{ input: { 'aria-label': UNEVIDENCED_LABEL } }}
            />
          }
          label={<Typography variant="body2">{UNEVIDENCED_LABEL}</Typography>}
        />
        <Typography
          variant="caption"
          component="p"
          sx={{ color: 'text.secondary', mt: 0.5, ml: 5.5 }}
          data-testid="unevidenced-help"
        >
          An unevidenced relationship is one the model extracted but that no sentence it cites
          actually states. Extraction keeps it, flagged (the graph draws it dashed), rather than
          deleting it. Off: the chat reads only relationships a sentence of the source states.
          On: unevidenced ones may enter the context too, ranked after every evidenced one.
        </Typography>
      </AccordionDetails>
    </Accordion>
  );
}
